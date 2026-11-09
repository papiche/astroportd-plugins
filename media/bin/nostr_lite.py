"""nostr_lite : le strict nécessaire pour publier un événement Nostr depuis le plugin, sans dépendance (bibliothèque standard).

- clé du compte : `~/.zen/game/nostr/<email>/.secret.nostr` (`NSEC=nsec1…;HEX=…;…`), la même que lisent les scripts d'Astroport.ONE ;
- signature Schnorr BIP-340 (secp256k1) et identifiant NIP-01 ;
- client WebSocket minimal (ws/wss) avec NIP-42 (AUTH) : publication (`publish`) et lecture (`query`).

Le plugin signe avec la clé du membre, qui reste sur la machine de la station : elle n'est jamais envoyée ni copiée.
"""
import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import time
import urllib.parse

# ───────────────────────────── secp256k1 / BIP-340 ─────────────────────────────

P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798, 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)


def _add(a, b):
    if a is None:
        return b
    if b is None:
        return a
    if a[0] == b[0] and (a[1] + b[1]) % P == 0:
        return None
    if a == b:
        lam = 3 * a[0] * a[0] * pow(2 * a[1], -1, P) % P
    else:
        lam = (b[1] - a[1]) * pow(b[0] - a[0], -1, P) % P
    x = (lam * lam - a[0] - b[0]) % P
    return (x, (lam * (a[0] - x) - a[1]) % P)


def _mul(point, k):
    out = None
    while k:
        if k & 1:
            out = _add(out, point)
        point = _add(point, point)
        k >>= 1
    return out


def _tagged(tag, *parts):
    t = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(t + t + b"".join(parts)).digest()


def _lift_x(x):
    if x >= P:
        return None
    y2 = (pow(x, 3, P) + 7) % P
    y = pow(y2, (P + 1) // 4, P)
    if pow(y, 2, P) != y2:
        return None
    return (x, y if y % 2 == 0 else P - y)


def pubkey(seckey: bytes) -> bytes:
    """Clé publique x-only (32 octets) d'une clé secrète de 32 octets."""
    d = int.from_bytes(seckey, "big")
    if not 1 <= d < N:
        raise ValueError("clé secrète hors de la courbe")
    return _mul(G, d)[0].to_bytes(32, "big")


def schnorr_sign(msg: bytes, seckey: bytes, aux: bytes = None) -> bytes:
    d0 = int.from_bytes(seckey, "big")
    if not 1 <= d0 < N or len(msg) != 32:
        raise ValueError("clé ou message invalide")
    aux = os.urandom(32) if aux is None else aux
    point = _mul(G, d0)
    d = d0 if point[1] % 2 == 0 else N - d0
    t = (d ^ int.from_bytes(_tagged("BIP0340/aux", aux), "big")).to_bytes(32, "big")
    k0 = int.from_bytes(_tagged("BIP0340/nonce", t, point[0].to_bytes(32, "big"), msg), "big") % N
    if k0 == 0:
        raise ValueError("nonce nul")
    r = _mul(G, k0)
    k = k0 if r[1] % 2 == 0 else N - k0
    e = int.from_bytes(_tagged("BIP0340/challenge", r[0].to_bytes(32, "big"), point[0].to_bytes(32, "big"), msg), "big") % N
    return r[0].to_bytes(32, "big") + ((k + e * d) % N).to_bytes(32, "big")


def schnorr_verify(msg: bytes, pub: bytes, sig: bytes) -> bool:
    if len(msg) != 32 or len(pub) != 32 or len(sig) != 64:
        return False
    point = _lift_x(int.from_bytes(pub, "big"))
    r, s = int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")
    if point is None or r >= P or s >= N:
        return False
    e = int.from_bytes(_tagged("BIP0340/challenge", sig[:32], pub, msg), "big") % N
    res = _add(_mul(G, s), _mul(point, N - e))
    return res is not None and res[1] % 2 == 0 and res[0] == r


# ───────────────────────────── bech32 (nsec) ─────────────────────────────

_B32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _polymod(values):
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if (top >> i) & 1 else 0
    return chk


def bech32_decode(text: str):
    """(préfixe, octets) d'une chaîne bech32 ; ValueError si elle est invalide."""
    text = text.strip().lower()
    pos = text.rfind("1")
    if pos < 1 or pos + 7 > len(text) or any(c not in _B32 for c in text[pos + 1:]):
        raise ValueError("bech32 invalide")
    hrp, data = text[:pos], [_B32.index(c) for c in text[pos + 1:]]
    expanded = [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]
    if _polymod(expanded + data) != 1:
        raise ValueError("somme de contrôle bech32 invalide")
    acc, bits, out = 0, 0, bytearray()
    for v in data[:-6]:
        acc = (acc << 5) | v
        bits += 5
        while bits >= 8:
            bits -= 8
            out.append((acc >> bits) & 0xFF)
    return hrp, bytes(out)


def load_secret(path):
    """Clé secrète (32 octets) d'un fichier `.secret.nostr` ; ValueError si elle est absente, illisible ou incohérente."""
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as e:
        raise ValueError(f"clé du compte illisible ({path}) : {e.strerror or e}")
    fields = {}
    for part in text.replace("\n", ";").split(";"):
        if "=" in part:
            k, _, v = part.partition("=")
            fields[k.strip()] = v.strip()
    hrp, key = bech32_decode(fields.get("NSEC", ""))
    if hrp != "nsec" or len(key) != 32:
        raise ValueError(f"{path} : NSEC invalide")
    if fields.get("HEX") and pubkey(key).hex() != fields["HEX"].lower():
        raise ValueError(f"{path} : la clé ne redonne pas la clé publique annoncée")
    return key


# ───────────────────────────── Événements ─────────────────────────────

def event_id(ev):
    data = json.dumps([0, ev["pubkey"], ev["created_at"], ev["kind"], ev["tags"], ev["content"]], separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def sign_event(seckey: bytes, kind: int, tags, content: str, created_at: int = None):
    ev = {"pubkey": pubkey(seckey).hex(), "created_at": int(time.time()) if created_at is None else created_at, "kind": kind, "tags": tags, "content": content}
    ev["id"] = event_id(ev)
    ev["sig"] = schnorr_sign(bytes.fromhex(ev["id"]), seckey).hex()
    return ev


def verify_event(ev) -> bool:
    try:
        return event_id(ev) == ev["id"] and schnorr_verify(bytes.fromhex(ev["id"]), bytes.fromhex(ev["pubkey"]), bytes.fromhex(ev["sig"]))
    except (KeyError, ValueError):
        return False


# ───────────────────────────── WebSocket minimal ─────────────────────────────

class RelayError(Exception):
    pass


class Ws:
    def __init__(self, url, timeout=15):
        u = urllib.parse.urlsplit(url)
        if u.scheme not in ("ws", "wss") or not u.hostname:
            raise RelayError(f"adresse de relai invalide : {url}")
        self.url = url
        port = u.port or (443 if u.scheme == "wss" else 80)
        try:
            raw = socket.create_connection((u.hostname, port), timeout=timeout)
            self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=u.hostname) if u.scheme == "wss" else raw
        except OSError as e:
            raise RelayError(f"relai injoignable ({u.hostname}:{port}) : {e}")
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        host = u.hostname + (f":{u.port}" if u.port else "")
        self.sock.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RelayError("relai : connexion fermée pendant la poignée de main")
            head += chunk
            if len(head) > 16384:
                raise RelayError("relai : réponse de poignée de main trop longue")
        head, _, self.buf = head.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise RelayError("relai : " + head.split(b"\r\n")[0].decode("latin1"))
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        if accept.lower().encode() not in head.lower():
            raise RelayError("relai : poignée de main WebSocket invalide")

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RelayError("relai : connexion fermée")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, obj, opcode=1):
        payload = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x80 | opcode]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 else bytes([0x80 | 127]) + struct.pack(">Q", n))
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv(self):
        """Prochain message texte (JSON décodé) ; None si le relai ferme proprement."""
        text = b""
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            if n > 16 << 20:
                raise RelayError("relai : message trop gros")
            mask = self._read(4) if b1 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            op = b0 & 0x0F
            if op == 0x9:
                self.send(data, opcode=0xA)
            elif op == 0x8:
                return None
            elif op in (0x0, 0x1):
                text += data
                if b0 & 0x80:
                    return json.loads(text.decode("utf-8"))

    def close(self):
        try:
            self.send(b"", opcode=0x8)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def _auth_event(seckey, relay_url, challenge):
    return sign_event(seckey, 22242, [["relay", relay_url], ["challenge", challenge]], "")


