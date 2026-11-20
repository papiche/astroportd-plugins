# media — films, séries, vidéos, musiques et mix de DJ

Remplace `Astroport.ONE/ajouter_media.sh`, et **tout se passe dans le plugin** : astroportd reste un serveur WebDAV qui
partage en P2P, N¹ ou N², il ne réagit à rien.

Le plugin sonde (`ffprobe`), réencode en H.264/AAC (`ffmpeg`), télécharge (`yt-dlp`), interroge le catalogue TMDB, extrait les
sous-titres, découpe les mix de DJ, puis **écrit lui-même dans l'uCloud du membre**, par WebDAV et avec le jeton du membre :

| Nature | Dossier | Nom |
|---|---|---|
| film | `/Vidéos/Films` | `Titre (année).mp4` |
| épisode | `/Vidéos/Séries/<série>/Saison NN` | `<série> S01E02.mp4` |
| vidéo hors catalogue | `/Vidéos/Autres` | `Titre (année).mp4` |
| morceau | `/Musique/<artiste>/<album>` (`Singles` sans album) | `NN - Titre.mp3` |
| morceau d'un mix | `/Musique/<DJ>/<mix>` | `NN - Artiste - Titre.mp3`, plus `<mix>.m3u8` |
| mix impossible à découper | `/Musique/<DJ>/Mixes` | `<mix>.mp3` (le mix entier) |

À côté de chaque fichier : `<nom>.media.json` (la fiche, contrat [`media/v1`](../contracts/media-v1.schema.json), base de la
vidéothèque), `<nom>.poster.jpg` & co, et `<nom>.<langue>.vtt` (un morceau : `<nom>.cover.jpg`, sa pochette). Avec `--level circle`, le plugin **signe l'événement
(NIP-71, kind 21 ou 22) avec la clé du membre et le publie sur le relai**, comme n'importe quel client Nostr.

