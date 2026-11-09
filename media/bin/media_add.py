#!/usr/bin/env python3
"""media-add : prépare un film, un épisode, une vidéo ou un morceau et le dépose dans l'uCloud d'un membre.

Remplace Astroport.ONE/ajouter_media.sh, et tout se passe ici : astroportd reste un serveur WebDAV qui partage en P2P, N¹ ou N².
Le plugin sonde (ffprobe), réencode (ffmpeg), télécharge (yt-dlp), interroge le catalogue TMDB, puis **écrit lui-même** dans l'uCloud
du membre, par WebDAV et avec son jeton : /Vidéos/Films, /Vidéos/Séries/<série>/Saison NN, /Vidéos/Autres, /Musique/<artiste>/<album>,
avec la fiche `<nom>.media.json` à côté (contrat `media/v1`). Pour un partage `circle`, il signe avec la clé du membre (celle de la
machine, jamais copiée) et publie l'événement vidéo (NIP-71, kind 21/22) sur le relai, comme n'importe quel client Nostr.

    media-add film.mkv --type film --title "Le Voyage de Chihiro" --year 2001 --level circle
    media-add https://www.youtube.com/watch?v=… --type video --level private
    media-add fichier.mp4 --title "Essai" --dry-run /tmp/sortie      (rien n'est envoyé : tout est écrit dans le dossier)

Configuration : ~/.zen/astroportd/plugins/media/config.json (écrit par setup.sh, droits 0600).
"""
import argparse
import base64
import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nostr_lite as nl  # noqa: E402

VERSION = "0.1.0"
STATION_HOME = Path(os.environ.get("ASTROPORTD_HOME", Path.home() / ".zen" / "astroportd"))
CONFIG = STATION_HOME / "plugins" / "media" / "config.json"
JOURNAL = STATION_HOME / "plugins" / "media" / "journal.jsonl"
NOSTR_DIR = Path.home() / ".zen" / "game" / "nostr"
CONSENT_TEXT = "J'atteste que les œuvres que je dépose dans mon uCloud sont des copies réalisées pour mon usage strictement privé (article L. 122-5, 2° du Code de la propriété intellectuelle), que je les possède légitimement, et qu'elles ne seront partagées qu'avec mon cercle d'amis de confiance (N¹)."
MAX_BYTES = 1024 << 20  # plafond de la station (1 024 Mo)
TMDB = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p/w780"
TEXT_SUBS = {"subrip", "ass", "ssa", "mov_text", "webvtt", "srt", "text"}
LANG3 = {"fra": "fr", "fre": "fr", "eng": "en", "spa": "es", "deu": "de", "ger": "de", "ita": "it", "por": "pt", "jpn": "ja", "nld": "nl", "dut": "nl", "rus": "ru", "zho": "zh", "chi": "zh", "ara": "ar"}
KEY_JOBS = {"Director", "Screenplay", "Writer", "Novel", "Original Music Composer", "Director of Photography", "Producer", "Editor", "Creator"}


class Refused(Exception):
    """Une étape refuse : le message est destiné à la personne qui dépose."""


def say(msg):
    print(msg, file=sys.stderr)


# ───────────────────────────── Sondage ─────────────────────────────

def lang2(code):
    code = (code or "").strip().lower()
    if not code or code in ("und", "unk"):
        return None
    return LANG3.get(code, code if re.fullmatch(r"[a-z]{2,3}", code) else None)


def run(cmd, timeout):
    try:
        out = subprocess.run(cmd, check=True, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise Refused(f"{cmd[0]} introuvable : installez-le (voir install.sh)")
    except subprocess.TimeoutExpired:
        raise Refused(f"{cmd[0]} : délai de {timeout} s dépassé")
    except subprocess.CalledProcessError as e:
        tail = e.stderr.decode("utf-8", "replace").strip()[-400:]
        raise Refused(f"{cmd[0]} a échoué : {tail}")
    return out.stdout


def ffprobe(path):
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)], 120)
    return json.loads(out)


