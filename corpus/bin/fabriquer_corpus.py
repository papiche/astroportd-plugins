#!/usr/bin/env python3
"""Fabrique un jeu de fichiers pour essayer l'indexation multimédia et la recherche d'une vraie station.

    python3 fabriquer_corpus.py [dossier]        # défaut : ~/.zen/tmp/corpus-recherche
    read -rs ASTRO_TOKEN && export ASTRO_TOKEN      # jeton d'appareil, tapé sans écho
    ASTRO_DAV=https://station/dav ASTRO_LOGIN=moi@exemple.org \\
        python3 fabriquer_corpus.py [dossier] --envoyer /Essais/Recherche [--seulement-envoyer]

Chaque fichier vise une voie de la recherche (`src/search_query.rs`, `src/library.rs`) :
- vision (SigLIP, `search_vision_vectors`) : de vraies photos sous licence libre, prises sur Wikimedia Commons (catégorie
  « Quality images », licences CC0, domaine public, CC BY, CC BY-SA ; auteurs et licences dans CREDITS.txt), et une vidéo dont
  les images clés montrent la plage, la montagne et Paris (`search_frames`, `t_ms`) ;
- texte lu (OCR, `search_text`) : un panneau photographié, un ticket de caisse, une attestation scannée (PDF sans texte) ;
- texte des documents : facture et ordonnance en PDF avec couche texte, notes .txt et .md ;
- lieux et dates : EXIF DateTimeOriginal et GPS **fictifs**, posés pour l'essai (villes de plus de 15 000 habitants, comme
  l'index embarqué) ; ceux d'origine sont retirés.
LISEZMOI.md dit quoi chercher et ce qui doit sortir. Le jeton n'est jamais passé en argument : il vient de ASTRO_TOKEN.
Les fichiers ne sont pas versionnés : seul ce script l'est. Les photos téléchargées sont gardées dans <dossier>.cache/ pour ne
les demander qu'une fois.
"""
import base64, html, json, os, re, shutil, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

ARGS = sys.argv[1:]
DEST = ARGS.pop(ARGS.index('--envoyer') + 1) if '--envoyer' in ARGS and ARGS.index('--envoyer') + 1 < len(ARGS) else None
OUT = Path(next((a for a in ARGS if not a.startswith('--')), Path.home() / '.zen/tmp/corpus-recherche')).expanduser()
CACHE = OUT.with_name(OUT.name + '.cache')
UA = 'astroportd-corpus/1.0 (jeu de test de la recherche)'
FREE = ('CC0', 'Public domain', 'PD', 'CC BY')  # CC BY et CC BY-SA ; jamais NC ni ND


def first_font(*names):
    """Première police présente : Noto (fonts-noto-core), sinon DejaVu (installée presque partout)."""
    for n in names:
        for d in ('/usr/share/fonts/truetype/noto', '/usr/share/fonts/truetype/dejavu', '/usr/share/fonts/TTF', '/usr/share/fonts/dejavu'):
            if os.path.exists(f'{d}/{n}'): return f'{d}/{n}'
    sys.exit(f'aucune police trouvée parmi {names} : sudo apt install fonts-noto-core (ou fonts-dejavu-core)')


if '--seulement-envoyer' not in ARGS:  # l'envoi seul n'a besoin ni de Pillow ni des polices
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    FONT = first_font('NotoSans-Regular.ttf', 'DejaVuSans.ttf')
    BOLD = first_font('NotoSans-Bold.ttf', 'DejaVuSans-Bold.ttf')
    MONO = first_font('NotoSansMono-Regular.ttf', 'DejaVuSansMono.ttf')
def font(size, f=None): return ImageFont.truetype(f or FONT, size)

