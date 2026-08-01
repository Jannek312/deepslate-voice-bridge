#!/usr/bin/env bash
# Regenerate deepslate_voice_bridge/app/realtime_pb2.py from the vendored proto.
# The generated file is checked in so the runtime image doesn't need protoc.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run python -m grpc_tools.protoc \
  -I deepslate_voice_bridge/app \
  --python_out=deepslate_voice_bridge/app \
  deepslate_voice_bridge/app/realtime.proto
echo "generated deepslate_voice_bridge/app/realtime_pb2.py"
