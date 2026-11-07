#!/usr/bin/env python3
"""Collecteur du plugin « energy » d'astroportd (contrat energy/v1).

Lit, sans droits administrateur, ce que produisent les capteurs installés par le plugin :

- consommation : le CSV de PowerJoular (service root, une mesure par seconde, lisible par tous) ;
- production : le CSV du moniteur Victron (régulateurs solaires, annonces Bluetooth « Instant Readout »).

Il relit chaque CSV **à partir de là où il s'était arrêté** (position mémorisée, remise à zéro si le fichier a été
raccourci ou remplacé), tient un historique par jour (`history.json`) et écrit le fichier contrat `energy.json`,
que la station astroportd lit pour sa balise publique (kind 30850) et son cockpit. Écriture atomique (fichier
temporaire puis renommage) : la station ne lit jamais un fichier à moitié écrit.

Aucune dépendance hors de la bibliothèque standard. Lancé par `energy-collect.timer` (systemd --user, 5 min).
"""
import argparse
import csv
import io
import json
import os
import sys
import time
from pathlib import Path

SCHEMA = "energy/v1"
DEFAULT_POWER_CSV = "/var/lib/powerjoular/power_24h.csv"
DEFAULT_VICTRON_CSV = str(Path.home() / ".zen/game/victron/readings.csv")
DEFAULT_OUT = str(Path(os.environ.get("ASTROPORTD_HOME", Path.home() / ".zen/astroportd")) / "plugins/energy")
# Puissance « instantanée » : moyenne des échantillons des 5 dernières minutes lues.
LIVE_WINDOW_SECS = 300
# Moyenne de consommation « récente » : les 30 derniers jours enregistrés (comme power_monitor.sh power-stats).
RECENT_DAYS = 30


def log(msg):
    print(f"[energy-collect] {msg}", file=sys.stderr)


def parse_ts(s):
    """« AAAA-MM-JJ HH:MM:SS » (heure locale, format de PowerJoular et du moniteur Victron) → secondes Unix."""
    try:
        return time.mktime(time.strptime(s.strip()[:19], "%Y-%m-%d %H:%M:%S"))
    except (ValueError, AttributeError):
        return None


def fnum(v):
    try:
        x = float(v)
        return x if x == x and abs(x) != float("inf") else None
    except (TypeError, ValueError):
        return None


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as e:
        log(f"ERREUR lecture {path} : {e} (repart de zéro)")
        return default


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def read_new_rows(csv_path, cursor):
    """Lignes ajoutées depuis `cursor` ({inode, offset, header}). Rend (lignes en dict, nouveau curseur)."""
    try:
        st = os.stat(csv_path)
    except FileNotFoundError:
        return [], cursor
    restart = cursor.get("inode") != st.st_ino or st.st_size < cursor.get("offset", 0)
    offset = 0 if restart else cursor.get("offset", 0)
    header = None if restart else cursor.get("header")
    with open(csv_path, "rb") as f:
        f.seek(offset)
        data = f.read()
    # On ne garde que des lignes complètes : la fin d'une ligne en cours d'écriture sera lue au prochain passage.
    end = data.rfind(b"\n")
    if end < 0:
        return [], {"inode": st.st_ino, "offset": offset, "header": header}
    chunk = data[: end + 1].decode("utf-8", errors="replace")
    lines = chunk.splitlines()
    if header is None and lines:
        header = next(csv.reader([lines[0]]))
        lines = lines[1:]
    rows = []
    for rec in csv.reader(lines):
        if header and len(rec) == len(header):
            rows.append(dict(zip(header, rec)))
    return rows, {"inode": st.st_ino, "offset": offset + end + 1, "header": header}


def ingest_power(rows, days, live, seen):
    """PowerJoular : colonnes Date, CPU Utilization, Total Power… Cumule par jour (somme, nombre, premier, dernier).
    `seen` : horodatage du dernier échantillon déjà compté. Le service réduit son CSV aux dernières 24 h à chaque
    redémarrage (nouveau fichier, relu en entier) : sans ce seuil, ces heures seraient comptées deux fois. Rend le
    nouveau seuil."""
    for r in rows:
        ts, w = parse_ts(r.get("Date", "")), fnum(r.get("Total Power"))
        if ts is None or w is None or w < 0 or (seen is not None and ts <= seen):
            continue
        seen = ts
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        acc = days.setdefault(day, {}).setdefault("power_acc", {"sum_w": 0.0, "n": 0, "first": ts, "last": ts})
        acc["sum_w"] += w
        acc["n"] += 1
        acc["first"] = min(acc["first"], ts)
        acc["last"] = max(acc["last"], ts)
        live.append((ts, w))
    return seen


