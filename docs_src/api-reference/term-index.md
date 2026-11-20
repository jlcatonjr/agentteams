# `term_index` **(beta)**

A term register, an occurrence index over a set of documents, and the checks on both. The module is **beta**:
outside SemVer in 1.3.x (see `STABILITY.md`). It is the core of researchteam's term index, moved into
agentteams (issue #548) so that a single implementation serves both. Standard library only. Nothing in it
writes a file.

!!! warning "Ceiling"
    Matching finds forms, not meanings. A clean check establishes that the register is well formed and,
    when an index is committed, that the index is current. It does **not** establish that a term is used
    in its defined sense.

## The register

`<dir_name>/terms.json` (default `terminology/terms.json`) under a project directory:

```json
{
  "schema": "agentteams-term-register",
  "schema_version": "1.0",
  "scope": {"include": ["**/*.md"], "exclude": []},
  "terms": [
    {"id": "constitutional-tier", "label": "constitutional tier", "definition": "…",
     "variants": ["authority tier"], "discouraged": ["Tier 1"], "source": "project definition"}
  ],
  "allow": [{"term": "constitutional-tier", "file": "history.md", "reason": "quotes the old wording"}]
}
```

- **`schema`.** Two values are accepted:
    - `agentteams-term-register`: the neutral name;
    - `researchteam-term-register`: researchteam's name, read unchanged. A researchteam register keeps its bytes,
      and so its hash, and its committed index stays current. (Renaming the marker would change the hash and make
      the index `STALE` until rebuilt.)

  A `terms.json` with any other `schema` is *foreign*: it is not treated as a register.
- **`terms`.** Each term has an `id`, a `label` and a `definition`, plus these optional keys:
    - `variants`;
    - `discouraged` forms;
    - `source`: either `"project definition"` or a list of keys, which `source_validator` checks;
    - `notes`;
    - `plural`: default `true`, which makes `s` and `es` match automatically.
- **Case.** A lower-case form matches without regard to case. A form with a capital letter matches exactly.
- **Masked text.** Matching skips front matter, fenced and inline code, HTML comments, link targets and URLs.
- **Quotations.** A discouraged form inside a quotation (a blockquote line or balanced double quotation
  marks) is exempt, and is reported as `QUOTED`.

## Configuration

```python
@dataclass(frozen=True)
class TermIndexConfig:
    generator: str = "agentteams-term-index/1"
    dir_name: str = "terminology"
    schemas: tuple[str, ...] = ("agentteams-term-register", "researchteam-term-register")
    skip_heading: re.Pattern | None = None          # sections that are not prose (researchteam: reference lists)
    source_validator: Callable[[Path, dict], list] | None = None   # -> [(code, message), ...]
    region_filter: Callable[[str], str] | None = None              # text actually scanned; must keep length
    default_include: tuple[str, ...] = ("**/*.md",)
    do_not_edit: str = "GENERATED from terminology/terms.json by agentteams.term_index. Do not edit."
    rebuild_hint: str = "term-build"
    glossary_hint: str = "Where each term occurs: the term-index query."
```

Every point that was specific to researchteam is now a parameter. With researchteam's values (its generator
string, reference-list heading, banner and glossary line), the module reproduces researchteam's generated
files byte for byte. `tests/test_term_index.py` holds a golden fixture written by researchteam's own engine.

The byte identity covers the **generated files**. A researchteam caller still supplies:
- `schemas=("researchteam-term-register",)`, if the neutral marker should stay foreign;
- `required` (researchteam: a named project, or `term_index_require` and a research project);
- `is_project` (its nested-project rule);
- `source_validator` (its `.bib` check).

`NOREG` and the `STALE` hints are worded neutrally, not as researchteam words them.

## Functions

```python
load_register(proj: Path, config=TermIndexConfig()) -> Assessment          # status: missing | foreign | bad | ok
register_text_problems(reg: dict) -> list[tuple[str, str]]
documents(proj, reg, config=..., is_project=None) -> list[str]
assess(proj, config=..., reuse=False, is_project=None) -> Assessment         # .files: what a build would write
check_project(proj, config=..., *, required=False, indexed=True, is_project=None) -> (checked, defects, reviews)
discover(base, is_project, *, pruned=frozenset(), skip_dir="terminology", max_depth=12) -> list[Path]
read_manifest(proj, config=...) -> dict | None
read_document(path, root=None) -> bytes | None                               # None: a link, not a regular file, or over MAX_DOC_BYTES
read_capped(root, rel, cap) -> bytes | None                                  # no link anywhere below root; reads at most cap + 1 bytes
stray_index_files(proj, files, config=...) -> set[str]
disc_violations(reg, rel, occs) -> list[dict]
glob_match(pattern, path) -> bool
mask(text, skip_heading=None) -> (masked, heading_ends)
Matcher(reg, skip_heading=None).occurrences(text) -> list[dict]             # {term, line, column, form, kind, quoted}
```

