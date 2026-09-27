#!/usr/bin/env bash
# Local checks: shell syntax, shellcheck when available, library and Python tests.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
shell_files=(install.sh macserver installer/lib.sh installer/steps/*.sh tests/*.sh iso/build.sh iso/setup/macserver-setup)
for f in "${shell_files[@]}"; do bash -n "$f"; done
echo "shell syntax ok"
if command -v shellcheck >/dev/null; then shellcheck -x "${shell_files[@]}"; echo "shellcheck ok"; fi
bash tests/test_lib.sh
python3 -m unittest discover -s tests -v
