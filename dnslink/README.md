# Plugin `dnslink`

Publie un nom DNSLink (`_dnslink.<nom>.<zone>`) chez OVH pour un site IPFS, une station ou un membre.
**Script de référence** : les autres projets (coracle, zelkova…) l'appellent, ils n'en gardent aucune copie.

## Où sont les paramètres

Dans NOSTR, jamais dans un fichier local : la configuration de chaque coopérative (kind 30800, `d=cooperative-config`) porte
`OVH_APP_KEY`, `OVH_APP_SECRET`, `OVH_CONSUMER_KEY` et `OVH_ZONE`. Ils se renseignent dans l'interface d'astroportd
(Options › « DNSLink OVH »). Le plugin, pour chaque plan de `planes.json` (une coopérative = un plan) :

1. déduit le nom de l'UPlanet (64 zéros pour ORIGIN, dernière ligne de `swarm.key` sinon) ;
2. en dérive la clé publique coopérative (scrypt Duniter de `<UPLANETNAME>.G1` → sha256 → secp256k1) ;
3. demande l'événement au relai local du plan, **vérifie sa signature** et que l'auteur est cette clé ;
4. déchiffre les secrets en mémoire (AES-256-CBC, clé = sha256(UPLANETNAME)) — jamais passés en argument de commande.

Aucun code d'Astroport.ONE, aucun `cooperative_config.sh`, aucune dépendance (Python 3 standard ; le client Nostr est
`media/bin/nostr_lite.py`, relié par un lien symbolique). Les variables d'environnement `OVH_APP_KEY`… priment si elles sont posées.

## Utilisation

```bash
./install.sh                       # pose la commande `ovh-me`
ovh-me coops                       # coopératives de la station et état de leurs paramètres OVH
ovh-me --coop copylaradio upsert coracle <CID>      # crée ou met à jour _dnslink.coracle.<zone de la coopérative>
ovh-me list | get <nom> | create|update|upsert <nom> <cible> | delete <nom>
```

Choix de la coopérative : `--coop NOM`, sinon `OVH_COOP=NOM`, sinon l'unique plan, sinon un menu dans un terminal
(sans terminal : erreur qui liste les coopératives, jamais d'attente muette). Un plan dont le démon est arrêté est dit
« injoignable » : démarrez-le (`astroportd planes`).

Cibles : `k51…` → `/ipns/`, `Qm…`/`bafy…` → `/ipfs/`, ou `/ipns/…`, `/ipfs/…`, `dnslink=…` tels quels. TTL 0, mêmes
enregistrements que la tâche `dnslink` d'astroportd. Journal (sans secret) : `<station>/plugins/dnslink/journal.jsonl`.

Tests : `python3 -m unittest tests.test_dnslink` (vecteurs de chiffrement d'astroportd, relai et API OVH simulés).