def finish_power_day(d):
    """kWh d'un jour = puissance moyenne × durée réellement couverte (comme power_monitor.sh record_daily_history)."""
    acc = d.get("power_acc")
    if not acc or acc["n"] < 2:
        return
    avg_w = acc["sum_w"] / acc["n"]
    hours = max(0.0, (acc["last"] - acc["first"]) / 3600.0)
    # Un jour déjà connu sur plus d'heures (repris d'Astroport.ONE, ou CSV raccourci depuis) garde sa mesure.
    if d.get("kwh") is not None and fnum(d.get("hours")) is not None and d["hours"] > hours:
        return
    d["avg_w"] = round(avg_w, 4)
    d["hours"] = round(hours, 4)
    d["samples"] = acc["n"]
    d["kwh"] = round(avg_w * hours / 1000.0, 6)


def ingest_victron(rows, days, live_by_mac, seen):
    """Victron : par régulateur (mac), production du jour = plus grande valeur de `yield_today` (Wh) ; la station en
    fait la somme. Puissance et tension : dernière lecture de chaque régulateur. `seen` : comme pour PowerJoular
    (plusieurs régulateurs peuvent annoncer dans la même seconde : seules les secondes antérieures sont écartées)."""
    newest = seen
    for r in rows:
        ts = parse_ts(r.get("timestamp", ""))
        if ts is None or (seen is not None and ts < seen):
            continue
        newest = ts if newest is None else max(newest, ts)
        mac = (r.get("mac") or r.get("name") or "?").lower()
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        sol = days.setdefault(day, {}).setdefault("solar_acc", {})
        dev = sol.setdefault(mac, {"yield_wh": 0.0, "sum_w": 0.0, "n": 0})
        y, p = fnum(r.get("yield_today")), fnum(r.get("solar_power"))
        if y is not None:
            dev["yield_wh"] = max(dev["yield_wh"], y)
        if p is not None:
            dev["sum_w"] += p
            dev["n"] += 1
        live_by_mac[mac] = {
            "at": ts,
            "power_w": p,
            "battery_v": fnum(r.get("battery_voltage")),
            "charge_state": (r.get("charge_state") or "unknown").strip() or "unknown",
        }
    return newest


def finish_solar_day(d):
    sol = d.get("solar_acc")
    if not sol:
        return
    d["yield_wh"] = round(sum(v["yield_wh"] for v in sol.values()), 2)
    n = sum(v["n"] for v in sol.values())
    d["solar_samples"] = n


def import_legacy(history, power_json, victron_json):
    """Reprend une fois l'historique d'Astroport.ONE (power_history.json, victron_history.json) pour les jours absents."""
    imported = 0
    for path, keys in ((power_json, ("kwh", "avg_w", "hours", "samples")), (victron_json, ("yield_wh",))):
        if not path:
            continue
        legacy = load_json(path, None)
        if not isinstance(legacy, dict) or not isinstance(legacy.get("days"), dict):
            continue
        for day, v in legacy["days"].items():
            if not isinstance(v, dict) or len(day) != 10:
                continue
            d = history["days"].setdefault(day, {})
            for k in keys:
                if k in v and k not in d and fnum(v[k]) is not None:
                    d[k] = fnum(v[k])
                    imported += 1
    return imported


def stats(history, now):
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    month = today[:7]
    days = history["days"]
    power_days = sorted(k for k, v in days.items() if "kwh" in v)
    solar_days = sorted(k for k, v in days.items() if "yield_wh" in v)
    recent = [days[k]["avg_w"] for k in power_days[-RECENT_DAYS:] if "avg_w" in days[k]]
    consumption = None
    if power_days:
        consumption = {
            "today_kwh": round(days.get(today, {}).get("kwh", 0.0), 4),
            "month_kwh": round(sum(days[k]["kwh"] for k in power_days if k.startswith(month)), 4),
            "total_kwh": round(sum(days[k]["kwh"] for k in power_days), 4),
            "avg_w_recent": round(sum(recent) / len(recent), 2) if recent else 0.0,
            "since": power_days[0],
            "days_recorded": len(power_days),
        }
    production = None
    if solar_days:
        production = {
            "today_wh": round(days.get(today, {}).get("yield_wh", 0.0), 2),
            "month_wh": round(sum(days[k]["yield_wh"] for k in solar_days if k.startswith(month)), 2),
            "total_wh": round(sum(days[k]["yield_wh"] for k in solar_days), 2),
            "since": solar_days[0],
            "days_recorded": len(solar_days),
        }
    return consumption, production


