# corpus — jeu d'essai pour vérifier la recherche

Plugin **facultatif** : il ne s'installe jamais tout seul (ni par `--all`, ni comme nouveauté). On le demande par son nom :

```bash
./install-plugins.sh corpus        # ou : cd corpus && ./install.sh
```

Il fabrique une quarantaine de fichiers qui exercent chaque voie de la recherche d'une station astroportd, puis propose de
les déposer dans votre uCloud (`/Essais/Recherche`). `LISEZMOI.md`, dans le corpus, dit quoi taper et ce qui doit sortir.

| Contenu | Ce qu'il vérifie |
|---|---|
| 10 photos libres de Wikimedia Commons (chat, plage, tour Eiffel…), dates et lieux GPS **fictifs** | recherche par l'image, lieux, dates |
| Portraits libres de personnalités (2 ou 3 chacune, dont des quasi-doublons) | FaceCloud (regroupement, nom), Doublons |
| Panneau de boulangerie photographié, ticket de caisse | texte lu dans les photos (OCR) |
| Facture et ordonnance en PDF, attestation scannée (PDF sans texte) | texte des PDF, OCR d'un PDF |
| Notes `.txt` et `.md` | texte des documents |
| Deux vidéos (diaporama plage → montagne → Paris, titre à l'écran) | images clés des vidéos |

Auteurs et licences des photos : `CREDITS.txt` du corpus (CC0, domaine public, CC BY, CC BY-SA ; jamais NC ni ND).

## Installer

```bash
./install.sh                       # vérifie les outils, prépare Python, fabrique le corpus, propose l'envoi [o/N]
CORPUS_DIR=/autre/dossier ./install.sh
CORPUS_NO_SEND=1 ./install.sh      # sans question
```

- Outils du système : `python3`, `exiftool` (paquet `libimage-exiftool-perl`), `ffmpeg` avec libx264, une police Noto ou
  DejaVu. Ce qui manque est signalé avec la commande `apt` à lancer ; rien n'est installé avec sudo.
- Python : celui d'astroportd (`~/.astro/bin/python`) ou du système s'il a Pillow et ReportLab ; sinon un environnement
  propre au plugin (`<station>/plugins/corpus/venv`).
- Le corpus va dans `~/.zen/tmp/corpus-recherche`. Les photos téléchargées sont gardées dans `corpus-recherche.cache` :
  Commons n'est interrogé qu'une fois, poliment (une requête à la fois, attente quand il demande de ralentir).

## Déposer dans votre uCloud

```bash
./setup.sh                         # adresse et compte déduits de la station (station.conf), jeton demandé sans écho
./setup.sh --dav https://station.exemple.org/dav --login vous@exemple.org --dest /Essais/Recherche
```

Le jeton d'appareil (Mon compte › Appareils) est lu sans écho et passé au script par l'environnement (`ASTRO_TOKEN`) :
jamais en argument de commande, jamais écrit sur le disque. Chaque envoi est noté (adresse, compte, dossier, nombre de
fichiers, sans le jeton) dans `<station>/plugins/corpus/journal.jsonl`.

## Contrat

Aucun : `produces = ""`. Le corpus est fait de fichiers ordinaires ; la station ne lit rien de ce plugin.

## État, retrait

```bash
./status.sh                        # {"plugin":"corpus","ok":…,"tools":{…},"corpus":{"files":n,…},"last_sent":{…}}
./uninstall.sh [--purge]           # --purge : corpus, cache des photos et journal
```

Ce qui a été déposé dans un uCloud y reste : supprimez `/Essais/Recherche` depuis Fichiers.
