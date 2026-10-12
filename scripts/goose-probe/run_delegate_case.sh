#!/bin/bash
# run_delegate_case.sh NAME PORT TARGET ARGS_JSON DELEGATE_ARGS_JSON PRIME_EXTENSIONS_YAML CHILD_EXTENSIONS_YAML
# Delegated-child probe: the prime recipe (PRIME_EXTENSIONS_YAML, plus a `child` sub_recipe) calls
# `delegate` with DELEGATE_ARGS_JSON; the child (whose own recipe declares CHILD_EXTENSIONS_YAML) then
# calls TARGET. Turn 0 lists the prime's tools, the next fresh turn the child's.
set -u
NAME=$1; PORT=$2; TARGET=$3; ARGS=$4; DARGS=$5; PEXT=$6; CEXT=$7
case $NAME in ""|*[!A-Za-z0-9_-]*) echo "NAME must be [A-Za-z0-9_-]+" >&2; exit 2;; esac
D=$PWD/cases/$NAME; rm -rf "$D"; mkdir -p "$D/home" "$D/work"
echo "hello" > "$D/work/sample.py"
cat > "$D/child.yaml" <<R
version: "1.0.0"
title: probe-child-$NAME
description: delegated child
instructions: Call the tool you are asked to call.
extensions:
$CEXT
R
cat > "$D/recipe.yaml" <<R
version: "1.0.0"
title: probe-prime-$NAME
description: delegate probe prime
instructions: Delegate to the child.
prompt: Delegate.
extensions:
$PEXT
sub_recipes:
  - name: child
    path: "$D/child.yaml"
R
python3 "$(dirname "$0")/stub_model.py" "$PORT" "$TARGET" "$ARGS" "$D/stub.log" "$DARGS" & SP=$!
sleep 0.7
before=$(cd "$D/work" && find . -type f -exec md5 -q {} + | sort | md5; find . | sort | md5)
( cd "$D/work" && env -i PATH="$PATH" HOME="$D/home" PROBE_DIR="$D/work" GOOSE_PROVIDER=openai GOOSE_MODEL=stub \
    OPENAI_HOST="http://127.0.0.1:$PORT" OPENAI_API_KEY=x GOOSE_DISABLE_KEYRING=1 GOOSE_MODE=auto \
    timeout 120 goose run --recipe "$D/recipe.yaml" --no-session > "$D/goose.out" 2>&1 ); echo "goose exit=$?" >> "$D/goose.out"
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