def run(args, now=None):
    now = now or time.time()
    out = Path(args.out)
    history = load_json(out / "history.json", {"days": {}})
    history.setdefault("days", {})
    state = load_json(out / "state.json", {})
    if not state.get("legacy_imported"):
        n = import_legacy(history, args.legacy_power, args.legacy_victron)
        state["legacy_imported"] = True
        if n:
            log(f"historique d'Astroport.ONE repris : {n} valeur(s)")

    days = history["days"]
    live_power = []
    rows, state["power"] = read_new_rows(args.power_csv, state.get("power", {}))
    state["power_seen"] = ingest_power(rows, days, live_power, state.get("power_seen"))
    live_solar = state.get("solar_live", {})
    rows_v, state["victron"] = read_new_rows(args.victron_csv, state.get("victron", {}))
    state["victron_seen"] = ingest_victron(rows_v, days, live_solar, state.get("victron_seen"))
    state["solar_live"] = live_solar
    for d in days.values():
        finish_power_day(d)
        finish_solar_day(d)
    # Les accumulateurs ne servent plus une fois le jour passé : l'historique garde les agrégats.
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    for k, d in days.items():
        if k < today:
            d.pop("power_acc", None)
            d.pop("solar_acc", None)

    consumption, production = stats(history, now)
    if consumption is not None:
        last = max((ts for ts, _ in live_power), default=None)
        if last is not None:
            window = [w for ts, w in live_power if ts >= last - LIVE_WINDOW_SECS]
            state["power_live"] = {"at": last, "power_w": round(sum(window) / len(window), 2)}
        pl = state.get("power_live") or {}
        consumption.update({"source": "powerjoular", "power_w": pl.get("power_w"), "last_sample_at": int(pl["at"]) if pl.get("at") else None})
    if production is not None or live_solar:
        production = production or {"today_wh": 0.0, "month_wh": 0.0, "total_wh": 0.0, "since": None, "days_recorded": 0}
        recent = [v for v in live_solar.values() if v.get("at") and v["at"] >= now - 3600]
        production.update({
            "source": "victron",
            "power_w": round(sum(v["power_w"] or 0.0 for v in recent), 2) if recent else None,
            "battery_v": next((v["battery_v"] for v in sorted(recent, key=lambda v: -v["at"]) if v.get("battery_v") is not None), None),
            "charge_state": next((v["charge_state"] for v in sorted(recent, key=lambda v: -v["at"])), "unknown"),
            "devices": len(live_solar),
            "last_sample_at": int(max(v["at"] for v in live_solar.values())) if live_solar else None,
        })

    history["since"] = min(days) if days else None
    save_json(out / "history.json", history)
    save_json(out / "state.json", state)
    doc = {"schema": SCHEMA, "generated_at": int(now), "consumption": consumption, "production": production}
    save_json(out / "energy.json", doc)
    return doc


def main(argv=None):
    p = argparse.ArgumentParser(description="Collecteur energy/v1 du plugin energy d'astroportd")
    p.add_argument("--out", default=DEFAULT_OUT, help="dossier du plugin côté station (défaut : %(default)s)")
    p.add_argument("--power-csv", default=os.environ.get("POWER_24H_CSV", DEFAULT_POWER_CSV))
    p.add_argument("--victron-csv", default=os.environ.get("VICTRON_CSV", DEFAULT_VICTRON_CSV))
    p.add_argument("--legacy-power", default=str(Path.home() / ".zen/game/power_history.json"))
    p.add_argument("--legacy-victron", default=str(Path.home() / ".zen/game/victron/victron_history.json"))
    p.add_argument("--print", action="store_true", help="affiche energy.json après écriture")
    args = p.parse_args(argv)
    try:
        doc = run(args)
    except OSError as e:
        log(f"ERREUR : {e}")
        return 1
    if args.print:
        json.dump(doc, sys.stdout, ensure_ascii=False, indent=1)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
