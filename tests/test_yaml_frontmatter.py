"""Unit tests for the shared YAML front-matter scanner (yaml_frontmatter.py)."""

from agentteams.yaml_frontmatter import parse_yaml_front_matter


class TestParseYamlFrontMatter:
    # --- happy path ---

    def test_basic_front_matter(self):
        content = "---\nname: foo\ndescription: bar\n---\nbody\n"
        yaml_text, body = parse_yaml_front_matter(content)
        assert yaml_text is not None
        assert "name: foo" in yaml_text
        assert "description: bar" in yaml_text
        assert "body" in body
        assert "---" not in body.splitlines()[0]

    def test_empty_front_matter(self):
        # yaml_text is '' (falsy) — but is NOT None; distinguish with 'is not None'
        yaml_text, body = parse_yaml_front_matter("---\n---\nbody\n")
        assert yaml_text == ""
        assert yaml_text is not None
        assert "body" in body

    def test_body_is_empty(self):
        yaml_text, body = parse_yaml_front_matter("---\nname: foo\n---\n")
        assert yaml_text is not None
        assert body == ""

    def test_trailing_whitespace_on_opening_delimiter(self):
        yaml_text, body = parse_yaml_front_matter("---  \nname: foo\n---\nbody\n")
        assert yaml_text is not None
        assert "name: foo" in yaml_text

    def test_trailing_whitespace_on_closing_delimiter(self):
        yaml_text, body = parse_yaml_front_matter("---\nname: foo\n---  \nbody\n")
        assert yaml_text is not None
        assert "body" in body

    # --- no front matter ---

    def test_no_front_matter_plain_body(self):
        content = "# Heading\n\nSome content\n"
        yaml_text, body = parse_yaml_front_matter(content)
        assert yaml_text is None
        assert body == content

    def test_no_closing_delimiter(self):
        content = "---\nname: foo\n"
        yaml_text, body = parse_yaml_front_matter(content)
        assert yaml_text is None
        assert body == content

    def test_empty_string(self):
        yaml_text, body = parse_yaml_front_matter("")
        assert yaml_text is None
        assert body == ""

    # --- THE BUG CASES (MAP-06 / MAP-17) ---

    def test_eof_terminated_front_matter_no_trailing_newline(self):
        """Regression (MAP-06): old regex required \\n---\\s*\\n so it missed files
        where the closing '---' is at EOF without a trailing newline.
        Old _YAML_FRONT_MATTER_RE.match returns None here; new scanner must succeed."""
        content = "---\nname: foo\n---"  # no trailing newline
        yaml_text, body = parse_yaml_front_matter(content)
        assert yaml_text is not None, (
            "front matter must be detected even without a trailing newline after '---'"
        )
        assert "name: foo" in yaml_text
        assert body == ""

    def test_pipe_block_scalar_with_embedded_dash_separator(self):
        """Regression: block scalar value containing '---' must not close the front matter early."""
        content = (
            "---\n"
            "name: my-agent\n"
            "description: |\n"
            "  Some explanation\n"
            "  ---\n"               # this is the trigger line; must be treated as scalar content
            "  More explanation\n"
            "---\n"
            "# Body heading\n"
        )
        yaml_text, body = parse_yaml_front_matter(content)
        assert yaml_text is not None, "front matter should be detected"
        assert "name: my-agent" in yaml_text
        assert "description: |" in yaml_text
        assert "---" not in yaml_text.strip().splitlines()[-1], \
            "trailing --- of scalar content must not appear as the final yaml line"
        assert "# Body heading" in body

    def test_folded_block_scalar_with_embedded_dash_separator(self):
        """Same as above but with the folded '>' block scalar indicator."""
        content = (
            "---\n"
            "name: my-agent\n"
            "notes: >\n"
            "  Line one.\n"
            "  ---\n"
            "  Line two.\n"
            "---\n"
            "Body text.\n"
        )
        yaml_text, body = parse_yaml_front_matter(content)
        assert yaml_text is not None
        assert "notes: >" in yaml_text
        assert "Body text." in body

    def test_multiple_embedded_dash_separators_in_scalar(self):
        """Multiple '---' lines inside a block scalar; only the real closing delimiter ends the block."""
        content = (
            "---\n"
            "name: x\n"
            "body: |\n"
            "  ---\n"
            "  ---\n"
            "  ---\n"
            "---\n"
            "real body\n"
        )
        yaml_text, body = parse_yaml_front_matter(content)
        assert yaml_text is not None
        assert "real body" in body

    def test_dash_separator_inside_inline_quoted_value_not_a_problem(self):
        """Inline quoted value containing '---' is safe (not at column 0) — scanner must not break."""
        content = (
            "---\n"
            'description: "a --- b"\n'
            "name: foo\n"
            "---\n"
            "body\n"
        )
        yaml_text, body = parse_yaml_front_matter(content)
        assert yaml_text is not None
        assert "name: foo" in yaml_text
        assert "body" in body

    def test_indented_block_scalar_in_nested_mapping(self):
        """Block scalar under a nested key (indented); embedded '---' inside still safe."""
        content = (
            "---\n"
            "metadata:\n"
            "  title: foo\n"
            "  notes: |\n"
            "    line one\n"
            "    ---\n"
            "    line two\n"
            "---\n"
            "# Body\n"
        )
        yaml_text, body = parse_yaml_front_matter(content)
        assert yaml_text is not None
        assert "# Body" in body

    def test_mid_line_dashes_in_yaml_value_not_treated_as_delimiter(self):
        """'---' as a substring of a value (not on its own line) must not close the block."""
        content = (
            "---\n"
            "name: my---agent\n"
            "version: 1---0\n"
            "---\n"
            "body\n"
        )
        yaml_text, body = parse_yaml_front_matter(content)
        assert yaml_text is not None
        assert "name: my---agent" in yaml_text
        assert "body" in body
