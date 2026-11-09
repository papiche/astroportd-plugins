#!/usr/bin/env bash
# Plugin « media » d'astroportd : vérifie les outils, prépare le dossier du plugin et pose la commande `media-add`.
# Idempotent. Aucun droit particulier : rien n'est installé dans le système, seuls les outils manquants sont signalés.
set -euo pipefail
STATION="${ASTROPORTD_HOME:-$HOME/.zen/astroportd}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DATA="$STATION/plugins/media"
BIN="${HOME}/.local/bin"

missing=()
for tool in python3 ffmpeg ffprobe; do
  command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
done
if ((${#missing[@]})); then
  echo "Outils manquants : ${missing[*]}" >&2
  echo "  Debian/Ubuntu : sudo apt install ${missing[*]//ffprobe/ffmpeg}" >&2
  exit 1
fi
command -v yt-dlp >/dev/null 2>&1 || echo "Avertissement : yt-dlp absent, les adresses web ne seront pas acceptées (pipx install yt-dlp)." >&2
ffmpeg -hide_banner -encoders 2>/dev/null | grep -q libx264 || echo "Avertissement : ffmpeg sans libx264, le réencodage H.264 échouera (--no-transcode pour déposer tel quel)." >&2

mkdir -p "$DATA" "$BIN"
chmod 700 "$DATA"
ln -sf "$HERE/bin/media-add" "$BIN/media-add"
echo "media-add installé : $BIN/media-add"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "Ajoutez $BIN à votre PATH." >&2 ;; esac
if [ ! -f "$DATA/config.json" ]; then
  echo "Étape suivante : ./setup.sh --station https://… --email vous@exemple.org --token …"
fi
