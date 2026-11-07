#!/usr/bin/env bash
# Plugin « energy » d'astroportd : installation.
#
#   ./install.sh                    consommation (PowerJoular) ; production solaire si un régulateur est déjà configuré
#   ./install.sh --solar            ajoute la production solaire (régulateurs Victron en Bluetooth)
#   ./install.sh --no-consumption   production seule
#   ./install.sh --yes              sans question
#
# Ce que fait le script (idempotent : relancé, il met à jour) :
#   1. copie le plugin dans ~/.local/share/astroportd-plugins/energy (le dossier cloné peut ensuite disparaître) ;
#   2. consommation : compile PowerJoular (github.com/papiche/powerjoular) s'il manque, installe /usr/bin/powerjoular
#      et le service système powerjoular.service (CSV /var/lib/powerjoular/power_24h.csv, redémarré chaque jour) ;
#   3. production : BlueZ, environnement Python du moniteur Victron, service victron-mppt-monitor.service ;
#   4. collecteur energy-collect.timer (systemd --user, sans droits administrateur), lancé une première fois.
#
# Les étapes 2 et 3 demandent l'administrateur (sudo) : chaque commande est affichée avant d'être lancée.
# Origine : Astroport.ONE install/install_powerjoular.sh et install/install_victron.sh.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LIB="$HOME/.local/share/astroportd-plugins/energy"
STATION="${ASTROPORTD_HOME:-$HOME/.zen/astroportd}"
DATA="$STATION/plugins/energy"
DEVICES_JSON="$HOME/.zen/victron.json"
VICTRON_CSV="$HOME/.zen/game/victron/readings.csv"

say() { printf '[energy] %s\n' "$*" >&2; }
die() { printf '[energy] ERREUR : %s\n' "$*" >&2; exit 1; }
run_root() { say "sudo $*"; sudo "$@"; }

WANT_CONSUMPTION=1
WANT_SOLAR=auto
YES=0
for arg in "$@"; do
  case "$arg" in
    --solar) WANT_SOLAR=1 ;;
    --no-solar) WANT_SOLAR=0 ;;
    --no-consumption) WANT_CONSUMPTION=0 ;;
    --yes|-y) YES=1 ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) die "option inconnue : $arg (voir --help)" ;;
  esac
done
if [ "$WANT_SOLAR" = auto ]; then
  if [ -s "$DEVICES_JSON" ] || systemctl cat victron-mppt-monitor.service >/dev/null 2>&1; then WANT_SOLAR=1; else WANT_SOLAR=0; fi
fi

ask() {
  [ "$YES" = 1 ] && return 0
  [ -r /dev/tty ] || return 0
  printf '%s [O/n] ' "$1" > /dev/tty
  read -r answer < /dev/tty || answer=""
  case "$answer" in n|N|non|Non) return 1 ;; *) return 0 ;; esac
}

command -v python3 >/dev/null || die "python3 est requis"
command -v systemctl >/dev/null || die "systemd est requis (systemctl introuvable)"

