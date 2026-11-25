#!/usr/bin/env bash
# Plugin « corpus » d'astroportd : vérifie les outils, prépare Python (Pillow, ReportLab) sans droits particuliers, fabrique
# le jeu d'essai dans ~/.zen/tmp/corpus-recherche (photos téléchargées une fois, gardées dans corpus-recherche.cache), puis
# propose de le déposer dans votre uCloud (./setup.sh). Idempotent : relancé, il refabrique le corpus depuis le cache.
#   CORPUS_DIR=/autre/dossier ./install.sh
set -euo pipefail
STATION="${ASTROPORTD_HOME:-$HOME/.zen/astroportd}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DATA="$STATION/plugins/corpus"
OUT="${CORPUS_DIR:-$HOME/.zen/tmp/corpus-recherche}"

missing=()
for tool in python3 exiftool ffmpeg; do
  command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
done
if ((${#missing[@]})); then
  echo "Outils manquants : ${missing[*]}" >&2
  echo "  Debian/Ubuntu : sudo apt install ${missing[*]//exiftool/libimage-exiftool-perl} python3-venv" >&2
  exit 1
fi
# Sortie lue en entier : avec pipefail, « | grep -q » coupe le tube et ffmpeg (SIGPIPE) ferait croire à l'absence.
encoders="$(ffmpeg -hide_banner -encoders 2>/dev/null || true)"
[[ "$encoders" == *libx264* ]] || { echo "ffmpeg sans libx264 : les deux vidéos du corpus ne peuvent pas être encodées." >&2; exit 1; }

mkdir -p "$DATA"
chmod 700 "$DATA"

# Python qui a Pillow et ReportLab : celui du plugin, celui d'astroportd (~/.astro), celui du système ; sinon un
# environnement propre au plugin (aucun paquet installé dans le système).
has_libs() { "$1" -c 'import PIL, reportlab' >/dev/null 2>&1; }
py=""
for c in "$DATA/venv/bin/python" "$HOME/.astro/bin/python" python3; do
  if command -v "$c" >/dev/null 2>&1 && has_libs "$c"; then py="$(command -v "$c")"; break; fi
done
if [ -z "$py" ]; then
  echo "▶ Préparation de Python pour le corpus (Pillow, ReportLab) dans $DATA/venv"
  python3 -m venv "$DATA/venv" || { echo "python3 -m venv impossible : sudo apt install python3-venv" >&2; exit 1; }
  "$DATA/venv/bin/python" -m pip install --quiet --upgrade pip
  "$DATA/venv/bin/python" -m pip install --quiet pillow reportlab
  py="$DATA/venv/bin/python"
fi
printf '%s\n' "$py" > "$DATA/python"

echo "▶ Fabrication du corpus dans $OUT (première fois : quelques minutes, Wikimedia Commons est interrogé poliment)"
"$py" "$HERE/bin/fabriquer_corpus.py" "$OUT"
echo "  Ce qu'il faut chercher et ce qui doit sortir : $OUT/LISEZMOI.md"

# Envoi : seulement sur demande explicite. Sans terminal (installation automatique), on ne demande rien.
answer=""
if [ -r /dev/tty ] && [ -z "${CORPUS_NO_SEND:-}" ]; then
  printf 'Déposer le corpus dans votre uCloud (dossier /Essais/Recherche) ? [o/N] ' > /dev/tty
  read -r answer < /dev/tty || answer=""
fi
case "$answer" in
  o|O|oui|Oui|y|Y) bash "$HERE/setup.sh" --dir "$OUT" ;;
  *) echo "Pour le déposer plus tard : $HERE/setup.sh (le jeton vous sera demandé, sans écho)." ;;
esac
