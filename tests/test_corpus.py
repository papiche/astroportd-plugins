"""Tests du plugin corpus : facultatif dans install-plugins.sh (jamais pris par --all ni proposé comme nouveauté, rangé en
fin de liste), manifeste sans contrat de données, et envoi WebDAV du corpus déjà fabriqué contre un faux serveur local.

    python3 -m unittest discover -s tests
"""
import os
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def status(home):
    env = dict(os.environ, ASTROPORTD_HOME=str(home))
    out = subprocess.run(["bash", str(ROOT / "install-plugins.sh"), "--no-pull", "--dir", str(ROOT), "--status"],
                         capture_output=True, text=True, env=env, check=True).stdout
    return [line.split("|") for line in out.splitlines()]


class Optional(unittest.TestCase):
    def test_manifest_is_optional_and_produces_nothing(self):
        m = tomllib.load(open(ROOT / "corpus/plugin.toml", "rb"))
        self.assertIs(m["optional"], True)
        self.assertEqual(m["produces"], "", "aucune donnée lue par la station")
        self.assertFalse(m["needs_root"])

    def test_listed_last_and_never_new(self):
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / "plugins").mkdir()
            # Station qui connaît déjà les autres plugins : seul un plugin d'usage courant inconnu serait « nouveau ».
            (Path(home) / "plugins/.known").write_text("dnslink\nenergy\nmedia\n")
            rows = status(home)
            self.assertEqual(rows[-1][0], "corpus", "les facultatifs en fin de liste")
            self.assertEqual(rows[-1][3], "absent", "un jeu d'essai n'est jamais présenté comme nouveauté")

    def test_all_skips_optional(self):
        # --all lance l'install.sh de chaque plugin retenu : on vérifie seulement la sélection, dans le script lui-même.
        script = (ROOT / "install-plugins.sh").read_text(encoding="utf-8")
        self.assertIn('[ "${OPTS[$i]}" = true ] || CHOSEN+=', script)


class Send(unittest.TestCase):
    def test_only_send_uploads_the_existing_corpus_with_the_token_from_the_environment(self):
        seen = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def answer(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                seen.append((self.command, self.path, self.headers.get("Authorization", "")))
                self.send_response(201)
                self.end_headers()

            do_MKCOL = do_PUT = answer

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as d:
                out = Path(d) / "corpus"
                (out / "Photos").mkdir(parents=True)
                (out / "LISEZMOI.md").write_text("# essai\n")
                (out / "Photos/chat.jpg").write_bytes(b"jpeg")
                env = dict(os.environ, ASTRO_TOKEN="jeton-essai", ASTRO_LOGIN="moi@exemple.org", ASTRO_DAV=f"http://127.0.0.1:{srv.server_address[1]}/dav")
                r = subprocess.run([sys.executable, str(ROOT / "corpus/bin/fabriquer_corpus.py"), str(out), "--envoyer", "/Essais/Recherche", "--seulement-envoyer"],
                                   capture_output=True, text=True, env=env, timeout=60)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn("jeton-essai", r.stdout + r.stderr, "le jeton n'est jamais affiché")
        finally:
            srv.shutdown()
        puts = sorted(p for m, p, _ in seen if m == "PUT")
        self.assertEqual(puts, ["/dav/Essais/Recherche/LISEZMOI.md", "/dav/Essais/Recherche/Photos/chat.jpg"])
        self.assertTrue(all(a.startswith("Basic ") for _, _, a in seen))

    def test_only_send_refuses_a_missing_corpus(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, ASTRO_TOKEN="x", ASTRO_LOGIN="x", ASTRO_DAV="http://127.0.0.1:9/dav")
            r = subprocess.run([sys.executable, str(ROOT / "corpus/bin/fabriquer_corpus.py"), str(Path(d) / "rien"), "--envoyer", "/E", "--seulement-envoyer"],
                               capture_output=True, text=True, env=env, timeout=60)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("pas de corpus", r.stderr)


if __name__ == "__main__":
    unittest.main()
