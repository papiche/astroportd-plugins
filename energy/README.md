# Plugin `energy`

Ce que consomme la station et ce que produisent ses panneaux solaires, pour que la **PAF** (participation aux frais)
reste honnête et visible : la station le publie dans sa balise (kind 30850, mode LIVE d'astroport.one) et l'affiche dans
le cockpit du Capitaine.

| | Capteur | Droits | Données brutes |
|---|---|---|---|
| Consommation | [PowerJoular](https://github.com/papiche/powerjoular) (compteurs RAPL du processeur, GPU) | service système (root) | `/var/lib/powerjoular/power_24h.csv`, une mesure par seconde |
| Production | régulateurs **Victron** (SmartSolar…), annonces Bluetooth « Instant Readout » | service système (utilisateur courant) | `~/.zen/game/victron/readings.csv` |

Le **collecteur** `bin/energy-collect.py` (Python, sans dépendance) tourne toutes les 5 minutes sans droits
particuliers (`energy-collect.timer`, systemd `--user`). Il relit chaque CSV là où il s'était arrêté, tient un
historique par jour et écrit `~/.zen/astroportd/plugins/energy/energy.json` au format
[`energy/v1`](../contracts/energy-v1.schema.json).

## Installer

```bash
./install.sh            # consommation (et production si un régulateur est déjà configuré)
./install.sh --solar    # avec la production solaire
./setup.sh victron      # trouver les régulateurs et enregistrer leur clé (VictronConnect › Instant Readout)
./status.sh             # état en JSON
loginctl enable-linger "$USER"   # pour que la collecte continue sans session ouverte
```

Venant d'**Astroport.ONE** : les deux services gardent leur nom et leurs fichiers (`powerjoular.service`,
`victron-mppt-monitor.service`, `~/.zen/victron.json`), et le premier passage du collecteur reprend l'historique
`~/.zen/game/power_history.json` et `~/.zen/game/victron/victron_history.json`. Un jour connu sur plus d'heures
n'est jamais remplacé par une mesure partielle.

## Ce qui change par rapport au legacy

- `powerjoular.service` redémarre chaque jour (`RuntimeMaxSec=1d`) : son `ExecStartPre` ramène le CSV aux dernières
  24 h, sans le `sudo` de 20h12 d'Astroport.ONE (le CSV grossit sinon d'environ 7,5 Mo par jour).
- Le jour est le **jour calendaire** (le legacy appelait « jour » les 24 h précédant 20h12).
- Plusieurs régulateurs Victron **s'additionnent** (le legacy prenait la plus grande valeur de tous).
- Chaque échantillon ne compte qu'une fois, même quand PowerJoular réécrit son CSV en redémarrant.

## Retirer

```bash
./uninstall.sh          # garde l'historique, le CSV et les clés Victron
./uninstall.sh --purge  # retire tout
```