def technical_of(probe, size, sha256, transcoded):
    """Bloc `technical` du contrat d'après la sortie de ffprobe."""
    streams = probe.get("streams", [])
    fmt = probe.get("format", {})
    video = next((s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    names = (fmt.get("format_name") or "").split(",")
    tech = {
        "duration_s": round(float(fmt.get("duration") or 0), 3),
        "size": size,
        "mime": mime_of(names, video is not None),
        "sha256": sha256,
        "container": names[0] or None,
        "transcoded": transcoded,
    }
    if video:
        fps = None
        num, _, den = (video.get("avg_frame_rate") or "0/0").partition("/")
        if den and float(den) > 0 and float(num) > 0:
            fps = round(float(num) / float(den), 3)
        v = {"codec": video.get("codec_name", ""), "width": int(video.get("width") or 0), "height": int(video.get("height") or 0)}
        if fps:
            v["fps"] = fps
        tech["video"] = v
    tech["audio"] = [
        {"codec": a.get("codec_name", ""), "lang": lang2(a.get("tags", {}).get("language")), "channels": a.get("channels"), "default": bool(a.get("disposition", {}).get("default"))}
        for a in audios
    ]
    return tech


def mime_of(names, has_video):
    if any(n in ("mov", "mp4", "m4a", "3gp") for n in names):
        return "video/mp4" if has_video else "audio/mp4"
    if "matroska" in names or "webm" in names:
        return ("video/webm" if "webm" in names else "video/x-matroska") if has_video else "audio/webm"
    if "mp3" in names:
        return "audio/mpeg"
    if "ogg" in names:
        return "audio/ogg"
    if "flac" in names:
        return "audio/flac"
    if "wav" in names:
        return "audio/wav"
    return "video/x-msvideo" if has_video else "application/octet-stream"


def plan_transcode(probe, container_ext):
    """Un fichier déjà MP4/H.264/AAC n'est jamais réencodé. Rend (raisons, index de la piste audio gardée en premier)."""
    streams = probe.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    if video is None:
        return [], 0
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    reasons = []
    if video.get("codec_name") != "h264":
        reasons.append(f"vidéo {video.get('codec_name')}")
    if any(a.get("codec_name") != "aac" for a in audios):
        reasons.append("audio non AAC")
    if container_ext not in ("mp4", "m4v"):
        reasons.append(f"conteneur {container_ext or '?'}")
    french = next((i for i, a in enumerate(audios) if lang2(a.get("tags", {}).get("language")) == "fr"), 0)
    return reasons, french


def ffmpeg_args(src, dst, french_audio, n_audio=1):
    """Réencodage H.264/AAC : la piste française d'abord, les autres derrière (chacune une seule fois)."""
    order = [french_audio] + [i for i in range(n_audio) if i != french_audio]
    maps = [a for i in order for a in ("-map", f"0:a:{i}")] if n_audio else []
    return [
        "ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src), "-map", "0:v:0", *maps,
        "-c:v", "libx264", "-preset", "medium", "-crf", "22", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-disposition:a", "0", "-disposition:a:0", "default", "-sn", "-movflags", "+faststart", str(dst),
    ]


def extract_text_subtitles(probe, src, workdir, stem):
    """Pistes de sous-titres textuelles → WebVTT à côté du fichier. Rend le bloc `subtitles` du contrat."""
    out = []
    subs = [s for s in probe.get("streams", []) if s.get("codec_type") == "subtitle"]
    for n, s in enumerate(subs):
        if s.get("codec_name") not in TEXT_SUBS:
            continue  # sous-titres d'image (DVD, PGS) : pas de transcription automatique
        lang = lang2(s.get("tags", {}).get("language"))
        if not lang:
            continue
        name = f"{stem}.{lang}{'.' + str(n) if any(o['lang'] == lang for o in out) else ''}.vtt"
        try:
            run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src), "-map", f"0:s:{n}", "-c:s", "webvtt", str(workdir / name)], 300)
        except Refused as e:
            say(f"sous-titres {lang} ignorés : {e}")
            continue
        out.append({"file": name, "lang": lang, "origin": "embedded", **({"forced": True} if s.get("disposition", {}).get("forced") else {})})
    return out


# ───────────────────────────── Sources (yt-dlp) ─────────────────────────────

