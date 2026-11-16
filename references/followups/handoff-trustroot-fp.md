# Follow-up: the handoff trust-root check refuses prose in git commit messages

**Status:** open (written 2026-11-15) · **Source:** remediation log line 738 / record 737 (that log is local and gitignored; its text is quoted below) · **Suggested owner:** @framework-adapters-expert or @primary-producer (code), @test-suite-expert (tests), @security (mandatory review), @code-hygiene (audit), @adversarial

> "sandbox_handoff's trust-root check (trust_roots_named -> target_is_governance_root) shlex-splits every step's run text and substring-matches each token after canon_path, so prose in a commit message (-m '... the security decisions log ...') is refused as a trust-root write. Seen 2026-11-15 writing leftovers-appendfix (twice; reworded to pass). A false positive, fail-closed, but it pushes authors to reword truthful messages. Consider matching only path-like tokens or command targets, not -m message arguments."

## Report

**What is wrong.** `trust_roots_named` in `agentteams/sandbox_handoff.py` collects `spec.touches` plus every `shlex.split` token of every step and verify `run`. A token is a hit when `target_is_governance_root(token)` is true or it contains an `EXTRA_TRUST_ROOT_TOKENS` substring. `target_is_governance_root` (`agentteams/cli/governance_targets.py`) runs `canon_path`, which folds whitespace, dots, slashes, underscores and `%` into hyphens, then substring-matches `GOVERNANCE_TARGET_STEMS`. A quoted commit message is one shlex token. So `'... the security decisions log'` folds to `...-the-security-decisions-log`, which contains the stem `security-decision`. `validate_spec` then raises "a handoff may not write a trust root".

**Evidence (verified on origin/dev 33824c72, from a `git archive` copy run with `python3 -I`).**
- `git commit -m 'fix: append to the security decisions log'` is a hit: token folds to `fix:-append-to-the-security-decisions-log`, stem `security-decision`.
- Same for `-m 'record the waiver discussion'` (stem `waiver`), `-m 'mention claude md rules'` (stem `claude-md`), and `--message='bump waiver tests'`.
- `printf '%s' 'waiver' > notes.txt` is also a hit. The false positive is not limited to git messages.
- Heredoc bodies are not affected. shlex splits them into single words, and one word rarely carries a two-part stem.
- Baseline: `tests/test_sandbox_handoff.py` passes on origin/dev (86 passed; rechecked at 7563b693, still 86).
- Record 736's gap is real too: the additional instruction files named in remediation record 736 (local log) produce no hit today.

**Known vs not yet known.** Known: the mechanism, the stems that fire on prose (`waiver`, `security-decision`, `claude-md`, `signing-key`, `verify-key` and others), and that the check is a lexical backstop. Values built at run time evade the lexical check. This is the bounded residual that `canon_path`'s docstring documents. Not yet known: how often non-git prose (`gh pr create --body`, `echo`) trips it (unverified; only the git case is reported).

**Risk of leaving it.** Authors reword truthful commit messages to get past a security control. That trains people to work around the check, and the history loses accuracy. Fail-closed, so there is no exposure.
**Risk of fixing it badly.** A trust-root write slips through unprompted. The script runs `s.run` verbatim in bash (`_block`), so anything the scanner hides can execute: `$(...)`, backticks, `;` in an unquoted `-m` value, `eval` or `bash -c` re-parsing, or a pathspec after `-Fm`.