# Photos : [recherche sur Commons, nom, date, (lat, lon, ville) ou None]. Dates et lieux fictifs, pour l'essai.
PHOTOS = [
    ('cat portrait', 'chat.jpg', '2024:03:10 16:20:00', (47.2184, -1.5536, 'Nantes')),
    ('dog running grass', 'chien.jpg', '2024:05:18 11:00:00', (48.1173, -1.6778, 'Rennes')),
    ('sandy beach sea', 'plage.jpg', '2024:07:20 15:42:00', (43.2965, 5.3698, 'Marseille')),
    ('snowy mountains Alps', 'montagnes-enneigees.jpg', '2025:01:20 10:15:00', (45.1885, 5.7245, 'Grenoble')),
    ('Eiffel Tower Paris', 'tour-eiffel.jpg', '2023:05:02 11:30:00', (48.8584, 2.2945, 'Paris')),
    ('bicycle parked street', 'velo.jpg', '2024:09:01 09:00:00', (48.5734, 7.7521, 'Strasbourg')),
    ('pizza margherita', 'pizza.jpg', '2024:07:14 20:30:00', (45.7640, 4.8357, 'Lyon')),
    ('sunset over the sea', 'coucher-de-soleil.jpg', '2024:07:21 21:30:00', (43.2965, 5.3698, 'Marseille')),
    ('red car', 'voiture-rouge.jpg', '2025:03:03 10:00:00', None),
    ('decorated Christmas tree with baubles', 'sapin-de-noel.jpg', '2024:12:24 20:12:00', (48.5734, 7.7521, 'Strasbourg')),
]
CREDITS = []


def fetch(url):
    """GET poli envers Commons : une requête à la fois, et on attend quand il demande de ralentir (429)."""
    for wait in (2, 5, 15, 30, 60):
        time.sleep(1)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=120) as r: return r.read()
        except urllib.error.HTTPError as e:
            if e.code not in (429, 503): raise
            print(f'Commons demande de ralentir ({url[:90]}) : nouvel essai dans {wait} s'); time.sleep(wait)
    sys.exit('Commons refuse encore : réessayez plus tard (les photos déjà reçues sont gardées dans le cache)')

def commons(query, n=1, quality=True):
    """Les `n` premières photos de Commons sous licence libre pour `query` : [(adresse, titre, auteur, licence)]."""
    pages = []
    if query.startswith('File:'):  # des fichiers désignés par leur titre (« File:A.jpg|File:B.jpg »), petits : vignette standard de 330 px
        q = urllib.parse.urlencode({'action': 'query', 'format': 'json', 'titles': query, 'prop': 'imageinfo', 'iiprop': 'url|extmetadata|mime', 'iiurlwidth': 330})
        pages = list(json.loads(fetch('https://commons.wikimedia.org/w/api.php?' + q)).get('query', {}).get('pages', {}).values())
        pages.sort(key=lambda p: query.split('|').index(p['title']) if p['title'] in query.split('|') else 99)
    for where in () if query.startswith('File:') else ((' incategory:Quality_images', '') if quality else ('',)):  # les photos de qualité d'abord, sinon tout Commons
        q = urllib.parse.urlencode({'action': 'query', 'format': 'json', 'generator': 'search', 'gsrnamespace': 6, 'gsrlimit': 10,
                                    'gsrsearch': f'{query} filetype:bitmap{where}', 'prop': 'imageinfo', 'iiprop': 'url|extmetadata|mime', 'iiurlwidth': 960})  # largeur standard : servie depuis le cache de Commons
        pages += sorted(json.loads(fetch('https://commons.wikimedia.org/w/api.php?' + q)).get('query', {}).get('pages', {}).values(), key=lambda p: p.get('index', 99))
    found, seen = [], set()
    for p in pages:
        if p['title'] in seen: continue
        seen.add(p['title'])
        ii = p['imageinfo'][0]; m = ii.get('extmetadata', {})
        lic = m.get('LicenseShortName', {}).get('value', '')
        if ii.get('mime') != 'image/jpeg' or not lic.startswith(FREE) or re.search(r'\b(NC|ND)\b', lic): continue
        if '/thumb/' not in ii.get('thumburl', ''): continue  # trop petite : Commons sert l'original, qu'il limite fortement
        artist = html.unescape(re.sub('<[^>]+>', '', m.get('Artist', {}).get('value', '?'))).strip()
        found.append((ii['thumburl'], p['title'].removeprefix('File:'), artist, lic))
        if len(found) == n: return found
    return found or sys.exit(f'aucune photo libre trouvée pour « {query} »')


