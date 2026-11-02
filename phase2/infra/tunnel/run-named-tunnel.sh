#!/usr/bin/env bash
# Run the lingren SVS named tunnel (foreground; use systemd/tmux for persistence).
# Prerequisites (see SETUP.md): `cloudflared tunnel login` done,
# tunnel created, DNS routed, and ~/.cloudflared/config-svs.yml filled in
# from config.yml.template. This script refuses to run against the
# existing SSH tunnel: it uses its own config file, never the default one.
set -eu
CONFIG="${SVS_TUNNEL_CONFIG:-$HOME/.cloudflared/config-svs.yml}"
if [ ! -f "$CONFIG" ]; then
  echo "missing tunnel config: $CONFIG (copy tunnel/config.yml.template and fill it in)" >&2
  exit 1
fi
if grep -qE "hostname:.*bigbangbang" "$CONFIG"; then
  echo "refusing: config routes the SSH tunnel hostname; use a separate SVS hostname" >&2
  exit 1
fi
if grep -q "<TUNNEL-ID\|<HOSTNAME" "$CONFIG"; then
  echo "refusing: config still has <...> placeholders; fill them in first" >&2
  exit 1
fi
exec cloudflared tunnel --config "$CONFIG" run lingren-svs
