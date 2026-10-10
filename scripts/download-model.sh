#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$TG_PROJECT_ROOT"
mkdir -p models/e5-small
revision=614241f622f53c4eeff9890bdc4f31cfecc418b3
base="https://huggingface.co/intfloat/multilingual-e5-small/resolve/$revision"
download_checked() {
    local url="$1" file="$2" checksum="$3" size="$4"
    if [[ -f "$file" ]] && printf '%s  %s\n' "$checksum" "$file" | sha256sum -c --status; then return; fi
    if [[ -f "$file" ]] && [[ $(stat -c %s "$file") -ge "$size" ]]; then
        mv "$file" "$file.rejected-$(date +%s)"
    fi
    curl -fLsS -C - --connect-timeout 20 --max-time 1800 "$url" -o "$file"
    printf '%s  %s\n' "$checksum" "$file" | sha256sum -c -
}
download_checked "$base/onnx/model_qint8_avx512_vnni.onnx" models/e5-small/model.onnx dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88 118346824
download_checked "$base/tokenizer.json" models/e5-small/tokenizer.json 0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39 17082730
echo 'Model downloaded. Run tg-index pack-model to validate and package it.'