def cached(name, pick):
    """Le fichier `name` du cache, téléchargé une fois d'après `pick()` → (adresse, titre, auteur, licence)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    raw, meta = CACHE / name, CACHE / (name + '.json')
    if not raw.exists():
        got = pick()
        if got is None: return None, None
        url, title, artist, lic = got
        raw.write_bytes(fetch(url))
        meta.write_text(json.dumps(dict(title=title, artist=artist, license=lic, url=url), ensure_ascii=False))
    return raw, json.loads(meta.read_text())


def photo(query, name, when, where):
    raw, info = cached(name, lambda: commons(query)[0])
    CREDITS.append(f"- {name} : « {info['title']} », {info['artist']}, {info['license']} — https://commons.wikimedia.org/wiki/File:{urllib.parse.quote(info['title'].replace(' ', '_'))}\n")
    im = Image.open(raw).convert('RGB'); im.thumbnail((1600, 1600))
    dst = OUT / 'Photos' / name; im.save(dst, quality=88)  # réenregistrée : les métadonnées d'origine (lieu, appareil) partent
    args = ['exiftool', '-q', '-overwrite_original', f'-DateTimeOriginal={when}', f'-CreateDate={when}', '-Model=Corpus Astroport']
    if where:
        lat, lon, _ = where
        args += [f'-GPSLatitude={abs(lat)}', f'-GPSLatitudeRef={"N" if lat >= 0 else "S"}', f'-GPSLongitude={abs(lon)}', f'-GPSLongitudeRef={"E" if lon >= 0 else "W"}']
    subprocess.run(args + [str(dst)], check=True)


# Visages : quelques personnalités, plusieurs photos chacune, pour que FaceCloud les regroupe et qu'on les nomme une fois.
# Les photos libres (souvent dans le domaine public : portraits anciens, photos officielles) ; aucun lieu, des dates fictives.
# Plusieurs photos presque identiques d'une même personne exercent aussi Photos › Doublons. Pas de personnage de dessin animé :
# aucun n'est sous licence libre (Homer Simpson appartient à son studio).
FACES = [('Albert Einstein portrait photograph', 'albert-einstein', 3), ('Marie Curie portrait photograph', 'marie-curie', 3),
         ('Grace Hopper portrait', 'grace-hopper', 2), ('John Horton Conway mathematician', 'john-conway', 2),
         ('File:Alan Turing Aged 16.jpg|File:Alan Turing az 1930-as években.jpg', 'alan-turing', 2), ('Katherine Johnson NASA mathematician', 'katherine-johnson', 2),
         ('Barack Obama official photograph', 'barack-obama', 3), ('Thomas Pesquet astronaut', 'thomas-pesquet', 2),
         ('Charlie Chaplin portrait', 'charlie-chaplin', 2), ('Marilyn Monroe publicity photo', 'marilyn-monroe', 2)]


def faces():
    (OUT / 'Photos' / 'Personnes').mkdir(parents=True, exist_ok=True)
    for query, who, n in FACES:
        picks = None
        for i in range(n):
            def pick(i=i):
                nonlocal picks
                picks = picks or commons(query, n, quality=False)
                return picks[i] if i < len(picks) else None
            name = f'{who}-{i + 1}.jpg'
            if not (CACHE / name).exists() and i and picks is not None and i >= len(picks):
                print(f'{who} : {i} photo(s) libre(s) seulement'); break
            raw, info = cached(name, pick)
            if raw is None: print(f'{who} : {i} photo(s) libre(s) seulement'); break
            CREDITS.append(f"- Personnes/{name} : « {info['title']} », {info['artist']}, {info['license']} — https://commons.wikimedia.org/wiki/File:{urllib.parse.quote(info['title'].replace(' ', '_'))}\n")
            im = Image.open(raw).convert('RGB'); im.thumbnail((1600, 1600))
            dst = OUT / 'Photos' / 'Personnes' / name; im.save(dst, quality=88)
            subprocess.run(['exiftool', '-q', '-overwrite_original', f'-DateTimeOriginal=2025:0{i + 4}:1{i} 12:00:00', '-Model=Corpus Astroport', str(dst)], check=True)


def sign():
    """Un panneau de boutique photographié de biais : l'OCR doit lire « boulangerie » et les horaires."""
    im = Image.new('RGB', (1400, 900), (96, 120, 140)); d = ImageDraw.Draw(im)
    d.rounded_rectangle((170, 170, 1230, 700), 30, fill=(250, 244, 230), outline=(90, 60, 30), width=14)
    d.text((700, 300), 'BOULANGERIE DU PORT', font=font(84, BOLD), fill=(90, 50, 20), anchor='mm')
    d.text((700, 420), 'Pain au levain · Viennoiseries', font=font(52), fill=(60, 40, 20), anchor='mm')
    d.text((700, 540), 'Ouvert de 7 h à 19 h, fermé le lundi', font=font(46), fill=(60, 40, 20), anchor='mm')
    im = im.rotate(4, resample=Image.BICUBIC, fillcolor=(96, 120, 140)).filter(ImageFilter.GaussianBlur(0.8))
    im.save(OUT / 'Photos' / 'panneau-boulangerie.jpg', quality=85)
    subprocess.run(['exiftool', '-q', '-overwrite_original', '-DateTimeOriginal=2024:07:21 08:10:00', '-GPSLatitude=43.2951', '-GPSLatitudeRef=N', '-GPSLongitude=5.3745', '-GPSLongitudeRef=E', str(OUT / 'Photos' / 'panneau-boulangerie.jpg')], check=True)


