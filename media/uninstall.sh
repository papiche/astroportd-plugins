#!/usr/bin/env bash
# Retire la commande `media-add`. La configuration (jeton) et le journal restent, sauf avec --purge.
set -euo pipefail
STATION="${ASTROPORTD_HOME:-$HOME/.zen/astroportd}"
BIN="${HOME}/.local/bin/media-add"
[ -L "$BIN" ] && rm -f "$BIN" && echo "media-add retiré."
if [ "${1:-}" = "--purge" ]; then
  rm -rf "$STATION/plugins/media"
  echo "configuration et journal supprimés."
else
  echo "Configuration et journal conservés dans $STATION/plugins/media (--purge pour les supprimer)."
fi