Every file the module reads (the register, each document, the manifest and the index files) goes through `read_capped`: each path component is opened with `O_NOFOLLOW` relative to its parent (where the platform has `dir_fd`), the file is checked with `fstat` and read through the same descriptor up to its cap (`MAX_REGISTER_BYTES`, `MAX_DOC_BYTES`, `MAX_GENERATED_BYTES`), so a link or a larger file swapped in after a check is refused, not followed or read whole.

`check_project` returns `(code, message)` findings in two groups.

**Defects** (blocking):

| Code | Meaning |
|---|---|
| `SCHEMA` | The register is malformed. |
| `AMBIG` | Two terms claim the same form, or one form is listed as both accepted and discouraged. |
| `SRC` | A `source` key failed the `source_validator`. |
| `SCOPE` | A scope pattern is unsafe, or the scope matches no document. |
| `STALE` | The committed index does not match the documents. Checked only when `indexed`. |
| `DISC` | A discouraged form appears outside a quotation, with no `allow` entry. |
| `ORPHAN` | Generated files exist without a register. |
| `NOREG` | The register is missing and `required` is set. |

**Reviews** (advisory):

| Code | Meaning |
|---|---|
| `REBUILD` | The inputs are unchanged, but the index was built by another generator. |
| `NOSRC` | A term has no `source`. |
| `UNUSED` | A term never occurs. |
| `QUOTED` | Discouraged forms appear inside quotations, which is exempt. The count keeps such quoting visible. |

## The agentteams gate

`agentteams --term-check [AGENTS_DIR]` (`agentteams.cli.term_check`) checks a team's register against the
**text inside `AGENTTEAMS` fences only**, using `fenced_only` as the region filter. So it never flags a
user-editable section, such as Project-Specific Rules or Notes.

- **Opt-in.** A team opts in by adding `<agents dir>/terminology/terms.json`.
- **Well-formed fences only.** Fence markers are read with the fence engine's grammar. A file whose fences are
  unclosed, nested or mismatched, or has an `END` without a `BEGIN`, is not scanned at all. It is a blocking
  `FENCE` defect when the file also holds a complete fence (a generated file whose fences broke), and an
  advisory `FENCE` review otherwise (a hand-written file quoting a lone marker). A broken marker never widens
  the scan into user text.
- **Live.** The check covers the register, its scope and discouraged forms (`SCHEMA AMBIG SCOPE DISC ORPHAN
  NOREG FENCE`). No source validator is set, so `SRC` cannot fire; only the advisory `NOSRC` can. A committed
  generated file (`term-index.json`, `index/`, `glossary.md`) is not compared: it gets an advisory `INDEX` note. Use `check_project(..., indexed=True)`
  for a committed-index workflow.
- **Exit codes.** `0` when there is no defect, `1` on a defect, `2` on an I/O error. A named directory with no
  register is `NOREG`. Running in the working directory with no register prints `NOTHING CHECKED` and exits
  `0`. `--term-check-advisory` (which requires `--term-check`) reports defects but exits `0`.

## The opt-in hook

When the `bridge:copilot-vscode-to-claude:hooks` feature is selected **and** the canonical agents directory
holds `terminology/terms.json`, [`hooks_emit`](hooks-emit.md) adds one `PostToolUse` (`Write|Edit`) entry. It
runs `agentteams --term-check "$CLAUDE_PROJECT_DIR"/<agents dir> --term-check-advisory` and appends the report
to `$CLAUDE_PROJECT_DIR/.claude/hook-notices/term-check.log`. Without `$CLAUDE_PROJECT_DIR`, it uses `.`. It is
advisory: it never blocks a tool call. It exits `0` even when `agentteams` is missing or too old, and the shell's
error goes into the log. The log grows on every Write or Edit and is not rotated.

## See also

- [`hooks_emit`](hooks-emit.md): the hooks example block.
- researchteam `docs/term-index-protocol.md`: the protocol this core came from.
