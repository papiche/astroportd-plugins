# astroportd-plugins

Plugins **système** pour [astroportd](https://github.com/papiche/astroportd), la station UPlanet en un seul binaire.

Un plugin système fait tourner sur la machine ce que la station ne fait pas elle-même, parce qu'il faut des droits
qu'elle n'a pas (root, Bluetooth, matériel) ou des outils lourds, et **prépare des données** que la station lit.

| Plugin | Ce qu'il apporte | Contrat |
|---|---|---|
| [`energy`](energy/) | Consommation électrique (PowerJoular) et production solaire (Victron) de la station : balise publique, cockpit, PAF | `energy/v1` |
| `media` *(à venir)* | Acquisition et préparation des films, séries, vidéos et musiques (métadonnées comprises) | — |

## Le principe : un contrat de fichier, jamais d'exécution

```text
 plugin (capteurs, outils)            station astroportd
 ┌───────────────────────┐   écrit    ┌──────────────────────────────────────────┐
 │ install / setup /     │ ─────────▶ │ <station>/plugins/<plugin>/<fichier>.json │
 │ uninstall / status    │  atomique  │ lu, validé (schéma), daté ; jamais exécuté │
 └───────────────────────┘            └──────────────────────────────────────────┘
```

- Le plugin écrit dans `<station>/plugins/<nom>/` (par défaut `~/.zen/astroportd/plugins/<nom>/`), de façon atomique
  (fichier temporaire puis renommage), au format versionné qu'il déclare dans `plugin.toml` (`produces = "energy/v1"`).
- Les formats sont décrits dans [`contracts/`](contracts/) : un schéma JSON par format et [`openapi.yaml`](contracts/openapi.yaml)
  (OpenAPI 3.1) qui les réunit. La CI vérifie schémas, manifestes et sorties des plugins.
- La station **n'exécute jamais** le code d'un plugin et ne lui ouvre aucune porte : un plugin cassé ou malveillant peut
  au pire lui donner de mauvais chiffres, qu'elle ignore s'ils ne suivent pas le contrat ou sont périmés.
- Ce qui demande l'administrateur se fait **une fois, à l'installation**, par `sudo`, chaque commande étant affichée.
  Ce qui tourne en continu côté données tourne sans droits particuliers (systemd `--user`).

## Installer un plugin

```bash
git clone https://github.com/papiche/astroportd-plugins.git
cd astroportd-plugins/energy
./install.sh          # puis ./status.sh ; ./uninstall.sh [--purge] pour retirer
```

Station installée ailleurs que dans `~/.zen/astroportd` : `ASTROPORTD_HOME=/chemin ./install.sh`.

## Écrire un plugin

```text
<nom>/
  plugin.toml     name, version, description, produces, data_dir, data_file, refresh_secs, stale_after_secs, needs_root
  install.sh      installe et démarre (idempotent : relancé, il met à jour)
  setup.sh        réglages après installation
  uninstall.sh    retire ce qu'il a posé ; les données restent sauf avec --purge
  status.sh       état en JSON sur la sortie standard ({"plugin", "version", "ok", …})
  bin/, files/    collecteurs, services, fichiers copiés
contracts/<format>.schema.json   + sa référence dans contracts/openapi.yaml
tests/                           tests de la production du plugin contre son schéma
```

Les autres familles d'extensions d'astroportd restent dans son dépôt : les modules **WebAssembly** (calcul pur, sans
fichier ni réseau) et les **agents MCP** (actions faites pour un membre, avec son accord).

Licence AGPL-3.0. Auteur : Fred (support@qo-op.com).