def publish(url, event, seckey, timeout=20):
    """Publie `event` ; répond à NIP-42 (AUTH) si le relai le demande. Rend (accepté, message du relai)."""
    ws = Ws(url, timeout=min(timeout, 15))
    try:
        authed, deadline = False, time.time() + timeout
        ws.send(["EVENT", event])
        while time.time() < deadline:
            try:
                msg = ws.recv()
            except socket.timeout:
                break
            if msg is None:
                break
            if msg[0] == "AUTH" and len(msg) > 1:
                ws.send(["AUTH", _auth_event(seckey, url, msg[1])])
                authed = True
            elif msg[0] == "OK" and len(msg) > 2 and msg[1] == event["id"]:
                if not msg[2] and str(msg[3] if len(msg) > 3 else "").startswith("auth-required") and authed:
                    ws.send(["EVENT", event])  # le défi a été relevé : on renvoie
                    authed = False
                    continue
                return bool(msg[2]), str(msg[3]) if len(msg) > 3 else ""
        raise RelayError("relai : pas de réponse à la publication")
    finally:
        ws.close()


def query(url, filt, seckey=None, timeout=20, limit=200):
    """Événements correspondant à `filt` (REQ jusqu'à EOSE) ; NIP-42 si `seckey` est fourni et que le relai le demande."""
    ws = Ws(url, timeout=min(timeout, 15))
    out, sub = [], "q" + os.urandom(4).hex()
    try:
        ws.send(["REQ", sub, filt])
        deadline = time.time() + timeout
        while time.time() < deadline and len(out) < limit:
            try:
                msg = ws.recv()
            except socket.timeout:
                break
            if msg is None:
                break
            if msg[0] == "AUTH" and seckey is not None:
                ws.send(["AUTH", _auth_event(seckey, url, msg[1])])
            elif msg[0] == "CLOSED" and seckey is not None and "auth-required" in str(msg[2:]):
                ws.send(["REQ", sub, filt])
            elif msg[0] == "EVENT" and msg[1] == sub and verify_event(msg[2]):
                out.append(msg[2])
            elif msg[0] == "EOSE" and msg[1] == sub:
                break
        return out
    finally:
        ws.close()
