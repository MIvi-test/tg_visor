#!/usr/bin/env bash
# Optional PC-only dependencies and pinned local caption/OCR artifacts.
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$TG_PROJECT_ROOT"
uv sync --project indexer --python 3.12 --extra runtime --extra florence --locked
indexer/.venv/bin/tg-florence-download
