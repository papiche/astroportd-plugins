"""Tests du plugin media : sondage, plan d'encodage, catalogue TMDB (réponses simulées), fiche conforme au contrat media/v1,
dépôt WebDAV contre un faux serveur (succès, refus de la station), et un passage complet avec ffmpeg si présent.

    python3 -m unittest discover -s tests        (la validation de schéma demande le paquet jsonschema)
"""
import base64
import hashlib
import importlib.util
import json
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("media_add", ROOT / "media/bin/media_add.py")
ma = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ma)
nl = ma.nl

try:
    import jsonschema
except ImportError:  # la CI l'installe ; en local, le reste des tests tourne quand même
    jsonschema = None

SCHEMA = json.loads((ROOT / "contracts/media-v1.schema.json").read_text(encoding="utf-8"))


def valid(doc):
    if jsonschema:
        jsonschema.validate(doc, SCHEMA)


def probe(video="mpeg4", audio=(("mp3", "eng"), ("mp3", "fra")), fmt="matroska,webm", duration="2.0"):
    streams = [{"codec_type": "video", "codec_name": video, "width": 160, "height": 120, "avg_frame_rate": "10/1", "disposition": {}}]
    streams += [{"codec_type": "audio", "codec_name": c, "channels": 2, "tags": {"language": lang}, "disposition": {}} for c, lang in audio]
    return {"streams": streams, "format": {"format_name": fmt, "duration": duration}}


class Probing(unittest.TestCase):
    def test_languages_are_two_letter_codes_or_nothing(self):
        self.assertEqual([ma.lang2(x) for x in ("fra", "FRE", "eng", "fr", "und", "", None, "français")], ["fr", "fr", "en", "fr", None, None, None, None])

    def test_an_mp4_h264_aac_file_is_never_reencoded(self):
        reasons, _ = ma.plan_transcode(probe("h264", (("aac", "fra"),), "mov,mp4,m4a,3gp,3g2,mj2"), "mp4")
        self.assertEqual(reasons, [])

    def test_the_reasons_and_the_french_track_are_found(self):
        reasons, french = ma.plan_transcode(probe(), "mkv")
        self.assertEqual(french, 1)
        self.assertTrue(any("mpeg4" in r for r in reasons) and "audio non AAC" in reasons and any("conteneur" in r for r in reasons))
        args = ma.ffmpeg_args("a.mkv", "b.mp4", french, 2)
        self.assertEqual([args[i + 1] for i, a in enumerate(args) if a == "-map"], ["0:v:0", "0:a:1", "0:a:0"], "française d'abord, chaque piste une fois")
        self.assertEqual(args[args.index("-c:v") + 1], "libx264")
        self.assertNotIn("-shell", args)

    def test_technical_follows_the_contract(self):
        t = ma.technical_of(probe("h264", (("aac", "fra"),), "mov,mp4,m4a,3gp,3g2,mj2"), 123, "a" * 64, False)
        self.assertEqual((t["mime"], t["container"], t["video"]["fps"], t["audio"][0]["lang"], t["size"]), ("video/mp4", "mov", 10.0, "fr", 123))
        self.assertEqual(ma.technical_of({"streams": [{"codec_type": "audio", "codec_name": "mp3"}], "format": {"format_name": "mp3", "duration": "212"}}, 5, None, False)["mime"], "audio/mpeg")


