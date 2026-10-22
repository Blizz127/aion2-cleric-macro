#!/usr/bin/env bash
set -euo pipefail

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$project_dir"
exec python3 ./aion2_cleric_gui.py \
  --profile-file ./cleric_personal.json \
  --level "${AION2_CLERIC_LEVEL:-50}"
