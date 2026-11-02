"""Test-only agent-loop guest for the P05 host tests (P07 ships the real one).

Speaks protocol v1 in step mode: read one event line on stdin, write one op line on stdout, exit. In-guest tools
run here against the WASI preopens; ``run_code`` is requested from the host. Model text starting with
``GUEST:`` selects a misbehaviour so the host's failure handling can be exercised through a real guest:
``GUEST:garbage`` (not JSON), ``GUEST:two-lines``, ``GUEST:v2`` (wrong version), ``GUEST:spin`` (fuel trap),
``GUEST:env`` (report the environment size), ``GUEST:unknown-tool`` (ask for an ungranted host tool).
"""

import json
import os
import sys

IN_GUEST = ("read_file", "list_dir", "search_files", "write_file")


def out(op):
    sys.stdout.write(json.dumps(op, separators=(",", ":")) + "\n")


def in_guest(name, args, limits):
    path = args.get("path", "")
    if not isinstance(path, str) or not (path.startswith("/project/") or path == "/scratch"
                                         or path.startswith("/scratch/")):
        return False, "path outside the grant"
    try:
        if name == "read_file":
            with open(path, encoding="utf-8") as fh:
                return True, fh.read(limits["max_read_bytes"])
        if name == "list_dir":
            return True, "\n".join(sorted(os.listdir(path)))
        if name == "write_file":
            if not (path.startswith("/scratch/")):
                return False, "writes only under /scratch"
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(args.get("content", ""))
            return True, "written"
        hits = []
        for dirpath, _, files in os.walk(path):
            for f in files:
                with open(os.path.join(dirpath, f), encoding="utf-8", errors="replace") as fh:
                    for i, line in enumerate(fh, 1):
                        if args.get("pattern", "") in line:
                            hits.append(f"{os.path.join(dirpath, f)}:{i}:{line.rstrip()}")
        return True, "\n".join(hits)
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc.strerror}"


def advance(carry, limits):
    """Work through the remaining tool uses; return the next op."""
    while carry["todo"]:
        use = carry["todo"][0]
        if use["name"] in IN_GUEST:
            ok, text = in_guest(use["name"], use["input"], limits)
            carry["results"].append({"tool_use_id": use["id"], "ok": ok, "content": text[:limits["max_tool_output_bytes"]]})
            carry["todo"].pop(0)
            continue
        return {"v": 1, "op": "tool", "id": use["id"], "name": use["name"], "input": use["input"], "carry": carry}
    return {"v": 1, "op": "model", "tool_results": carry["results"], "continue": False, "carry": None}


def main():
    frame = json.loads(sys.stdin.readline())
    ev, limits = frame["event"], frame["limits"]
    kind = ev["kind"]
    if kind == "start":
        return out({"v": 1, "op": "model", "tool_results": [], "continue": False, "carry": None})
    if kind == "model_reply":
        text = "".join(b.get("text", "") for b in ev["content"] if b["type"] == "text")
        if text.startswith("GUEST:garbage"):
            return sys.stdout.write("this is not json\n")
        if text.startswith("GUEST:two-lines"):
            return sys.stdout.write('{"v":1,"op":"done","status":"ok"}\n{"v":1}\n')
        if text.startswith("GUEST:v2"):
            return out({"v": 2, "op": "done", "status": "ok"})
        if text.startswith("GUEST:spin"):
            while True:
                pass
        if text.startswith("GUEST:env"):
            return out({"v": 1, "op": "done", "status": "failed", "reason": f"env-{len(os.environ)}"})
        uses = [b for b in ev["content"] if b["type"] == "tool_use"]
        if not uses:
            if ev["stop"] == "max_tokens":
                return out({"v": 1, "op": "model", "tool_results": [], "continue": True, "carry": None})
            return out({"v": 1, "op": "done", "status": "ok"})
        return out(advance({"todo": uses, "results": []}, limits))
    if kind == "tool_result":
        carry = frame["carry"]
        use = carry["todo"].pop(0)
        carry["results"].append({"tool_use_id": use["id"], "ok": ev["ok"], "content": "(host result)"})
        return out(advance(carry, limits))
    return out({"v": 1, "op": "done", "status": "failed", "reason": "denied-" + ev.get("code", "x")})


main()
