#!/usr/bin/env bash
# Linux x86_64, project-local tools only. Android SDK licenses are accepted for development.
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$TG_PROJECT_ROOT"
mkdir -p .tools "$ANDROID_USER_HOME"
download() {
    local url="$1" destination="$2"
    for attempt in 1 2 3; do
        if curl -fLsS -C - --connect-timeout 20 --max-time 1800 "$url" -o "$destination"; then return; fi
    done
    return 1
}
if [[ ! -x "$JAVA_HOME/bin/java" ]] || [[ ! -f "$JAVA_HOME/release" ]]; then
    download 'https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.20.1%2B1/OpenJDK17U-jdk_x64_linux_hotspot_17.0.20.1_1.tar.gz' .tools/jdk17.tar.gz
    printf '%s  %s\n' '3808d1d15e3ec6bd5b84057fb5d84c33d8a1536a258146bcea2e603fc726e08e' '.tools/jdk17.tar.gz' | sha256sum -c -
    mkdir -p "$JAVA_HOME"
    tar -xzf .tools/jdk17.tar.gz --strip-components=1 -C "$JAVA_HOME"
fi
if [[ ! -x .tools/gradle-8.11.1/bin/gradle ]]; then
    download https://services.gradle.org/distributions/gradle-8.11.1-bin.zip .tools/gradle.zip
    download https://services.gradle.org/distributions/gradle-8.11.1-bin.zip.sha256 .tools/gradle.zip.sha256
    printf '%s  %s\n' "$(cat .tools/gradle.zip.sha256)" '.tools/gradle.zip' | sha256sum -c -
    unzip -q .tools/gradle.zip -d .tools
fi
if [[ ! -x "$CARGO_HOME/bin/rustup" ]]; then
    download https://static.rust-lang.org/rustup/dist/x86_64-unknown-linux-gnu/rustup-init .tools/rustup-init
    chmod +x .tools/rustup-init
    RUSTUP_INIT_SKIP_PATH_CHECK=yes .tools/rustup-init -y --no-modify-path --profile minimal --default-toolchain 1.90.0
fi
rustup show
if [[ ! -x "$ANDROID_HOME/cmdline-tools/19.0/bin/sdkmanager" ]]; then
    download https://dl.google.com/android/repository/commandlinetools-linux-13114758_latest.zip .tools/commandlinetools.zip
    mkdir -p "$ANDROID_HOME/cmdline-tools"
    unzip -q .tools/commandlinetools.zip -d "$ANDROID_HOME/cmdline-tools"
    mv "$ANDROID_HOME/cmdline-tools/cmdline-tools" "$ANDROID_HOME/cmdline-tools/19.0"
fi
"$ANDROID_HOME/cmdline-tools/19.0/bin/sdkmanager" --sdk_root="$ANDROID_HOME" --licenses < <(yes)
"$ANDROID_HOME/cmdline-tools/19.0/bin/sdkmanager" --sdk_root="$ANDROID_HOME" 'platform-tools' 'platforms;android-35' 'build-tools;35.0.0' 'ndk;27.2.12479018'
uv python install 3.12
uv sync --project indexer --python 3.12 --extra runtime --locked
