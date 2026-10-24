#!/usr/bin/env bash
# Live probe of OpenRouter's Anthropic Messages and OpenAI Responses routes (vulnscan plan, revision 13).
# Run on the operator's machine, OUTSIDE any sandbox: it makes 8 small paid calls to OpenRouter.
#
# For each route (/api/v1/messages, /api/v1/responses) it sends one tool-calling request with a
# non-native model (your configured agent model, which OpenRouter must translate) and with a native
# model (Anthropic on messages, OpenAI on responses), streamed and not, and reports per call: the HTTP
# status, whether a tool call came back, and whether usage (tokens, cost) came back, parsed with the
# gateway's own per-dialect parser. That tells you whether the claude/codex drivers can use a
# non-native model through OpenRouter, or must stay on native models.
#
# The key is read from ~/.config/agentteams-secrets/openrouter.env (OPENROUTER_API_KEY=...) inside the
# probe; it is never printed, logged or put on a command line. Error bodies are shown truncated, with the
# key redacted should it ever appear.
#
# Usage:   bash scripts/vulnscan-gateway-probe.sh [--model M] [--anthropic-model M] [--openai-model M]
#                                                 [--key-file FILE]
#   --model            the non-native model (default: $VULNSCAN_MODEL, else z-ai/glm-5.3)
#   --anthropic-model  native model for /messages (default anthropic/claude-sonnet-4.5)
#   --openai-model     native model for /responses (default openai/gpt-5-mini)
# Exit status: 0 when every native call returned a tool call and usage; 1 otherwise; 2 on a usage error.
set -euo pipefail

MODEL="${VULNSCAN_MODEL:-z-ai/glm-5.3}"; AMODEL="anthropic/claude-sonnet-4.5"; OMODEL="openai/gpt-5-mini"
KEYFILE="$HOME/.config/agentteams-secrets/openrouter.env"
while [ $# -gt 0 ]; do case "$1" in
  --model) MODEL="${2:?--model needs a value}"; shift;; --anthropic-model) AMODEL="${2:?}"; shift;;
  --openai-model) OMODEL="${2:?}"; shift;; --key-file) KEYFILE="${2:?}"; shift;;
  -h|--help) sed -n '2,21p' "$0"; exit 0;;
  *) echo "unknown option: $1" >&2; exit 2;; esac; shift; done
[ -r "$KEYFILE" ] || { echo "cannot read the key file $KEYFILE" >&2; exit 2; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

python3 -I - "$ROOT/agentteams/vulnscan/gateway_dialects.py" "$KEYFILE" "$MODEL" "$AMODEL" "$OMODEL" <<'PY'
import importlib.util, json, sys, urllib.error, urllib.request

dialects_py, keyfile, model, amodel, omodel = sys.argv[1:6]
spec = importlib.util.spec_from_file_location("gateway_dialects", dialects_py)
d = sys.modules["gateway_dialects"] = importlib.util.module_from_spec(spec)   # dataclasses need it registered
spec.loader.exec_module(d)

key = ""
for line in open(keyfile, encoding="utf-8").read().splitlines():
    line = line.strip().removeprefix("export ").strip()
    if line.startswith("OPENROUTER_API_KEY="):
        key = line.split("=", 1)[1].strip().strip("'\"")
if not key:
    sys.exit("no OPENROUTER_API_KEY in the key file")

BASE = "https://openrouter.ai/api/v1"
PROMPT = "Call the get_weather tool for Paris. Do not answer in text."
SCHEMA = {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}


def body(dialect, m, stream):
    if dialect == d.MESSAGES:
        return {"model": m, "max_tokens": 256, "stream": stream, "messages": [{"role": "user", "content": PROMPT}],
                "tools": [{"name": "get_weather", "description": "Weather for a city", "input_schema": SCHEMA}]}
    return {"model": m, "max_output_tokens": 256, "stream": stream, "store": False, "input": PROMPT,
            "tools": [{"type": "function", "name": "get_weather", "description": "Weather for a city",
                       "parameters": SCHEMA}]}


def tool_called(dialect, payload):
    text = payload.decode("utf-8", errors="replace")
    if dialect == d.MESSAGES:
        return '"tool_use"' in text and "get_weather" in text
    return '"function_call"' in text and "get_weather" in text


def probe(dialect, m, stream):
    req = urllib.request.Request(f"{BASE}/{dialect}", data=json.dumps(body(dialect, m, stream)).encode(),
                                 method="POST", headers={"Authorization": f"Bearer {key}",
                                                         "Content-Type": "application/json",
                                                         "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            status, payload = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status, payload = exc.code, exc.read()
    except OSError as exc:
        return {"status": f"error {type(exc).__name__}", "tool": False, "usage": False, "note": ""}
    usage, found = d.usage_for(dialect, payload)
    note = "" if status == 200 else payload.decode("utf-8", errors="replace").replace(key, "<key>")[:160]
    return {"status": status, "tool": status == 200 and tool_called(dialect, payload),
            "usage": found and bool(usage.prompt_total or usage.output_tokens), "cost": usage.cost,
            "tokens": f"{usage.prompt_total}/{usage.output_tokens}", "note": note}


rows, native_ok = [], True
for dialect, native in ((d.MESSAGES, amodel), (d.RESPONSES, omodel)):
    for m, kind in ((model, "non-native"), (native, "native")):
        for stream in (False, True):
            r = probe(dialect, m, stream)
            rows.append((f"/api/v1/{dialect}", kind, m, "stream" if stream else "json", r))
            if kind == "native" and not (r["tool"] and r["usage"]):
                native_ok = False

print(f"{'route':18} {'kind':10} {'model':32} {'mode':6} {'HTTP':5} {'tool':5} {'usage':5} {'in/out':11} cost")
for route, kind, m, mode, r in rows:
    cost = "-" if r.get("cost") is None else f"{r['cost']:.6f}"
    print(f"{route:18} {kind:10} {m[:32]:32} {mode:6} {str(r['status']):5} {'yes' if r['tool'] else 'NO':5} "
          f"{'yes' if r['usage'] else 'NO':5} {r.get('tokens', '-'):11} {cost}")
    if r["note"]:
        print(f"    {r['note']}")
print()
print("A non-native row with tool=yes and usage=yes on both modes means OpenRouter's translated route works for"
      " that model; anything else means the claude/codex drivers should use a native model (plan revision 13).")
sys.exit(0 if native_ok else 1)
PY
