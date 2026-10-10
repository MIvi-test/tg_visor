#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
"$TG_PROJECT_ROOT/scripts/build-native.sh"
cd "$TG_PROJECT_ROOT/android"
./gradlew --no-daemon assembleDebug assembleDebugAndroidTest
