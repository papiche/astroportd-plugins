#!/usr/bin/env bash
# Plugin « corpus » d'astroportd : état en JSON sur la sortie standard.
#   {"plugin":"corpus","version":…,"ok":bool,"tools":{…},"corpus":{"dir":…,"files":n,"made_at":…},"last_sent":{…}}
set -euo pipefail
STATION="${ASTROPORTD_HOME:-$HOME/.zen/astroportd}"
HERE="$(cd "$(dirname "$0")" && pwd)"
python3 - "$STATION/plugins/corpus" "$HERE/plugin.toml" "${CORPUS_DIR:-$HOME/.zen/tmp/corpus-recherche}" <<'PY'
import json, os, shutil, subprocess, sys
data, manifest, out = sys.argv[1:4]
version = "?"
for line in open(manifest, encoding="utf-8"):
    if line.startswith("version"):
        version = line.split("=", 1)[1].strip().strip('"')
tools = {t: shutil.which(t) is not None for t in ("python3", "exiftool", "ffmpeg")}
try:
    py = open(data + "/python", encoding="utf-8").read().strip()
    tools["pillow_reportlab"] = subprocess.run([py, "-c", "import PIL, reportlab"], capture_output=True, timeout=20).returncode == 0
except (OSError, subprocess.SubprocessError):
    tools["pillow_reportlab"] = False
readme = os.path.join(out, "LISEZMOI.md")
corpus = None
if os.path.exists(readme):
    corpus = {"dir": out, "files": sum(len(f) for _, _, f in os.walk(out)), "made_at": int(os.path.getmtime(readme))}
last = None
try:
    lines = open(data + "/journal.jsonl", encoding="utf-8").read().splitlines()
    last = json.loads(lines[-1]) if lines else None
except (OSError, ValueError):
    pass
json.dump({"plugin": "corpus", "version": version, "ok": all(tools.values()) and corpus is not None, "tools": tools, "corpus": corpus, "last_sent": last},
          sys.stdout, ensure_ascii=False, indent=1)
print()
PY
