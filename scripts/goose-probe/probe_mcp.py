"""Minimal stdio MCP server: read_probe (harmless) and write_probe (creates PROBE_WRITTEN in cwd)."""
import json, sys, os
TOOLS = [{"name": "read_probe", "description": "Return a fixed string.", "inputSchema": {"type": "object", "properties": {}}},
         {"name": "write_probe", "description": "Create a marker file.", "inputSchema": {"type": "object", "properties": {}}}]
for line in sys.stdin:
    m = json.loads(line); mid = m.get("id"); meth = m.get("method")
    if mid is None: continue
    if meth == "initialize":
        res = {"protocolVersion": m["params"].get("protocolVersion", "2024-11-05"), "capabilities": {"tools": {}},
               "serverInfo": {"name": "probe", "version": "0"}}
    elif meth == "tools/list":
        res = {"tools": TOOLS}
    elif meth == "tools/call":
        name = m["params"]["name"]
        if name == "write_probe":
            open(os.path.join(os.environ.get("PROBE_DIR", "."), "PROBE_WRITTEN"), "w").write("x")
            text = "wrote"
        else:
            text = "read-ok"
        res = {"content": [{"type": "text", "text": text}]}
    else:
        res = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": res}) + "\n"); sys.stdout.flush()
