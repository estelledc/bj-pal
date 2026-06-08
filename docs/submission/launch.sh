#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cd "${ROOT_DIR}"

PORT="${PORT:-8501}"
ADDRESS="${ADDRESS:-127.0.0.1}"
SKIP_INDEX="${SKIP_INDEX:-0}"
DRY_RUN="${DRY_RUN:-0}"

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

BJ_PAL_LLM="${BJ_PAL_LLM:-longcat}"
export BJ_PAL_LLM

require_env() {
  local name="$1"
  local hint="$2"
  if [[ -z "${!name:-}" ]]; then
    echo "missing required env: ${name}" >&2
    echo "${hint}" >&2
    exit 1
  fi
}

echo "BJ-Pal launch"
echo "root: ${ROOT_DIR}"
echo "backend: ${BJ_PAL_LLM}"
echo "url: http://${ADDRESS}:${PORT}"

case "${BJ_PAL_LLM}" in
  longcat)
    require_env "LONGCAT_API_KEY" "Set LONGCAT_API_KEY in .env, or run BJ_PAL_LLM=mock for offline demo."
    echo "llm api: LONGCAT_API_KEY configured"
    ;;
  dpsk|deepseek)
    require_env "DPSK_API_KEY" "Set DPSK_API_KEY in .env, or run BJ_PAL_LLM=mock for offline demo."
    echo "llm api: DPSK_API_KEY configured"
    ;;
  anthropic)
    require_env "ANTHROPIC_API_KEY" "Set ANTHROPIC_API_KEY in .env, or run BJ_PAL_LLM=mock for offline demo."
    echo "llm api: ANTHROPIC_API_KEY configured"
    ;;
  mock)
    echo "llm api: mock backend, no API key required"
    ;;
  *)
    echo "unknown BJ_PAL_LLM=${BJ_PAL_LLM}; use longcat, dpsk, deepseek, anthropic, or mock" >&2
    exit 1
    ;;
esac

if [[ "${DRY_RUN}" == "1" ]]; then
  echo "dry run: launch script configuration is valid"
  exit 0
fi

if [[ "${SKIP_INDEX}" != "1" ]]; then
  echo "building/loading local data index..."
  python3 src/loader.py
fi

echo "starting Streamlit..."
python3 -m streamlit run src/ui/app.py \
  --server.address "${ADDRESS}" \
  --server.port "${PORT}"
