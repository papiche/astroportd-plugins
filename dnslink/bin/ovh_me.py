#!/usr/bin/env python3
"""ovh_me : publication DNSLink OVH selon les paramètres des coopératives de la station (script de référence).

Aucune dépendance (bibliothèque standard seulement) et aucun code d'Astroport.ONE. Les paramètres d'API
(OVH_APP_KEY, OVH_APP_SECRET, OVH_CONSUMER_KEY, OVH_ZONE) sont ceux inscrits dans NOSTR par la coopérative
(kind 30800, `d=cooperative-config`) : le plugin les demande au relai de chaque plan d'astroportd (`planes.json`),
vérifie la signature de l'événement, puis déchiffre les secrets avec le nom de l'UPlanet.

    ovh_me.py coops                              coopératives de la station et état de leurs paramètres OVH
    ovh_me.py [--coop NOM] list [zone]
    ovh_me.py [--coop NOM] get|delete <sous-domaine> [zone]
    ovh_me.py [--coop NOM] create|update|upsert <sous-domaine> <cible> [zone]

Choix de la coopérative : `--coop NOM`, sinon `OVH_COOP`, sinon l'unique plan, sinon un menu (terminal) ou une erreur
qui liste les choix. Variables d'environnement OVH_APP_KEY/OVH_APP_SECRET/OVH_CONSUMER_KEY/OVH_ZONE : elles priment.
Cibles : `k51…` → /ipns/, `Qm…`/`bafy…` → /ipfs/, `/ipns/…`, `/ipfs/…` et `dnslink=…` tels quels (TTL 0).
Les enregistrements sont les mêmes que ceux d'`ovh.me.sh` et de la tâche `dnslink` d'astroportd.
"""
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nostr_lite  # noqa: E402  (lien vers media/bin : une seule copie du client Nostr)

ENDPOINT = "https://eu.api.ovh.com/1.0"
ORIGIN_UPLANETNAME = "0" * 64
DEFAULT_ZONE = "astroport.one"
CONFIG_KIND, CONFIG_D = 30800, "cooperative-config"
OVH_KEYS = ("OVH_APP_KEY", "OVH_APP_SECRET", "OVH_CONSUMER_KEY", "OVH_ZONE")


class Failure(Exception):
    """Erreur dite à l'utilisateur (jamais avalée)."""


# ───────────────────────────── AES-256-CBC (déchiffrement) ─────────────────────────────
# Les secrets de la coopérative sont `iv_hex:base64` chiffrés en AES-256-CBC, clé = sha256(UPLANETNAME), bourrage PKCS7.
# openssl exigerait la clé en argument (visible dans `ps`) : on déchiffre ici, en mémoire.

def _make_sbox():
    sbox, p, q = [0] * 256, 1, 1
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        rot = lambda x, s: ((x << s) | (x >> (8 - s))) & 0xFF
        sbox[p] = q ^ rot(q, 1) ^ rot(q, 2) ^ rot(q, 3) ^ rot(q, 4) ^ 0x63
        if p == 1:
            break
    sbox[0] = 0x63
    return sbox


_SBOX = _make_sbox()
_INV = [0] * 256
for _i, _v in enumerate(_SBOX):
    _INV[_v] = _i


