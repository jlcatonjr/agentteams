"""branch_inventory.py — read-only branch inventory and deletion plan (``--branch-inventory``).

Classifies every local branch and every branch on the project's **push remote** against the
default branch, applies the holds that override deletion, and derives a deletion plan of exact,
leased commands. It never writes a ref: the only git command it runs that touches the repository
is ``git fetch --prune`` (skippable with ``fetch=False``). Execution lives in
:mod:`agentteams.branch_cleanup`, behind a recorded clearance or a signed grant.

The state model and guards are the emitted ``references/branch-lifecycle.reference.md``; this
module is its executable form. Origin: baseAgent's 2026-10-04 audit and handoff
(``references/branch-lifecycle-policy.handoff.md``), revised for squash-merged PR workflows —
ancestry alone misclassifies a squash-merged branch, and ``git cherry`` only recognises a squash
of a single-commit branch, so **Merged-by-PR** is verified through the GitHub API instead.

Every API failure (401/403/404 on a list call, rate limit, network, ``gh`` absent) is
**unknown**, never "no PR" or "not protected"; when the open-PR hold cannot be checked, no
remote deletion is planned at all.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

#: States, in evaluation order (first match wins). Names are the reference's.
STATES: tuple[str, ...] = (
    "default", "protected", "evergreen", "release-maintained", "release-finished",
    "merged", "merged-by-pr", "patch-equivalent", "active", "stale-unmerged",
)

#: States whose proposed action a hold overrides.
HOLDABLE_STATES = frozenset({"release-finished", "merged", "merged-by-pr", "patch-equivalent"})

#: Verdicts the API helper returns besides ``ok``.
API_UNKNOWN = "unknown"
API_NOT_FOUND = "notfound"
#: GitHub's answer for a feature the repository's plan lacks: rulesets and classic branch protection
#: on a private repository on GitHub Free. Only ``_protection`` reads it; to every other caller it is
#: just "not ok", which is fail-closed.
API_PLAN_UNAVAILABLE = "plan-unavailable"
PLAN_UNAVAILABLE_MESSAGE = "Upgrade to GitHub Pro or make this repository public to enable this feature."

#: CSV column order of the inventory.
CSV_COLUMNS: tuple[str, ...] = (
    "branch", "ref_type", "tip", "tip_short", "last_activity", "ahead", "behind", "ancestor",
    "own_commits", "unique_patches", "authors", "upstream", "upstream_track", "protected",
    "open_pr", "merged_pr", "cited_by", "state", "holds", "action", "via",
)

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_GITHUB_REMOTE_RE = re.compile(
    r"^(?:git@github\.com:|ssh://git@github\.com/|https://(?:[^@/]+@)?github\.com/)"
    r"(?P<repo>[^/]+/[^/]+?)(?:\.git)?/?$"
)
_WORKFLOW_BRANCH_RE = re.compile(r"""\bbranch\s*[:=]\s*['"]?([A-Za-z0-9_./${}()\-]+)""")
_WORKFLOW_REF_RE = re.compile(r"""\bref\s*:\s*['"]?([A-Za-z0-9_./\-]+)['"]?\s*$""", re.M)
_ERE_SPECIALS = set(".[](){}*+?|^$\\")


class InventoryError(RuntimeError):
    """Raised when the repository cannot be inventoried (not a repo, no default branch)."""


@dataclass
class InventoryConfig:
    """Inputs to :func:`build_inventory`.

    Attributes:
        repo: Repository working tree.
        push_remote: The only remote acted on (never an upstream or fork remote).
        default_branch: Default branch name; resolved from ``<remote>/HEAD`` when None.
        stale_days: *N* — days without activity after which unmerged work is stale.
        release_pattern: Regex for finished-release branches (tag, then delete).
        maintained_patterns: Regexes for maintained release lines (always kept).
        operator_emails: Author emails treated as the operator; ``git config user.email`` is
            always included. An author outside this set places an Owner hold (fail closed).
        use_api: Query GitHub for open PRs, merged PRs and protection.
        fetch: Run ``git fetch --prune <push_remote>`` first.
        cite_exclude: Pathspecs excluded from the informational ``cited_by`` column.
        now: Reference time (tests).
    """

    repo: Path
    push_remote: str = "origin"
    default_branch: str | None = None
    stale_days: int = 14
    release_pattern: str = r"^release/"
    maintained_patterns: tuple[str, ...] = ()
    operator_emails: tuple[str, ...] = ()
    use_api: bool = True
    fetch: bool = True
    cite_exclude: tuple[str, ...] = ("workSummaries",)
    now: datetime | None = None


@dataclass
class RefRecord:
    """One inventoried ref. Field meanings follow :data:`CSV_COLUMNS`."""

    branch: str
    ref_type: str
    full_ref: str
    tip: str
    last_activity: str = ""
    ahead: int = 0
    behind: int = 0
    ancestor: bool = False
    own_commits: int = 0
    unique_patches: int = 0
    authors: list[str] = field(default_factory=list)
    upstream: str = ""
    upstream_track: str = ""
    protected: str = "n/a"
    open_pr: str = "n/a"
    merged_pr: str = ""
    pr_ref_ok: bool = False
    cited_by: list[str] = field(default_factory=list)
    state: str = ""
    holds: list[str] = field(default_factory=list)
    action: str = "keep"
    via: str = ""

    @property
    def tip_short(self) -> str:
        """The abbreviated tip SHA."""
        return self.tip[:7]


# ---------------------------------------------------------------------------
# git and GitHub access
# ---------------------------------------------------------------------------


def _git_env() -> dict[str, str]:
    """The environment for every git call: C locale, never prompt for credentials."""
    return {**os.environ, "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0"}


def git(repo: Path, *args: str, check: bool = True) -> str:
    """Run ``git -C repo args`` and return stripped stdout.

    Args:
        repo: Repository path.
        *args: git arguments.
        check: Raise on a non-zero exit.

    Returns:
        Standard output, stripped.

    Raises:
        InventoryError: The command failed and ``check`` is set.
    """
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=_git_env(),
        check=False,
    )
    if check and proc.returncode != 0:
        raise InventoryError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def git_ok(repo: Path, *args: str) -> bool:
    """Return whether ``git -C repo args`` exits 0 (used for ``merge-base --is-ancestor``).

    Args:
        repo: Repository path.
        *args: git arguments.

    Returns:
        True when the command exited 0.
    """
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, env=_git_env(), check=False,
    ).returncode == 0


