#!/usr/bin/env bash
# Plugins système d'astroportd : présentation, choix, puis « git clone » du dépôt et installation de chaque plugin retenu.
#
#   curl -fsSL https://raw.githubusercontent.com/papiche/astroportd-plugins/main/install-plugins.sh | bash
#   ./install-plugins.sh --list              # présente les plugins et leur état, n'installe rien
#   ./install-plugins.sh media energy        # installe (ou réinstalle) ceux-là, sans question
#   ./install-plugins.sh --all --yes         # tous, sans question
#   ./install-plugins.sh --update            # met à jour les plugins installés, propose les nouveaux (--yes : mises à jour seules)
#   ./install-plugins.sh --status            # une ligne « nom|installée|disponible|état » par plugin (lu par la station)
#
# Idempotent : un dépôt déjà cloné est mis à jour (fast-forward seulement), jamais écrasé. Chaque plugin garde son propre
# install.sh (outils à vérifier, droits éventuels) : ce script ne fait que les présenter et les lancer. La version installée
# de chaque plugin est notée dans <station>/plugins/.installed/<nom> ; les plugins déjà vus, dans <station>/plugins/.known
# (ce qui n'y est pas encore est « nouveau »).
set -euo pipefail

REPO="${ASTRO_PLUGINS_REPO:-https://github.com/papiche/astroportd-plugins.git}"
DIR="${ASTRO_PLUGINS_DIR:-$HOME/.zen/astroportd-plugins}"
STATION="${ASTROPORTD_HOME:-$HOME/.zen/astroportd}"
STATE="$STATION/plugins/.installed"
KNOWN="$STATION/plugins/.known"
LIST=0 ALL=0 YES=0 UPDATE=0 STATUS=0 PULL=1
WANTED=()

say()  { [ "$STATUS" = 1 ] || printf '%s\n' "$*"; }
warn() { printf '⚠  %s\n' "$*" >&2; }
die()  { printf '✘ %s\n' "$*" >&2; exit 1; }

usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --list) LIST=1 ;;
    --all) ALL=1 ;;
    --yes|-y) YES=1 ;;
    --update) UPDATE=1 ;;
    --status) STATUS=1 ;;
    --no-pull) PULL=0 ;;
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
  if [ "$PULL" = 1 ]; then
    say "▶ Plugins déjà présents dans $DIR : récupération des nouveautés"
    git -C "$DIR" pull --ff-only --quiet || warn "mise à jour impossible (modifications locales ?) : on garde la version présente."
  fi
else
  [ ! -e "$DIR" ] || die "$DIR existe et n'est pas un dépôt git : choisissez un autre dossier (--dir)."
  say "▶ Récupération des plugins : git clone $REPO"
  mkdir -p "$(dirname "$DIR")"
  git clone --depth 1 --quiet "$REPO" "$DIR" || die "git clone impossible : $REPO"
fi

# --- Inventaire : un plugin = un dossier avec plugin.toml + install.sh ------------------------------------------
toml_get() { sed -n "s/^$1 *= *\"\(.*\)\"[[:space:]]*\$/\1/p" "$2" | head -n 1; }
toml_bool() { sed -n "s/^$1 *= *\(true\|false\).*/\1/p" "$2" | head -n 1; }

# Version installée : celle notée à la dernière installation ; « ? » pour un plugin installé avant ce suivi (son dossier
# de données existe) ; vide s'il n'est pas installé.
installed_version() {
  local name="$1" data_dir="$2"
  if [ -f "$STATE/$name" ]; then head -n 1 "$STATE/$name"
  elif [ -n "$data_dir" ] && [ -d "$STATION/$data_dir" ]; then printf '?'
  fi
}
was_known() { [ -f "$KNOWN" ] && grep -qx "$1" "$KNOWN"; }

