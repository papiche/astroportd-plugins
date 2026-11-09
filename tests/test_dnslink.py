"""Tests du plugin dnslink : déchiffrement des secrets (vecteurs d'astroportd), lecture NOSTR, choix de coopérative, API OVH simulée.

    python3 -m unittest discover -s tests
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dnslink/bin"))
spec = importlib.util.spec_from_file_location("ovh_me", ROOT / "dnslink/bin/ovh_me.py")
o = importlib.util.module_from_spec(spec)
spec.loader.exec_module(o)

UP = "UPlanetTestName"
# Vecteurs de crates/astro-crypto/src/coop.rs (produits par `openssl enc`, identiques à cooperative_config.sh)
SECRET = "000102030405060708090a0b0c0d0e0f:a6Z5mnHrgxk3mKSxMbaOS1AYdF9UZuUQGjeKKZUKMAI="   # mon-secret-ocapikey


class Crypto(unittest.TestCase):
    def test_vecteurs_astroportd(self):
        self.assertEqual(o.coop_decrypt(UP, SECRET), "mon-secret-ocapikey")
        self.assertEqual(o.coop_decrypt(UP, "000102030405060708090a0b0c0d0e0f:19zUWXWziOfptoJ016JSVw=="), "été € ça")
        self.assertEqual(o.coop_decrypt(UP, "000102030405060708090a0b0c0d0e0f:/mYqRiNMq7L2+6Jv28yPzA=="), "")

    def test_mauvaise_uplanet_ou_valeur_tronquee(self):
        with self.assertRaises(o.Failure):
            o.coop_decrypt("autre", SECRET)
        with self.assertRaises(o.Failure):
            o.coop_decrypt(UP, "000102030405060708090a0b0c0d0e0f:AAAA")

    def test_is_encrypted(self):
        self.assertTrue(o.is_encrypted(SECRET))
        self.assertFalse(o.is_encrypted("astroport.one"))


class Nommage(unittest.TestCase):
    def test_sous_domaines_et_cibles(self):
        self.assertEqual(o.norm_sub("coracle"), "_dnslink.coracle")
        self.assertEqual(o.norm_sub("_dnslink.origin"), "_dnslink.origin")
        self.assertEqual(o.norm_target("k51qzi5"), "dnslink=/ipns/k51qzi5")
        self.assertEqual(o.norm_target("QmAbc"), "dnslink=/ipfs/QmAbc")
        self.assertEqual(o.norm_target("/ipns/k51x"), "dnslink=/ipns/k51x")
        self.assertEqual(o.norm_target("dnslink=/ipfs/Qm"), "dnslink=/ipfs/Qm")

    def test_signature(self):
        s = o.sign("sec", "con", "GET", "https://x/y", "", "1700000000")
        self.assertTrue(s.startswith("$1$") and len(s) == 43)


def fake_config(uplanet, values):
    """Relai simulé : renvoie la configuration signée (en apparence) par la clé de la coopérative."""
    pub = o.coop_pubkey(uplanet)
    ev = {"pubkey": pub, "kind": 30800, "created_at": 100, "content": json.dumps(values)}
    return lambda url, filt, timeout=0: [ev] if filt["authors"] == [pub] else []


def values(up=UP):
    return {"OVH_APP_KEY": SECRET, "OVH_APP_SECRET": SECRET, "OVH_CONSUMER_KEY": SECRET, "OVH_ZONE": "Coop.Example"}


class Coopératives(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {k: "" for k in (*o.OVH_KEYS, "OVH_COOP")})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.plane = {"name": "zen1", "uplanet": UP, "relay": "ws://x"}

    def test_parametres_lus_et_dechiffres(self):
        s = o.ovh_settings(self.plane, fake_config(UP, values()))
        self.assertEqual(s["OVH_APP_KEY"], "mon-secret-ocapikey")
        self.assertEqual(s["OVH_ZONE"], "coop.example")

    def test_evenement_d_un_autre_auteur_ignore(self):
        q = lambda url, filt, timeout=0: [{"pubkey": "f" * 64, "kind": 30800, "created_at": 1, "content": json.dumps(values())}]  # noqa: E731
        self.assertIsNone(o.ovh_settings(self.plane, q))

    def test_parametres_absents(self):
        self.assertIsNone(o.ovh_settings(self.plane, fake_config(UP, {})))

    def test_planes_json_et_uplanet(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "swarm.key").write_text("/key/swarm/psk/1.0.0/\n/base16/\nabcdef0123\n\n")
            (Path(d) / "planes.json").write_text(json.dumps({"planes": [
                {"name": "origin", "http_listen": "127.0.0.1:18080"},
                {"name": "zen1", "swarm_key": str(Path(d) / "swarm.key"), "http_listen": "127.0.0.1:18081"}]}))
            planes = o.load_planes(d)
        self.assertEqual([p["name"] for p in planes], ["origin", "zen1"])
        self.assertEqual(planes[0]["uplanet"], "0" * 64)
        self.assertEqual(planes[1]["uplanet"], "abcdef0123")
        self.assertEqual(planes[1]["relay"], "ws://127.0.0.1:18081")

    def test_choix(self):
        planes = [{"name": "a", "uplanet": UP, "relay": "ws://x"}, {"name": "b", "uplanet": UP, "relay": "ws://y"}]
        self.assertEqual(o.choose_plane(planes[:1])["name"], "a")
        self.assertEqual(o.choose_plane(planes, "b")["name"], "b")
        with self.assertRaisesRegex(o.Failure, "inconnue"):
            o.choose_plane(planes, "z")
        with self.assertRaisesRegex(o.Failure, "plusieurs coopératives"):
            o.choose_plane(planes, stdin=io.StringIO())      # pas de terminal : jamais de menu bloquant
        with mock.patch.dict(os.environ, {"OVH_COOP": "a"}):
            self.assertEqual(o.choose_plane(planes)["name"], "a")


class Ovh(unittest.TestCase):
    def test_upsert_cree_puis_met_a_jour(self):
        calls, state = [], {"ids": []}

        def transport(method, url, headers, body):
            if url.endswith("/auth/time"):
                return "1700000000"
            calls.append((method, url.replace(o.ENDPOINT, ""), body))
            if method == "GET":
                return json.dumps(state["ids"])
            if method == "POST" and url.endswith("/record"):
                state["ids"] = [42]
                return json.dumps({"id": 42})
            return "null"

        s = {"OVH_APP_KEY": "k", "OVH_APP_SECRET": "s", "OVH_CONSUMER_KEY": "c", "OVH_ZONE": "z.tld"}
        ovh = o.Ovh(s, transport)
        self.assertEqual(ovh.upsert("_dnslink.coracle", "dnslink=/ipfs/Qm", "z.tld"), "créé")
        self.assertEqual(ovh.upsert("_dnslink.coracle", "dnslink=/ipfs/Qm2", "z.tld"), "mis à jour")
        self.assertIn(("POST", "/domain/zone/z.tld/record", '{"fieldType":"TXT","subDomain":"_dnslink.coracle","target":"dnslink=/ipfs/Qm","ttl":0}'), calls)
        self.assertIn(("PUT", "/domain/zone/z.tld/record/42", '{"target":"dnslink=/ipfs/Qm2"}'), calls)

    def test_erreur_ovh_remontee(self):
        ovh = o.Ovh({"OVH_APP_KEY": "k", "OVH_APP_SECRET": "s", "OVH_CONSUMER_KEY": "c"},
                    lambda m, u, h, b: "1" if u.endswith("/auth/time") else '{"message":"This credential is not valid"}')
        with self.assertRaisesRegex(o.Failure, "not valid"):
            ovh.record_id("_dnslink.x", "z.tld")

    def test_run_de_bout_en_bout(self):
        planes = [{"name": "zen1", "uplanet": UP, "relay": "ws://x"}]
        sent, lines = [], []

        def transport(method, url, headers, body):
            sent.append(method)
            return "1" if url.endswith("/auth/time") else ("[]" if method == "GET" else '{"id":1}')

        with mock.patch.object(o, "load_planes", return_value=planes), mock.patch.object(o, "journal"), \
                mock.patch.dict(os.environ, {k: "" for k in (*o.OVH_KEYS, "OVH_COOP")}):
            self.assertEqual(o.run(["upsert", "coracle", "QmXyz"], fake_config(UP, values()), transport, lines.append), 0)
        self.assertEqual(lines, ["OK : _dnslink.coracle.coop.example créé → dnslink=/ipfs/QmXyz"])
        self.assertIn("POST", sent)


if __name__ == "__main__":
    unittest.main()
