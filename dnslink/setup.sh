#!/usr/bin/env bash
# Réglages du plugin « dnslink » : il n'y en a pas, les paramètres d'API viennent de NOSTR (kind 30800).
# Pour les renseigner : interface d'astroportd, Options › « DNSLink OVH » (OVH_APP_KEY, OVH_APP_SECRET, OVH_CONSUMER_KEY, OVH_ZONE).
exec "$(dirname "$0")/bin/ovh_me.py" coops
