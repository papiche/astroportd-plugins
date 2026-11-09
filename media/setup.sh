#!/usr/bin/env bash
# Réglages du plugin « media » : adresse de la station et jeton WebDAV d'un membre (écrits en 0600).
#   ./setup.sh --station https://station.exemple.org --email vous@exemple.org [--token …]   (sans --token : demandé, non affiché)
#              [--relay wss://relai.exemple.org/]   (défaut : la station) [--nostr-dir ~/.zen/game/nostr]
#   ./setup.sh --remove vous@exemple.org
set -euo pipefail
STATION_HOME="${ASTROPORTD_HOME:-$HOME/.zen/astroportd}"
DATA="$STATION_HOME/plugins/media"
CFG="$DATA/config.json"
station="" email="" token="" remove="" relay="" nostr_dir=""
while (($#)); do
  case "$1" in
    --station) station="${2:?}"; shift 2 ;;
    --email) email="${2:?}"; shift 2 ;;
    --token) token="${2:?}"; shift 2 ;;
    --remove) remove="${2:?}"; shift 2 ;;
    --relay) relay="${2:?}"; shift 2 ;;
    --nostr-dir) nostr_dir="${2:?}"; shift 2 ;;
    *) echo "Option inconnue : $1" >&2; exit 2 ;;
  esac
done
mkdir -p "$DATA"
chmod 700 "$DATA"
if [ -n "$remove" ]; then
  [ -f "$CFG" ] || { echo "Rien à retirer."; exit 0; }
  python3 - "$CFG" "$remove" <<'PY'
import json, os, sys
cfg, who = sys.argv[1:3]
d = json.load(open(cfg, encoding="utf-8"))
d.get("accounts", {}).pop(who, None)
tmp = cfg + ".tmp"
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
os.replace(tmp, cfg)
print("compte retiré :", who)
PY
  exit 0
fi
if [ -z "$station" ] || [ -z "$email" ]; then
  echo "Usage : $0 --station URL --email ADRESSE [--token JETON]" >&2
  exit 2
fi
if [ -z "$token" ]; then
  read -r -s -p "Jeton WebDAV de $email : " token
  echo
fi
[ -n "$token" ] || { echo "Jeton vide." >&2; exit 2; }
# Le jeton ne passe jamais par la ligne de commande de python : il est lu dans l'environnement.
STATION="$station" EMAIL="$email" TOKEN="$token" RELAY="$relay" NOSTR_DIR="$nostr_dir" python3 - "$CFG" <<'PY'
import json, os, sys
cfg = sys.argv[1]
try:
    d = json.load(open(cfg, encoding="utf-8"))
except (FileNotFoundError, ValueError):
    d = {}
d["station_url"] = os.environ["STATION"].rstrip("/")
d.setdefault("mount", "/dav")
if os.environ.get("RELAY"):
    d["relay_url"] = os.environ["RELAY"]
if os.environ.get("NOSTR_DIR"):
    d["nostr_dir"] = os.environ["NOSTR_DIR"]
d.setdefault("accounts", {})[os.environ["EMAIL"]] = os.environ["TOKEN"]
tmp = cfg + ".tmp"
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
os.replace(tmp, cfg)
print("configuration écrite :", cfg)
PY
