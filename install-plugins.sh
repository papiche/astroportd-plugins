#!/usr/bin/env bash
# Plugins système d'astroportd : présentation, choix, puis « git clone » du dépôt et installation de chaque plugin retenu.
#
#   curl -fsSL https://raw.githubusercontent.com/papiche/astroportd-plugins/main/install-plugins.sh | bash
#   ./install-plugins.sh --list              # présente les plugins, n'installe rien
#   ./install-plugins.sh media energy        # installe ceux-là, sans question
#   ./install-plugins.sh --all --yes         # tous, sans question
#
# Idempotent : un dépôt déjà cloné est mis à jour (fast-forward seulement), jamais écrasé. Chaque plugin garde son propre
# install.sh (outils à vérifier, droits éventuels) : ce script ne fait que les présenter et les lancer.
set -euo pipefail

REPO="${ASTRO_PLUGINS_REPO:-https://github.com/papiche/astroportd-plugins.git}"
DIR="${ASTRO_PLUGINS_DIR:-$HOME/.zen/astroportd-plugins}"
LIST=0 ALL=0 YES=0
WANTED=()

say()  { printf '%s\n' "$*"; }
warn() { printf '⚠  %s\n' "$*" >&2; }
die()  { printf '✘ %s\n' "$*" >&2; exit 1; }

usage() { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --list) LIST=1 ;;
    --all) ALL=1 ;;
    --yes|-y) YES=1 ;;
    --dir) shift; DIR="${1:?--dir attend un dossier}" ;;
    -h|--help) usage; exit 0 ;;
    -*) die "option inconnue : $1" ;;
    *) WANTED+=("$1") ;;
  esac
  shift
done

command -v git >/dev/null 2>&1 || die "git est nécessaire (sudo apt install git)."

# --- git clone (ou mise à jour) ---------------------------------------------------------------------------------
if [ -d "$DIR/.git" ]; then
  say "▶ Plugins déjà présents dans $DIR : mise à jour"
  git -C "$DIR" pull --ff-only --quiet || warn "mise à jour impossible (modifications locales ?) : on garde la version présente."
else
  [ ! -e "$DIR" ] || die "$DIR existe et n'est pas un dépôt git : choisissez un autre dossier (--dir)."
  say "▶ Récupération des plugins : git clone $REPO"
  mkdir -p "$(dirname "$DIR")"
  git clone --depth 1 --quiet "$REPO" "$DIR" || die "git clone impossible : $REPO"
fi

# --- Présentation : un plugin = un dossier avec plugin.toml + install.sh ------------------------------------------
toml_get() { sed -n "s/^$1 *= *\"\(.*\)\"[[:space:]]*\$/\1/p" "$2" | head -n 1; }
toml_bool() { sed -n "s/^$1 *= *\(true\|false\).*/\1/p" "$2" | head -n 1; }

NAMES=(); DESCS=(); ROOTS=()
for manifest in "$DIR"/*/plugin.toml; do
  [ -f "$manifest" ] || continue
  d="$(dirname "$manifest")"
  [ -x "$d/install.sh" ] || [ -f "$d/install.sh" ] || continue
  NAMES+=("$(basename "$d")")
  DESCS+=("$(toml_get description "$manifest")")
  ROOTS+=("$(toml_bool needs_root "$manifest")")
done
[ "${#NAMES[@]}" -gt 0 ] || die "aucun plugin trouvé dans $DIR."

say ""
say "Plugins disponibles (ils s'ajoutent à la station, qui ne les exécute jamais elle-même) :"
for i in "${!NAMES[@]}"; do
  extra=""; [ "${ROOTS[$i]}" = true ] && extra="  [demande sudo une fois, à l'installation]"
  printf '  %d) %-8s %s%s\n' "$((i + 1))" "${NAMES[$i]}" "${DESCS[$i]}" "$extra"
done
say ""
[ "$LIST" = 1 ] && exit 0

# --- Choix -------------------------------------------------------------------------------------------------------
CHOSEN=()
if [ "$ALL" = 1 ]; then
  CHOSEN=("${NAMES[@]}")
elif [ "${#WANTED[@]}" -gt 0 ]; then
  for w in "${WANTED[@]}"; do
    ok=0; for n in "${NAMES[@]}"; do [ "$n" = "$w" ] && ok=1; done
    [ "$ok" = 1 ] || die "plugin inconnu : $w (disponibles : ${NAMES[*]})"
    CHOSEN+=("$w")
  done
elif [ "$YES" = 1 ]; then
  say "Rien à installer : indiquez des noms ou --all."
  exit 0
else
  if [ -r /dev/tty ]; then
    printf 'Lesquels installer ? (numéros ou noms séparés par des espaces, « tous », entrée = aucun) : ' > /dev/tty
    read -r answer < /dev/tty || answer=""
  else
    say "Pas de terminal interactif. Relancez : $0 <plugin>… (ou --all)."
    exit 0
  fi
  for token in $answer; do
    case "$token" in
      tous|all) CHOSEN=("${NAMES[@]}") ;;
      *[!0-9]*) CHOSEN+=("$token") ;;
      *) idx=$((token - 1)); [ "$idx" -ge 0 ] && [ "$idx" -lt "${#NAMES[@]}" ] && CHOSEN+=("${NAMES[$idx]}") ;;
    esac
  done
fi
[ "${#CHOSEN[@]}" -gt 0 ] || { say "Aucun plugin installé. Quand vous voulez : $DIR/install-plugins.sh"; exit 0; }

# --- Installation ------------------------------------------------------------------------------------------------
failed=()
for n in "${CHOSEN[@]}"; do
  [ -f "$DIR/$n/install.sh" ] || { warn "$n : pas d'install.sh"; failed+=("$n"); continue; }
  say ""; say "▶ Plugin $n"
  if (cd "$DIR/$n" && bash ./install.sh); then
    say "  ✔ $n installé. Suite : $DIR/$n/README.md"
  else
    warn "$n : l'installation s'est arrêtée (voir le message ci-dessus). Les autres plugins continuent."
    failed+=("$n")
  fi
done
say ""
if [ "${#failed[@]}" -gt 0 ]; then
  say "Terminé, sauf : ${failed[*]}"
  exit 1
fi
say "Terminé. L'écran « Plugins » de la station affiche l'état de chacun."
