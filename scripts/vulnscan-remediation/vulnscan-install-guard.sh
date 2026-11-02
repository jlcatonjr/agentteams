# vulnscan-install-guard.sh — sourced by vulnscan-daily-wrapped.sh and vulnscan-remediate-auto.sh (not run).
#
# Refuses a scheduled run on the wrong code. The pipx venv the launchd jobs run is shared: anything that
# `pipx install --force`es agentteams (a release tag, PyPI, another checkout) replaces what vulnscan-deploy.sh
# installed. On 2026-11-01 a "latest release" reinstall put v1.0.4, which has no agentteams.vulnscan, over a
# deployed main, and the scheduled runs failed with "No module named agentteams.vulnscan" (vfail-1101).
#
#   install_guard <job name> <state dir>   # 0 = the installed package is the deployed one; 1 = refused (logged, notified)
#
# Checks, in order:
#   1. "$PY" imports agentteams.vulnscan;
#   2. when vulnscan-deploy.sh recorded a deploy (<state>/deployed.json, {"commit": <sha>, ...}), the installed
#      distribution came from that commit's archive (its direct_url.json names .../agentteams-<first 8 of sha>).
#      No record (a deploy before this check existed) checks 1 only.
# A refusal is written to <state>/install-guard.json, logged on stderr and shown as a desktop notification; the
# fix is to re-run vulnscan-deploy.sh. Needs PY from the caller; the state dir is passed explicitly, so a STATE
# inherited from the environment cannot redirect the check. Reads JSON
# with the system python (/usr/bin/python3 -I), since the venv itself is what may be broken.

install_guard() {
  local who="${1:-vulnscan}" state="${2:-}" problem="" want="" got=""
  [[ -n "$state" ]] || { echo "install-guard: no state dir" >&2; return 1; }
  if ! "$PY" -P -c 'import agentteams.vulnscan' >/dev/null 2>&1; then
    problem="the installed agentteams ($PY) has no agentteams.vulnscan"
  elif [[ -f "$state/deployed.json" ]]; then
    want="$(/usr/bin/python3 -I -c 'import json, re, sys
c = str(json.load(open(sys.argv[1])).get("commit", ""))
print(c if re.fullmatch(r"[0-9a-f]{40}", c) else "")' "$state/deployed.json" 2>/dev/null || true)"
    got="$("$PY" -P -c 'import importlib.metadata as m, json
d = m.distribution("agentteams").read_text("direct_url.json")
print(json.loads(d).get("url", "") if d else "")' 2>/dev/null || true)"
    if [[ -z "$want" ]]; then
      problem="$state/deployed.json is unreadable or names no commit"
    elif [[ "$got" != */agentteams-"${want:0:8}" ]]; then
      problem="the installed agentteams (${got:-no direct_url.json}) is not the deployed commit ${want:0:12}"
    fi
  fi
  if [[ -z "$problem" ]]; then
    rm -f "$state/install-guard.json"
    return 0
  fi
  /usr/bin/python3 -I -c 'import json, sys, datetime
json.dump({"refused": sys.argv[1], "job": sys.argv[2], "fix": "re-run scripts/vulnscan-remediation/vulnscan-deploy.sh",
           "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}, open(sys.argv[3], "w"))' \
    "$problem" "$who" "$state/install-guard.json" 2>/dev/null || true
  echo "$(date -u +%FT%TZ) install-guard: REFUSED $who: $problem; re-run vulnscan-deploy.sh" >&2
  osascript -e "display notification \"${who//\"/\'} refused: ${problem//\"/\'}. Re-run vulnscan-deploy.sh.\" with title \"vulnscan: wrong agentteams installed\"" >/dev/null 2>&1 || true
  return 1
}