def receipt():
    """Un ticket de caisse en photo (texte mono, papier grisé) : « Comté », « total », un montant."""
    lines = ['SUPERETTE DES LILAS', '12 rue des Lilas, 44000 Nantes', '', '21/06/2025  18:47', '', 'Pain de campagne      3,20', 'Comté 18 mois         6,85',
             'Tomates anciennes     4,10', 'Huile d’olive         9,90', '', 'TOTAL EUR            24,05', 'Carte bancaire', '', 'Merci de votre visite !']
    im = Image.new('RGB', (760, 1180), (238, 236, 228)); d = ImageDraw.Draw(im)
    for i, l in enumerate(lines): d.text((60, 70 + i * 72), l, font=font(38, MONO), fill=(40, 40, 40))
    im.rotate(-2, resample=Image.BICUBIC, fillcolor=(180, 170, 150)).save(OUT / 'Photos' / 'ticket-de-caisse.jpg', quality=82)


def pdfs():
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas
    pdfmetrics.registerFont(TTFont('Noto', FONT)); pdfmetrics.registerFont(TTFont('NotoB', BOLD))
    def doc(name, title, rows):
        c = canvas.Canvas(str(OUT / 'Documents' / name), pagesize=A4); c.setTitle(title)
        y = 780
        for size, bold, text in rows:
            c.setFont('NotoB' if bold else 'Noto', size); c.drawString(60, y, text); y -= size + 12
        c.save()
    doc('facture-electricite-avril-2025.pdf', 'Facture électricité avril 2025', [
        (20, True, 'Enercoop — Facture n° 2025-0412'), (12, False, 'Date : 30 avril 2025'), (12, False, 'Client : Alice Martin, 12 rue des Lilas, 44000 Nantes'),
        (12, False, ''), (14, True, 'Électricité 100 % renouvelable'), (12, False, 'Consommation du 1er au 30 avril : 212 kWh'),
        (12, False, 'Abonnement : 14,52 €'), (12, False, 'Énergie : 69,78 €'), (14, True, 'Total à payer : 84,30 € TTC'), (12, False, 'Prélèvement le 10 mai 2025')])
    doc('ordonnance-dr-martin.pdf', 'Ordonnance', [
        (18, True, 'Docteur Claire Martin — Médecin généraliste'), (12, False, '3 place Bellecour, 69002 Lyon'), (12, False, 'Lyon, le 4 mars 2025'),
        (12, False, ''), (12, False, 'Mme Alice Martin'), (13, False, 'Doliprane 1000 mg : 1 comprimé jusqu’à 3 fois par jour, 5 jours.'),
        (13, False, 'Sérum physiologique : lavage du nez matin et soir.'), (12, False, ''), (12, False, 'Signature')])
    # Attestation scannée : une image dans un PDF, sans couche texte. Seul l'OCR peut la lire.
    im = Image.new('RGB', (1240, 1754), (250, 250, 247)); d = ImageDraw.Draw(im)
    for i, (s, b, t) in enumerate([(54, True, 'ATTESTATION D’ASSURANCE HABITATION'), (34, False, 'Mutuelle des Coopérateurs'), (34, False, 'Contrat n° MH-77821'),
                                    (34, False, 'Assurée : Alice Martin'), (34, False, 'Valable du 1er janvier au 31 décembre 2025'), (34, False, 'Garanties : incendie, dégât des eaux, vol')]):
        d.text((110, 180 + i * 110), t, font=font(s, BOLD if b else FONT), fill=(30, 30, 30))
    im.rotate(0.7, resample=Image.BICUBIC, fillcolor=(250, 250, 247)).filter(ImageFilter.GaussianBlur(0.6)).save(OUT / 'Documents' / 'attestation-assurance-scannee.pdf', resolution=150)


