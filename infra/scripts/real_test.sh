#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example. Fill credentials in .env and rerun."
  exit 1
fi

set -a
source .env
set +a

required_vars=(
  GOOGLE_CLIENT_ID
  GOOGLE_CLIENT_SECRET
  GOOGLE_OAUTH_REDIRECT_URI
  OPENAI_API_KEY
)

missing=()
for var_name in "${required_vars[@]}"; do
  if [[ -z "${!var_name:-}" ]]; then
    missing+=("$var_name")
  fi
done

if [[ "${PRODUCT_DATA_MODE:-auto}" != "mock" ]] && [[ -z "${SERPAPI_API_KEY:-}" ]]; then
  missing+=("SERPAPI_API_KEY")
fi

if (( ${#missing[@]} > 0 )); then
  echo "Missing required variables in .env:"
  for var_name in "${missing[@]}"; do
    echo "- $var_name"
  done
  exit 1
fi

if [[ -z "${OPENAI_API_BASE:-}" ]]; then
  export OPENAI_API_BASE="https://api.openai.com/v1"
fi
if [[ -z "${OPENAI_VISION_MODEL:-}" ]]; then
  export OPENAI_VISION_MODEL="gpt-4.1-mini"
fi

./infra/scripts/dev_up.sh

EMAIL="${1:-}"
if [[ -z "$EMAIL" ]]; then
  echo "Usage: ./infra/scripts/real_test.sh <email> [folder_id]"
  exit 1
fi

FOLDER_ID="${2:-}"

if [[ -n "$FOLDER_ID" ]]; then
  python3 ./infra/scripts/real_e2e.py --email "$EMAIL" --folder-id "$FOLDER_ID"
else
  python3 ./infra/scripts/real_e2e.py --email "$EMAIL"
fi