class Catalogue(unittest.TestCase):
    MOVIE = {
        "id": 129, "title": "Le Voyage de Chihiro", "original_title": "千と千尋の神隠し", "tagline": "", "overview": "Chihiro…", "release_date": "2001-07-20", "runtime": 125,
        "genres": [{"name": "Animation"}], "production_countries": [{"iso_3166_1": "JP"}], "original_language": "ja", "spoken_languages": [{"iso_639_1": "ja"}],
        "vote_average": 8.5, "vote_count": 17000, "belongs_to_collection": None, "production_companies": [{"name": "Studio Ghibli"}], "imdb_id": "tt0245429",
        "keywords": {"keywords": [{"name": "esprit"}]},
        "release_dates": {"results": [{"iso_3166_1": "FR", "release_dates": [{"certification": ""}, {"certification": "U"}]}]},
        "credits": {"cast": [{"id": 19587, "name": "Rumi Hiiragi", "character": "Chihiro", "order": 0}], "crew": [{"id": 608, "name": "Hayao Miyazaki", "job": "Director", "department": "Directing"}, {"id": 9, "name": "X", "job": "Gaffer"}]},
        "poster_path": "/p.jpg", "backdrop_path": None,
    }

    def get(self, path, params):
        if path == "/search/movie":
            return {"results": [{"id": 129}]}
        if path == "/movie/129":
            return self.MOVIE
        if path == "/search/tv":
            return {"results": [{"id": 1234}]}
        if path == "/tv/1234":
            return {"id": 1234, "name": "Ma Série", "original_name": "My Show", "first_air_date": "2019-01-01", "genres": [{"name": "Drame"}], "networks": [{"name": "Arte"}],
                    "seasons": [{"season_number": 1, "episode_count": 8}], "external_ids": {"imdb_id": None}, "original_language": "fr"}
        if path == "/tv/1234/season/1/episode/3":
            return {"name": "Le Chant du loup", "air_date": "2019-03-14", "overview": "…", "runtime": 52, "still_path": "/s.jpg", "credits": {"crew": [{"id": 5, "name": "Jane Doe", "job": "Director"}]}}
        raise AssertionError(path)

    def test_a_movie_is_filled_from_the_catalogue(self):
        fields, poster, back = ma.tmdb_movie(self.get, "Le Voyage de Chihiro", 2001)
        self.assertEqual((fields["year"], fields["certification"], fields["ids"]["imdb"], poster, back), (2001, {"country": "FR", "value": "U"}, "tt0245429", "/p.jpg", None))
        self.assertEqual([c["name"] for c in fields["people"]["crew"]], ["Hayao Miyazaki"], "seules les fonctions clés sont gardées")
        self.assertNotIn("tagline", fields)

    def test_an_unknown_title_is_refused_with_the_way_out(self):
        with self.assertRaises(ma.Refused) as e:
            ma.tmdb_movie(lambda p, q: {"results": []}, "Inconnu", None)
        self.assertIn("--tmdb-id", str(e.exception))

    def test_an_episode_carries_its_series(self):
        fields, block, still = ma.tmdb_episode(self.get, "Ma Série", 1, 3)
        self.assertEqual((fields["title"], block["name"], block["episode_count"], still), ("Le Chant du loup", "Ma Série", 8, "/s.jpg"))

    def test_web_defaults_read_ytdlp_infos(self):
        d = ma.web_defaults({"id": "dQw4w9WgXcQ", "title": "Titre", "upload_date": "20240131", "uploader": "Chaîne", "license": "Creative Commons Attribution license (reuse allowed)", "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
        self.assertEqual((d["year"], d["youtube"], d["licence"], d["uploader"]), (2024, "dQw4w9WgXcQ", "CC-BY-4.0", "Chaîne"))
        self.assertIsNone(ma.web_defaults({"id": "pas-un-id-youtube"})["youtube"])


class Fiche(unittest.TestCase):
    TECH = {"duration_s": 2.0, "size": 10, "mime": "video/mp4", "sha256": "a" * 64, "container": "mov", "transcoded": False, "audio": []}
    SRC = {"kind": "disc", "rights": "private_copy"}

    def test_a_film_fiche_is_conform(self):
        f = ma.build_fiche("film", {"title": "Essai", "year": 2020, "genres": ["Drame"], "keywords": []}, self.TECH, self.SRC, {"level": "circle"}, "Essai (2020).mp4")
        self.assertNotIn("keywords", f, "les listes vides ne sont pas écrites")
        valid(f)

    def test_episode_and_music_need_their_blocks(self):
        with self.assertRaises(ma.Refused):
            ma.build_fiche("episode", {"title": "x"}, self.TECH, self.SRC, {"level": "private"}, "x.mp4")
        with self.assertRaises(ma.Refused):
            ma.build_fiche("music", {"title": "x"}, self.TECH, self.SRC, {"level": "private"}, "x.mp3", music={"artist": None})
        valid(ma.build_fiche("music", {"title": "Morceau"}, self.TECH, self.SRC, {"level": "private"}, "x.mp3", music={"artist": "A", "track": 3}))
        valid(ma.build_fiche("episode", {"title": "Pilote"}, self.TECH, self.SRC, {"level": "private"}, "x.mp4", series={"name": "S", "season": 1, "episode": 1}))

    def test_names_are_safe(self):
        self.assertEqual(ma.clean_name('a/b\\c:d*e?"f<g>h|i'), "a_b_c_d_e__f_g_h_i")
        self.assertEqual(ma.clean_name("..."), "sans_titre")
        self.assertEqual(ma.default_rights("own"), "owner")
        self.assertEqual(ma.default_rights("web"), "private_copy")


class FakeStation(BaseHTTPRequestHandler):
    """Un WebDAV minimal : MKCOL, PUT, GET, DELETE et PROPFIND (le CID du blob dans `getetag`)."""

    files = {}
    dirs = set()

    def log_message(self, *a):
        pass

    def _path(self):
        p = unquote(self.path)
        return p[len("/dav"):] if p.startswith("/dav") else p

    def _reply(self, status, body=b"", extra=None):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_MKCOL(self):
        p = self._path()
        parent = p.rsplit("/", 1)[0]
        if p in self.dirs:
            return self._reply(405)
        if parent and parent not in self.dirs:
            return self._reply(409)
        self.dirs.add(p)
        self._reply(201)

    def do_PUT(self):
        data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        p = self._path()
        if p.rsplit("/", 1)[0] not in self.dirs:
            return self._reply(409)
        self.files[p] = data
        self._reply(201)

    def do_GET(self):
        data = self.files.get(self._path())
        self._reply(200, data) if data is not None else self._reply(404)

    def do_DELETE(self):
        self.files.pop(self._path(), None)
        self._reply(204)

    def do_PROPFIND(self):
        data = self.files.get(self._path())
        if data is None:
            return self._reply(404)
        cid = "bafkrei" + hashlib.sha256(data).hexdigest()[:40]
        self._reply(207, f'<?xml version="1.0"?><D:multistatus xmlns:D="DAV:"><D:response><D:propstat><D:prop><D:getetag>"{cid}"</D:getetag></D:prop></D:propstat></D:response></D:multistatus>'.encode())


def ws_server(handler, ready):
    """Un faux relai WebSocket : poignée de main puis `handler(recv, send)` sur des messages JSON."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    ready.append(srv.getsockname()[1])

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=session, args=(conn,), daemon=True).start()

    def session(conn):
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                return
            buf += chunk
        key = [l.split(b": ")[1] for l in buf.split(b"\r\n") if l.lower().startswith(b"sec-websocket-key")][0]
        acc = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
        conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + acc + b"\r\n\r\n")

        def recv():
            nonlocal buf
            def need(n):
                nonlocal buf
                while len(buf) < n:
                    c = conn.recv(65536)
                    if not c:
                        raise EOFError
                    buf += c
                out, buf = buf[:n], buf[n:]
                return out
            _, b1 = need(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", need(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", need(8))[0]
            mask = need(4)
            return json.loads(bytes(b ^ mask[i % 4] for i, b in enumerate(need(n))))

        def send(obj):
            data = json.dumps(obj).encode()
            head = bytes([0x81]) + (bytes([len(data)]) if len(data) < 126 else bytes([126]) + struct.pack(">H", len(data)))
            conn.sendall(head + data)

        buf = buf.split(b"\r\n\r\n", 1)[1]
        try:
            handler(recv, send)
        except (EOFError, OSError, ValueError):  # ValueError : trame de fermeture du client
            pass
        finally:
            conn.close()

    threading.Thread(target=serve, daemon=True).start()
    return srv


class Keys(unittest.TestCase):
    NSEC = "nsec1vl029mgpspedva04g90vltkh6fvh240zqtv9k0t9af8935ke9laqsnlfe5"  # exemple de la NIP-19
    HEX = "67dea2ed018072d675f5415ecfaed7d2597555e202d85b3d65ea4e58d2d92ffa"

    def test_bip340_vector_zero(self):
        sk = (3).to_bytes(32, "big")
        self.assertEqual(nl.pubkey(sk).hex().upper(), "F9308A019258C31049344F85F89D5229B531C845836F99B08601F113BCE036F9")
        sig = nl.schnorr_sign(bytes(32), sk, aux=bytes(32))
        self.assertEqual(sig.hex().upper(), "E907831F80848D1069A5371B402410364BDF1C5F8307B0084C55F1CE2DCA821525F66A4A85EA8B71E482A74F382D2CE5EBEEE8FDB2172F477DF4900D310536C0")
        self.assertTrue(nl.schnorr_verify(bytes(32), nl.pubkey(sk), sig))
        self.assertFalse(nl.schnorr_verify(bytes([1]) + bytes(31), nl.pubkey(sk), sig))

    def test_nsec_decodes_and_a_wrong_checksum_is_refused(self):
        hrp, key = nl.bech32_decode(self.NSEC)
        self.assertEqual((hrp, key.hex()), ("nsec", self.HEX))
        with self.assertRaises(ValueError):
            nl.bech32_decode(self.NSEC[:-1] + ("q" if self.NSEC[-1] != "q" else "p"))

    def test_the_secret_file_is_read_and_checked_against_the_announced_key(self):
        d = Path(tempfile.mkdtemp())
        f = d / ".secret.nostr"
        f.write_text(f"NSEC={self.NSEC}; NPUB=npub1x; HEX={nl.pubkey(bytes.fromhex(self.HEX)).hex()};\n")
        self.assertEqual(nl.load_secret(f).hex(), self.HEX)
        f.write_text(f"NSEC={self.NSEC}; HEX={'00' * 32};")
        with self.assertRaises(ValueError):
            nl.load_secret(f)
        with self.assertRaises(ValueError):
            nl.load_secret(d / "absent")
        shutil.rmtree(d)

    def test_a_signed_event_verifies_and_a_tampered_one_does_not(self):
        sk = bytes.fromhex(self.HEX)
        ev = nl.sign_event(sk, 21, [["title", "é"]], "résumé", created_at=1_794_000_000)
        self.assertTrue(nl.verify_event(ev))
        self.assertFalse(nl.verify_event({**ev, "content": "autre"}))


class Relay(unittest.TestCase):
    def relay(self, auth_required):
        got, ready = [], []

        def handler(recv, send):
            if auth_required:
                send(["AUTH", "défi-1"])
            authed = False
            while True:
                m = recv()
                if m[0] == "AUTH":
                    ev = m[1]
                    authed = nl.verify_event(ev) and ev["kind"] == 22242 and ["challenge", "défi-1"] in ev["tags"]
                elif m[0] == "EVENT":
                    if auth_required and not authed:
                        send(["OK", m[1]["id"], False, "auth-required: authentifiez-vous"])
                    else:
                        got.append(m[1])
                        send(["OK", m[1]["id"], nl.verify_event(m[1]), ""])
                elif m[0] == "REQ":
                    for ev in got:
                        send(["EVENT", m[1], ev])
                    send(["EOSE", m[1]])

        self.srv = ws_server(handler, ready)
        return f"ws://127.0.0.1:{ready[0]}/", got

    def tearDown(self):
        if hasattr(self, "srv"):
            self.srv.close()

    def test_publish_and_query_with_the_nip42_challenge(self):
        url, got = self.relay(True)
        sk = bytes.fromhex(Keys.HEX)
        ev = nl.sign_event(sk, 21, [["t", "film"]], "x")
        self.assertEqual(nl.publish(url, ev, sk), (True, ""))
        self.assertEqual([e["id"] for e in got], [ev["id"]])
        self.assertEqual([e["id"] for e in nl.query(url, {"kinds": [21]}, sk)], [ev["id"]])

    def test_a_relay_that_is_down_is_a_clear_error(self):
        with self.assertRaises(nl.RelayError):
            nl.publish("ws://127.0.0.1:1/", {"id": "x"}, bytes(32), timeout=2)
        with self.assertRaises(nl.RelayError):
            nl.publish("http://x", {"id": "x"}, bytes(32))


class Library(unittest.TestCase):
    def test_folders_and_names_follow_the_library_layout(self):
        self.assertEqual(ma.library_plan("film", {"title": "Le Voyage", "year": 2001}, None, None), ("/Vidéos/Films", "Le Voyage (2001)"))
        self.assertEqual(ma.library_plan("episode", {"title": "Pilote"}, {"name": "Ma/Série", "season": 1, "episode": 2}, None), ("/Vidéos/Séries/Ma_Série/Saison 01", "Ma_Série S01E02"))
        self.assertEqual(ma.library_plan("video", {"title": "Souvenir"}, None, None), ("/Vidéos/Autres", "Souvenir"))
        self.assertEqual(ma.library_plan("music", {"title": "Morceau"}, None, {"artist": "Artiste", "album": None, "track": 3}), ("/Musique/Artiste/Singles", "03 - Morceau"))

    def test_the_video_event_uses_the_station_cid_and_the_nip71_tags(self):
        fiche = {"type": "episode", "title": "Pilote", "year": 2019, "genres": ["Science-Fiction"], "ids": {"tmdb": 99}, "series": {"name": "Test", "season": 1, "episode": 2},
                 "source": {"kind": "broadcast", "rights": "private_copy"},
                 "technical": {"duration_s": 1500.0, "size": 5_000_000, "mime": "video/mp4", "sha256": "a" * 64, "video": {"codec": "h264", "width": 1280, "height": 720}}}
        kind, tags = ma.video_event_tags(fiche, "bafkreiabc")
        t = {k: [x for x in tags if x[0] == k] for k in ("title", "url", "x", "t", "series_name", "season_number", "episode_name", "i", "-", "imeta")}
        self.assertEqual((kind, t["title"][0][1], t["url"][0][1], t["x"][0][1]), (21, "Test S01E02 — Pilote", "/ipfs/bafkreiabc", "a" * 64))
        self.assertIn(["t", "science-fiction"], t["t"] + [x for x in tags if x[0] == "t"])
        self.assertTrue(t["-"] and ["i", "tmdb:99"] in t["i"] and ["t", "private-copy"] in t["t"])
        self.assertIn("encrypted uenc-v2", t["imeta"][0])
        fiche["technical"]["duration_s"] = 45
        self.assertEqual(ma.video_event_tags(fiche, "c")[0], 22, "60 s ou moins : vidéo courte")
        fiche["source"] = {"kind": "own", "rights": "owner", "licence": "CC-BY-4.0"}
        self.assertNotIn(["t", "private-copy"], ma.video_event_tags(fiche, "c")[1])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg absent")
class Whole(unittest.TestCase):
    """Un vrai fichier, un faux WebDAV et un faux relai : sondage, réencodage, écriture dans la bibliothèque, événement signé."""

    def setUp(self):
        FakeStation.files, FakeStation.dirs = {}, {""}
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), FakeStation)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.dir = Path(tempfile.mkdtemp())
        self.src = self.dir / "source.mkv"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=160x120:rate=10", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
             "-f", "lavfi", "-i", "sine=frequency=880:duration=2", "-map", "0:v", "-map", "1:a", "-map", "2:a", "-c:v", "mpeg4", "-c:a", "libmp3lame",
             "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=fra", str(self.src)],
            check=True,
        )
        # Compte : clé du membre, comme sur la station.
        self.nostr = self.dir / "nostr"
        (self.nostr / "alice@example.org").mkdir(parents=True)
        (self.nostr / "alice@example.org" / ".secret.nostr").write_text(f"NSEC={Keys.NSEC}; HEX={nl.pubkey(bytes.fromhex(Keys.HEX)).hex()};")
        self.events, ready = [], []

        def handler(recv, send):
            while True:
                m = recv()
                if m[0] == "EVENT":
                    self.events.append(m[1])
                    send(["OK", m[1]["id"], nl.verify_event(m[1]), ""])

        self.relay = ws_server(handler, ready)
        cfg = {"station_url": f"http://127.0.0.1:{self.http.server_address[1]}", "relay_url": f"ws://127.0.0.1:{ready[0]}/", "nostr_dir": str(self.nostr), "accounts": {"alice@example.org": "jeton"}}
        ma.CONFIG = self.dir / "config.json"
        ma.CONFIG.write_text(json.dumps(cfg))
        ma.JOURNAL = self.dir / "journal.jsonl"

    def tearDown(self):
        self.http.shutdown()
        self.relay.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_main(self, *extra):
        return ma.main([str(self.src), "--title", "Essai", "--year", "2020", "--no-catalog", "--source-kind", "disc", *extra])

    def test_dry_run_writes_the_files_a_conform_fiche_and_the_event_tags(self):
        out = self.dir / "sortie"
        self.assertEqual(self.run_main("--level", "circle", "--dry-run", str(out)), 0)
        fiche = json.loads((out / "Essai (2020).media.json").read_text(encoding="utf-8"))
        valid(fiche)
        t = fiche["technical"]
        self.assertTrue(t["transcoded"] and t["video"]["codec"] == "h264" and t["mime"] == "video/mp4")
        self.assertEqual([a["lang"] for a in t["audio"]], ["fr", "en"], "la piste française passe en premier")
        self.assertEqual([a["default"] for a in t["audio"]], [True, False])
        self.assertEqual((out / "Essai (2020).mp4").stat().st_size, t["size"])

    def test_the_attestation_is_automatic_local_and_publishes_nothing(self):
        legal = self.nostr / "alice@example.org" / "legal"
        self.assertFalse(legal.exists())
        self.assertEqual(self.run_main("--level", "private"), 0)
        self.assertTrue(legal.is_file(), "posée seule au premier dépôt de copie privée")
        self.assertEqual(self.events, [], "aucun événement 31903")

    def test_deposit_writes_the_library_and_publishes_a_signed_event_with_the_station_cid(self):
        self.assertEqual(self.run_main("--level", "circle"), 0)
        self.assertEqual(sorted(p for p in FakeStation.files), ["/Vidéos/Films/Essai (2020).media.json", "/Vidéos/Films/Essai (2020).mp4"])
        self.assertTrue({"/Vidéos", "/Vidéos/Films"} <= FakeStation.dirs)
        ev = self.events[-1]
        self.assertTrue(nl.verify_event(ev))
        self.assertEqual(ev["pubkey"], nl.pubkey(bytes.fromhex(Keys.HEX)).hex())
        self.assertEqual(ev["kind"], 22, "2 secondes : vidéo courte")
        url = next(t[1] for t in ev["tags"] if t[0] == "url")
        cid = "bafkrei" + hashlib.sha256(FakeStation.files["/Vidéos/Films/Essai (2020).mp4"]).hexdigest()[:40]
        self.assertEqual(url, f"/ipfs/{cid}", "l'adresse est celle que la station a donnée au blob")
        self.assertEqual(next(t[1] for t in ev["tags"] if t[0] == "x"), hashlib.sha256(FakeStation.files["/Vidéos/Films/Essai (2020).mp4"]).hexdigest())
        # Redéposer le même titre : refus net, rien d'envoyé de plus.
        before = dict(FakeStation.files)
        self.assertEqual(self.run_main("--level", "circle"), 2)
        self.assertEqual(FakeStation.files, before)

    def test_private_level_publishes_nothing(self):
        self.assertEqual(self.run_main("--level", "private"), 0)
        self.assertEqual(self.events, [])
        self.assertIn("/Vidéos/Films/Essai (2020).mp4", FakeStation.files)

    def test_a_duplicate_is_refused_before_any_re_encoding(self):
        self.assertEqual(self.run_main("--level", "private"), 0)
        calls = []
        real = ma.run
        ma.run = lambda cmd, t: calls.append(cmd) or real(cmd, t)
        try:
            self.assertEqual(self.run_main("--level", "private"), 2)
        finally:
            ma.run = real
        self.assertFalse([c for c in calls if "ffmpeg" in str(c[0])], "pas de réencodage pour un doublon")

    def test_a_missing_file_or_a_missing_title_is_refused_without_a_traceback(self):
        self.assertEqual(ma.main([str(self.dir / "absent.mkv"), "--dry-run", str(self.dir / "o")]), 2)
        self.assertEqual(ma.main([str(self.src), "--type", "episode", "--dry-run", str(self.dir / "o")]), 2)


if __name__ == "__main__":
    unittest.main()
