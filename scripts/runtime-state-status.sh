#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-}"

if [[ "$MODE" == "--from-stdin" ]]; then
  STATUS_TEXT="$(cat)"
else
  STATUS_TEXT="$(git -C "$ROOT_DIR" status --short --untracked-files=all)"
fi

runtime_lines=()
source_lines=()

while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  case "$line" in
    *" runtime/"*|*" \"runtime/"*|*" .venv/"*|*" \".venv/"*|*" scripts/stack.env"*|*" scripts/openwakeword.env"*|*" scripts/piper-tts.env"*)
      runtime_lines+=("$line")
      ;;
    *)
      source_lines+=("$line")
      ;;
  esac
done <<< "$STATUS_TEXT"

printf 'Source/review changes:\n'
if [[ "${#source_lines[@]}" -eq 0 ]]; then
  printf '  none\n'
else
  printf '  %s\n' "${source_lines[@]}"
fi

printf '\nRuntime/local mutable state:\n'
if [[ "${#runtime_lines[@]}" -eq 0 ]]; then
  printf '  none\n'
else
  printf '  %s\n' "${runtime_lines[@]}"
fi

printf '\nPolicy:\n'
printf '  Review and commit source changes. Runtime/local mutable state is generated,\n'
printf '  downloaded, migrated, or host-specific and should normally stay uncommitted.\n'