def texts():
    (OUT / 'Documents' / 'recette-crepes.txt').write_text('Recette des crêpes de Mamie\n\n250 g de farine de blé noir, 4 œufs, 50 cl de lait, une pincée de sel.\nLaisser reposer la pâte une heure. Cuire dans une poêle bien chaude, beurre salé.\n', encoding='utf-8')
    (OUT / 'Documents' / 'compte-rendu-reunion.md').write_text('# Compte rendu — réunion de la coopérative\n\n12 septembre 2025, salle des fêtes de Nantes.\n\n- Achat d’un deuxième panneau solaire pour la station : voté à l’unanimité.\n- Le potager partagé ouvre le samedi matin.\n- Prochaine réunion : 10 octobre.\n', encoding='utf-8')
    (OUT / 'Documents' / 'liste-de-courses.txt').write_text('Liste de courses\n- comté\n- tomates\n- huile d’olive\n- piles pour la lampe frontale\n', encoding='utf-8')


def video():
    """Diaporama de vacances : la plage (0–4 s), la montagne (4–8 s), Paris (8–12 s). Les images clés portent la recherche par l'image."""
    parts = [OUT / 'Photos' / n for n in ('plage.jpg', 'montagnes-enneigees.jpg', 'tour-eiffel.jpg')]
    inputs = sum((['-loop', '1', '-t', '4', '-i', str(p)] for p in parts), [])
    flt = ';'.join(f'[{i}:v]scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,setsar=1,fps=25[v{i}]' for i in range(3)) + ';[v0][v1][v2]concat=n=3:v=1:a=0,format=yuv420p[v]'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', *inputs, '-filter_complex', flt, '-map', '[v]', '-c:v', 'libx264', '-crf', '28', '-metadata', 'title=Diaporama des vacances',
                    '-metadata', 'creation_time=2024-08-15T10:00:00Z', str(OUT / 'Videos' / 'diaporama-vacances.mp4')], check=True)
    # Une vidéo dont le texte est à l'écran (titre de réunion) : la recherche du texte lu dans les images clés.
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=0x1d3557:s=1280x720:d=6', '-vf',
                    f"drawtext=fontfile={BOLD}:text='Assemblée de la coopérative':fontcolor=white:fontsize=64:x=(w-text_w)/2:y=(h-text_h)/2",
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-metadata', 'creation_time=2025-09-12T18:00:00Z', str(OUT / 'Videos' / 'assemblee-cooperative.mp4')], check=True)


README = """# Corpus pour essayer la recherche

Déposez le dossier dans votre uCloud (Fichiers › Ajouter, ou glissez-le), attendez l'analyse (Moi › Appareils › « Documents et
photos cherchables » pour le texte lu), puis cherchez dans Fichiers. Les dates et les lieux des photos sont fictifs, posés pour
l'essai. Ce qui doit sortir :

| Tapez | Doit trouver | Voie |
|---|---|---|
| `chat` | chat.jpg | image (vision) |
| `chien` | chien.jpg | image |
| `plage` ou `mer` | plage.jpg, coucher-de-soleil.jpg, diaporama-vacances.mp4 (au début) | image, image clé de vidéo |
| `montagne` ou `neige` | montagnes-enneigees.jpg, diaporama-vacances.mp4 (vers 4 s) | image, image clé |
| `tour Eiffel` | tour-eiffel.jpg, diaporama-vacances.mp4 (vers 8 s) | image, image clé |
| `vélo` | velo.jpg | image |
| `pizza` | pizza.jpg | image |
| `voiture rouge` | voiture-rouge.jpg (sans lieu) | image |
| `noël` | sapin-de-noel.jpg | image et nom |
| `Lyon` | pizza.jpg, ordonnance-dr-martin.pdf | lieu (GPS) et texte du PDF |
| `Marseille` | plage.jpg, coucher-de-soleil.jpg, panneau-boulangerie.jpg | lieu (GPS) |
| `juillet 2024` | plage.jpg, pizza.jpg, coucher-de-soleil.jpg, panneau-boulangerie.jpg | date de prise de vue |
| `boulangerie` | panneau-boulangerie.jpg | texte lu dans la photo (OCR) |
| `comté` | ticket-de-caisse.jpg, liste-de-courses.txt | OCR et texte |
| `facture` ou `84,30` | facture-electricite-avril-2025.pdf | texte du PDF |
| `Doliprane` | ordonnance-dr-martin.pdf | texte du PDF |
| `assurance` | attestation-assurance-scannee.pdf | OCR d'un PDF sans texte |
| `crêpes` | recette-crepes.txt | texte |
| `panneau solaire` | compte-rendu-reunion.md | texte |
| `assemblée` | assemblee-cooperative.mp4 | texte lu dans l'image clé |
| (FaceCloud) | Personnes/ : Einstein, Marie Curie, Grace Hopper, John Conway, Alan Turing, Katherine Johnson, Barack Obama, Thomas Pesquet, Charlie Chaplin, Marilyn Monroe (2 ou 3 photos chacun) | visages regroupés par personne ; nommez-en un, les autres suivent |
| (Photos › Doublons) | albert-einstein-1 et -3, marie-curie-1 et -3 | deux tirages presque identiques |
| `Einstein` (après l'avoir nommé) | les 3 photos d'Einstein | personne reconnue |
| `girafe` | rien de sûr : « Peut-être » ou aucun résultat | contrôle négatif |

Photos : Wikimedia Commons, licences libres (auteurs et licences dans CREDITS.txt). Le reste est fabriqué par le plugin
`corpus` d'astroportd-plugins (`corpus/bin/fabriquer_corpus.py`).
"""


