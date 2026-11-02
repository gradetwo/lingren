#!/usr/bin/env bash
# Colab-side service launcher (runs INSIDE the Colab session, where the
# GPU and the weights live -- not on the dev VM).
#
# Flow (design section 7.4): weights from Drive cache when present,
# otherwise download from Hugging Face, then write back to Drive cache.
# The tunnel runs on the same machine as the service.
#
# Required env: SVS_API_KEY (remote deployments must set it; the service
# rejects unauthenticated calls when it is set, and the tunnel URL must
# not be posted publicly).
#   SVS_TUNNEL_MODE=named  -> named tunnel (needs config-svs.yml on the
#                             Colab machine; see SETUP.md)
#   SVS_TUNNEL_MODE=quick  -> quick tunnel (dev only; URL changes every
#                             start, update the Yeban service address)
#   unset                  -> no tunnel, local service only
set -eu
: "${SVS_API_KEY:?set SVS_API_KEY before starting a remote service}"
SERVICE_DIR="${SERVICE_DIR:-$HOME/lingren/service}"
DATA_DIR="${SVS_DATA_DIR:-$SERVICE_DIR/data}"
DRIVE_MODELS="${DRIVE_MODELS:-$HOME/drive/lingren-models}"
LOCAL_MODELS="${LOCAL_MODELS:-$HOME/lingren-models}"

mkdir -p "$DATA_DIR" "$LOCAL_MODELS"
if [ -d "$DRIVE_MODELS" ]; then
  echo "weights: using Drive cache $DRIVE_MODELS"
  ln -sfn "$DRIVE_MODELS" "$LOCAL_MODELS"
else
  echo "weights: no Drive cache; download weights into $LOCAL_MODELS first"
  echo "  SoulX model.pt (~2.8 GB) and VocalRender weights (~9.5 GB) go here;"
  echo "  exact paths are set via SOULX_MODEL_PATH etc. below."
fi

cd "$SERVICE_DIR"
export SOULX_DEVICE="${SOULX_DEVICE:-cuda}"
export SOULX_REPO="${SOULX_REPO:-$HOME/soulx}"
export SOULX_MODEL_PATH="${SOULX_MODEL_PATH:-$LOCAL_MODELS/soulx/model.pt}"
export SOULX_CONFIG="${SOULX_CONFIG:-$SOULX_REPO/soulxsinger/config/soulxsinger.yaml}"
export SOULX_PHONESET="${SOULX_PHONESET:-$SOULX_REPO/soulxsinger/utils/phoneme/phone_set.json}"
SVS_DATA_DIR="$DATA_DIR" nohup uvicorn app.main:app --host 127.0.0.1 --port 8000 \
  > "$DATA_DIR/service.log" 2>&1 &
echo "service log: $DATA_DIR/service.log"

case "${SVS_TUNNEL_MODE:-}" in
  named)
    SVS_TUNNEL_CONFIG="${SVS_TUNNEL_CONFIG:-$HOME/.cloudflared/config-svs.yml}" \
      ./run-named-tunnel.sh > "$DATA_DIR/tunnel.log" 2>&1 &
    echo "named tunnel log: $DATA_DIR/tunnel.log"
    ;;
  quick)
    cloudflared tunnel --url http://localhost:8000 > "$DATA_DIR/tunnel.log" 2>&1 &
    echo "quick tunnel log (URL changes every start): $DATA_DIR/tunnel.log"
    ;;
  *)
    echo "no tunnel started (SVS_TUNNEL_MODE unset); service is local-only"
    ;;
esac
echo "health check: curl http://127.0.0.1:8000/health"
