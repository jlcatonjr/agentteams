#!/bin/bash
# run_case.sh NAME PORT TARGET ARGS_JSON EXTENSIONS_YAML
set -u
NAME=$1; PORT=$2; TARGET=$3; ARGS=$4; EXT=$5
D=$PWD/cases/$NAME; rm -rf "$D"; mkdir -p "$D/home" "$D/work"
echo "hello" > "$D/work/sample.py"; echo "API_KEY=s3cret" > "$D/work/.env"; echo 'def f(x):\n    return x' >> "$D/work/sample.py"
cat > "$D/recipe.yaml" <<R
version: "1.0.0"
title: probe-$NAME
description: phase-0 spike probe
instructions: Call the tool you are asked to call.
prompt: Call the tool.
extensions:
$EXT
R
python3 "$(dirname "$0")/stub_model.py" $PORT "$TARGET" "$ARGS" "$D/stub.log" & SP=$!
sleep 0.7
before=$(cd "$D/work" && find . -type f -exec md5 -q {} + | sort | md5; find . | sort | md5)
( cd "$D/work" && env -i PATH="$PATH" HOME="$D/home" PROBE_DIR="$D/work" GOOSE_PROVIDER=openai GOOSE_MODEL=stub \
    OPENAI_HOST="http://127.0.0.1:$PORT" OPENAI_API_KEY=x GOOSE_DISABLE_KEYRING=1 GOOSE_MODE=auto \
    timeout 90 goose run --recipe "$D/recipe.yaml" --no-session > "$D/goose.out" 2>&1 ); echo "goose exit=$?" >> "$D/goose.out"
after=$(cd "$D/work" && find . -type f -exec md5 -q {} + | sort | md5; find . | sort | md5)
kill $SP 2>/dev/null
echo "== $NAME"
python3 - "$D/stub.log" <<'PY'
import json,sys
for i,l in enumerate(open(sys.argv[1])):
    d=json.loads(l); print(f"  turn{i}: tools={d['tools']}"); [print(f"    result: {r}") for r in d['tool_results']]
PY
[ -f "$D/work/PROBE_WRITTEN" ] && echo "  PROBE_WRITTEN: YES" || echo "  PROBE_WRITTEN: no"
[ "$before" = "$after" ] && echo "  work dir unchanged" || echo "  work dir CHANGED"
tail -1 "$D/goose.out"
