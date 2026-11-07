"""Tests du collecteur energy/v1 : CSV synthétiques, relecture incrémentale, redémarrage de PowerJoular, schéma.

    python3 -m unittest discover -s tests        (la validation de schéma demande le paquet jsonschema)
"""
import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("energy_collect", ROOT / "energy/bin/energy-collect.py")
ec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ec)

try:
    import jsonschema
except ImportError:  # la CI l'installe ; en local, le reste des tests tourne quand même
    jsonschema = None

DAY = "2026-11-06"


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def ts(h, m=0, s=0, day=DAY):
    return f"{day} {h:02d}:{m:02d}:{s:02d}"


class Collect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.out, self.power, self.victron = d / "out", d / "power.csv", d / "victron.csv"
        self.now = time.mktime(time.strptime(ts(12), "%Y-%m-%d %H:%M:%S"))

    def tearDown(self):
        self.tmp.cleanup()

    def args(self, **kw):
        a = dict(out=str(self.out), power_csv=str(self.power), victron_csv=str(self.victron), legacy_power="", legacy_victron="")
        a.update(kw)
        return type("A", (), a)

    def write_power(self, rows, mode="w"):
        with open(self.power, mode) as f:
            if mode == "w":
                f.write("Date,CPU Utilization,Total Power,CPU Power,GPU Power\n")
            for t, w in rows:
                f.write(f"{t},0.1,{w},{w},0\n")

    def test_kwh_is_mean_power_times_covered_hours_and_rereading_counts_nothing_twice(self):
        # 10 W de 00:00 à 10:00 (un échantillon par minute) : 0,1 kWh.
        self.write_power([(ts(h, m), 10) for h in range(10) for m in range(60)] + [(ts(10), 10)])
        doc = ec.run(self.args(), now=self.now)
        self.assertAlmostEqual(doc["consumption"]["today_kwh"], 0.1, places=4)
        self.assertEqual(doc["consumption"]["power_w"], 10)
        # Une ligne à moitié écrite n'est pas lue ; complétée, elle compte au passage suivant.
        with open(self.power, "a") as f:
            f.write(f"{ts(11)},0.1,30,30,0\n{ts(11, 1)},0.1,3")
        ec.run(self.args(), now=self.now)
        with open(self.power, "a") as f:
            f.write("0,30,0\n")
        doc = ec.run(self.args(), now=self.now)
        hist = load(self.out / "history.json")["days"][DAY]
        self.assertEqual(hist["power_acc"]["n"], 603, "chaque échantillon une seule fois")
        # PowerJoular redémarre : son service réécrit le CSV (nouveau fichier) avec les dernières heures.
        os.remove(self.power)
        self.write_power([(ts(10), 10), (ts(11), 30), (ts(11, 1), 30), (ts(11, 2), 20)])
        ec.run(self.args(), now=self.now)
        hist = load(self.out / "history.json")["days"][DAY]
        self.assertEqual(hist["power_acc"]["n"], 604, "après réécriture, seul le nouvel échantillon compte")

    def test_a_partial_day_never_replaces_a_better_known_one(self):
        legacy = Path(self.tmp.name) / "power_history.json"
        legacy.write_text(json.dumps({"since": "2026-11-05", "days": {"2026-11-05": {"kwh": 0.2, "avg_w": 8.3, "hours": 24, "samples": 86000}}}))
        self.write_power([(ts(21, 0, 0, "2026-11-05"), 8), (ts(23, 0, 0, "2026-11-05"), 8), (ts(1), 8), (ts(2), 8)])
        doc = ec.run(self.args(legacy_power=str(legacy)), now=self.now)
        days = load(self.out / "history.json")["days"]
        self.assertEqual(days["2026-11-05"]["kwh"], 0.2, "2 h vues ne remplacent pas 24 h reprises d'Astroport.ONE")
        self.assertEqual(doc["consumption"]["since"], "2026-11-05")
        self.assertEqual(doc["consumption"]["days_recorded"], 2)

    def test_several_solar_controllers_add_up(self):
        with open(self.victron, "w") as f:
            f.write("solar_power,yield_today,battery_voltage,charge_state,timestamp,name,mac,rssi\n")
            f.write(f"100,500,12.8,bulk,{ts(11)},Toit,aa:aa,-60\n")
            f.write(f"50,300,12.9,absorption,{ts(11, 30)},Garage,bb:bb,-70\n")
            f.write(f"120,650,13.1,bulk,{ts(11, 50)},Toit,aa:aa,-60\n")
        doc = ec.run(self.args(), now=self.now)
        p = doc["production"]
        self.assertEqual(p["today_wh"], 950, "650 Wh (Toit, plus grande valeur du jour) + 300 Wh (Garage)")
        self.assertEqual(p["power_w"], 170)
        self.assertEqual((p["battery_v"], p["charge_state"], p["devices"]), (13.1, "bulk", 2))
        self.assertIsNone(doc["consumption"], "pas de PowerJoular : consommation absente")

    @unittest.skipIf(jsonschema is None, "jsonschema absent")
    def test_the_written_file_follows_the_contract(self):
        schema = load(ROOT / "contracts/energy-v1.schema.json")
        self.write_power([(ts(9), 7), (ts(10), 9)])
        jsonschema.validate(ec.run(self.args(), now=self.now), schema)
        os.remove(self.power)
        empty = ec.run(self.args(out=str(Path(self.tmp.name) / "vide")), now=self.now)
        jsonschema.validate(empty, schema)
        self.assertEqual((empty["consumption"], empty["production"]), (None, None))


if __name__ == "__main__":
    unittest.main()
