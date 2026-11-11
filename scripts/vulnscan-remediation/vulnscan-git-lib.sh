# Hardened git for the vulnscan remediation scripts (sourced, not run): vulnscan-handoff-lib.sh, vulnscan-deploy.sh and
# the site-local vulnscan-operator-actions.sh source it; --install copies it with the other tracked scripts. Requires $PY (any python3 >= 3.8) for git_config_refusal.
PY="${PY:-$HOME/.local/pipx/venvs/agentteams/bin/python}"
# Hardened git for every call these scripts make (they run unconfined, with the operator's credentials, in repositories
# whose git data a review agent may have written): the vulnscan executor's _GIT_HARDENING flags, so repository config
# cannot run hooks, an fsmonitor, an ssh command, a credential helper, gpg, askpass, a pager or a remote helper.
GIT_HARDENING=(-c core.hooksPath=/dev/null -c core.fsmonitor=false -c core.sshCommand=ssh
  -c submodule.recurse=false -c fetch.recurseSubmodules=false -c credential.helper=
  -c commit.gpgsign=false -c tag.gpgSign=false -c log.showSignature=false -c core.askPass= -c core.pager=cat
  -c protocol.allow=never -c protocol.https.allow=always -c protocol.ssh.allow=always -c protocol.file.allow=always
  -c protocol.ext.allow=never -c push.recurseSubmodules=no)
# GIT_PAGER: a repository-set pager.<cmd> outranks core.pager; only the environment outranks it.
_git_raw() { GIT_TERMINAL_PROMPT=0 GIT_LFS_SKIP_SMUDGE=1 GIT_NO_REPLACE_OBJECTS=1 GIT_PAGER=cat PAGER=cat git "${GIT_HARDENING[@]}" "$@"; }
# What -c cannot neutralise: a filter/smudge/process driver (restore, switch, worktree add, status), git-lfs config,
# gpg, per-URL http settings (a proxy, sslVerify or CA that would see the token), url rewrites, includes, credential
# settings, core.worktree (it would point restore and the cleanup at another tree), core.alternateRefsCommand (fetch
# runs it), and a remote's proxy or vcs helper. A repository
# whose own (local or worktree) config sets any of them is refused before any command runs in it.
git_config_refusal() {   # git_config_refusal <dir>: prints the offending keys and fails, or prints nothing
  local bad rc
  # -z: each entry is "scope<NUL>key<LF>value<NUL>", so a value with a newline in it is judged whole (a line-based
  # read would see a stock first line and miss the command after it).
  bad="$(set -o pipefail; _git_raw -C "$1" config -z --show-scope --get-regexp \
    '^(filter|lfs|gpg|http|url|include|includeif|credential|pager)\.|^core\.(worktree|attributesfile|alternaterefscommand|alternaterefsprefixes)$|^remote\..*\.(proxy|vcs)$' \
    2>/dev/null | "$PY" -P -c '
import re, sys
raw = sys.stdin.buffer.read()
url = sys.argv[1]
m = re.fullmatch(r"(?:git@github\.com:|ssh://git@github\.com/|https://github\.com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?", url)
lfs = f"https://github.com/{m.group(1)}.git/info/lfs" if m else None
# Known-harmless keys tools write into a repository'"'"'s own config (2026-11-10 scan of the VK checkouts:
# http.postbuffer, git-lfs'"'"'s stock `git lfs install --local`). Allowed only by exact key, or exact key and value;
# every other key the pattern matches stays refused (Rule 12: what an exclusion permits is itself audited).
STOCK = {"filter.lfs.clean": "git-lfs clean -- %f", "filter.lfs.smudge": "git-lfs smudge -- %f",
         "filter.lfs.process": "git-lfs filter-process", "filter.lfs.required": "true"}
TUNING = {"http.postbuffer", "http.lowspeedlimit", "http.lowspeedtime", "http.maxrequests", "http.minsessions"}
bad = set()
parts = raw.split(b"\0")
if parts and parts[-1] == b"":
    parts.pop()
if len(parts) % 2:
    print("unparseable git config output"); sys.exit(4)
for i in range(0, len(parts), 2):
    scope = parts[i].decode("utf-8", "replace")
    key, _, val = parts[i + 1].decode("utf-8", "replace").partition("\n")
    if scope not in ("local", "worktree"):
        continue
    if key in TUNING and re.fullmatch(r"[0-9]{1,10}[km]?", val, re.I):   # no g suffix: caps postbuffer below 10 GiB
        continue
    if key == "lfs.repositoryformatversion" and re.fullmatch(r"[0-9]+", val):
        continue
    if lfs and key in (f"lfs.{lfs}.access", f"lfs.{lfs}.locksverify") and val in ("basic", "none", "true", "false"):
        continue
    if STOCK.get(key) == val:
        continue
    bad.add(key.encode("unicode_escape").decode()[:120])
print(" ".join(sorted(bad)[:5]))
sys.exit(3 if bad else 0)' "$(_git_raw -C "$1" config --get remote.origin.url 2>/dev/null)")"; rc=$?
  if (( rc == 0 || rc == 1 )); then return 0; fi   # 1: git config found no matching key
  if (( rc == 3 )); then echo "refusing git in $1: its own config sets $bad" >&2; return 1; fi
  echo "refusing git in $1: its config could not be checked (exit $rc)" >&2; return 1
}
hgit() {   # hgit [-C <dir>] <subcommand> ...: hardened git, refused in a repository whose config could run code
  local dir="$PWD"
  [[ "${1:-}" == -C ]] && dir="${2:-.}"
  git_config_refusal "$dir" || return 128
  _git_raw "$@"
}
# Network git (fetch / ls-remote / push) for one repository: refuses unless origin has at least one URL and every fetch and
# push URL is https, ssh, scp-like or a local path (a repository can name a remote helper, which outranks
# protocol.allow), pins git's own upload-pack (a repository's remote.<name>.uploadpack would run a program), and
# offers only the gh credential helper, and only to https://github.com (subproc_confine scopes it to one host too).
hgit_net() {   # hgit_net <repo> fetch|ls-remote|push <args...>
  local repo="$1" sub="$2" urls url pack; shift 2
  case "$sub" in fetch|ls-remote) pack=--upload-pack=git-upload-pack ;; push) pack=--receive-pack=git-receive-pack ;;
    *) echo "hgit_net: $sub is not a network subcommand" >&2; return 2 ;; esac
  git_config_refusal "$repo" || return 128
  urls="$(_git_raw -C "$repo" remote get-url --all origin 2>/dev/null)" || { echo "refusing $sub: origin has no readable URL" >&2; return 128; }
  urls+=$'\n'"$(_git_raw -C "$repo" remote get-url --push --all origin 2>/dev/null)" || { echo "refusing $sub: origin push URL unreadable" >&2; return 128; }
  [[ -n "${urls//[[:space:]]/}" ]] || { echo "refusing $sub: origin has no URL" >&2; return 128; }
  while IFS= read -r url; do
    [[ -n "$url" ]] || continue
    [[ "$url" != *::* && "$url" =~ ^((https|ssh)://[^[:space:]:]+[^[:space:]]*|[[:alnum:]_.-]+@[[:alnum:]_.-]+:[^[:space:]:][^[:space:]]*|/[^[:space:]:]*)$ ]] ||
      { echo "refusing $sub: origin URL ${url:0:80} is not an https, ssh or local path URL" >&2; return 128; }
  done <<<"$urls"
  _git_raw -c 'credential.https://github.com.helper=!gh auth git-credential' -C "$repo" \
    "$sub" "$pack" "$@"
}
