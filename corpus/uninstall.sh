#!/usr/bin/env bash
# Retire l'environnement Python du plugin. Le corpus, son cache de photos et le journal restent, sauf avec --purge.
# Ce qui a été déposé dans un uCloud y reste : supprimez le dossier /Essais/Recherche depuis Fichiers.
set -euo pipefail
STATION="${ASTROPORTD_HOME:-$HOME/.zen/astroportd}"
DATA="$STATION/plugins/corpus"
OUT="${CORPUS_DIR:-$HOME/.zen/tmp/corpus-recherche}"
rm -rf "$DATA/venv" "$DATA/python"
echo "Environnement Python du corpus retiré."
if [ "${1:-}" = "--purge" ]; then
  rm -rf "$DATA" "$OUT" "$OUT.cache"
  echo "corpus, cache des photos et journal supprimés."
else
  echo "Corpus conservé dans $OUT (cache : $OUT.cache), journal dans $DATA (--purge pour tout supprimer)."
fi