def _gmul(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = ((a << 1) & 0xFF) ^ (0x1B if a & 0x80 else 0)
        b >>= 1
    return r


def _expand_key(key):
    w = [list(key[4 * i:4 * i + 4]) for i in range(8)]
    rcon = 1
    for i in range(8, 60):
        t = list(w[i - 1])
        if i % 8 == 0:
            t = [_SBOX[b] for b in t[1:] + t[:1]]
            t[0] ^= rcon
            rcon = _gmul(rcon, 2)
        elif i % 8 == 4:
            t = [_SBOX[b] for b in t]
        w.append([a ^ b for a, b in zip(w[i - 8], t)])
    return [bytes(sum(w[4 * r:4 * r + 4], [])) for r in range(15)]


def _decrypt_block(rk, block):
    s = [a ^ b for a, b in zip(block, rk[14])]
    for rnd in range(13, -1, -1):
        t = [0] * 16                       # InvShiftRows : la ligne r se décale de r colonnes vers la droite
        for r in range(4):
            for c in range(4):
                t[r + 4 * ((c + r) % 4)] = s[r + 4 * c]
        s = [_INV[b] for b in t]
        s = [a ^ b for a, b in zip(s, rk[rnd])]
        if rnd:                            # InvMixColumns (pas au dernier tour)
            out = []
            for c in range(4):
                a0, a1, a2, a3 = s[4 * c:4 * c + 4]
                out += [_gmul(a0, 14) ^ _gmul(a1, 11) ^ _gmul(a2, 13) ^ _gmul(a3, 9),
                        _gmul(a0, 9) ^ _gmul(a1, 14) ^ _gmul(a2, 11) ^ _gmul(a3, 13),
                        _gmul(a0, 13) ^ _gmul(a1, 9) ^ _gmul(a2, 14) ^ _gmul(a3, 11),
                        _gmul(a0, 11) ^ _gmul(a1, 13) ^ _gmul(a2, 9) ^ _gmul(a3, 14)]
            s = out
    return bytes(s)


_ENC = re.compile(r"^[0-9a-f]{32}:[A-Za-z0-9+/=]+$")


def is_encrypted(value):
    return bool(_ENC.match(re.sub(r"[\r\n]", "", value or "")))


def coop_decrypt(uplanet_name, value):
    """Déchiffre `iv_hex:base64` (format de cooperative_config.sh et d'astroportd)."""
    import base64
    v = re.sub(r"[\r\n]", "", value)
    if not is_encrypted(v):
        raise Failure("valeur chiffrée mal formée")
    iv_hex, data = v.split(":", 1)
    ct, prev = base64.b64decode(data), bytes.fromhex(iv_hex)
    if not ct or len(ct) % 16:
        raise Failure("valeur chiffrée tronquée")
    rk, out = _expand_key(hashlib.sha256(uplanet_name.encode()).digest()), b""
    for i in range(0, len(ct), 16):
        blk = ct[i:i + 16]
        out += bytes(a ^ b for a, b in zip(_decrypt_block(rk, blk), prev))
        prev = blk
    pad = out[-1]
    if not 1 <= pad <= 16 or out[-pad:] != bytes([pad]) * pad:
        raise Failure("déchiffrement impossible (nom d'UPlanet différent ?)")
    return out[:-pad].decode("utf-8")


# ───────────────────────────── coopératives = plans d'astroportd ─────────────────────────────

def station_home():
    return Path(os.environ.get("ASTROPORTD_HOME", "~/.zen/astroportd")).expanduser()


def load_planes(home=None):
    """Plans de la station : [{name, uplanet, relay}]. Sans planes.json : le seul plan ORIGIN."""
    home = Path(home) if home else station_home()
    f = Path(os.environ.get("ASTRO_PLANES_JSON", home / "planes.json"))
    if not f.exists():
        return [{"name": "origin", "uplanet": ORIGIN_UPLANETNAME, "relay": "ws://127.0.0.1:8090"}]
    try:
        specs = json.loads(f.read_text(encoding="utf-8"))["planes"]
    except (OSError, ValueError, KeyError) as e:
        raise Failure(f"{f} illisible : {e}")
    planes = []
    for rank, s in enumerate(specs):
        key = s.get("swarm_key")
        if key:
            lines = [l.strip() for l in Path(key).expanduser().read_text(encoding="utf-8").splitlines() if l.strip()]
            if not lines:
                raise Failure(f"plan {s['name']} : swarm.key vide")
            uplanet = lines[-1]            # dernière ligne non vide, comme astroportd (station_uplanet_name)
        else:
            uplanet = ORIGIN_UPLANETNAME
        planes.append({"name": s["name"], "uplanet": uplanet, "relay": "ws://" + s.get("http_listen", f"127.0.0.1:{8090 + rank}")})
    return planes


def coop_pubkey(uplanet_name):
    """Clé publique (hex x-only) de la coopérative : scrypt(`<UPLANETNAME>.G1`, N=4096, r=16, p=1) → sha256 → secp256k1."""
    label = f"{uplanet_name}.G1".encode()
    seed = hashlib.scrypt(label, salt=label, n=4096, r=16, p=1, dklen=32, maxmem=64 * 1024 * 1024)
    return nostr_lite.pubkey(hashlib.sha256(seed).digest()).hex()


def fetch_config(plane, query=None):
    """Dernière configuration coopérative publiée par la clé de la coopérative : {clé: valeur publiée} (secrets chiffrés)."""
    query = query or nostr_lite.query
    pub = coop_pubkey(plane["uplanet"])
    try:
        events = query(plane["relay"], {"kinds": [CONFIG_KIND], "authors": [pub], "#d": [CONFIG_D], "limit": 5}, timeout=10)
    except Exception as e:  # noqa: BLE001 — l'échec est dit, pas avalé
        raise Failure(f"plan {plane['name']} : relai {plane['relay']} injoignable ({e})")
    events = [e for e in events if e.get("pubkey") == pub and e.get("kind") == CONFIG_KIND]
    if not events:
        return {}
    try:
        data = json.loads(max(events, key=lambda e: e["created_at"])["content"])
    except ValueError:
        raise Failure(f"plan {plane['name']} : configuration coopérative illisible")
    return {k: v for k, v in data.items() if isinstance(v, str)}


def ovh_settings(plane, query=None):
    """Paramètres OVH déchiffrés de la coopérative ; variables d'environnement prioritaires. `None` si incomplets."""
    stored = fetch_config(plane, query)
    got = {}
    for k in OVH_KEYS:
        v = os.environ.get(k) or stored.get(k, "")
        if v and is_encrypted(v):
            v = coop_decrypt(plane["uplanet"], v)
        got[k] = v.strip()
    if not all(got[k] for k in OVH_KEYS[:3]):
        return None
    got["OVH_ZONE"] = (got["OVH_ZONE"] or DEFAULT_ZONE).lower()
    return got


def choose_plane(planes, wanted=None, query=None, stdin=sys.stdin, stderr=sys.stderr):
    """Choix de la coopérative : nom demandé, plan unique, menu (terminal) ou erreur explicite."""
    wanted = wanted or os.environ.get("OVH_COOP")
    names = [p["name"] for p in planes]
    if wanted:
        for p in planes:
            if p["name"] == wanted:
                return p
        raise Failure(f"coopérative inconnue : {wanted} (connues : {', '.join(names)})")
    if len(planes) == 1:
        return planes[0]
    if stdin.isatty() and stderr.isatty():
        print("Coopératives de cette station :", file=stderr)
        for i, p in enumerate(planes, 1):
            try:
                s = ovh_settings(p, query)
                state = f"zone {s['OVH_ZONE']}" if s else "paramètres OVH absents"
            except Failure as e:
                state = f"indisponible ({e})"
            print(f"  {i}) {p['name']:<14} {state}", file=stderr)
        try:
            n = int(input(f"Coopérative [1-{len(planes)}] : "))
            return planes[n - 1] if n >= 1 else (_ for _ in ()).throw(IndexError)
        except (ValueError, IndexError, EOFError):
            raise Failure("choix invalide")
    raise Failure(f"plusieurs coopératives ({', '.join(names)}) : précisez --coop NOM ou OVH_COOP=NOM")


# ───────────────────────────── API OVH v1 ─────────────────────────────

def norm_sub(sub):
    return sub if sub.startswith("_dnslink") else f"_dnslink.{sub}"


def norm_target(t):
    if t.startswith("dnslink="):
        return t
    if t.startswith(("/ipns/", "/ipfs/")):
        return "dnslink=" + t
    return "dnslink=/ipns/" + t if t.startswith("k51") else "dnslink=/ipfs/" + t


def sign(secret, consumer, method, url, body, ts):
    return "$1$" + hashlib.sha1(f"{secret}+{consumer}+{method}+{url}+{body}+{ts}".encode()).hexdigest()


class Ovh:
    def __init__(self, settings, transport=None):
        self.s, self.transport = settings, transport or self._http

    @staticmethod
    def _http(method, url, headers, body):
        req = urllib.request.Request(url, data=body.encode() if body else None, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode()
        except urllib.error.HTTPError as e:
            return e.read().decode()      # le corps d'erreur OVH est analysé par l'appelant
        except OSError as e:
            raise Failure(f"API OVH injoignable : {e}")

    def call(self, method, path, payload=None):
        url, body = ENDPOINT + path, json.dumps(payload, separators=(",", ":")) if payload is not None else ""
        ts = self.transport("GET", ENDPOINT + "/auth/time", {}, "").strip()
        headers = {"Content-Type": "application/json", "X-Ovh-Application": self.s["OVH_APP_KEY"], "X-Ovh-Consumer": self.s["OVH_CONSUMER_KEY"],
                   "X-Ovh-Timestamp": ts, "X-Ovh-Signature": sign(self.s["OVH_APP_SECRET"], self.s["OVH_CONSUMER_KEY"], method, url, body, ts)}
        raw = self.transport(method, url, headers, body)
        try:
            out = json.loads(raw) if raw.strip() else None
        except ValueError:
            raise Failure(f"réponse OVH illisible : {raw[:200]}")
        if isinstance(out, dict) and "message" in out:
            raise Failure(f"API OVH ({method} {path}) : {out['message']}")
        return out

    def record_id(self, sub, zone):
        ids = self.call("GET", f"/domain/zone/{zone}/record?fieldType=TXT&subDomain={urllib.parse.quote(sub)}")
        return ids[0] if ids else None

    def refresh(self, zone):
        self.call("POST", f"/domain/zone/{zone}/refresh")

    def upsert(self, sub, target, zone):
        rid = self.record_id(sub, zone)
        if rid is None:
            self.call("POST", f"/domain/zone/{zone}/record", {"fieldType": "TXT", "subDomain": sub, "target": target, "ttl": 0})
            what = "créé"
        else:
            self.call("PUT", f"/domain/zone/{zone}/record/{rid}", {"target": target})
            what = "mis à jour"
        self.refresh(zone)
        return what


# ───────────────────────────── commandes ─────────────────────────────

def journal(plane_name, line):
    """Trace des publications (indicatif) dans <station>/plugins/dnslink/journal.jsonl, sans secret."""
    try:
        d = station_home() / "plugins" / "dnslink"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "journal.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": int(time.time()), "coop": plane_name, **line}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def cmd_coops(planes, query=None, out=print):
    for p in planes:
        try:
            s = ovh_settings(p, query)
            out(f"{p['name']}\t{'zone ' + s['OVH_ZONE'] if s else 'paramètres OVH absents (Options › DNSLink OVH)'}")
        except Failure as e:
            out(f"{p['name']}\tindisponible : {e}")


def run(argv, query=None, transport=None, out=print):
    coop, args = None, []
    it = iter(argv)
    for a in it:
        if a == "--coop":
            coop = next(it, None) or sys.exit("--coop demande un nom")
        elif a.startswith("--coop="):
            coop = a.split("=", 1)[1]
        else:
            args.append(a)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = args[0], args[1:]
    planes = load_planes()
    if cmd == "coops":
        cmd_coops(planes, query, out)
        return 0
    arity = {"list": (0, 1), "get": (1, 2), "delete": (1, 2), "create": (2, 3), "update": (2, 3), "upsert": (2, 3)}
    if cmd not in arity or not arity[cmd][0] <= len(rest) <= arity[cmd][1]:
        raise Failure(f"usage : ovh_me.py [--coop NOM] list|get|delete|create|update|upsert … (voir --help)")
    plane = choose_plane(planes, coop, query)
    settings = ovh_settings(plane, query)
    if not settings:
        raise Failure(f"coopérative « {plane['name']} » : OVH_APP_KEY / OVH_APP_SECRET / OVH_CONSUMER_KEY absents de sa configuration (Options › DNSLink OVH)")
    ovh = Ovh(settings, transport)
    zone_at = {"list": 0, "get": 1, "delete": 1}.get(cmd, 2)
    zone = (rest[zone_at] if len(rest) > zone_at else "") or settings["OVH_ZONE"]
    if cmd == "list":
        out(f"## DNSLink de {zone} (coopérative {plane['name']})")
        for rid in ovh.call("GET", f"/domain/zone/{zone}/record?fieldType=TXT") or []:
            r = ovh.call("GET", f"/domain/zone/{zone}/record/{rid}")
            if r and r["subDomain"].startswith("_dnslink"):
                out(f"  {r['subDomain'] + '.' + zone:<36} {r['target']}  (ttl={r['ttl']})")
        return 0
    sub = norm_sub(rest[0])
    if cmd == "get":
        rid = ovh.record_id(sub, zone)
        if rid is None:
            raise Failure(f"introuvable : {sub}.{zone}")
        out(json.dumps(ovh.call("GET", f"/domain/zone/{zone}/record/{rid}"), ensure_ascii=False))
    elif cmd == "delete":
        rid = ovh.record_id(sub, zone)
        if rid is None:
            out(f"rien à supprimer : {sub}.{zone}")
        else:
            ovh.call("DELETE", f"/domain/zone/{zone}/record/{rid}")
            ovh.refresh(zone)
            out(f"supprimé : {sub}.{zone}")
            journal(plane["name"], {"op": "delete", "name": f"{sub}.{zone}"})
    else:
        target = norm_target(rest[1])
        rid = ovh.record_id(sub, zone)
        if cmd == "create" and rid is not None:
            raise Failure(f"{sub}.{zone} existe déjà (utilisez update ou upsert)")
        if cmd == "update" and rid is None:
            raise Failure(f"{sub}.{zone} introuvable (utilisez create ou upsert)")
        what = ovh.upsert(sub, target, zone)
        out(f"OK : {sub}.{zone} {what} → {target}")
        journal(plane["name"], {"op": cmd, "name": f"{sub}.{zone}", "target": target})
    return 0


def main():
    try:
        sys.exit(run(sys.argv[1:]))
    except Failure as e:
        print(f"ERREUR : {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
