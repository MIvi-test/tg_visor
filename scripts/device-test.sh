#!/usr/bin/env bash
# Installs this project's debug APK and an isolated synthetic test dataset.
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$TG_PROJECT_ROOT"
export TG_TEST_SNAPSHOT="${1:-data/demo-v1}"
adb get-state
adb install -r android/app/build/outputs/apk/debug/app-debug.apk
adb install -r android/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
indexer/.venv/bin/python - <<'PY'
from pathlib import Path
import tarfile
import os
with tarfile.open('data/device-test.tar', 'w') as archive:
    for path in Path('models/e5-small').iterdir():
        if path.name in ('model.onnx', 'tokenizer.json', 'model.json'):
            archive.add(path, arcname='model/' + path.name)
    archive.add(os.environ['TG_TEST_SNAPSHOT'], arcname='snapshot')
    archive.add('data/request.json', arcname='request.json')
PY
adb shell run-as dev.tgfeature.search mkdir -p files/device-test
adb exec-in run-as dev.tgfeature.search tar -xf - -C files/device-test < data/device-test.tar
result_file=$(mktemp "$TG_PROJECT_ROOT/data/device-result.XXXXXX")
adb shell am instrument -w dev.tgfeature.search.test/dev.tgfeature.search.SmokeInstrumentation | tee "$result_file"
indexer/.venv/bin/python - "$result_file" <<'PY'
from pathlib import Path
import sys
output=Path(sys.argv[1]).read_text()
if 'PASS: JNI, E5, semantic search, filters, close;' not in output or 'FAIL:' in output:
    raise SystemExit('Device integration test failed; see output above')
PY
