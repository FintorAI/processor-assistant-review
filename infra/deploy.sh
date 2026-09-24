#!/usr/bin/env bash
# Deploy ProcessorReviewStack to AWS (Lambda + Fargate).
# Usage: ./infra/deploy.sh [dev|prod]
set -euo pipefail

STAGE="${1:-dev}"
if [[ "$STAGE" != "dev" && "$STAGE" != "prod" ]]; then
  echo "Usage: $0 [dev|prod]"
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STACK_NAME="ProcessorReviewStack-${STAGE}"
REPO_NAME=$(basename "$ROOT" | tr '[:upper:]' '[:lower:]' | tr '_' '-')

if ! aws sts get-caller-identity >/dev/null 2>&1; then
  echo "AWS credentials not configured."
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running."
  exit 1
fi

load_env_file() {
  local file="$1" line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
    [[ "$line" != *"="* ]] && continue
    key="${line%%=*}"
    key="${key#export }"
    key="${key//[[:space:]]/}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    value="${line#*=}"
    if [[ "$value" == \"*\" && "$value" == *\" && ${#value} -ge 2 ]]; then
      value="${value#\"}"; value="${value%\"}"
    elif [[ "$value" == \'*\' && "$value" == *\' && ${#value} -ge 2 ]]; then
      value="${value#\'}"; value="${value%\'}"
    fi
    if [[ -z "${!key:-}" ]]; then
      export "$key=$value"
    fi
  done < "$file"
}

if [[ -f "$ROOT/.env" ]]; then
  load_env_file "$ROOT/.env"
fi
export STAGE="$STAGE"
export REVIEW_STAGE="$STAGE"

if [[ -z "${API_KEY:-}" ]]; then
  export API_KEY
  API_KEY=$(openssl rand -hex 32)
  echo "API_KEY=$API_KEY" >> "$ROOT/.env"
  echo "Auto-generated API_KEY (saved to .env)"
fi
export LANGSMITH_DEV_PROJECT="${LANGSMITH_DEV_PROJECT:-${REPO_NAME}-dev}"
export LANGSMITH_PROD_PROJECT="${LANGSMITH_PROD_PROJECT:-${REPO_NAME}-prod}"
export CORS_ALLOW_ORIGINS="${CORS_ALLOW_ORIGINS:-*}"
export LANGCHAIN_API_KEY="${LANGCHAIN_API_KEY:-${LANGSMITH_API_KEY:-}}"

if [[ -x "$ROOT/venv/bin/python" ]]; then
  echo "Running agent import smoke..."
  (cd "$ROOT" && PYTHONPATH="$ROOT/output:$ROOT" ./venv/bin/python -c \
    "from output.proc_agent import create_agent; g=create_agent(); print('agent OK', type(g).__name__)") \
    || echo "WARNING: agent import failed — continuing deploy"
fi

cd "$ROOT/infra"
if [[ ! -d .venv ]] || [[ ! -f .venv/bin/activate ]]; then
  rm -rf .venv
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

if command -v cdk >/dev/null 2>&1; then
  CDK=(cdk)
elif command -v npx >/dev/null 2>&1; then
  CDK=(npx --yes aws-cdk)
else
  echo "Install aws-cdk (npm i -g aws-cdk)"
  exit 1
fi

echo "Bootstrapping CDK..."
"${CDK[@]}" bootstrap --app "python3 app.py" || true

echo "Deploying $STACK_NAME..."
OUTPUTS_TMP="$(mktemp)"
"${CDK[@]}" deploy "$STACK_NAME" \
  --app "python3 app.py" \
  --require-approval never \
  --outputs-file "$OUTPUTS_TMP"

OUTPUT_FILE="$ROOT/output-${STAGE}.json"
python3 - <<PY
import json
from pathlib import Path
stacks = json.loads(Path("$OUTPUTS_TMP").read_text())
flat = {}
for stack_name, outputs in stacks.items():
    for key, value in outputs.items():
        flat[f"{stack_name}.{key}"] = value
Path("$OUTPUT_FILE").write_text(json.dumps(flat, indent=2) + "\n")
PY
rm -f "$OUTPUTS_TMP"
echo "Wrote $OUTPUT_FILE"
cat "$OUTPUT_FILE"

SECRET_ARN="$(python3 -c "import json; print(json.load(open('$OUTPUT_FILE')).get('${STACK_NAME}.AgentSecretsArn',''))")"
if [[ -n "$SECRET_ARN" ]]; then
  SECRET_REGION="$(echo "$SECRET_ARN" | cut -d: -f4)"
  echo "Pushing secrets to $SECRET_ARN ($SECRET_REGION)..."
  if [[ "$STAGE" == "prod" ]]; then
    export LANGSMITH_PROJECT="${LANGSMITH_PROD_PROJECT}"
  else
    export LANGSMITH_PROJECT="${LANGSMITH_DEV_PROJECT}"
  fi
  SECRET_PAYLOAD_FILE="$(mktemp)"
  python3 - <<'PY' > "$SECRET_PAYLOAD_FILE"
import json, os
KEYS = [
    "API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
    "LANGSMITH_API_KEY", "LANGCHAIN_API_KEY", "LANGGRAPH_API_KEY", "LANGSMITH_PROJECT",
    "ENCOMPASS_API_BASE_URL", "ENCOMPASS_CLIENT_ID", "ENCOMPASS_CLIENT_SECRET",
    "ENCOMPASS_INSTANCE_ID", "ENCOMPASS_USERNAME", "ENCOMPASS_PASSWORD",
    "ENCOMPASS_SUBJECT_USER_ID",
    "PROD_ENCOMPASS_API_BASE_URL", "PROD_ENCOMPASS_CLIENT_ID", "PROD_ENCOMPASS_CLIENT_SECRET",
    "PROD_ENCOMPASS_INSTANCE_ID", "PROD_ENCOMPASS_USERNAME", "PROD_ENCOMPASS_PASSWORD",
    "PROD_ENCOMPASS_SUBJECT_USER_ID", "PROD_ENCOMPASS_ACCESS_TOKEN",
    "LANDINGAI_API_KEY", "USPS_CLIENT_ID", "USPS_CLIENT_SECRET",
    "DOCREPO_AUTH_TOKEN", "DOCREPO_PUT_API_BASE", "DOCREPO_GET_API_BASE", "DOCREPO_CREATE_API_BASE",
    "EFOLDER_API_TOKEN", "EFOLDER_API_BASE_URL",
    "TASKTILE_PROD_CLIENT_KEY", "TASKTILE_PROD_CLIENT_SECRET",
    "TASKTILE_DEV_CLIENT_KEY", "TASKTILE_DEV_CLIENT_SECRET",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION",
]
print(json.dumps({k: os.environ.get(k, "") for k in KEYS}))
PY
  aws secretsmanager put-secret-value \
    --region "$SECRET_REGION" \
    --secret-id "$SECRET_ARN" \
    --secret-string "file://$SECRET_PAYLOAD_FILE" >/dev/null
  rm -f "$SECRET_PAYLOAD_FILE"
  echo "Secrets updated. Verifying SET/EMPTY:"
  aws secretsmanager get-secret-value \
    --region "$SECRET_REGION" \
    --secret-id "$SECRET_ARN" \
    --query SecretString --output text \
    | python3 -c "import json,sys; [print(f'  {k}: {\"SET\" if v else \"EMPTY\"}') for k,v in json.load(sys.stdin).items()]"
fi

API_URL="$(python3 -c "import json; print(json.load(open('$OUTPUT_FILE')).get('${STACK_NAME}.ApiUrl',''))")"
echo ""
echo "=== API URL ==="
echo "$API_URL"
echo "Dashboard cutover:"
echo "  VITE_REVIEW_DEPLOYMENT_URL=$API_URL"
echo "  Set VITE_LANGSMITH_API_KEY (or dedicated key) = stack API_KEY"
echo "Next: ./infra/smoke_test_aws.sh $STAGE"
