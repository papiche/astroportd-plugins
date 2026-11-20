"""Mix de DJ : tracklist d'une vidéo YouTube (chapitres, sinon repères de la description) et choix du morceau à télécharger.

Repris d'Astroport.ONE/tools/youtube_dj_playlist_downloader.py (2.1.0) ; ici, chaque morceau garde **son repère dans le mix**
(`start_s`, contrat media/v1 `music.from_mix`) et rien ne touche au réseau : `media_add` appelle yt-dlp et passe ses réponses.
"""
import re

# Lignes de description à ignorer même si elles portent un repère (réseaux sociaux, liens, mentions génériques).
NOISE_KEYWORDS = (
    "http://", "https://", "subscribe", "follow me", "instagram.com",
    "facebook.com", "twitter.com", "soundcloud.com", "spotify.com",
    "patreon", "youtube.com/", "bit.ly", "linktr.ee",
)
# Mots trop communs pour compter dans la comparaison requête / résultat.
STOPWORDS = {
    "the", "a", "an", "of", "is", "in", "on", "at", "to", "and", "or",
    "feat", "ft", "featuring", "vs", "with", "for", "de", "la", "le",
}
TIMESTAMP = re.compile(r"(?:\[|\()?(\d{1,2}:\d{2}(?::\d{2})?)(?:\]|\))?")


def seconds(ts):
    """`1:02:58` ou `14:35` → secondes."""
    n = 0
    for part in ts.split(":"):
        n = n * 60 + int(part)
    return n


def clean_track_line(line, ts=None):
    """Ne garde d'une ligne (description ou chapitre) que « Artiste - Titre »."""
    s = line.replace(ts, "").strip() if ts else line
    s = re.sub(r"^[^\w\s]+", "", s).strip()  # tirets, puces, points de début
    s = re.sub(r"^\d+[\)\.\-]+\s*", "", s).strip()  # numéro de piste « 02. » (exige une ponctuation : « 1 year » reste)
    s = re.sub(r"\s*@[a-zA-Z0-9_]+", "", s).strip()  # @pseudos
    s = re.sub(r"\s*\([^)]*\b(records|recording|music|label|rec|mix|remix|dub|edit|rework)\b[^)]*\)", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"\s*\[[^\]]*\b(records|recording|music|label|rec)\b[^\]]*\]", "", s, flags=re.IGNORECASE).strip()
    return s


def extract_tracklist(description, chapters=None):
    """Morceaux du mix, dans l'ordre : [(« Artiste - Titre », début en secondes ou None)].

    Les chapitres YouTube (`[{"title", "start_time"}]`) d'abord, plus fiables que le texte libre ; sinon les lignes de la
    description qui portent un repère ; sinon celles de forme « Artiste - Titre » (début inconnu).
    """
    tracks = []
    for c in chapters or []:
        line = clean_track_line(c.get("title") or "")
        if len(line) > 3:
            start = c.get("start_time")
            tracks.append((line, int(start) if isinstance(start, (int, float)) else None))
    lines = [l.strip() for l in (description or "").splitlines()]
    lines = [l for l in lines if l and not any(k in l.lower() for k in NOISE_KEYWORDS)]
    if not tracks:
        for l in lines:
            m = TIMESTAMP.search(l)
            if m:
                line = clean_track_line(l, m.group(0))
                if len(line) > 3:
                    tracks.append((line, seconds(m.group(1))))
    if not tracks:
        for l in lines:
            if " - " in l or " – " in l:
                line = clean_track_line(l)
                if len(line) > 5:
                    tracks.append((line, None))
    seen, out = set(), []
    for line, start in tracks:
        if line.lower() not in seen:
            seen.add(line.lower())
            out.append((line, start))
    return out


def keywords(text):
    return {w for w in re.findall(r"[a-zA-Z0-9']+", text.lower()) if len(w) > 2 and w not in STOPWORDS}


def match_score(query, title):
    """Part des mots de la requête retrouvés dans le titre du candidat."""
    q = keywords(query)
    return len(q & keywords(title)) / len(q) if q else 0.0


def best_match(query, candidates):
    """Meilleur candidat `(score, id, titre)` parmi les réponses de recherche yt-dlp (`[{"id", "title"}]`), ou None."""
    scored = [(match_score(query, c["title"]), c["id"], c["title"]) for c in candidates if c.get("id") and c.get("title")]
    return max(scored, key=lambda c: c[0]) if scored else None


def split_artist_title(query):
    """« Artiste - Titre » → (artiste, titre), coupé au **premier** séparateur (« A – B - Remix » : artiste A) ;
    sans séparateur : (None, requête)."""
    cuts = [(query.index(sep), sep) for sep in (" - ", " – ") if sep in query]
    if not cuts:
        return None, query.strip()
    at, sep = min(cuts)
    return query[:at].strip() or None, query[at + len(sep):].strip()
