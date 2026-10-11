#!/usr/bin/env bash
set -euo pipefail

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$project_dir"
exec python3 ./aion2_cleric_rotation.py --level 3 --profile leveling --verbose
