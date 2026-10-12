#!/usr/bin/env bash
# Provision one model at a time; weights stay in /tmp and never enter Git.
set -euo pipefail
candidate="$1"
output_dir="$2"
test -n "$candidate" && test -n "$output_dir"
mkdir -p "$output_dir"
release="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"

case "$candidate" in
  sensevoice)
    archive="sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09"
    ;;
  qwen3-0.6b)
    archive="sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25"
    ;;
  zipformer-ko)
    archive="sherpa-onnx-streaming-zipformer-korean-2024-06-16"
    ;;
  whispercpp-base)
    commit="0c7be696371069e0baa2515ed7aa08d5a5473aa6"
    git clone --filter=blob:none https://github.com/ggml-org/whisper.cpp.git /tmp/ltf-whispercpp
    git -C /tmp/ltf-whispercpp checkout "$commit"
    cmake -S /tmp/ltf-whispercpp -B /tmp/ltf-whispercpp/build \
      -DGGML_CUDA=OFF -DGGML_METAL=OFF -DWHISPER_SDL2=OFF \
      -DCMAKE_BUILD_TYPE=Release
    cmake --build /tmp/ltf-whispercpp/build --config Release -j 2
    test -x /tmp/ltf-whispercpp/build/bin/whisper-cli
    bash /tmp/ltf-whispercpp/models/download-ggml-model.sh base
    cp /tmp/ltf-whispercpp/models/ggml-base.bin "$output_dir/ggml-base.bin"
    cp /tmp/ltf-whispercpp/build/bin/whisper-cli "$output_dir/whisper-cli"
    echo "$commit" > "$output_dir/whispercpp-commit.txt"
    sha256sum "$output_dir/ggml-base.bin" > "$output_dir/model-sha256.txt"
    exit 0
    ;;
  qwen3-1.7b)
    python - "$output_dir" <<'PY'
import json
import sys
from huggingface_hub import HfApi, snapshot_download
repo = "thieunv/sherpa-onnx-qwen3-asr-1.7B-int8"
sha = HfApi().model_info(repo).sha
snapshot_download(
    repo, revision=sha, local_dir=sys.argv[1],
    allow_patterns=["*.onnx", "tokenizer/*", "*.json", "*.txt"],
)
with open(sys.argv[1] + "/hf-revision.json", "w", encoding="utf-8") as out:
    json.dump({"repo": repo, "sha": sha}, out)
PY
    find "$output_dir" -name '*.onnx' -type f -print0 \
      | sort -z | xargs -0 -r sha256sum > "$output_dir/model-sha256.txt"
    exit 0
    ;;
  *)
    echo "Unsupported candidate: $candidate" >&2
    exit 2
    ;;
esac
archive_file="/tmp/$archive.tar.bz2"
curl --fail --location --retry 3 --retry-all-errors \
  --connect-timeout 20 --max-time 600 \
  "$release/$archive.tar.bz2" --output "$archive_file"
sha256sum "$archive_file" > "$output_dir/download-sha256.txt"
tar -xjf "$archive_file" --no-same-owner --no-same-permissions -C "$output_dir" --strip-components=1
find "$output_dir" -maxdepth 2 -name '*.onnx' -type f -print0 \
  | sort -z | xargs -0 -r sha256sum > "$output_dir/model-sha256.txt"
rm -f "$archive_file"
