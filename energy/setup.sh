#!/usr/bin/env bash
# Plugin « energy » d'astroportd : réglages après installation.
#
#   ./setup.sh victron     trouve les régulateurs Victron proches (20 s) et enregistre adresse + clé de chacun
#   ./setup.sh import      reprend à nouveau l'historique d'Astroport.ONE (~/.zen/game/power_history.json, victron)
#   ./setup.sh collect     lance une collecte tout de suite et affiche le fichier produit
set -euo pipefail

LIB="$HOME/.local/share/astroportd-plugins/energy"
STATION="${ASTROPORTD_HOME:-$HOME/.zen/astroportd}"
DATA="$STATION/plugins/energy"
DEVICES_JSON="$HOME/.zen/victron.json"

say() { printf '[energy] %s\n' "$*" >&2; }
die() { printf '[energy] ERREUR : %s\n' "$*" >&2; exit 1; }
[ -x "$LIB/bin/energy-collect.py" ] || die "plugin non installé : lancez d'abord ./install.sh"

case "${1:-}" in
  victron)
    [ -x "$LIB/venv/bin/python3" ] || die "lecture Victron non installée : ./install.sh --solar"
    say "recherche des régulateurs Victron pendant 20 s (Bluetooth)…"
    "$LIB/venv/bin/python3" "$LIB/files/victron_monitor.py" discover --duration 20 || true
    say "la clé de chaque régulateur se lit dans VictronConnect : Réglages › Infos produit › Instant Readout › Afficher"
    [ -r /dev/tty ] || die "réglage interactif : lancez-le dans un terminal"
    tmp="$(mktemp)"
    printf '{"devices": [' > "$tmp"
    first=1
    while :; do
      printf 'Adresse MAC du régulateur (vide pour terminer) : ' > /dev/tty; read -r mac < /dev/tty || mac=""
      [ -z "$mac" ] && break
      printf 'Nom (ex. MPPT Toit) : ' > /dev/tty; read -r name < /dev/tty || name="$mac"
      printf 'Clé Instant Readout (32 caractères hexadécimaux) : ' > /dev/tty; read -r key < /dev/tty || key=""
      [[ "$mac" =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]] || { say "adresse MAC invalide : ignorée"; continue; }
      [[ "$key" =~ ^[0-9A-Fa-f]{32}$ ]] || { say "clé invalide : ignorée"; continue; }
      [ "$first" = 1 ] || printf ',' >> "$tmp"
      python3 -c 'import json,sys; print(json.dumps({"name": sys.argv[1], "mac": sys.argv[2], "key": sys.argv[3]}), end="")' "$name" "$mac" "$key" >> "$tmp"
      first=0
    done
    printf ']}\n' >> "$tmp"
    [ "$first" = 0 ] || { rm -f "$tmp"; die "aucun régulateur saisi : rien n'a changé"; }
    install -m 0600 "$tmp" "$DEVICES_JSON"; rm -f "$tmp"
    say "$DEVICES_JSON enregistré (lisible par vous seul : il contient les clés)"
    say "sudo systemctl restart victron-mppt-monitor.service"
    sudo systemctl restart victron-mppt-monitor.service
    ;;
  import)
    python3 - "$DATA/state.json" <<'PY'
import json, sys
p = sys.argv[1]
try:
    s = json.load(open(p))
except (OSError, ValueError):
    s = {}
s["legacy_imported"] = False
json.dump(s, open(p, "w"))
PY
    python3 "$LIB/bin/energy-collect.py" --out "$DATA"
    ;;
  collect)
    python3 "$LIB/bin/energy-collect.py" --out "$DATA" --print
    ;;
  *)
    sed -n '2,7p' "$0"; exit 1 ;;
esac
