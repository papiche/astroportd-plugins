#!/usr/bin/env bash
# Plugin « media » d'astroportd : état en JSON sur la sortie standard.
#   {"plugin":"media","version":…,"ok":bool,"tools":{…},"configured":bool,"accounts":[…],"last":{…}}
set -euo pipefail
STATION="${ASTROPORTD_HOME:-$HOME/.zen/astroportd}"
HERE="$(cd "$(dirname "$0")" && pwd)"
python3 - "$STATION/plugins/media" "$HERE/plugin.toml" <<'PY'
import json, shutil, subprocess, sys
data, manifest = sys.argv[1:3]
version = "?"
for line in open(manifest, encoding="utf-8"):
    if line.startswith("version"):
        version = line.split("=", 1)[1].strip().strip('"')
tools = {t: shutil.which(t) is not None for t in ("python3", "ffmpeg", "ffprobe", "yt-dlp")}
try:
    enc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10).stdout
    tools["libx264"] = "libx264" in enc
except (OSError, subprocess.SubprocessError):
    tools["libx264"] = False
try:
    cfg = json.load(open(data + "/config.json", encoding="utf-8"))
except (OSError, ValueError):
    cfg = {}
last = None
try:
    lines = open(data + "/journal.jsonl", encoding="utf-8").read().splitlines()
    last = json.loads(lines[-1]) if lines else None
except (OSError, ValueError):
    pass
configured = bool(cfg.get("station_url")) and bool(cfg.get("accounts"))
out = {"plugin": "media", "version": version, "ok": tools["python3"] and tools["ffmpeg"] and tools["ffprobe"] and configured,
       "tools": tools, "configured": configured, "station": cfg.get("station_url"), "accounts": sorted(cfg.get("accounts", {})), "last": last}
json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
print()
PY
