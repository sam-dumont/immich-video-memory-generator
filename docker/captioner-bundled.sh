#!/bin/sh
# Same CUDA image, second service: all paths are baked and never downloaded.
exec /opt/llama/llama-server \
    --model /opt/immich-models/captioner/model.gguf \
    --mmproj /opt/immich-models/captioner/mmproj.gguf \
    --alias smolvlm2-500m-base-public \
    --host 0.0.0.0 --port "${CAPTION_PORT:-8092}" \
    --jinja --ctx-size 8192 --n-gpu-layers 99 "$@"