**Options weighed.**
- (b) Match only path-like tokens: **rejected.** `EXTRA_TRUST_ROOT_TOKENS` includes environment-variable names (`AGENTTEAMS_MANAGEMENT_SIGNING_KEY`), which are not paths. Rejecting them would break `test_trust_roots_are_refused_in_commands_and_touches`. It would also miss `X=…; echo > $X`, `python -c "open('…')"` and `tee` targets in odd spellings.
- (c) An operator-reviewed exemption field: **rejected.** The spec author would write it, so it is a self-declared relaxation. Rule 15 requires relaxing classes to be derived, never self-declared, and the module's own comment says a reviewed-looking script is how a forged grant would arrive.
- (a) Skip `-m`/`--message` values: **recommended, in a narrow form.** Mask a message value only when bash cannot execute it in any context:
  - it belongs to a `git … commit|merge|tag` segment, using the existing `_GIT` prefix, and git is the command word of that segment;
  - the segment is not re-parsed by a wrapper: never mask inside or after `eval`, `sh -c`, `bash -c`, `zsh -c`, `xargs`, `env`, `command`, `exec`, `sudo`, `nohup` or `time`, or in any segment where git is not the command word;
  - the value comes before any `--` in the segment (after `--` every argument is a pathspec, so nothing after it is masked);
  - the token before `-m` is not a flag that takes a value: `-C`, `-c`, `-F`, `-t`, `--author`, `--date`, `--file`, `--template`, `--reuse-message`, `--reedit-message`, `--fixup`, `--squash`, `-S`/`--gpg-sign` (in `git commit -C -m 'x'` the `-m` is `-C`'s value, not a flag). The attached forms (`-Cvalue`, `--author=value`, `-Skeyid`, `--gpg-sign=keyid`) consume no next token, so they do not block masking; the bare `-S`/`--gpg-sign` takes no value, but treat it as value-taking anyway (fail closed);
  - the flag is `-m`, `-[aqv]*m` or `--message[= ]`;
  - the value is a single- or double-quoted literal whose characters all come from an allow-list: `[\w .,:/#+=@%()\[\]-]`, plus `'` when it is double-quoted;
  - the closing quote is followed by end of text, whitespace, `;&|` or `)`.
  Anything else is still scanned. `-F` is not masked, because its value is a path. A prototype of this regex (`(?P<q>'[\w .,:/#+=@%()\[\]-]*'|"[\w .,:/#+=@%()\[\]'-]*")`) passed every case listed below. It is an untested sketch, not the final code.

## Plan

**Preconditions and constraints.**
- Work in a worktree off origin/dev: `git worktree add --no-track ../agentteams-wt/handoff-trustroot-fp -b fix/handoff-trustroot-fp origin/dev`. The shared checkout stays on `main`.
- Merge into `dev` with `--no-ff` behind the §G local gate in `references/git-procedures.md`.
- The Claude Code sandbox cannot add worktrees, commit or push. Those steps go through an operator handoff (§H).
- The handoff writer an agent runs from the shared checkout is `main`'s code. This fix only helps once it is released, or when the writer is run from the item's worktree: `cd <worktree> && python3 -I -m agentteams.sandbox_handoff ...` imports that tree's code only if run from its root. Verify with `python3 -I -c 'import agentteams,sys;print(agentteams.__file__)'` from that directory.
- @security review is mandatory, because this narrows a C-3 / Rule 15(c) refusal. @code-hygiene audits the diff.
- Sequencing: land record 736 first, then this item, then the local integration-merge template (`dev-merge-template.md`, record 734). All three edit `sandbox_handoff.py`, so run them one after another, not on parallel branches.
  - 736 only widens refusal, so it is safe to land alone. The adversarial suite here should then include the additional instruction files named in remediation record 736 (local log).
  - The integration-merge template will emit `git merge --no-ff -m '…'` steps, which this fix un-blocks (unverified, until that template is drafted).

**Steps.**
1. Operator: create the worktree and branch. Done when `git -C ../agentteams-wt/handoff-trustroot-fp log -1` shows the origin/dev tip.
2. @test-suite-expert: add the tests first. Put the ALLOW cases in a new parametrized test that must pass, and the REFUSE cases in one that must raise "trust root". Done when the ALLOW cases fail on unmodified code and the REFUSE cases pass.
3. @primary-producer: add a private `_inert_git_messages_masked(run) -> str` to `sandbox_handoff.py`. It replaces each qualifying literal with a neutral placeholder. Call it in `trust_roots_named` before `shlex.split`. Leave `touches`, `canon_path` and the stems unchanged. Add a comment that states the allow-list rationale (wrapper re-parsing such as `eval` / `bash -c` / `xargs`, `--` pathspecs, value-taking flags before `-m`). Done when every new and existing test passes.
4. @security, then @adversarial: review the regex against the REFUSE list and try new evasions. Done when there is a recorded clearance or a list of findings, and findings are fixed.
5. @code-hygiene: audit. Done when it reports no findings, or its findings are fixed.
6. Operator: commit, merge `--no-ff` into `dev` behind the §G gate, and push. Done when `dev` contains the merge and CI is green.
7. @agent-updater: Rule 13 cross-reference. Governance logs here are append-only, so do not edit record 737 in place. Append a new remediation-log row that cross-references record 737 and the merge sha, using `agentteams.governance_logs.append_row`; record 737's status stays untouched for independent verification. Until the `append_row` LF-tail fix (dev f9b8b674) is released, import that function from an origin/dev copy, because `main`'s copy adds an empty record after a bare-LF row. Add a CHANGELOG entry under Unreleased. Done when the new log row and the CHANGELOG entry exist.

| step | agent | action | inputs | outputs | status | notes | depends_on |
|---|---|---|---|---|---|---|---|
| 1 | operator | create worktree fix/handoff-trustroot-fp off origin/dev | origin/dev | ../agentteams-wt/handoff-trustroot-fp | pending | sandbox cannot add worktrees | |
| 2 | test-suite-expert | add ALLOW/REFUSE parametrized tests | this doc | tests/test_sandbox_handoff.py | pending | ALLOW fail before fix | 1 |
| 3 | primary-producer | mask bash-inert git -m literals before shlex scan | sandbox_handoff.py | sandbox_handoff.py | pending | touches/stems unchanged | 2 |
| 4 | security | C-3/Rule 15(c) review of narrowed refusal; adversarial probing | diff | clearance or findings | pending | mandatory | 3 |
| 5 | code-hygiene | audit diff | diff | audit result | pending | | 4 |
| 6 | operator | commit, --no-ff merge into dev behind §G gate, push | branch | dev merge | pending | sandbox cannot commit/push | 5 |
| 7 | agent-updater | append Rule 13 cross-reference row for record 737 via append_row (origin/dev copy); CHANGELOG Unreleased | merge sha | new log row, CHANGELOG.md | pending | append-only; do not edit or flip 737 | 6 |

**Adversarial cases that must keep refusing:**
- `git commit -m "$(echo x > CLAUDE.md)"`
- ``git commit -m "`cp a CLAUDE.md`"``
- `git commit -m msg;echo>CLAUDE.md`
- `git commit -m 'ok' > CLAUDE.md`
- `git commit -m 'ok' -- CLAUDE.md`
- `git commit -m 'ok' 'CLAUDE.md'`
- `git commit -m 'it'\''s' CLAUDE.md`
- `git commit -m 'x'CLAUDE.md`
- `git commit -m 'waiver'"$(touch CLAUDE.md)"`
- `git commit -m 'ok' && echo hi > references/security-decisions.jsonl`
- `git commit -F references/security-decisions.jsonl`
- `git commit -Fm 'CLAUDE.md'`, `git commit -cm 'CLAUDE.md'`, `git tag -um 'CLAUDE.md'`
- `bash -c "git commit -m 'x $(cp a CLAUDE.md)'"`
- `eval git commit -m "a;cp b CLAUDE.md"`, `eval git commit -m "a > CLAUDE.md"`
- `sh -c "git commit -m 'a' ; cp b CLAUDE.md"`
- `eval git commit -m "x CLAUDE.md"`, `xargs git commit -m 'CLAUDE.md'`
- `git commit -a -- -m 'CLAUDE.md'`
- `git commit -C -m 'CLAUDE.md'`, `git commit -F -m 'CLAUDE.md'`
- `echo -m 'security decisions' > CLAUDE.md`, `python -m 'x' > CLAUDE.md`
- `cat > CLAUDE.md <<'EOF'` (heredoc)
- `cp a .claude/settings.json`
- the four existing parametrized trust-root tests and `touches=[…]`
- after 736 lands: the same shapes aimed at the additional instruction files named in remediation record 736 (local log)

**Cases that must now pass:**
- `-m 'fix: append to the security decisions log'`
- `-m "record the waiver discussion"`
- `git -C ../wt commit -qm 'claude md wording'`
- `--message='bump waiver tests'`
- `git merge --no-ff feat/x -m '…'`
- two `-m` paragraphs
- `git tag -a v1 -m 'waiver notes'`
- `-m "fix(sandbox): don't refuse the waiver prose"`

**Verification.** In the worktree:
- `python3 -m pytest -q tests/test_sandbox_handoff.py tests/test_handoff_payloads.py tests/test_sidecar_handoffs.py tests/test_vulnscan_handoff.py`
- then the full `python3 -m pytest -q`
- `git diff origin/dev --stat` shows only the files listed below.

**Rollback.** `git revert -m 1 <merge-sha>` on `dev`, done by the operator. A revert restores the stricter, fail-closed behaviour, so it is always safe.

**Files likely touched:** `agentteams/sandbox_handoff.py`, `tests/test_sandbox_handoff.py`, `CHANGELOG.md`.
**Must NOT be touched:** `agentteams/cli/governance_targets.py`. Its stems and `canon_path` serve every caller on origin/dev: `carveouts`, `cli/dev_merge`, `cli/effect_classifier`, `cli/management_directives`, `cli/operator_signing`, `cli/toolchain_group`, `integrity`, `sandbox_handoff`, `vulnscan/known` and `wasm_config`. Loosening them there would widen all of them. Also leave `EXTRA_TRUST_ROOT_TOKENS` (736 owns it), `_DESTRUCTIVE_PATTERNS`, and all constitution and settings files alone.

**Open questions for the operator.**
- Should `gh pr create --title/--body` literals get the same masking? Recommend no until a reported case exists.
- Should the `printf 'waiver' > notes.txt` class of false positive stay? Recommend yes: it is fail-closed and rare (unverified).
