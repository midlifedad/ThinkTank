#!/usr/bin/env bash
# Install the Mac Studio transcription stack (Option C, 2026-07-12).
#
# Two pieces:
#   1. NATIVE inference service (launchd): parakeet-mlx + pyannote on
#      Metal/MPS, serving /transcribe on 127.0.0.1:8765. Native because
#      Docker's Linux VM has no GPU/ANE access on macOS.
#   2. DOCKER pull-worker: polls the Railway Postgres queue, downloads and
#      converts audio, POSTs WAVs to the native service.
#
# Prereqs: uv, Docker Desktop, an HF token with access to
# pyannote/speaker-diarization-3.1 (gated model -- accept its terms on
# HuggingFace first), and scripts/mac-worker/mac-worker.env populated with
# the Railway DATABASE_URL.
#
# Usage:  HF_TOKEN=hf_xxx ./scripts/mac-worker/install.sh

set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PLIST_LABEL="com.thinktank.local-inference"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
ENV_FILE="$REPO/scripts/mac-worker/mac-worker.env"

if [[ -z "${HF_TOKEN:-}" ]]; then
    echo "ERROR: HF_TOKEN is required (pyannote's diarization pipeline is gated)." >&2
    echo "Usage: HF_TOKEN=hf_xxx $0" >&2
    exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: $ENV_FILE missing. Copy mac-worker.env.example and set DATABASE_URL." >&2
    exit 1
fi

echo "==> [1/4] Building native inference venv (.venv-inference)"
cd "$REPO"
mkdir -p var
UV_PROJECT_ENVIRONMENT="$REPO/.venv-inference" uv sync --frozen --no-dev --group local-inference

echo "==> [2/4] Installing launchd service ($PLIST_LABEL)"
sed -e "s|__REPO__|$REPO|g" -e "s|__HF_TOKEN__|$HF_TOKEN|g" \
    "$REPO/scripts/mac-worker/$PLIST_LABEL.plist.template" > "$PLIST_DST"
chmod 600 "$PLIST_DST"
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"

echo "==> [3/4] Waiting for inference service (model downloads can take minutes on first run)"
for i in $(seq 1 120); do
    if curl -sf -m 2 http://127.0.0.1:8765/health | grep -q '"models_loaded":true'; then
        echo "    inference service healthy"
        break
    fi
    if [[ $i -eq 120 ]]; then
        echo "ERROR: inference service not healthy after 10 min; check $REPO/var/local-inference.err.log" >&2
        exit 1
    fi
    sleep 5
done

echo "==> [4/4] Starting dockerized pull-worker"
docker compose -f "$REPO/docker/compose.mac-worker.yml" --env-file "$ENV_FILE" up -d --build

echo
echo "Done. Verify:"
echo "  curl -s http://127.0.0.1:8765/health"
echo "  docker compose -f docker/compose.mac-worker.yml logs -f --tail 50"
echo "Uninstall: launchctl unload $PLIST_DST && rm $PLIST_DST; docker compose -f docker/compose.mac-worker.yml down"
