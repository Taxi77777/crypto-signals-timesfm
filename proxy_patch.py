"""
╔══════════════════════════════════════════════════════════════════╗
║  proxy_patch.py — RELAIS CLOUDFLARE POUR LES CARNETS D'ORDRES    ║
║                                                                   ║
║  Probleme resolu :                                                ║
║  Tes alertes affichent "sur 4 exchanges" ou "sur 5 exchanges"     ║
║  au lieu de 6. Binance et Kraken refusent les IP des runners      ║
║  GitHub Actions. Resultat : moins d'exchanges pour calculer le    ║
║  consensus, donc le seuil de 80 % est plus dur a atteindre, donc  ║
║  moins de signaux.                                                ║
║                                                                   ║
║  Ce module reroute automatiquement les appels PUBLICS de carnet   ║
║  d'ordres vers ton Worker Cloudflare, dont l'IP passe.            ║
║                                                                   ║
║  Ce qu'il ne touche PAS : tout ce qui concerne MEXC. Les requetes ║
║  signees restent en direct — une signature ne doit jamais         ║
║  transiter par un intermediaire.                                  ║
║                                                                   ║
║  Utilisation : une seule ligne, tout en haut de bot_once.py,      ║
║  AVANT l'import de exchanges.                                     ║
║                                                                   ║
║      import proxy_patch  # noqa: F401                             ║
║                                                                   ║
║  Puis definir deux secrets GitHub :                               ║
║      EXCHANGE_PROXY_URL   = https://ihp-scheduler.<toi>.workers.dev
║      EXCHANGE_PROXY_TOKEN = <le meme que PROXY_TOKEN du Worker>   ║
║                                                                   ║
║  Sans ces variables, le module ne fait rien du tout : le bot      ║
║  fonctionne exactement comme avant. Aucun risque a l'installer.   ║
╚══════════════════════════════════════════════════════════════════╝
"""
import os
import logging
from urllib.parse import quote

import requests

log = logging.getLogger("IHP-PROXY")

PROXY_URL = os.environ.get("EXCHANGE_PROXY_URL", "").strip().rstrip("/")
PROXY_TOKEN = os.environ.get("EXCHANGE_PROXY_TOKEN", "").strip()

# Uniquement des endpoints publics. MEXC est volontairement absent.
DOMAINES_RELAYES = (
    "api.binance.com",
    "data-api.binance.vision",
    "api.kraken.com",
    "api.bybit.com",
    "www.okx.com",
    "api.bitget.com",
)

_compteur = {"relayees": 0, "directes": 0, "echecs_relais": 0}


def statistiques() -> dict:
    """Utile pour verifier que le relais sert vraiment a quelque chose."""
    return dict(_compteur)


def _doit_relayer(method: str, url: str) -> bool:
    if not PROXY_URL:
        return False
    if method.upper() != "GET":
        return False
    return any(d in url for d in DOMAINES_RELAYES)


_requete_origine = requests.sessions.Session.request


def _requete_patchee(self, method, url, *args, **kwargs):
    if not _doit_relayer(method, str(url)):
        _compteur["directes"] += 1
        return _requete_origine(self, method, url, *args, **kwargs)

    cible = f"{PROXY_URL}/proxy?url={quote(str(url), safe='')}"
    entetes = dict(kwargs.get("headers") or {})
    if PROXY_TOKEN:
        entetes["X-Proxy-Token"] = PROXY_TOKEN
    kwargs["headers"] = entetes

    try:
        reponse = _requete_origine(self, method, cible, *args, **kwargs)
        if reponse.status_code < 400:
            _compteur["relayees"] += 1
            return reponse
        log.warning(
            "[PROXY] Relais HTTP %s sur %s — repli en direct.",
            reponse.status_code, url,
        )
    except Exception as e:
        log.warning("[PROXY] Relais indisponible (%s) — repli en direct.", e)

    # Repli : si le Worker tombe, le bot continue comme avant.
    _compteur["echecs_relais"] += 1
    kwargs["headers"] = {k: v for k, v in entetes.items() if k != "X-Proxy-Token"}
    return _requete_origine(self, method, url, *args, **kwargs)


requests.sessions.Session.request = _requete_patchee

if PROXY_URL:
    log.info("[PROXY] Relais actif via %s pour %d domaines.",
             PROXY_URL, len(DOMAINES_RELAYES))
else:
    log.info("[PROXY] EXCHANGE_PROXY_URL non defini — appels en direct.")
