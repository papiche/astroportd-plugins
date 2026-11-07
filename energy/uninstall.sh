#!/usr/bin/env bash
# Plugin « energy » d'astroportd : désinstallation.
#
#   ./uninstall.sh           arrête et retire collecteur et services ; garde l'historique et les clés Victron
#   ./uninstall.sh --purge   retire aussi l'historique, le CSV de PowerJoular et ~/.zen/victron.json (clés)
#
# /usr/bin/powerjoular n'est retiré que s'il a été posé par ce plugin.
set -euo pipefail

LIB="$HOME/.local/share/astroportd-plugins/energy"
STATION="${ASTROPORTD_HOME:-$HOME/.zen/astroportd}"
DATA="$STATION/plugins/energy"
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

say() { printf '[energy] %s\n' "$*" >&2; }
run_root() { say "sudo $*"; sudo "$@"; }

systemctl --user disable --now energy-collect.timer 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/energy-collect.service" "$HOME/.config/systemd/user/energy-collect.timer"
systemctl --user daemon-reload || true
say "collecteur retiré"

for unit in victron-mppt-monitor.service powerjoular.service; do
  if systemctl cat "$unit" >/dev/null 2>&1; then
    run_root systemctl disable --now "$unit" || true
    run_root rm -f "/etc/systemd/system/$unit"
  fi
done
run_root systemctl daemon-reload
if [ -f "$LIB/.installed-powerjoular" ]; then run_root rm -f /usr/bin/powerjoular; fi

if [ "$PURGE" = 1 ]; then
  rm -rf "$DATA"
  rm -f "$HOME/.zen/victron.json"
  run_root rm -rf /var/lib/powerjoular
  say "historique, CSV et clés Victron supprimés"
else
  say "gardés : historique ($DATA), clés Victron (~/.zen/victron.json), CSV de PowerJoular ; --purge pour tout retirer"
fi
rm -rf "$LIB"
say "plugin energy désinstallé"
