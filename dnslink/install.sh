#!/usr/bin/env bash
# Plugin « dnslink » d'astroportd : pose la commande `ovh-me`. Idempotent, sans droits particuliers.
set -euo pipefail
STATION="${ASTROPORTD_HOME:-$HOME/.zen/astroportd}"
HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="${HOME}/.local/bin"
command -v python3 >/dev/null 2>&1 || { echo "python3 manquant (sudo apt install python3)" >&2; exit 1; }
[ -f "$HERE/../media/bin/nostr_lite.py" ] || { echo "media/bin/nostr_lite.py introuvable : clonez le dépôt astroportd-plugins en entier" >&2; exit 1; }
mkdir -p "$STATION/plugins/dnslink" "$BIN"
chmod 700 "$STATION/plugins/dnslink"
ln -sf "$HERE/bin/ovh_me.py" "$BIN/ovh-me"
echo "ovh-me installé : $BIN/ovh-me"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "Ajoutez $BIN à votre PATH." >&2 ;; esac
echo "Étape suivante : ovh-me coops   (coopératives de la station et état de leurs paramètres OVH)"