NAMES=(); DESCS=(); ROOTS=(); VERS=(); HAVE=(); STATES=()
for manifest in "$DIR"/*/plugin.toml; do
  [ -f "$manifest" ] || continue
  d="$(dirname "$manifest")"
  [ -f "$d/install.sh" ] || continue
  n="$(basename "$d")"; v="$(toml_get version "$manifest")"; h="$(installed_version "$n" "$(toml_get data_dir "$manifest")")"
  if [ -n "$h" ] && [ "$h" = "$v" ]; then s=ajour
  elif [ -n "$h" ]; then s=maj
  elif [ -f "$KNOWN" ] && ! was_known "$n"; then s=nouveau
  else s=absent; fi
  NAMES+=("$n"); DESCS+=("$(toml_get description "$manifest")"); ROOTS+=("$(toml_bool needs_root "$manifest")")
  VERS+=("$v"); HAVE+=("$h"); STATES+=("$s")
done
[ "${#NAMES[@]}" -gt 0 ] || die "aucun plugin trouvé dans $DIR."

remember_known() { mkdir -p "$(dirname "$KNOWN")" && printf '%s\n' "${NAMES[@]}" > "$KNOWN"; }

if [ "$STATUS" = 1 ]; then
  for i in "${!NAMES[@]}"; do printf '%s|%s|%s|%s\n' "${NAMES[$i]}" "${HAVE[$i]}" "${VERS[$i]}" "${STATES[$i]}"; done
  exit 0
fi

state_words() {
  case "${STATES[$1]}" in
    ajour) printf 'installé, à jour (%s)' "${VERS[$1]}" ;;
    maj) printf 'mise à jour : %s → %s' "${HAVE[$1]/#\?/version inconnue}" "${VERS[$1]}" ;;
    nouveau) printf 'NOUVEAU (%s)' "${VERS[$1]}" ;;
    *) printf 'non installé (%s)' "${VERS[$1]}" ;;
  esac
}

say ""
say "Plugins (ils s'ajoutent à la station, qui ne les exécute jamais elle-même) :"
for i in "${!NAMES[@]}"; do
  extra=""; [ "${ROOTS[$i]}" = true ] && extra="  [demande sudo une fois, à l'installation]"
  say "$(printf '  %d) %-8s %s' "$((i + 1))" "${NAMES[$i]}" "$(state_words "$i")")"
  say "     ${DESCS[$i]}${extra}"
done
say ""
[ "$LIST" = 1 ] && exit 0

ask() { # question → réponse lue au terminal (vide sans terminal)
  local a=""
  if [ -r /dev/tty ]; then printf '%s' "$1" > /dev/tty; read -r a < /dev/tty || a=""; fi
  printf '%s' "$a"
}
# Numéros ou noms → noms connus (« tous » = la liste donnée en second).
pick() {
  local token idx out=()
  for token in $1; do
    case "$token" in
      tous|all) out+=("${@:2}") ;;
      *[!0-9]*) out+=("$token") ;;
      *) idx=$((token - 1)); [ "$idx" -ge 0 ] && [ "$idx" -lt "${#NAMES[@]}" ] && out+=("${NAMES[$idx]}") ;;
    esac
  done
  printf '%s\n' "${out[@]}"
}

# --- Choix -------------------------------------------------------------------------------------------------------
CHOSEN=()
if [ "$UPDATE" = 1 ]; then
  STALE=(); FRESH=()
  for i in "${!NAMES[@]}"; do
    case "${STATES[$i]}" in maj) STALE+=("${NAMES[$i]}") ;; nouveau) FRESH+=("${NAMES[$i]}") ;; esac
  done
  if [ "${#STALE[@]}" -gt 0 ]; then
    a="o"; [ "$YES" = 1 ] || a="$(ask "Mettre à jour ${STALE[*]} ? [O/n] ")"
    case "${a:-o}" in n|N|non|Non) say "Mises à jour laissées de côté." ;; *) CHOSEN+=("${STALE[@]}") ;; esac
  else
    say "Plugins installés : tous à jour."
  fi
  if [ "${#FRESH[@]}" -gt 0 ] && [ "$YES" = 0 ]; then
    a="$(ask "Nouveaux plugins : ${FRESH[*]}. Lesquels installer ? (noms, « tous », entrée = aucun) : ")"
    while IFS= read -r n; do [ -n "$n" ] && CHOSEN+=("$n"); done < <(pick "$a" "${FRESH[@]}")
  fi
elif [ "$ALL" = 1 ]; then
  CHOSEN=("${NAMES[@]}")
elif [ "${#WANTED[@]}" -gt 0 ]; then
  CHOSEN=("${WANTED[@]}")
elif [ "$YES" = 1 ]; then
  say "Rien à installer : indiquez des noms, --all ou --update."
  exit 0
elif [ -r /dev/tty ]; then
  a="$(ask 'Lesquels installer ou mettre à jour ? (numéros ou noms séparés par des espaces, « tous », entrée = aucun) : ')"
  while IFS= read -r n; do [ -n "$n" ] && CHOSEN+=("$n"); done < <(pick "$a" "${NAMES[@]}")
else
  say "Pas de terminal interactif. Relancez : $0 <plugin>… (ou --all, --update)."
  exit 0
fi
for w in ${CHOSEN[@]+"${CHOSEN[@]}"}; do
  ok=0; for n in "${NAMES[@]}"; do [ "$n" = "$w" ] && ok=1; done
  [ "$ok" = 1 ] || die "plugin inconnu : $w (disponibles : ${NAMES[*]})"
done
remember_known
[ "${#CHOSEN[@]}" -gt 0 ] || { say "Aucun plugin installé. Quand vous voulez : $DIR/install-plugins.sh"; exit 0; }

# --- Installation ------------------------------------------------------------------------------------------------
failed=()
for n in "${CHOSEN[@]}"; do
  say ""; say "▶ Plugin $n"
  if (cd "$DIR/$n" && bash ./install.sh); then
    mkdir -p "$STATE"
    toml_get version "$DIR/$n/plugin.toml" > "$STATE/$n"
    say "  ✔ $n installé ($(cat "$STATE/$n")). Suite : $DIR/$n/README.md"
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