def github_repo_of(remote_url: str) -> str | None:
    """Return ``owner/name`` when ``remote_url`` points at github.com.

    Args:
        remote_url: A git remote URL (ssh, scp-like or https).

    Returns:
        ``owner/name``, or None for a non-GitHub remote.
    """
    m = _GITHUB_REMOTE_RE.match(remote_url.strip())
    return m.group("repo") if m else None


class GitHubAPI:
    """Minimal read-only GitHub REST client: token over urllib, else the ``gh`` CLI.

    ``get`` returns ``(verdict, data)`` with verdict ``ok``, ``notfound`` or ``unknown``.
    Nothing here writes to GitHub.
    """

    def __init__(self, repo: str, *, timeout: float = 20.0) -> None:
        """Bind the client to ``owner/name``.

        Args:
            repo: ``owner/name``.
            timeout: Per-request timeout in seconds.
        """
        self.repo = repo
        self.timeout = timeout
        self._token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
        #: Set once GitHub reports that the plan lacks protection/rulesets (for the report note).
        self.plan_unavailable = False

    def _plan_verdict(self, status: int, message: str) -> str:
        """``API_PLAN_UNAVAILABLE`` for GitHub's exact plan-limit 403, else ``API_UNKNOWN``."""
        if status == 403 and message.strip() == PLAN_UNAVAILABLE_MESSAGE:
            self.plan_unavailable = True
            return API_PLAN_UNAVAILABLE
        return API_UNKNOWN

    def get(self, path: str) -> tuple[str, Any]:
        """GET ``path`` (relative to ``repos/<owner>/<name>/``, or absolute from ``/``).

        Args:
            path: API path, e.g. ``pulls?state=open``.

        Returns:
            ``(verdict, parsed JSON or None)``.
        """
        full = path[1:] if path.startswith("/") else f"repos/{self.repo}/{path}"
        if self._token:
            return self._get_urllib(full)
        return self._get_gh(full)

    def _get_urllib(self, full: str) -> tuple[str, Any]:
        req = urllib.request.Request(
            f"https://api.github.com/{full}",
            headers={"Authorization": f"Bearer {self._token}",
                     "Accept": "application/vnd.github+json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 — fixed https host
                return "ok", json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return API_NOT_FOUND, None
            try:
                message = str(json.loads(exc.read().decode("utf-8")).get("message", ""))
            except (OSError, ValueError, AttributeError):
                message = ""
            return self._plan_verdict(exc.code, message), None
        except (urllib.error.URLError, OSError, ValueError):
            return API_UNKNOWN, None

    def _get_gh(self, full: str) -> tuple[str, Any]:
        try:
            proc = subprocess.run(
                ["gh", "api", full], capture_output=True, text=True, timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return API_UNKNOWN, None
        if proc.returncode != 0:
            if "HTTP 404" in proc.stderr:
                return API_NOT_FOUND, None
            if "(HTTP 403)" in proc.stderr:
                try:  # gh prints the error body as JSON on stdout
                    message = str(json.loads(proc.stdout).get("message", ""))
                except (ValueError, AttributeError):
                    message = ""
                return self._plan_verdict(403, message), None
            return API_UNKNOWN, None
        try:
            return "ok", json.loads(proc.stdout)
        except ValueError:
            return API_UNKNOWN, None

    def get_all(self, path: str) -> tuple[str, list[Any]]:
        """Paginate a list endpoint (``per_page=100``).

        Args:
            path: List endpoint path (may already carry a query string).

        Returns:
            ``(verdict, items)``; any failing page makes the whole result ``unknown``.
        """
        out: list[Any] = []
        sep = "&" if "?" in path else "?"
        for page in range(1, 51):
            verdict, data = self.get(f"{path}{sep}per_page=100&page={page}")
            if verdict != "ok" or not isinstance(data, list):
                return API_UNKNOWN, []
            out.extend(data)
            if len(data) < 100:
                return "ok", out
        return API_UNKNOWN, out


# ---------------------------------------------------------------------------
# Repository facts
# ---------------------------------------------------------------------------


def _resolve_default(repo: Path, remote: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    head = git(repo, "symbolic-ref", "--quiet", f"refs/remotes/{remote}/HEAD", check=False)
    if head.startswith(f"refs/remotes/{remote}/"):
        return head[len(f"refs/remotes/{remote}/"):]
    for cand in ("main", "master"):
        if git_ok(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{cand}"):
            return cand
    raise InventoryError(f"cannot determine the default branch of remote {remote!r}; pass it")


def _ere_escape(text: str) -> str:
    return "".join(f"\\{c}" if c in _ERE_SPECIALS else c for c in text)


def _workflow_patterns(repo: Path, default_ref: str) -> tuple[list[re.Pattern[str]], set[str]]:
    """Evergreen branch patterns (``branch:``/``branch=``) and pinned refs (``ref:``) in workflows.

    A templated name (``auto/update-${{ x }}``, ``$hash``) becomes a prefix pattern; a name
    that is variable from its first character is ignored rather than matching everything.
    """
    listing = git(repo, "ls-tree", "-r", "--name-only", default_ref, "--", ".github/workflows",
                  check=False)
    patterns: list[re.Pattern[str]] = []
    pinned: set[str] = set()
    for path in listing.splitlines():
        if not path.endswith((".yml", ".yaml")):
            continue
        text = git(repo, "show", f"{default_ref}:{path}", check=False)
        for raw in _WORKFLOW_BRANCH_RE.findall(text):
            literal = re.split(r"\$", raw, maxsplit=1)[0]
            if len(literal) < 3:
                continue
            tail = "" if literal == raw else ".*"
            patterns.append(re.compile("^" + re.escape(literal) + tail + "$"))
        pinned.update(m for m in _WORKFLOW_REF_RE.findall(text) if "$" not in m)
    gitmodules = git(repo, "show", f"{default_ref}:.gitmodules", check=False)
    pinned.update(re.findall(r"^\s*branch\s*=\s*(\S+)", gitmodules, re.M))
    return patterns, pinned


def _worktree_branches(repo: Path) -> dict[str, bool]:
    """Map branch name → whether its worktree directory still exists."""
    out: dict[str, bool] = {}
    path = ""
    for line in git(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line.startswith("branch refs/heads/"):
            out[line[len("branch refs/heads/"):]] = Path(path).exists()
    return out


def _own_commits(repo: Path, tip: str, default_ref: str, ancestor: bool,
                 fp: list[str], fp_set: set[str]) -> list[str]:
    """Commits that belong to the branch: not on the default branch's first-parent history.

    For an unmerged branch that is ``tip ^default``. For a merged one it is the commits brought
    in by its merge: find the oldest first-parent commit containing the tip (binary search; the
    containment is monotone along first-parent history) and take ``tip ^<its first parent>``.
    A tip that is itself on first-parent history (an empty fresh branch, or a fast-forward
    merge) owns no commits.
    """
    if not ancestor:
        return git(repo, "rev-list", tip, f"^{default_ref}").split()
    if tip in fp_set:
        return []
    lo, hi = 0, len(fp) - 1  # fp is newest-first; fp[0] contains the tip
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if git_ok(repo, "merge-base", "--is-ancestor", tip, fp[mid]):
            lo = mid
        else:
            hi = mid - 1
    exclude = [f"^{fp[lo + 1]}"] if lo + 1 < len(fp) else []
    return git(repo, "rev-list", tip, *exclude).split()


def _last_activity(repo: Path, ref: RefRecord) -> datetime:
    """Latest of the tip's committer date and (local branches) the newest reflog entry."""
    stamps = [int(git(repo, "log", "-1", "--format=%ct", ref.tip))]
    if ref.ref_type == "local":
        # %gd with --date=unix is the reflog ENTRY time (`refs/heads/x@{1759...}`); %ct would be
        # the commit's time, which makes a branch freshly created at an old commit look old.
        selector = git(repo, "reflog", "show", "-1", "--date=unix", "--format=%gd", ref.full_ref,
                       check=False)
        m = re.search(r"@\{(\d+)\}$", selector)
        if m:
            stamps.append(int(m.group(1)))
    return datetime.fromtimestamp(max(stamps), tz=timezone.utc)


# ---------------------------------------------------------------------------
# API-backed facts
# ---------------------------------------------------------------------------


@dataclass
class _ApiFacts:
    status: str = "n/a"
    open_heads: set[str] = field(default_factory=set)
    open_bases: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)


def _open_pr_facts(api: GitHubAPI | None) -> _ApiFacts:
    if api is None:
        return _ApiFacts(status="n/a", notes=["GitHub API not used: open-PR holds unchecked"])
    verdict, prs = api.get_all("pulls?state=open")
    if verdict != "ok":
        return _ApiFacts(status=API_UNKNOWN, notes=["open PRs could not be listed"])
    facts = _ApiFacts(status="ok")
    for pr in prs:
        head_repo = ((pr.get("head") or {}).get("repo") or {}).get("full_name")
        if head_repo == api.repo:
            facts.open_heads.add((pr.get("head") or {}).get("ref", ""))
        facts.open_bases.add((pr.get("base") or {}).get("ref", ""))
    return facts


def _protection(api: GitHubAPI, branch: str) -> str:
    quoted = urllib.parse.quote(branch, safe="")
    verdict, data = api.get(f"branches/{quoted}")
    if verdict == API_NOT_FOUND:
        return "no"
    if verdict != "ok" or not isinstance(data, dict):
        return API_UNKNOWN
    if data.get("protected"):
        return "yes"
    verdict, rules = api.get(f"rules/branches/{quoted}")
    if verdict == API_NOT_FOUND:
        return "no"
    if verdict == API_PLAN_UNAVAILABLE:
        # The plan has no rulesets or classic protection, and branches/ already said protected: false.
        return "no"
    if verdict != "ok" or not isinstance(rules, list):
        return API_UNKNOWN
    return "yes" if rules else "no"


def _merged_pr(api: GitHubAPI, repo: Path, cfg: InventoryConfig, default_ref: str,
               rec: RefRecord) -> tuple[str, int | None]:
    """Return ``(verdict, pr_number)`` for the Merged-by-PR test.

    A match needs every condition: the PR's head repository is the push-remote repository (a
    fork with the same branch name never matches), its base is the default branch, it is merged,
    its ``head.sha`` equals this ref's tip, and its merge commit is reachable from the default
    branch. Every PR with that head is checked.
    """
    owner = api.repo.split("/", 1)[0]
    head = urllib.parse.quote(f"{owner}:{rec.branch}", safe="")
    verdict, prs = api.get_all(f"pulls?state=closed&head={head}")
    if verdict != "ok":
        return API_UNKNOWN, None
    default = cfg.default_branch or ""
    for pr in prs:
        head_obj = pr.get("head") or {}
        if ((head_obj.get("repo") or {}).get("full_name") != api.repo
                or (pr.get("base") or {}).get("ref") != default
                or not pr.get("merged_at")
                or head_obj.get("sha") != rec.tip):
            continue
        merge_sha = pr.get("merge_commit_sha") or ""
        if _SHA_RE.match(merge_sha) and git_ok(repo, "merge-base", "--is-ancestor",
                                                merge_sha, default_ref):
            return "ok", int(pr["number"])
    return "ok", None


def pr_ref_matches(repo: Path, remote: str, number: int | str, tip: str) -> bool:
    """Whether ``refs/pull/<number>/head`` exists on ``remote`` and points at ``tip``.

    The restore path of a Merged-by-PR branch: its commits are not reachable from the default
    branch, so deletion is allowed only while GitHub still holds this ref at the audited tip.

    Args:
        repo: Repository path.
        remote: The push remote.
        number: PR number.
        tip: The audited tip SHA.

    Returns:
        True when the ref exists and equals ``tip``.
    """
    out = git(repo, "ls-remote", remote, f"refs/pull/{number}/head", check=False)
    return bool(out) and out.split()[0] == tip


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _classify(rec: RefRecord, cfg: InventoryConfig, *, evergreen: list[re.Pattern[str]],
              age_days: float) -> str:
    name = rec.branch
    if name == cfg.default_branch:
        return "default"
    if rec.protected == "yes":
        return "protected"
    if any(p.match(name) for p in evergreen):
        return "evergreen"
    if any(re.search(p, name) for p in cfg.maintained_patterns):
        return "release-maintained"
    merged = rec.ancestor and (rec.own_commits > 0 or age_days > cfg.stale_days)
    by_pr = bool(rec.merged_pr)
    if cfg.release_pattern and re.search(cfg.release_pattern, name) and (merged or by_pr):
        return "release-finished"
    if merged:
        return "merged"
    if by_pr:
        return "merged-by-pr"
    if not rec.ancestor and rec.unique_patches == 0:
        return "patch-equivalent"
    if age_days <= cfg.stale_days:
        return "active"
    return "stale-unmerged"


def _holds(rec: RefRecord, *, operators: set[str], api: _ApiFacts, worktrees: dict[str, bool],
           pinned: set[str], linked: bool) -> list[str]:
    holds: list[str] = []
    strangers = sorted(a for a in rec.authors if a.lower() not in operators)
    if strangers:
        holds.append("owner:" + ";".join(strangers))
    if linked:
        holds.append("link")
    if api.status == "ok":
        if rec.branch in api.open_heads:
            holds.append("open-pr:head")
        if rec.branch in api.open_bases:
            holds.append("open-pr:base")
    if rec.branch in worktrees:
        holds.append("worktree" if worktrees[rec.branch] else "worktree:prunable")
    if rec.branch in pinned:
        holds.append("pinned")
    return holds


def _decide(rec: RefRecord, api_status: str) -> None:
    """Set ``action`` (and ``via``) from state, holds and API status."""
    if rec.state == "stale-unmerged" or (rec.state == "patch-equivalent" and not rec.holds):
        rec.action = "ask-operator"
        return
    if rec.state not in HOLDABLE_STATES or rec.holds:
        return
    if rec.ref_type == "remote" and api_status != "ok":
        rec.holds.append("api-unknown")
        return
    if rec.merged_pr and not rec.ancestor and not rec.pr_ref_ok:
        rec.holds.append("pr-ref-missing")
        return
    rec.via = "ancestor" if rec.ancestor else f"pr#{rec.merged_pr}"
    rec.action = "tag-then-delete" if rec.state == "release-finished" else "delete"


def build_inventory(cfg: InventoryConfig, *, api: GitHubAPI | None = None,
                    api_factory: Callable[[str], GitHubAPI] | None = None) -> dict[str, Any]:
    """Inventory every local branch and every branch on the push remote.

    Args:
        cfg: Inventory inputs.
        api: A ready API client (tests inject a stub). When None and ``cfg.use_api`` is set, one
            is built for a github.com push remote (``api_factory`` overrides the constructor).
        api_factory: Constructor for the client given ``owner/name``.

    Returns:
        ``{"meta": {...}, "refs": [RefRecord as dict, ...], "plan": {...}}``.

    Raises:
        InventoryError: Not a git repository, unknown remote, or no default branch.
    """
    repo = cfg.repo.resolve()
    if not git_ok(repo, "rev-parse", "--git-dir"):
        raise InventoryError(f"not a git repository: {repo}")
    remote_url = git(repo, "remote", "get-url", cfg.push_remote, check=False)
    if not remote_url:
        raise InventoryError(f"no remote named {cfg.push_remote!r}")
    if cfg.fetch:
        git(repo, "fetch", "--prune", "--quiet", cfg.push_remote)
    cfg.default_branch = _resolve_default(repo, cfg.push_remote, cfg.default_branch)
    default_ref = f"refs/remotes/{cfg.push_remote}/{cfg.default_branch}"
    default_tip = git(repo, "rev-parse", default_ref)
    now = cfg.now or datetime.now(timezone.utc)

    gh_repo = github_repo_of(remote_url)
    if api is None and cfg.use_api and gh_repo:
        api = (api_factory or GitHubAPI)(gh_repo)
    api_facts = _open_pr_facts(api if cfg.use_api else None)
    if cfg.use_api and api is None:
        api_facts.notes.append("push remote is not github.com: remote deletions are not planned")

    operators = {e.strip().lower() for e in cfg.operator_emails if e.strip()}
    me = git(repo, "config", "user.email", check=False)
    if me:
        operators.add(me.lower())
    evergreen, pinned = _workflow_patterns(repo, default_ref)
    worktrees = _worktree_branches(repo)
    fp = git(repo, "rev-list", "--first-parent", default_ref).split()
    fp_set = set(fp)

    refs: list[RefRecord] = []
    fmt = "%(refname)%09%(objectname)%09%(upstream:short)%09%(upstream:track)"
    for line in git(repo, "for-each-ref", f"--format={fmt}", "refs/heads",
                    f"refs/remotes/{cfg.push_remote}").splitlines():
        full_ref, tip, upstream, track = (line.split("\t") + ["", "", ""])[:4]
        if full_ref.startswith("refs/heads/"):
            rec = RefRecord(full_ref[len("refs/heads/"):], "local", full_ref, tip,
                            upstream=upstream, upstream_track=track.strip("[]"))
        else:
            name = full_ref[len(f"refs/remotes/{cfg.push_remote}/"):]
            if name == "HEAD":
                continue
            rec = RefRecord(name, "remote", full_ref, tip)
        refs.append(rec)

    for rec in refs:
        behind, ahead = git(repo, "rev-list", "--left-right", "--count",
                            f"{default_ref}...{rec.tip}").split()
        rec.ahead, rec.behind = int(ahead), int(behind)
        rec.ancestor = git_ok(repo, "merge-base", "--is-ancestor", rec.tip, default_ref)
        own = _own_commits(repo, rec.tip, default_ref, rec.ancestor, fp, fp_set)
        rec.own_commits = len(own)
        if own:
            rec.authors = sorted(set(git(repo, "log", "--no-walk", "--format=%ae", *own)
                                     .lower().split()))
        if not rec.ancestor:
            rec.unique_patches = sum(1 for ln in git(repo, "cherry", default_ref, rec.tip)
                                     .splitlines() if ln.startswith("+"))
        activity = _last_activity(repo, rec)
        rec.last_activity = activity.strftime("%Y-%m-%d")
        age_days = (now - activity).total_seconds() / 86400
        if rec.branch != cfg.default_branch:
            spec = [":(exclude)" + p for p in cfg.cite_exclude]
            cited = git(repo, "grep", "-l", "-w", "-F", "-e", rec.branch, default_ref, "--", ".",
                        *spec, check=False)
            rec.cited_by = [ln.split(":", 1)[1] for ln in cited.splitlines() if ":" in ln]
            link_re = (r"(tree|blob|commits?|compare)/([^ )'\"]*\.\.\.?)?"
                       + _ere_escape(rec.branch) + r"([/ )'\"#>]|$)")
            linked = bool(git(repo, "grep", "-l", "-E", "-e", link_re, default_ref, "--", ".",
                              check=False))
        else:
            linked = False
        if api is not None and api_facts.status == "ok" and rec.branch != cfg.default_branch:
            if rec.ref_type == "remote":
                rec.protected = _protection(api, rec.branch)
                if rec.protected == API_UNKNOWN:
                    api_facts.status = API_UNKNOWN
                    api_facts.notes.append(f"protection unknown for {rec.branch}")
            rec.open_pr = "yes" if (rec.branch in api_facts.open_heads
                                    or rec.branch in api_facts.open_bases) else "no"
            if not rec.ancestor:
                verdict, number = _merged_pr(api, repo, cfg, default_ref, rec)
                if verdict != "ok":
                    rec.holds.append("pr-lookup-unknown")
                elif number is not None:
                    rec.merged_pr = str(number)
                    rec.pr_ref_ok = pr_ref_matches(repo, cfg.push_remote, number, rec.tip)
        rec.state = _classify(rec, cfg, evergreen=evergreen, age_days=age_days)
        rec.holds = [*rec.holds, *_holds(rec, operators=operators, api=api_facts,
                                         worktrees=worktrees, pinned=pinned, linked=linked)]

    for rec in refs:
        _decide(rec, api_facts.status)
    if api is not None and getattr(api, "plan_unavailable", False):
        api_facts.notes.append("branch protection read from the branches endpoint: rulesets and "
                               "classic protection are not available on this repository's GitHub plan")

    meta = {
        "repo": str(repo), "push_remote": cfg.push_remote, "github_repo": gh_repo or "",
        "default_branch": cfg.default_branch, "default_tip": default_tip,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "stale_days": cfg.stale_days,
        "api_status": api_facts.status, "notes": api_facts.notes,
        "operators": sorted(operators),
    }
    return {"meta": meta, "refs": [_record_dict(r) for r in refs],
            "plan": deletion_plan(meta, refs)}


def _record_dict(rec: RefRecord) -> dict[str, Any]:
    out = asdict(rec)
    out["tip_short"] = rec.tip_short
    return out


# ---------------------------------------------------------------------------
# Deletion plan and output
# ---------------------------------------------------------------------------


def item_commands(item: dict[str, Any], remote: str) -> dict[str, list[str]]:
    """The exact commands for one plan item, and how to restore it.

    Args:
        item: A plan item.
        remote: The push remote.

    Returns:
        ``{"run": [...], "restore": [...]}`` as shell strings (documentation and dry-run).
    """
    b, sha = item["branch"], item["tip"]
    run: list[str] = []
    if item.get("tag"):
        run += [f"git tag -a {item['tag']} -m 'archive of branch tip {sha[:7]}' {sha}",
                f"git push {remote} refs/tags/{item['tag']}"]
    if item["ref_type"] == "remote":
        run.append(f"git push {remote} --delete --force-with-lease=refs/heads/{b}:{sha} "
                   f"refs/heads/{b}")
        restore = [f"git push {remote} {sha}:refs/heads/{b}"]
    elif item["via"] == "ancestor":
        run.append(f"git branch -d -- {b}")
        restore = [f"git branch {b} {sha}"]
    else:
        run.append(f"git update-ref -d refs/heads/{b} {sha}")
        restore = [f"git branch {b} {sha}"]
    if item["via"].startswith("pr#"):
        restore.insert(0, f"git fetch {remote} pull/{item['via'][3:]}/head")
    return {"run": run, "restore": restore}


def deletion_plan(meta: dict[str, Any], refs: list[RefRecord]) -> dict[str, Any]:
    """Build the deletion plan (only refs whose action is ``delete`` or ``tag-then-delete``).

    The plan's ``sha256`` binds a ``@security`` clearance to exactly this set of refs and tips;
    :mod:`agentteams.branch_cleanup` refuses to execute a plan whose clearance names another.

    Args:
        meta: Inventory metadata (repo, push remote, default branch and tip, time).
        refs: Classified refs.

    Returns:
        The plan: binding fields, ``items`` (each with ``run``/``restore``), ``sha256``.
    """
    items = []
    for rec in refs:
        if rec.action not in ("delete", "tag-then-delete"):
            continue
        item = {"branch": rec.branch, "ref_type": rec.ref_type, "tip": rec.tip,
                "state": rec.state, "via": rec.via,
                "tag": f"archive/{rec.branch}" if rec.action == "tag-then-delete" else ""}
        item.update(item_commands(item, meta["push_remote"]))
        items.append(item)
    body = {"repo": meta["repo"], "push_remote": meta["push_remote"],
            "default_branch": meta["default_branch"], "default_tip": meta["default_tip"],
            "items": items}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()
    return {**body, "generated_at": meta["generated_at"], "sha256": digest}


def plan_digest(plan: dict[str, Any]) -> str:
    """Recompute a plan's sha256 over its binding fields (ignores ``generated_at``/``sha256``).

    Args:
        plan: A deletion plan.

    Returns:
        The hex digest.

    Raises:
        KeyError: A binding field is missing.
    """
    body = {k: plan[k] for k in ("repo", "push_remote", "default_branch", "default_tip", "items")}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


def write_reports(inventory: dict[str, Any], out_dir: Path) -> list[Path]:
    """Write ``branch-inventory.csv``, ``branch-inventory.json`` and ``branch-deletion-plan.json``.

    Args:
        inventory: :func:`build_inventory` output.
        out_dir: Directory to write into (created).

    Returns:
        The written paths.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "branch-inventory.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for rec in inventory["refs"]:
            row = {k: rec.get(k, "") for k in CSV_COLUMNS}
            for key in ("authors", "cited_by", "holds"):
                row[key] = "; ".join(rec[key])
            row["ancestor"] = "yes" if rec["ancestor"] else "no"
            writer.writerow(row)
    json_path = out_dir / "branch-inventory.json"
    json_path.write_text(json.dumps({k: inventory[k] for k in ("meta", "refs")}, indent=2) + "\n",
                         encoding="utf-8")
    plan_path = out_dir / "branch-deletion-plan.json"
    plan_path.write_text(json.dumps(inventory["plan"], indent=2) + "\n", encoding="utf-8")
    return [csv_path, json_path, plan_path]


def summary_line(inventory: dict[str, Any]) -> str:
    """The Output Contract's ``Branch inventory`` value.

    Returns:
        ``clean`` or ``N merged-undeleted | N patch-equivalent | N stale-unmerged`` (non-zero
        parts only), with the API status when it is not ``ok``.
    """
    refs = inventory["refs"]
    merged = sum(1 for r in refs if r["state"] in ("merged", "merged-by-pr", "release-finished"))
    parts = [f"{n} {label}" for n, label in (
        (merged, "merged-undeleted"),
        (sum(1 for r in refs if r["state"] == "patch-equivalent"), "patch-equivalent"),
        (sum(1 for r in refs if r["state"] == "stale-unmerged"), "stale-unmerged"),
    ) if n]
    line = " | ".join(parts) or "clean"
    status = inventory["meta"]["api_status"]
    return line if status == "ok" else f"{line} (api: {status})"


__all__ = [
    "CSV_COLUMNS", "STATES", "GitHubAPI", "InventoryConfig", "InventoryError", "RefRecord",
    "build_inventory", "deletion_plan", "git", "git_ok", "github_repo_of", "item_commands",
    "plan_digest", "pr_ref_matches", "summary_line", "write_reports",
]