def send(base, dest):
    """Envoi WebDAV (MKCOL puis PUT) avec le jeton de ASTRO_TOKEN : jamais en argument de commande."""
    login, token = os.environ.get('ASTRO_LOGIN'), os.environ.get('ASTRO_TOKEN')
    if not (login and token): sys.exit('ASTRO_LOGIN et ASTRO_TOKEN sont nécessaires pour --envoyer')
    auth = 'Basic ' + base64.b64encode(f'{login}:{token}'.encode()).decode()
    def call(method, path, data=None):
        url = base.rstrip('/') + urllib.parse.quote(path)
        req = urllib.request.Request(url, data=data, method=method, headers={'Authorization': auth})
        try: urllib.request.urlopen(req, timeout=120).close()
        except urllib.error.HTTPError as e:
            if not (method == 'MKCOL' and e.code == 405): raise  # 405 : le dossier existe déjà
    parts = [p for p in dest.split('/') if p]
    for i in range(1, len(parts) + 1): call('MKCOL', '/' + '/'.join(parts[:i]) + '/')
    for f in sorted(OUT.rglob('*')):
        rel = f.relative_to(OUT).as_posix()
        if f.is_dir(): call('MKCOL', f'{dest.rstrip("/")}/{rel}/')
        else: call('PUT', f'{dest.rstrip("/")}/{rel}', f.read_bytes()); print('envoyé', rel)


if __name__ == '__main__':
    if '--seulement-envoyer' in ARGS:  # le corpus est déjà là : on l'envoie tel quel, sans rien refabriquer
        if not (OUT / 'LISEZMOI.md').exists(): sys.exit(f'pas de corpus dans {OUT} : fabriquez-le d\'abord (sans --seulement-envoyer)')
        send(os.environ.get('ASTRO_DAV') or sys.exit('ASTRO_DAV est nécessaire (https://station/dav)'), DEST or sys.exit('--envoyer attend un dossier du uCloud, par exemple /Essais/Recherche'))
        sys.exit(0)
    if OUT.exists(): shutil.rmtree(OUT)
    for d in ('Photos', 'Documents', 'Videos'): (OUT / d).mkdir(parents=True)
    for p in PHOTOS: photo(*p)
    faces(); sign(); receipt(); pdfs(); texts(); video()
    (OUT / 'LISEZMOI.md').write_text(README, encoding='utf-8')
    (OUT / 'CREDITS.txt').write_text('Photos de Wikimedia Commons, sous licence libre (réduites, métadonnées remplacées par des dates et lieux fictifs) :\n' + ''.join(CREDITS), encoding='utf-8')
    n = sum(1 for f in OUT.rglob('*') if f.is_file())
    print(f'{n} fichiers dans {OUT}')
    if '--envoyer' in sys.argv:
        send(os.environ.get('ASTRO_DAV') or sys.exit('ASTRO_DAV est nécessaire (https://station/dav)'), DEST or sys.exit('--envoyer attend un dossier du uCloud, par exemple /Essais/Recherche'))