# --- 1. Copie du plugin ----------------------------------------------------------------------------------------
mkdir -p "$LIB" "$DATA"
cp -r "$HERE/bin" "$HERE/files" "$HERE/plugin.toml" "$HERE/install.sh" "$HERE/setup.sh" "$HERE/uninstall.sh" "$HERE/status.sh" "$LIB/"
chmod +x "$LIB"/bin/*.py "$LIB"/*.sh
say "plugin copié dans $LIB ; données pour la station dans $DATA"

# --- 2. Consommation : PowerJoular -----------------------------------------------------------------------------
install_powerjoular() {
  if command -v powerjoular >/dev/null 2>&1; then
    say "PowerJoular déjà présent : $(command -v powerjoular)"
  else
    ask "PowerJoular n'est pas installé : le compiler depuis github.com/papiche/powerjoular (Ada, demande sudo) ?" || { say "consommation : sautée"; return 0; }
    if ! command -v gprbuild >/dev/null 2>&1; then
      if command -v apt-get >/dev/null 2>&1; then run_root apt-get update; run_root apt-get install -y gnat gprbuild git
      elif command -v dnf >/dev/null 2>&1; then run_root dnf install -y gcc-gnat gprbuild git
      elif command -v pacman >/dev/null 2>&1; then run_root pacman -S --noconfirm --needed gcc-ada git; command -v gprbuild >/dev/null || die "gprbuild introuvable (AUR : yay -S gprbuild)"
      else die "gestionnaire de paquets non reconnu : installez gnat et gprbuild"; fi
    fi
    local src="$LIB/src/powerjoular"
    if [ -d "$src/.git" ]; then git -C "$src" pull --ff-only; else mkdir -p "$LIB/src"; git clone --depth 1 https://github.com/papiche/powerjoular.git "$src"; fi
    (cd "$src" && mkdir -p obj && gprbuild powerjoular.gpr) || die "compilation de PowerJoular impossible"
    local bin="$src/obj/powerjoular"; [ -x "$bin" ] || bin="$src/powerjoular"
    [ -x "$bin" ] || die "binaire PowerJoular introuvable après compilation"
    run_root install -m 0755 "$bin" /usr/bin/powerjoular
    touch "$LIB/.installed-powerjoular"   # uninstall.sh ne retire que ce qu'il a posé
  fi
  if [ ! -d /sys/class/powercap/intel-rapl ] && [ "$(uname -m)" = x86_64 ]; then
    say "attention : compteurs RAPL absents (/sys/class/powercap/intel-rapl) : PowerJoular ne mesurera rien sur cette machine"
  fi
  # Service : celui d'Astroport.ONE, plus un redémarrage quotidien qui borne le CSV aux dernières 24 h.
  run_root install -m 0644 "$LIB/files/powerjoular.service" /etc/systemd/system/powerjoular.service
  run_root systemctl daemon-reload
  run_root systemctl enable powerjoular.service
  run_root systemctl restart powerjoular.service
  if systemctl is-active --quiet powerjoular.service; then say "powerjoular.service actif (CSV /var/lib/powerjoular/power_24h.csv)"
  else say "attention : powerjoular.service ne tourne pas : journalctl -u powerjoular -n 30"; fi
}

# --- 3. Production : moniteur Victron --------------------------------------------------------------------------
install_victron() {
  ask "Installer la lecture des régulateurs solaires Victron (Bluetooth, demande sudo) ?" || { say "production : sautée"; return 0; }
  if ! command -v bluetoothctl >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then run_root apt-get install -y bluez python3-venv
    elif command -v dnf >/dev/null 2>&1; then run_root dnf install -y bluez
    elif command -v pacman >/dev/null 2>&1; then run_root pacman -S --noconfirm --needed bluez bluez-utils
    else die "installez BlueZ (bluetoothctl) à la main"; fi
  fi
  if [ ! -x "$LIB/venv/bin/python3" ]; then
    python3 -m venv "$LIB/venv" || die "python3 -m venv impossible (paquet python3-venv ?)"
  fi
  "$LIB/venv/bin/pip" install --quiet -U -r "$LIB/files/requirements.txt"
  if [ ! -s "$DEVICES_JSON" ]; then
    install -m 0600 "$LIB/files/devices.example.json" "$DEVICES_JSON"
    say "$DEVICES_JSON créé depuis l'exemple : renseignez vos régulateurs avec ./setup.sh victron"
  fi
  mkdir -p "$(dirname "$VICTRON_CSV")"
  local unit; unit="$(mktemp)"
  cat > "$unit" <<UNIT
[Unit]
Description=astroportd-plugins energy : régulateurs solaires Victron (Bluetooth)
After=bluetooth.target
Requires=bluetooth.target

[Service]
Type=simple
User=$USER
ExecStart=$LIB/venv/bin/python3 $LIB/files/victron_monitor.py monitor --config $DEVICES_JSON --csv $VICTRON_CSV
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
  run_root install -m 0644 "$unit" /etc/systemd/system/victron-mppt-monitor.service
  rm -f "$unit"
  run_root systemctl daemon-reload
  run_root systemctl enable victron-mppt-monitor.service
  run_root systemctl restart victron-mppt-monitor.service
  if systemctl is-active --quiet victron-mppt-monitor.service; then say "victron-mppt-monitor.service actif (CSV $VICTRON_CSV)"
  else say "attention : le moniteur Victron ne tourne pas : journalctl -u victron-mppt-monitor -n 30 (régulateurs configurés ? ./setup.sh victron)"; fi
}

[ "$WANT_CONSUMPTION" = 1 ] && install_powerjoular
[ "$WANT_SOLAR" = 1 ] && install_victron

# --- 4. Collecteur (sans droits administrateur) ----------------------------------------------------------------
mkdir -p "$HOME/.config/systemd/user"
install -m 0644 "$LIB/files/energy-collect.service" "$LIB/files/energy-collect.timer" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now energy-collect.timer
python3 "$LIB/bin/energy-collect.py" --out "$DATA" || die "première collecte impossible"
say "collecteur actif (toutes les 5 min) : $DATA/energy.json"
say "pour que la collecte continue sans session ouverte : loginctl enable-linger $USER"
"$LIB/status.sh"
