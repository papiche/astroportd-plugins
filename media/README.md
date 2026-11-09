# media — films, séries, vidéos et musiques

Remplace `Astroport.ONE/ajouter_media.sh`, et **tout se passe dans le plugin** : astroportd reste un serveur WebDAV qui
partage en P2P, N¹ ou N², il ne réagit à rien.

Le plugin sonde (`ffprobe`), réencode en H.264/AAC (`ffmpeg`), télécharge (`yt-dlp`), interroge le catalogue TMDB, extrait les
sous-titres, puis **écrit lui-même dans l'uCloud du membre**, par WebDAV et avec le jeton du membre :

| Nature | Dossier | Nom |
|---|---|---|
| film | `/Vidéos/Films` | `Titre (année).mp4` |
| épisode | `/Vidéos/Séries/<série>/Saison NN` | `<série> S01E02.mp4` |
| vidéo hors catalogue | `/Vidéos/Autres` | `Titre (année).mp4` |
| morceau | `/Musique/<artiste>/<album>` (`Singles` sans album) | `NN - Titre.mp3` |

À côté de chaque fichier : `<nom>.media.json` (la fiche, contrat [`media/v1`](../contracts/media-v1.schema.json), base de la
vidéothèque), `<nom>.poster.jpg` & co, et `<nom>.<langue>.vtt`. Avec `--level circle`, le plugin **signe l'événement vidéo
(NIP-71, kind 21 ou 22) avec la clé du membre et le publie sur le relai**, comme n'importe quel client Nostr.

## Installer

```bash
cd astroportd-plugins/media
./install.sh                                    # vérifie python3, ffmpeg, ffprobe ; avertit si yt-dlp ou libx264 manquent
./setup.sh --station https://ma-station.exemple.org --email vous@exemple.org     # jeton demandé, jamais affiché
./status.sh                                     # état en JSON
```

- **Jeton WebDAV** du compte : écrit dans `~/.zen/astroportd/plugins/media/config.json` en `0600`, jamais passé en argument.
  Plusieurs comptes possibles (`--account` choisit). Rien n'est installé dans le système : pas de `sudo`.
- **Clé du membre** : lue dans `~/.zen/game/nostr/<email>/.secret.nostr` (`--nostr-dir` pour un autre dossier), comme le
  faisaient les scripts d'Astroport.ONE. Elle reste sur la machine, n'est jamais copiée ni envoyée ; la signature est faite
  par le plugin (Schnorr BIP-340, sans dépendance).
- **Relai** : celui de la station par défaut (`--relay` pour un autre), avec NIP-42 si le relai le demande.

## Utiliser

```bash
media-add film.mkv --title "Le Voyage de Chihiro" --year 2001 --level circle      # TMDB si TMDB_API_KEY est défini
media-add S01E03.mkv --type episode --series "Ma Série" --season 1 --episode 3
media-add https://www.youtube.com/watch?v=… --type video --level private          # yt-dlp : sous-titres et licence repris
media-add https://www.youtube.com/watch?v=… --type music --artist "Artiste"       # mp3
media-add fichier.mp4 --title "Essai" --level circle --dry-run /tmp/sortie        # tout écrire dans un dossier (fiche et
                                                                                  #   étiquettes de l'événement comprises), rien envoyer
```

| Option | Rôle |
|---|---|
| `--level private\|circle` | `private` (défaut) : le membre seul, aucun événement. `circle` : événement vidéo pour les amis N¹ (film, épisode, vidéo). |
| `--source-kind`, `--rights` | origine (`disc`, `purchase`, `broadcast`, `web`, `own`) et droits. Par défaut `private_copy` (attestation locale posée automatiquement au premier dépôt) ; `own` donne `owner`. |
| `--no-transcode` | déposer tel quel. Un fichier déjà MP4/H.264/AAC n'est de toute façon jamais réencodé. |
| `--no-event` | écrire sans publier d'événement. |
| `--no-catalog`, `--tmdb-id`, `--lang` | catalogue : le couper, désigner la fiche exacte, changer la langue. |

Le réencodage met la **piste audio française en premier** (et par défaut). Les sous-titres textuels du fichier sont extraits
en WebVTT ; ceux d'image (DVD, PGS) sont ignorés : pas de transcription automatique.

## Ce qui se passe, dans l'ordre

1. Copie privée : au premier dépôt `private_copy`, le fichier `legal` du compte est posé automatiquement (texte affiché). Rien n'est publié.
2. Si le fichier existe déjà dans la bibliothèque, on s'arrête tout de suite, avant tout réencodage et sans rien envoyer.
3. Dossiers créés au besoin (un `MKCOL` par niveau), puis fichiers, puis la fiche en dernier.
4. Le CID du blob chiffré que la station a donné au fichier est lu (`PROPFIND`, `getetag`) : l'événement vidéo désigne `/ipfs/<cid>`.
5. L'événement est signé et publié. S'il échoue, les fichiers restent écrits et le plugin le dit (relancer avec un autre titre
   serait refusé : le fichier existe ; republier à la main demande aujourd'hui de supprimer le fichier).

## Limites actuelles

- Partage `public` et accès payant (`price_zen_cents`) : non pris en charge.
- Un fichier de plus de 1 024 Mo est refusé par le plugin (plafond frugal hérité de la station).
- Pas de reprise d'un dépôt interrompu, ni de republication d'un événement seul.
- TMDB et `yt-dlp` ne sont testés qu'avec des réponses simulées.

Origine : `Astroport.ONE/ajouter_media.sh`. Tests : `python3 -m unittest discover -s tests`.
