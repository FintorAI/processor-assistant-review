#!/usr/bin/env bash
# Smoke test ProcessorReviewStack.
# Usage: ./infra/smoke_test_aws.sh [dev|prod]
set -euo pipefail

STAGE="${1:-dev}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STACK_NAME="ProcessorReviewStack-${STAGE}"
OUTPUT_FILE="$ROOT/output-${STAGE}.json"

if [[ ! -f "$OUTPUT_FILE" ]]; then
  echo "Missing $OUTPUT_FILE — run ./infra/deploy.sh $STAGE first"
  exit 1
fi

API_URL="$(python3 -c "import json; print(json.load(open('$OUTPUT_FILE')).get('${STACK_NAME}.ApiUrl',''))")"
API_URL="${API_URL%/}/"
if [[ -z "$API_URL" || "$API_URL" == "/" ]]; then
  echo "ApiUrl missing in $OUTPUT_FILE"
  exit 1
fi

API_KEY=""
if [[ -f "$ROOT/.env" ]]; then
  API_KEY=$(python3 - <<PY
from pathlib import Path
for line in Path("$ROOT/.env").read_text().splitlines():
    if line.startswith("API_KEY="):
        print(line.split("=",1)[1].strip().strip("'").strip('"'))
        break
PY
)
fi
AUTH=()
[[ -n "$API_KEY" ]] && AUTH=(-H "x-api-key: $API_KEY")

echo "=== Smoke $STAGE @ $API_URL ==="

echo -n "Health... "
curl -sf "${API_URL}health" | grep -q ok && echo OK || { echo FAIL; exit 1; }

echo -n "Assistants... "
ASSISTANTS=$(curl -sf -X POST "${API_URL}assistants/search" "${AUTH[@]}" -H "Content-Type: application/json" -d '{}')
echo "$ASSISTANTS" | grep -q '"review"' && echo OK || { echo "FAIL: $ASSISTANTS"; exit 1; }

echo -n "Create thread... "
THREAD=$(curl -sf -X POST "${API_URL}threads" "${AUTH[@]}" -H "Content-Type: application/json" -d '{}')
THREAD_ID=$(echo "$THREAD" | python3 -c "import json,sys; print(json.load(sys.stdin)['thread_id'])")
echo "OK ($THREAD_ID)"

echo -n "Get thread... "
curl -sf "${API_URL}threads/${THREAD_ID}" "${AUTH[@]}" >/dev/null && echo OK

echo "=== Smoke passed ==="
echo "Note: full review run requires Fargate worker + live Encompass secrets."