def ytdlp_fetch(url, workdir, kind, langs="fr,en"):
    """Télécharge `url` avec yt-dlp. Rend (fichier média, infos yt-dlp, sous-titres {fichier: lang})."""
    cmd = ["yt-dlp", "--no-playlist", "--write-info-json", "--no-progress", "-o", str(workdir / "%(id)s.%(ext)s")]
    if kind == "music":
        cmd += ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
    else:
        cmd += ["-f", "bv*[height<=1080]+ba/b[height<=1080]/b", "--merge-output-format", "mp4", "--write-subs", "--sub-langs", langs, "--convert-subs", "vtt"]
    run(cmd + [url], 3600)
    info_file = next(iter(sorted(workdir.glob("*.info.json"))), None)
    if info_file is None:
        raise Refused("yt-dlp n'a rien téléchargé")
    info = json.loads(info_file.read_text(encoding="utf-8"))
    media = next((p for p in sorted(workdir.iterdir()) if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mp3", ".m4a", ".opus")), None)
    if media is None:
        raise Refused("yt-dlp n'a produit aucun fichier média")
    subs = {}
    for p in sorted(workdir.glob("*.vtt")):
        m = re.search(r"\.([a-z]{2,3})(?:-[A-Za-z0-9]+)?\.vtt$", p.name)
        if m:
            subs[p.name] = m.group(1)
    return media, info, subs


def web_defaults(info):
    """Valeurs par défaut tirées des informations yt-dlp."""
    date = str(info.get("upload_date") or "")
    year = int(date[:4]) if re.fullmatch(r"\d{8}", date) else None
    yid = info.get("id") if re.fullmatch(r"[A-Za-z0-9_-]{11}", str(info.get("id") or "")) else None
    return {
        "title": info.get("track") or info.get("title") or "Sans titre",
        "overview": (info.get("description") or "").strip()[:4000] or None,
        "year": year,
        "keywords": [t for t in (info.get("tags") or [])[:30] if isinstance(t, str)],
        "uploader": info.get("uploader") or info.get("channel"),
        "licence": ("CC-BY-4.0" if "creative commons" in str(info.get("license") or "").lower() else (info.get("license") or "youtube-standard")),
        "url": info.get("webpage_url") or info.get("original_url"),
        "youtube": yid,
        "artist": info.get("artist") or info.get("uploader"),
        "album": info.get("album"),
    }


# ───────────────────────────── Catalogue TMDB ─────────────────────────────

def tmdb_get(api_key, path, params):
    q = urllib.parse.urlencode({**params, "api_key": api_key})
    with urllib.request.urlopen(f"{TMDB}{path}?{q}", timeout=30) as r:  # noqa: S310 (hôte fixe)
        return json.loads(r.read())


def people_of(credits):
    cast = [
        {"name": c["name"], "character": c.get("character") or None, "order": c.get("order"), "ids": {"tmdb": c["id"]}, "photo": None}
        for c in (credits.get("cast") or [])[:20] if c.get("name")
    ]
    crew, seen = [], set()
    for c in credits.get("crew") or []:
        if c.get("job") in KEY_JOBS and c.get("name") and (c["name"], c["job"]) not in seen:
            seen.add((c["name"], c["job"]))
            crew.append({"name": c["name"], "job": c["job"], "department": c.get("department"), "ids": {"tmdb": c["id"]}})
    return {"cast": cast, "crew": crew[:30]}


def tmdb_movie(get, title, year, lang="fr-FR", tmdb_id=None):
    """Fiche d'un film d'après TMDB. `get(path, params)` rend le JSON. Rend (champs de fiche, chemin d'affiche, chemin de fond)."""
    if tmdb_id is None:
        hits = get("/search/movie", {"query": title, "language": lang, **({"year": year} if year else {})}).get("results") or []
        if not hits:
            raise Refused(f"TMDB : aucun film « {title} »{f' ({year})' if year else ''} : précisez --tmdb-id ou déposez sans catalogue (--no-catalog)")
        tmdb_id = hits[0]["id"]
    d = get(f"/movie/{tmdb_id}", {"language": lang, "append_to_response": "credits,keywords,release_dates"})
    cert = None
    for r in (d.get("release_dates") or {}).get("results", []):
        if r.get("iso_3166_1") == "FR":
            cert = next(({"country": "FR", "value": x["certification"]} for x in r.get("release_dates", []) if x.get("certification")), None)
    coll = d.get("belongs_to_collection")
    rd = d.get("release_date") or None
    fields = {
        "title": d.get("title") or title,
        "original_title": d.get("original_title") or None,
        "tagline": d.get("tagline") or None,
        "overview": d.get("overview") or None,
        "year": int(rd[:4]) if rd else year,
        "release_date": rd,
        "runtime_min": d.get("runtime") or None,
        "genres": [g["name"] for g in d.get("genres", [])],
        "keywords": [k["name"] for k in (d.get("keywords") or {}).get("keywords", [])][:30],
        "countries": [c["iso_3166_1"] for c in d.get("production_countries", [])],
        "original_language": d.get("original_language") or None,
        "spoken_languages": [x["iso_639_1"] for x in d.get("spoken_languages", []) if x.get("iso_639_1")],
        "certification": cert,
        "rating": {"source": "tmdb", "value": d["vote_average"], "max": 10, "votes": d.get("vote_count")} if d.get("vote_count") else None,
        "collection": {"name": coll["name"], "tmdb_id": coll.get("id")} if coll else None,
        "companies": [c["name"] for c in d.get("production_companies", [])],
        "people": people_of(d.get("credits") or {}),
        "ids": {"tmdb": d["id"], "imdb": d.get("imdb_id") or None},
    }
    return {k: v for k, v in fields.items() if v is not None}, d.get("poster_path"), d.get("backdrop_path")


def tmdb_episode(get, series, season, episode, year=None, lang="fr-FR", tmdb_id=None):
    """Fiche d'un épisode : (champs, bloc `series`, chemin d'image d'épisode)."""
    if tmdb_id is None:
        hits = get("/search/tv", {"query": series, "language": lang, **({"first_air_date_year": year} if year else {})}).get("results") or []
        if not hits:
            raise Refused(f"TMDB : aucune série « {series} » : précisez --tmdb-id ou déposez sans catalogue (--no-catalog)")
        tmdb_id = hits[0]["id"]
    s = get(f"/tv/{tmdb_id}", {"language": lang, "append_to_response": "external_ids"})
    e = get(f"/tv/{tmdb_id}/season/{season}/episode/{episode}", {"language": lang, "append_to_response": "credits"})
    air = e.get("air_date") or None
    count = next((x.get("episode_count") for x in s.get("seasons", []) if x.get("season_number") == season), None)
    block = {
        "name": s.get("name") or series, "original_name": s.get("original_name") or None, "season": season, "episode": episode,
        "episode_count": count, "air_date": air, "first_air_date": s.get("first_air_date") or None,
        "networks": [n["name"] for n in s.get("networks", [])], "overview": s.get("overview") or None,
        "ids": {"tmdb": s["id"], "imdb": (s.get("external_ids") or {}).get("imdb_id") or None},
    }
    fields = {
        "title": e.get("name") or f"Épisode {episode}", "overview": e.get("overview") or None, "year": int(air[:4]) if air else year,
        "release_date": air, "runtime_min": e.get("runtime") or None, "genres": [g["name"] for g in s.get("genres", [])],
        "original_language": s.get("original_language") or None, "people": people_of(e.get("credits") or {}),
    }
    return {k: v for k, v in fields.items() if v is not None}, {k: v for k, v in block.items() if v is not None}, e.get("still_path")


def download(url, dest):
    with urllib.request.urlopen(url, timeout=60) as r, open(dest, "wb") as f:  # noqa: S310
        shutil.copyfileobj(r, f)


# ───────────────────────────── Fiche ─────────────────────────────

def clean_name(s):
    s = re.sub(r"[/\\:*?\"<>|\x00-\x1f]", "_", s).strip().strip(".")
    return s[:120] or "sans_titre"


def build_fiche(kind, fields, technical, source, sharing, file_name, artwork=None, subtitles=None, series=None, music=None, generator_version=VERSION):
    """Assemble la fiche `media/v1`. `fields` : champs descriptifs (titre, résumé, genres, people, ids…)."""
    fiche = {"schema": "media/v1", "generated_at": int(time.time()), "generator": {"plugin": "media", "version": generator_version}, "type": kind, "file": file_name}
    fiche.update({k: v for k, v in fields.items() if v not in (None, [], {}, "")})
    if "title" not in fiche:
        raise Refused("un titre est nécessaire (--title)")
    if kind == "episode":
        if not series:
            raise Refused("un épisode exige --series, --season et --episode")
        fiche["series"] = series
    if kind == "music":
        if not music or not music.get("artist"):
            raise Refused("un morceau exige un artiste (--artist)")
        fiche["music"] = music
    if artwork:
        fiche["artwork"] = artwork
    if subtitles:
        fiche["subtitles"] = subtitles
    fiche["technical"] = technical
    fiche["source"] = source
    fiche["sharing"] = sharing
    return fiche


def default_rights(source_kind):
    return {"own": "owner"}.get(source_kind, "private_copy")


# ───────────────────────────── Dépôt WebDAV ─────────────────────────────

class Dav:
    """Client WebDAV minimal (stdlib) : MKCOL, PUT en flux, GET, DELETE ; authentification Basic avec le jeton du membre."""

    def __init__(self, station_url, email, token, mount="/dav"):
        u = urllib.parse.urlsplit(station_url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise Refused(f"adresse de station invalide : {station_url}")
        self.scheme, self.host, self.port = u.scheme, u.hostname, u.port
        self.mount = mount.rstrip("/")
        self.auth = "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()

    def _conn(self):
        cls = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        return cls(self.host, self.port, timeout=600)

    def _path(self, path):
        return self.mount + urllib.parse.quote(path)

    def request(self, method, path, body=None, length=None, extra=None):
        c = self._conn()
        try:
            headers = {"Authorization": self.auth, **(extra or {})}
            if length is not None:
                headers["Content-Length"] = str(length)
            c.request(method, self._path(path), body=body, headers=headers)
            r = c.getresponse()
            return r.status, r.read()
        finally:
            c.close()

    def mkcol(self, path):
        status, _ = self.request("MKCOL", path)
        if status not in (201, 405):  # 405 : existe déjà
            raise Refused(f"MKCOL {path} : HTTP {status}")

    def mkcol_p(self, path):
        """Crée `path` et ses parents (un MKCOL par niveau ; ce qui existe déjà est laissé tel quel)."""
        cur = ""
        for part in [p for p in path.split("/") if p]:
            cur += "/" + part
            self.mkcol(cur)

    def etag(self, path):
        """Empreinte-CID du fichier (propriété `getetag`, entre guillemets) ; None s'il n'existe pas."""
        status, body = self.request("PROPFIND", path, extra={"Depth": "0"})
        if status != 207:
            return None
        m = re.search(rb"<(?:[A-Za-z]+:)?getetag>\s*\"?([^<\"\s]+)\"?\s*</(?:[A-Za-z]+:)?getetag>", body)
        return m.group(1).decode() if m else ""

    def put_file(self, path, local):
        size = Path(local).stat().st_size
        with open(local, "rb") as f:
            status, body = self.request("PUT", path, body=f, length=size)
        if status not in (200, 201, 204):
            raise Refused(f"dépôt de {path} refusé : HTTP {status} {body[:200].decode('utf-8', 'replace')}")

    def get(self, path):
        status, body = self.request("GET", path)
        return body if status == 200 else None

    def delete(self, path):
        self.request("DELETE", path)


def library_plan(kind, fields, series, music):
    """Dossier et nom (sans extension) dans l'uCloud : /Vidéos/Films, /Vidéos/Séries/<série>/Saison NN, /Vidéos/Autres, /Musique/<artiste>/<album>."""
    title = clean_name(fields.get("title") or "sans titre")
    year = fields.get("year")
    if kind == "episode":
        name = clean_name(series["name"])
        return f"/Vidéos/Séries/{name}/Saison {series['season']:02d}", f"{name} S{series['season']:02d}E{series['episode']:02d}"
    if kind == "music":
        artist = clean_name((music or {}).get("artist") or "Inconnu")
        album = clean_name((music or {}).get("album") or "Singles")
        track = (music or {}).get("track")
        return f"/Musique/{artist}/{album}", (f"{track:02d} - {title}" if track is not None else title)
    stem = f"{title} ({year})" if year else title
    return ("/Vidéos/Films" if kind == "film" else "/Vidéos/Autres"), stem


def deposit(dav, folder, files, fiche_path):
    """Écrit `files` (nom → chemin local) puis la fiche dans `folder` de l'uCloud ; rend {nom: CID du blob chiffré}.

    Jamais d'écrasement : si le fichier média existe déjà, on s'arrête avant d'envoyer quoi que ce soit.
    """
    names = list(files)
    if dav.etag(f"{folder}/{names[0]}") is not None:
        raise Refused(f"{folder}/{names[0]} existe déjà : rien n'a été envoyé")
    dav.mkcol_p(folder)
    cids = {}
    for name, local in files.items():
        say(f"dépôt de {name}…")
        dav.put_file(f"{folder}/{name}", local)
        cids[name] = dav.etag(f"{folder}/{name}")
    dav.put_file(f"{folder}/{Path(fiche_path).name}", fiche_path)  # la fiche en dernier : base de la vidéothèque
    return cids


# ───────────────────────────── Copie privée et événement vidéo ─────────────────────────────

def account_dir(cfg, email):
    return Path(cfg.get("nostr_dir") or NOSTR_DIR) / email


def genre_slug(g):
    folded = "".join({"é": "e", "è": "e", "ê": "e", "ë": "e", "à": "a", "â": "a", "ä": "a", "î": "i", "ï": "i", "ô": "o", "ö": "o", "ù": "u", "û": "u", "ü": "u", "ç": "c"}.get(c, c) for c in g.lower())
    return "-".join(p for p in re.split(r"[^a-z0-9]+", folded) if p)


def display_title(fiche):
    s = fiche.get("series")
    if fiche["type"] == "episode" and s:
        base = f"{s['name']} S{s['season']:02d}E{s['episode']:02d}"
        return base if fiche["title"] == s["name"] else f"{base} — {fiche['title']}"
    return f"{fiche['title']} ({fiche['year']})" if fiche.get("year") else fiche["title"]


def video_event_tags(fiche, cid):
    """Tags NIP-71 (kind 21/22) d'un film, épisode ou vidéo ; mêmes étiquettes que l'ancien publish_nostr_video.sh et astroportd."""
    t, v = fiche["technical"], fiche["technical"]["video"]
    kind = 22 if round(t["duration_s"]) <= 60 else 21
    dim, url, sha = f"{v['width']}x{v['height']}", f"/ipfs/{cid}", t["sha256"]
    imeta = ["imeta", f"dim {dim}", f"url {url}", f"m {t['mime']}", f"x {sha}", f"size {t['size']}"]
    if t["duration_s"] > 0:
        imeta += [f"duration {t['duration_s']:.3f}", f"bitrate {int(t['size'] * 8 / t['duration_s'])}"]
    imeta.append("encrypted uenc-v2")
    episode = fiche["type"] == "episode"
    private = fiche["source"]["rights"] == "private_copy"
    tags = [
        ["title", display_title(fiche)], ["url", url], ["m", t["mime"]], ["dim", dim], imeta, ["size", str(t["size"])],
        ["duration", str(round(t["duration_s"]))], ["published_at", str(int(time.time()))], ["x", sha],
        ["encrypted", "uenc-v2"],  # chiffré dans l'uCloud : l'adresse ne se lit qu'avec la clé remise par un lien de partage
        ["t", "series" if episode else "film"], ["t", "tv-show" if episode else "movie"], ["t", "short" if kind == 22 else "regular"],
        ["-"],  # NIP-70 : événement protégé, jamais republié par un tiers
        ["L", "genre"], ["L", "license"],
    ]
    if private:
        tags += [["t", "private-copy"], ["l", "private-copy-cpi-l122-5", "license"]]
    elif fiche["source"].get("licence"):
        tags.append(["l", fiche["source"]["licence"], "license"])
    for g in fiche.get("genres", []):
        if genre_slug(g):
            tags += [["l", g, "genre"], ["t", genre_slug(g)]]
    if fiche.get("year"):
        tags.append(["year", str(fiche["year"])])
    ids = fiche.get("ids") or {}
    if ids.get("tmdb"):
        tags.append(["i", f"tmdb:{ids['tmdb']}"])
    if ids.get("youtube"):
        tags.append(["i", f"youtube:{ids['youtube']}"])
    if episode:
        s = fiche["series"]
        tags += [["series_name", s["name"]], ["season_number", str(s["season"])], ["episode_number", str(s["episode"])]]
        if fiche["title"] != s["name"]:
            tags.append(["episode_name", fiche["title"]])
    return kind, tags


def relay_of(cfg):
    if cfg.get("relay_url"):
        return cfg["relay_url"]
    u = urllib.parse.urlsplit(cfg.get("station_url", ""))
    return f"{'wss' if u.scheme == 'https' else 'ws'}://{u.netloc}/" if u.netloc else ""


def sha256_file(path):
    h, n = hashlib.sha256(), 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def load_config():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise Refused(f"pas de configuration : lancez setup.sh (attendue dans {CONFIG})")
    except ValueError as e:
        raise Refused(f"configuration illisible ({CONFIG}) : {e}")


def parser():
    p = argparse.ArgumentParser(prog="media-add", description=__doc__.split("\n\n")[0])
    p.add_argument("source", nargs="?", help="fichier local ou adresse web (yt-dlp)")
    p.add_argument("--attest", action="store_true", help="poser l'attestation de copie privée sans rien déposer (sinon elle est posée seule au premier dépôt)")
    p.add_argument("--type", choices=["film", "episode", "video", "music"], help="nature (défaut : film pour un fichier, video pour une adresse web)")
    p.add_argument("--title")
    p.add_argument("--year", type=int)
    p.add_argument("--series")
    p.add_argument("--season", type=int)
    p.add_argument("--episode", type=int)
    p.add_argument("--artist")
    p.add_argument("--album")
    p.add_argument("--track", type=int)
    p.add_argument("--tmdb-id", type=int, help="identifiant TMDB (évite la recherche)")
    p.add_argument("--no-catalog", action="store_true", help="ne pas interroger TMDB")
    p.add_argument("--lang", default="fr-FR", help="langue du catalogue")
    p.add_argument("--level", choices=["private", "circle"], default="private", help="partage : le membre seul (défaut) ou son cercle N¹")
    p.add_argument("--source-kind", choices=["disc", "purchase", "broadcast", "web", "own", "other"], help="origine (défaut : web pour une adresse, other sinon)")
    p.add_argument("--rights", choices=["private_copy", "free_licence", "public_domain", "owner", "licensed"], help="droits (défaut selon l'origine)")
    p.add_argument("--no-transcode", action="store_true", help="déposer tel quel, sans réencodage H.264/AAC")
    p.add_argument("--account", help="compte (e-mail) du membre ; défaut : le seul de la configuration")
    p.add_argument("--dry-run", metavar="DOSSIER", help="écrire fichiers, fiche et événement dans ce dossier, sans rien envoyer")
    p.add_argument("--no-event", action="store_true", help="déposer sans publier d'événement vidéo")
    return p


def prepare(args, work, get=None, exists=None):
    """Prépare tout dans `work` (fichiers + fiche) ; rend (fiche, {nom: chemin local}, stem, chemin de la fiche, dossier de l'uCloud).

    `get(chemin, paramètres)` remplace l'accès à TMDB (tests) ; sans lui, la clé est lue dans TMDB_API_KEY.
    `exists(dossier, nom)` dit si la cible est déjà dans l'uCloud : on s'arrête alors avant tout réencodage.
    """
    is_url = re.match(r"https?://", args.source) is not None
    kind = args.type or ("video" if is_url else "film")
    source_kind = args.source_kind or ("web" if is_url else "other")
    info, sub_files = None, {}
    if is_url:
        media, info, sub_files = ytdlp_fetch(args.source, work, kind)
    else:
        media = Path(args.source)
        if not media.is_file():
            raise Refused(f"{media} : fichier introuvable")
        if media.stat().st_size > MAX_BYTES:
            raise Refused(f"{media.stat().st_size >> 20} Mo : la station refuse plus de {MAX_BYTES >> 20} Mo")
    web = web_defaults(info) if info else {}

    fields = {"title": args.title or web.get("title"), "year": args.year or web.get("year"), "overview": web.get("overview"), "keywords": web.get("keywords")}
    series = music = None
    artwork = {}
    if not get and not args.no_catalog and os.environ.get("TMDB_API_KEY"):
        key = os.environ["TMDB_API_KEY"]
        get = lambda p, q: tmdb_get(key, p, q)  # noqa: E731
    if args.no_catalog:
        get = None
    if kind == "film" and get and (args.title or args.tmdb_id):
        cat, poster, back = tmdb_movie(get, args.title or "", args.year, args.lang, args.tmdb_id)
        fields.update(cat)
        for role, path in (("poster", poster), ("backdrop", back)):
            if path:
                artwork[role] = path
    elif kind == "episode":
        if not (args.series and args.season is not None and args.episode is not None):
            raise Refused("un épisode exige --series, --season et --episode")
        series = {"name": args.series, "season": args.season, "episode": args.episode}
        if get:
            cat, block, still = tmdb_episode(get, args.series, args.season, args.episode, args.year, args.lang, args.tmdb_id)
            fields.update({k: v for k, v in cat.items() if not (k == "title" and args.title)})
            series = block
            if still:
                artwork["still"] = still
        fields["title"] = fields.get("title") or args.title or f"Épisode {args.episode}"
    elif kind == "music":
        music = {"artist": args.artist or web.get("artist"), "album": args.album or web.get("album"), "track": args.track}
        music = {k: v for k, v in music.items() if v is not None}
    if web.get("youtube") and "ids" not in fields:
        fields["ids"] = {"youtube": web["youtube"]}

    if not fields.get("title"):
        fields["title"] = media.stem
    folder, stem = library_plan(kind, fields, series, music)

    # Sondage, puis réencodage si besoin.
    probe = original_probe = ffprobe(media)
    ext = media.suffix.lower().lstrip(".")
    transcoded = False
    upload = media
    if kind == "music" and exists and exists(folder, f"{stem}.{ext}"):
        raise Refused(f"{folder}/{stem}.{ext} existe déjà : rien n'a été traité ni envoyé")
    if kind != "music" and not args.no_transcode:
        reasons, french = plan_transcode(probe, ext)
        if exists and exists(folder, f"{stem}.{'mp4' if reasons else ext}"):
            raise Refused(f"{folder}/{stem}.{'mp4' if reasons else ext} existe déjà : rien n'a été traité ni envoyé")
        if reasons:
            say(f"réencodage H.264/AAC ({', '.join(reasons)})…")
            upload = work / f"{stem}.mp4"
            run(ffmpeg_args(media, upload, french, sum(1 for s in probe.get("streams", []) if s.get("codec_type") == "audio")), 4 * 3600)
            probe, ext, transcoded = ffprobe(upload), "mp4", True
    final_name = f"{stem}.{ext}"
    staged = work / "out"
    staged.mkdir(exist_ok=True)
    target = staged / final_name
    shutil.copy2(upload, target)
    sha, size = sha256_file(target)
    if size > MAX_BYTES:
        raise Refused(f"{size >> 20} Mo après traitement : la station refuse plus de {MAX_BYTES >> 20} Mo")
    technical = technical_of(probe, size, sha, transcoded)

    # Images et sous-titres.
    files = {final_name: target}
    for role, path in list(artwork.items()):
        name = f"{stem}.{role}.jpg"
        try:
            download(f"{TMDB_IMG}{path}", staged / name)
            files[name], artwork[role] = staged / name, name
        except OSError as e:
            say(f"image {role} ignorée : {e}")
            del artwork[role]
    subs = []
    if kind != "music":
        subs = extract_text_subtitles(original_probe, media, staged, stem)
        for name, lang in sub_files.items():
            new = f"{stem}.{lang}.web.vtt"
            shutil.copy2(work / name, staged / new)
            subs.append({"file": new, "lang": lang, "origin": "platform"})
        for s in subs:
            files[s["file"]] = staged / s["file"]

    rights = args.rights or default_rights(source_kind)
    source = {"kind": source_kind, "rights": rights, "acquired_at": int(time.time())}
    if web.get("url"):
        source["url"] = web["url"]
    if web.get("uploader"):
        source["uploader"] = web["uploader"]
    if source_kind == "web" and web.get("licence"):
        source["licence"] = web["licence"]
    fiche = build_fiche(kind, fields, technical, source, {"level": args.level}, final_name, artwork or None, subs or None, series, music)
    fiche_path = staged / f"{stem}.media.json"
    tmp = fiche_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(fiche, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, fiche_path)  # atomique : jamais une fiche à moitié écrite
    return fiche, files, stem, fiche_path, folder


def journal(entry):
    try:
        JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        with open(JOURNAL, "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": int(time.time()), **entry}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def pick_account(cfg, wanted):
    accounts = cfg.get("accounts") or {}
    email = wanted or (next(iter(accounts)) if len(accounts) == 1 else None)
    if not email or email not in accounts:
        raise Refused("compte inconnu : précisez --account parmi " + (", ".join(accounts) or "(aucun : lancez setup.sh)"))
    return email, accounts[email]


def secret_of(cfg, email):
    try:
        return nl.load_secret(account_dir(cfg, email) / ".secret.nostr")
    except ValueError as e:
        raise Refused(str(e))


def attest(cfg, email):
    """Attestation de copie privée : posée **automatiquement** au premier dépôt `private_copy`. Locale (fichier `legal` du
    compte, comme le faisait ajouter_media.sh) : rien n'est publié, la station n'en lit rien."""
    legal = account_dir(cfg, email) / "legal"
    if legal.is_file():
        return False
    seckey = secret_of(cfg, email)
    legal.parent.mkdir(parents=True, exist_ok=True)
    legal.write_text(nl.pubkey(seckey).hex() + "\n", encoding="utf-8")
    say("copie privée attestée pour ce compte : " + CONSENT_TEXT)
    return True


def main(argv=None):
    args = parser().parse_args(argv)
    work = Path(tempfile.mkdtemp(prefix="media-add-"))
    try:
        if args.attest:
            cfg = load_config()
            email, _ = pick_account(cfg, args.account)
            attest(cfg, email)
            print(json.dumps({"attested": email}, ensure_ascii=False))
            return 0
        if not args.source:
            raise Refused("indiquez un fichier ou une adresse web (ou --attest)")
        cfg = {} if args.dry_run else load_config()
        email = token = None
        if not args.dry_run:
            email, token = pick_account(cfg, args.account)
        dav = None if args.dry_run else Dav(cfg.get("station_url", ""), email, token, cfg.get("mount", "/dav"))
        exists = (lambda folder, name: dav.etag(f"{folder}/{name}") is not None) if dav else None
        fiche, files, stem, fiche_path, folder = prepare(args, work, exists=exists)
        needs_event = args.level == "circle" and fiche["type"] in ("film", "episode", "video") and not args.no_event and "video" in fiche["technical"]
        if args.dry_run:
            out = Path(args.dry_run)
            out.mkdir(parents=True, exist_ok=True)
            for name, p in files.items():
                shutil.copy2(p, out / name)
            shutil.copy2(fiche_path, out / fiche_path.name)
            report = {"dry_run": str(out), "folder": folder, "fiche": fiche_path.name, "files": sorted(files)}
            if needs_event:
                kind, tags = video_event_tags(fiche, "bafkrei-dry-run")
                report["event"] = {"kind": kind, "tags": tags, "content": fiche.get("overview") or ""}
            print(json.dumps(report, ensure_ascii=False))
            return 0
        if fiche["source"]["rights"] == "private_copy":
            attest(cfg, email)
        seckey = secret_of(cfg, email) if needs_event else None
        cids = deposit(dav, folder, files, fiche_path)
        result = {"deposited": f"{folder}/{fiche['file']}", "account": email, "event": None}
        if needs_event:
            cid = cids.get(fiche["file"])
            if not cid:
                say("la station n'a pas rendu l'empreinte (CID) du fichier : événement vidéo non publié")
            else:
                kind, tags = video_event_tags(fiche, cid)
                event = nl.sign_event(seckey, kind, tags, fiche.get("overview") or "")
                try:
                    ok, why = nl.publish(relay_of(cfg), event, seckey)
                    result["event"] = {"id": event["id"], "kind": kind, "accepted": ok, "relay": why}
                    if not ok:
                        say(f"le relai refuse l'événement vidéo : {why}")
                except nl.RelayError as e:
                    say(f"événement vidéo non publié : {e}")
        journal({"account": email, "title": fiche["title"], "type": fiche["type"], "path": result["deposited"], "event": result["event"]})
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Refused as e:
        journal({"error": str(e)})
        say(f"media-add : {e}")
        return 2
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
