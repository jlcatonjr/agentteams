# `pr_management`

GitHub PR lifecycle helpers for AgentTeamsModule. Drives the end-of-task three-way disposition prompt (`continue-branch` / `push-main` / `open-pr`), provides gh-CLI wrappers behind a mockable runner, and powers the stale-PR scan + dedup that the daily `pr-reminders.yml` workflow runs.

All `gh` CLI calls go through a single `_run_gh` shim so tests can inject a mock runner; no real subprocess is invoked unless the caller (or the CLI) actually runs it.

## Layout

- **Module:** `agentteams.pr_management` (importable)
- **CLI:** `python -m agentteams.pr_management {prompt, remind}`
- **Schema:** `schemas/pr-recipient-registry.schema.json`
- **Seed registry:** `references/pr-recipients.json`
- **Daily workflow:** `.github/workflows/pr-reminders.yml`
- **Workflow entry-point:** `scripts/post_pr_reminders.py`

## Public Surface

### Registry

```python
@dataclass(frozen=True)
class Recipient:
    login: str
    role: str                       # "owner" | "reviewer" | "maintainer" | "contributor"
    default_assignee: bool = False
    opt_out: bool = False
    reminder_interval_hours: int | None = None
    notifications_confirmed: bool = False

@dataclass(frozen=True)
class Registry:
    version: int
    default_reminder_interval_hours: int
    recipients: tuple[Recipient, ...]
    def active(self) -> tuple[Recipient, ...]: ...
    def default_assignees(self) -> tuple[str, ...]: ...
    def interval_for(self, login: str) -> int: ...
```

```python
load_registry(path: Path = DEFAULT_REGISTRY) -> Registry
```
Load and lightly validate the recipient registry. Missing file → empty registry with defaults. Malformed JSON raises.

### gh CLI wrappers

```python
list_stale_prs(
    label: str = "pr-mgmt",
    interval_hours: int = 24,
    *,
    now: datetime | None = None,
    runner: Callable[[list[str]], CompletedProcess] = _run_gh,
) -> list[Pr]
```
Return open PRs whose age exceeds `interval_hours` and whose most recent comment is not itself a fresh `[pr-reminder]` / `[pr-notifier]` ping (dedup).

```python
post_reminder(
    pr_number: int,
    body: str,
    *,
    runner: GhRunner = _run_gh,
) -> Result
```
Post a `[pr-reminder]`-prefixed comment to a PR. Prefix is added when absent; never double-prefixed.

```python
assign_recipients(
    pr_number: int,
    recipients: Iterable[str],
    *,
    as_reviewers: bool = True,
    runner: GhRunner = _run_gh,
) -> Result
```
Set assignees (and optionally reviewer-requests). Empty iterable is a no-op success.

### End-of-task prompt

```python
prompt_next_action(
    *,
    non_interactive: bool = False,
    default: Literal["continue-branch", "push-main", "open-pr"] = "continue-branch",
    reader: Callable[[], str] = input,
    writer: Callable[[str], None] = lambda s: print(s, end=""),
) -> Literal["continue-branch", "push-main", "open-pr"]
```
Return the operator's chosen next action. `non_interactive=True` returns `default` without reading stdin — suitable for CI / autonomous loops.

## CLI

```bash
# End-of-task three-way prompt (interactive)
python -m agentteams.pr_management prompt

# CI-safe variant
python -m agentteams.pr_management prompt --non-interactive --default open-pr

# Stale-PR reminder pass (uses gh CLI; respects REMINDER_INTERVAL_HOURS env)
python -m agentteams.pr_management remind
python -m agentteams.pr_management remind --dry-run
```

## Recipient Registry Schema

See `schemas/pr-recipient-registry.schema.json` for the JSON Schema (Draft 2020-12). The seed registry at `references/pr-recipients.json` ships with a single entry for the repo owner; extend it by adding `recipients[]` entries.

Per-recipient `notifications_confirmed: false` triggers a step-summary warning in the daily reminder workflow (operator-confirmed signal that the recipient actually receives GitHub notifications).
