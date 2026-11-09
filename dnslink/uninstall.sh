#!/usr/bin/env bash
# Retire la commande `ovh-me`. Le journal reste, sauf avec --purge. Aucun secret n'est stocké par ce plugin.
set -euo pipefail
STATION="${ASTROPORTD_HOME:-$HOME/.zen/astroportd}"
BIN="${HOME}/.local/bin/ovh-me"
[ -L "$BIN" ] && rm -f "$BIN" && echo "ovh-me retiré."
if [ "${1:-}" = "--purge" ]; then rm -rf "$STATION/plugins/dnslink"; echo "journal supprimé."
else echo "Journal conservé dans $STATION/plugins/dnslink (--purge pour le supprimer)."; fi
