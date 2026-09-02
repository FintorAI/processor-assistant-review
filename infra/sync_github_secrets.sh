#!/usr/bin/env bash
# Sync local credentials into GitHub Actions secrets/variables for AWS CI/CD.
#
# Usage:
#   ./infra/sync_github_secrets.sh              # push secrets + variables from .env
#   ./infra/sync_github_secrets.sh --dry-run    # print what would be set (no values)
#   ./infra/sync_github_secrets.sh --aws-cli    # also fill AWS_* from ~/.aws/credentials
#                                               # if missing from .env
#
# Prerequisites:
#   gh auth login   # needs repo admin (or secrets write) on FintorAI/LG-discOrch
#
# Never commits .env. Never prints secret values.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DRY_RUN=0
FILL_AWS_CLI=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --aws-cli) FILL_AWS_CLI=1 ;;
    -h|--help)
      sed -n '2,16p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown arg: $arg (use --dry-run or --aws-cli)"
      exit 1
      ;;
  esac
done

# Same load_env_file pattern as deploy.sh — never `source` .env.
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

upsert_env_key() {
  # Upsert KEY=value in .env without printing the value.
  local key="$1" value="$2" file="$ROOT/.env"
  python3 - "$file" "$key" "$value" <<'PY'
import sys
from pathlib import Path
path, key, value = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
lines = path.read_text().splitlines() if path.exists() else []
out, seen = [], False
for line in lines:
    if line.startswith(f"{key}=") or line.startswith(f"#{key}="):
        out.append(f"{key}={value}")
        seen = True
    else:
        out.append(line)
if not seen:
    if out and out[-1].strip():
        out.append("")
    out.append(f"# synced from AWS CLI default profile ({key})")
    out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n")
print(f"  wrote {key} to .env (len={len(value)})")
PY
}

if [[ ! -f "$ROOT/.env" ]]; then
  echo "Missing $ROOT/.env — copy from env.example first."
  exit 1
fi
load_env_file "$ROOT/.env"

if [[ "$FILL_AWS_CLI" -eq 1 ]]; then
  echo "Filling AWS_* from ~/.aws/credentials (default profile) if missing..."
  if [[ -z "${AWS_ACCESS_KEY_ID:-}" || -z "${AWS_SECRET_ACCESS_KEY:-}" ]]; then
    eval "$(python3 - <<'PY'
from pathlib import Path
import configparser
c = configparser.ConfigParser()
c.read(Path.home() / ".aws" / "credentials")
if "default" not in c:
    raise SystemExit("No [default] profile in ~/.aws/credentials")
print(f"export AWS_ACCESS_KEY_ID={c['default']['aws_access_key_id'].strip()!r}")
print(f"export AWS_SECRET_ACCESS_KEY={c['default']['aws_secret_access_key'].strip()!r}")
PY
)"
    upsert_env_key AWS_ACCESS_KEY_ID "$AWS_ACCESS_KEY_ID"
    upsert_env_key AWS_SECRET_ACCESS_KEY "$AWS_SECRET_ACCESS_KEY"
  else
    echo "  AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY already set in env — leaving alone."
  fi
  # DiscOrch stacks are pinned to us-east-2
  if [[ -z "${AWS_REGION:-}" ]]; then
    export AWS_REGION=us-east-2
    upsert_env_key AWS_REGION "$AWS_REGION"
  fi
fi

# Secrets consumed by .github/workflows/aws-deploy.yml
SECRET_KEYS=(
  AWS_ACCESS_KEY_ID
  AWS_SECRET_ACCESS_KEY
  AWS_REGION
  API_KEY
  ANTHROPIC_API_KEY
  LANGSMITH_API_KEY
  LANGCHAIN_API_KEY
  ENCOMPASS_API_BASE_URL
  ENCOMPASS_CLIENT_ID
  ENCOMPASS_CLIENT_SECRET
  ENCOMPASS_INSTANCE_ID
  ENCOMPASS_USERNAME
  ENCOMPASS_PASSWORD
  ENCOMPASS_SUBJECT_USER_ID
  PROD_ENCOMPASS_API_BASE_URL
  PROD_ENCOMPASS_CLIENT_ID
  PROD_ENCOMPASS_CLIENT_SECRET
  PROD_ENCOMPASS_INSTANCE_ID
  PROD_ENCOMPASS_USERNAME
  PROD_ENCOMPASS_PASSWORD
  PROD_ENCOMPASS_SUBJECT_USER_ID
  LANDINGAI_API_KEY
  USPS_CLIENT_ID
  USPS_CLIENT_SECRET
  DOCREPO_AUTH_TOKEN
  EFOLDER_API_TOKEN
)

# Non-secret deploy-time overrides (workflow vars.*)
VARIABLE_KEYS=(
  LANGSMITH_DEV_PROJECT
  LANGSMITH_PROD_PROJECT
  CORS_ALLOW_ORIGINS
  CONSOLIDATED_CUA_URL
  HOME_COUNSELING_CUA_URL
  DISCLOSURE_SUBMISSION_CUA_URL_DEV
  DISCLOSURE_SUBMISSION_CUA_URL_PROD
)

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI not installed. brew install gh"
  exit 1
fi
if [[ "$DRY_RUN" -eq 0 ]] && ! gh auth status >/dev/null 2>&1; then
  echo "Not logged into GitHub CLI. Run: gh auth login"
  echo "Then re-run: ./infra/sync_github_secrets.sh"
  exit 1
fi

REPO="FintorAI/LG-discOrch"
if gh auth status >/dev/null 2>&1; then
  REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || echo "$REPO")"
fi
echo "Target repo: $REPO"
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "(dry-run — no GitHub writes)"
fi
echo

set_secret() {
  local key="$1" value="$2"
  if [[ -z "$value" ]]; then
    echo "  SKIP  secret $key (empty in .env)"
    return 0
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  WOULD set secret $key (len=${#value})"
    return 0
  fi
  printf '%s' "$value" | gh secret set "$key" --repo "$REPO"
  echo "  SET   secret $key (len=${#value})"
}

set_variable() {
  local key="$1" value="$2"
  if [[ -z "$value" ]]; then
    echo "  SKIP  variable $key (empty in .env)"
    return 0
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  WOULD set variable $key (len=${#value})"
    return 0
  fi
  # gh variable set does not accept stdin on all versions — use --body
  gh variable set "$key" --repo "$REPO" --body "$value" >/dev/null
  echo "  SET   variable $key (len=${#value})"
}

echo "── Repository secrets ──"
missing=0
for key in "${SECRET_KEYS[@]}"; do
  value="${!key:-}"
  if [[ -z "$value" ]]; then
    echo "  SKIP  secret $key (empty in .env)"
    # AWS_* and API_KEY / LLM keys are required for CI to be useful
    case "$key" in
      AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|API_KEY|ANTHROPIC_API_KEY|LANGSMITH_API_KEY)
        missing=$((missing + 1))
        ;;
    esac
    continue
  fi
  set_secret "$key" "$value"
done

echo
echo "── Repository variables ──"
for key in "${VARIABLE_KEYS[@]}"; do
  set_variable "$key" "${!key:-}"
done

echo
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "Dry run only — nothing written to GitHub."
else
  echo "Done. Verify (names only):"
  echo "  gh secret list --repo $REPO"
  echo "  gh variable list --repo $REPO"
fi

if [[ "$missing" -gt 0 ]]; then
  echo
  echo "Warning: $missing required secret(s) were empty in .env."
  echo "Fill them (or re-run with --aws-cli for AWS_*) then run again."
  exit 2
fi
