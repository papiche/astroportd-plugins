#!/usr/bin/env bash
# Plugin « dnslink » : état en JSON ({"plugin","version","ok","coops":[{"name","zone"|"error"}]}).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PYTHONPATH="$HERE/bin" python3 - "$HERE/plugin.toml" <<'PY'
import json, sys
import ovh_me as o
version = next((l.split("=", 1)[1].strip().strip('"') for l in open(sys.argv[1], encoding="utf-8") if l.startswith("version")), "?")
coops = []
try:
    for p in o.load_planes():
        try:
            s = o.ovh_settings(p)
            coops.append({"name": p["name"], "zone": s["OVH_ZONE"]} if s else {"name": p["name"], "error": "paramètres OVH absents"})
        except o.Failure as e:
            coops.append({"name": p["name"], "error": str(e)})
except o.Failure as e:
    coops.append({"name": "?", "error": str(e)})
json.dump({"plugin": "dnslink", "version": version, "ok": any("zone" in c for c in coops), "coops": coops}, sys.stdout, ensure_ascii=False, indent=1)
print()
PY
