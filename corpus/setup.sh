#!/usr/bin/env bash
# Dépose le corpus d'essai dans un uCloud par WebDAV (dossier /Essais/Recherche par défaut).
#   ./setup.sh [--dav https://station.exemple.org/dav] [--login vous@exemple.org] [--dest /Essais/Recherche] [--dir dossier]
# Adresse et compte : déduits de la station (station.conf : domain, captain_email), sinon demandés. Le jeton (Mon compte ›
# Appareils) est lu sans écho et passé par l'environnement (ASTRO_TOKEN) : jamais en argument, jamais écrit sur le disque.
set -euo pipefail
STATION="${ASTROPORTD_HOME:-$HOME/.zen/astroportd}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DATA="$STATION/plugins/corpus"
CONF="$STATION/station.conf"
dav="" login="" dest="/Essais/Recherche" dir="${CORPUS_DIR:-$HOME/.zen/tmp/corpus-recherche}"
while (($#)); do
  case "$1" in
    --dav) dav="${2:?}"; shift 2 ;;
    --login) login="${2:?}"; shift 2 ;;
    --dest) dest="${2:?}"; shift 2 ;;
    --dir) dir="${2:?}"; shift 2 ;;
    *) echo "Option inconnue : $1" >&2; exit 2 ;;
  esac
done
[ -f "$dir/LISEZMOI.md" ] || { echo "Pas de corpus dans $dir : lancez d'abord ./install.sh" >&2; exit 1; }
[ -r /dev/tty ] || { echo "Il faut un terminal : le jeton est demandé sans écho." >&2; exit 1; }

conf() { [ -f "$CONF" ] && sed -n "s/^$1 *= *//p" "$CONF" | head -n 1 | tr -d '[:space:]'; }
ask() { # question [défaut] → réponse (le défaut si entrée vide)
  local a=""
  printf '%s%s : ' "$1" "${2:+ [$2]}" > /dev/tty
  read -r a < /dev/tty || a=""
  printf '%s' "${a:-${2:-}}"
}
if [ -z "$dav" ]; then
  domain="$(conf domain || true)"
  dav="$(ask "Adresse WebDAV de la station" "${domain:+https://$domain/dav}")"
fi
case "$dav" in http://*|https://*) ;; *) echo "Adresse WebDAV attendue (https://station/dav) : « $dav »" >&2; exit 1 ;; esac
[ -n "$login" ] || login="$(ask "Compte (adresse du MULTIPASS)" "$(conf captain_email || true)")"
[ -n "$login" ] || { echo "Compte manquant." >&2; exit 1; }

printf 'Jeton d'"'"'appareil de %s (Mon compte › Appareils, non affiché) : ' "$login" > /dev/tty
token=""
read -rs token < /dev/tty || token=""
printf '\n' > /dev/tty
[ -n "$token" ] || { echo "Jeton vide : rien n'est envoyé." >&2; exit 1; }

py="$(cat "$DATA/python" 2>/dev/null || true)"
[ -x "$py" ] || py="$(command -v python3)"
echo "▶ Envoi de $dir vers $dav$dest"
ASTRO_TOKEN="$token" ASTRO_LOGIN="$login" ASTRO_DAV="$dav" "$py" "$HERE/bin/fabriquer_corpus.py" "$dir" --envoyer "$dest" --seulement-envoyer
unset token

mkdir -p "$DATA" && chmod 700 "$DATA"
files="$(find "$dir" -type f | wc -l | tr -d ' ')"
python3 - "$DATA/journal.jsonl" "$dav" "$login" "$dest" "$files" <<'PY'
import json, sys, time
path, dav, login, dest, files = sys.argv[1:6]
with open(path, "a", encoding="utf-8") as f:
    f.write(json.dumps({"at": int(time.time()), "dav": dav, "login": login, "dest": dest, "files": int(files)}, ensure_ascii=False) + "\n")
PY
echo "✔ Corpus déposé dans $dest. Attendez l'analyse, puis cherchez dans Fichiers (voir LISEZMOI.md)."
