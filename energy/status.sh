#!/usr/bin/env bash
# Plugin « energy » d'astroportd : état en JSON (sur la sortie standard), pour le Capitaine et la station.
#   {"plugin":"energy","version":…,"ok":bool,"consumption":{…},"production":{…},"collector":{…},"data":{…}}
set -euo pipefail
STATION="${ASTROPORTD_HOME:-$HOME/.zen/astroportd}"
HERE="$(cd "$(dirname "$0")" && pwd)"
# active|exists [--user] <unité>
active() { if systemctl "${@:1:$#-1}" is-active --quiet "${!#}" 2>/dev/null; then echo true; else echo false; fi; }
exists() { if systemctl "${@:1:$#-1}" cat "${!#}" >/dev/null 2>&1; then echo true; else echo false; fi; }
python3 - "$STATION/plugins/energy/energy.json" "$HERE/plugin.toml" \
  "$(exists powerjoular.service)" "$(active powerjoular.service)" \
  "$(exists victron-mppt-monitor.service)" "$(active victron-mppt-monitor.service)" \
  "$(active --user energy-collect.timer)" <<'PY'
import json, os, sys, time
data, manifest, pj_inst, pj_act, vi_inst, vi_act, timer = sys.argv[1:8]
version = "?"
try:
    for line in open(manifest, encoding="utf-8"):
        if line.startswith("version"):
            version = line.split("=", 1)[1].strip().strip('"')
except OSError:
    pass
b = lambda v: v == "true"
doc, err = None, None
try:
    doc = json.load(open(data, encoding="utf-8"))
except FileNotFoundError:
    err = "pas encore de collecte"
except (OSError, ValueError) as e:
    err = f"illisible : {e}"
age = int(time.time() - doc["generated_at"]) if doc and isinstance(doc.get("generated_at"), int) else None
valid = bool(doc) and doc.get("schema") == "energy/v1"
fresh = valid and age is not None and age < 3600
out = {
    "plugin": "energy", "version": version,
    "ok": fresh and b(timer) and (b(pj_act) or not b(pj_inst)) and (b(vi_act) or not b(vi_inst)),
    "consumption": {"service": "powerjoular.service", "installed": b(pj_inst), "active": b(pj_act), "csv": os.path.exists("/var/lib/powerjoular/power_24h.csv")},
    "production": {"service": "victron-mppt-monitor.service", "installed": b(vi_inst), "active": b(vi_act)},
    "collector": {"timer": "energy-collect.timer", "active": b(timer)},
    "data": {"file": data, "schema": doc.get("schema") if doc else None, "valid": valid, "age_secs": age, "fresh": fresh, "error": err},
}
json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
print()
PY