**Un morceau est un kind 21** (22 s'il dure 60 s ou moins) dont l'`imeta` est **audio** (`m audio/mpeg`, `duration`,
`bitrate`, `image` = la pochette) avec `t music`, `artist`, `album`, `album_artist`, `track_number`. NIP-71 prévoit l'audio :
ni vidéo fabriquée, ni nouveau kind, et le relai d'astroportd réserve déjà les kinds 21/22 au cercle N¹ de l'auteur.
**Un mix est une playlist NIP-51** (kind 30005, « curation set », `d = mix:<id YouTube>`) qui liste ses morceaux par
leur événement (`e`), dans l'ordre ; astroportd la réserve au même cercle N¹ que les morceaux.

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
media-add https://www.youtube.com/watch?v=… --type music --artist "Artiste"       # mp3 (pochette et métadonnées intégrées)
media-add https://www.youtube.com/watch?v=… --type mix --level circle               # mix de DJ : un mp3 par morceau + playlist
media-add fichier.mp4 --title "Essai" --level circle --dry-run /tmp/sortie        # tout écrire dans un dossier (fiche et
                                                                                  #   étiquettes de l'événement comprises), rien envoyer
```

| Option | Rôle |
|---|---|
| `--level private\|circle` | `private` (défaut) : le membre seul, aucun événement. `circle` : événement vidéo pour les amis N¹ (film, épisode, vidéo). |
| `--source-kind`, `--rights` | origine (`disc`, `purchase`, `broadcast`, `web`, `own`) et droits. Par défaut `private_copy` (attestation locale posée automatiquement au premier dépôt) ; `own` donne `owner`. |
| `--no-transcode` | déposer tel quel. Un fichier déjà MP4/H.264/AAC n'est de toute façon jamais réencodé. |
| `--no-event` | écrire sans publier d'événement. |
| `--own-copy` | déposer sa propre copie même si un ami a déjà l'œuvre (voir *Déjà chez un ami*). |
| `--no-catalog`, `--tmdb-id`, `--lang` | catalogue : le couper, désigner la fiche exacte, changer la langue. |
| `--cookies`, `--cookies-from-browser` | cookies YouTube pour yt-dlp. Par défaut : le cookie du MULTIPASS (`~/.zen/game/nostr/<compte>/.youtube.com.cookie`, `.cookie.txt` ou `cookies.txt`), sinon aucun. |
| `--limit`, `--candidates`, `--min-match` | mix : seulement les N premiers morceaux ; résultats YouTube examinés par morceau (5) ; part minimale des mots du morceau retrouvés dans le résultat (0,5) : en dessous, le morceau est écarté plutôt que remplacé par une chanson sans rapport. |

### Déjà chez un ami : la copie plutôt qu'un nouveau fichier

Pour que les disques ne gonflent pas, le plugin demande à la station, **dès que l'œuvre est connue**, si un proche
réciproque l'a déjà partagée avec son cercle :

- une adresse YouTube (vidéo, morceau, chaque morceau d'un mix) : par son identifiant, lu dans l'adresse **avant tout
  téléchargement** ;
- un film : par son identifiant TMDB, avant le réencodage et l'envoi ;
- un épisode : par sa série (identifiant TMDB, ou nom sans catalogue), sa saison et son numéro.

Si un ami l'a, sa copie est demandée — **le même fichier chiffré**, qui ne crée pas de nouveau CID et arrive rangé comme chez
lui — et rien n'est téléchargé ni envoyé ; le rapport JSON le dit (`from_friend`, `state` : `asked`, `sent` ou `copied` si
l'œuvre est déjà dans l'uCloud du membre). Plusieurs amis l'ont : la copie la plus répandue est choisie. Seuls les
identifiants de catalogue font foi, jamais un titre seul. `--own-copy` dépose quand même (autre version, meilleure qualité) ;
une station injoignable ou trop ancienne laisse le dépôt se faire normalement. Dans un mix, un morceau venu d'un ami n'entre
pas dans la playlist (`from_friends` dans le rapport).

### Mix de DJ (`--type mix`)

Repris d'Astroport.ONE `tools/youtube_dj_playlist_downloader.py`. La tracklist vient des **chapitres** YouTube, sinon des
**repères** de la description (`00:00`, `[12:34]`, `(1:02:58)`), sinon des lignes « Artiste - Titre » ; liens et réseaux
sociaux sont ignorés. Chaque morceau est cherché par son nom sur YouTube, téléchargé en mp3 (pochette et métadonnées
intégrées) et déposé comme un morceau ordinaire, avec son repère dans le mix (`music.from_mix.start_s`). Le rapport JSON
liste ce qui a été déposé (`deposited`), ce qui était déjà là (`kept`), écarté faute de correspondance fiable (`no_match`)
ou en échec (`failed` : code de sortie 1).

- **Sans tracklist, ou si aucun morceau n'est trouvé, le mix est gardé en entier** (`/Musique/<DJ>/Mixes/<mix>.mp3`).
- **Relancer le même lien ne retélécharge rien** : un morceau déjà dans l'uCloud est gardé, son événement retrouvé sur le
  relai par l'empreinte du fichier, et la playlist republiée garde tous ses morceaux.
- `--dry-run DOSSIER` sur un mix affiche la tracklist détectée sans rien chercher ni télécharger.

### Depuis Firefox : extension « Open With »

Comme avec Astroport.ONE ([Open With](https://addons.mozilla.org/firefox/addon/open-with/)), une entrée par usage ; l'adresse
de la page est ajoutée à la fin de la commande. Aucun mot de passe n'est demandé : le jeton est lu dans `config.json`.

```
# Vidéo (uCloud, partagée au cercle N¹)
~/.local/bin/media-add --type video --level circle
# Musique (un morceau)
~/.local/bin/media-add --type music --level circle
# Musique / DJ Mix (un mp3 par morceau et la playlist ; le mix entier s'il ne se découpe pas)
~/.local/bin/media-add --type mix --level circle
```

Ajoutez `--cookies-from-browser firefox` si le compte n'a pas de cookie YouTube enregistré.

Le réencodage met la **piste audio française en premier** (et par défaut). Les sous-titres textuels du fichier sont extraits
en WebVTT ; ceux d'image (DVD, PGS) sont ignorés : pas de transcription automatique.

## Ce qui se passe, dans l'ordre

0. Un ami a déjà l'œuvre : sa copie est demandée, et l'on s'arrête là (voir plus haut).
1. Copie privée : au premier dépôt `private_copy`, le fichier `legal` du compte est posé automatiquement (texte affiché). Rien n'est publié.
2. Si le fichier existe déjà dans la bibliothèque, on s'arrête tout de suite, avant tout réencodage et sans rien envoyer.
3. Dossiers créés au besoin (un `MKCOL` par niveau), puis fichiers, puis la fiche en dernier.
4. Le CID du blob chiffré que la station a donné au fichier est lu (`PROPFIND`, `getetag`) : l'événement vidéo désigne `/ipfs/<cid>`.
5. L'événement est signé et publié. S'il échoue, les fichiers restent écrits et le plugin le dit (relancer avec un autre titre
   serait refusé : le fichier existe ; republier à la main demande aujourd'hui de supprimer le fichier).

## Limites actuelles

- Partage `public` et accès payant (`price_zen_cents`) : non pris en charge.
- Mix : le choix d'un morceau repose sur les mots communs avec le titre YouTube trouvé ; un remix ou une version live peut
  être pris pour l'original. Le mix n'est pas coupé lui-même : chaque morceau est retéléchargé séparément.
- Taille d'un fichier : 4 Gio pour un MULTIPASS, 16 Gio pour une ZEN Card ou le Capitaine (plafonds de la station, qui
  refuse le dépôt au-delà ; le plugin refuse d'emblée plus de 16 Gio).
- Pas de reprise d'un dépôt interrompu, ni de republication d'un événement seul.
- TMDB et `yt-dlp` ne sont testés qu'avec des réponses simulées.

Origine : `Astroport.ONE/ajouter_media.sh`. Tests : `python3 -m unittest discover -s tests`.
