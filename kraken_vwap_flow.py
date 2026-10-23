#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================================
  BOT "VWAP ORDER FLOW"  -  perpetuels Kraken Pro (Futures)
  Strategie de la video "Comment confirmer une entree sur la VWAP"
  (Dualis Flow, partie 2).
==========================================================================
  Le bot recoit CHAQUE trade Kraken en direct (WebSocket) et construit
  lui-meme des bougies FOOTPRINT 15 minutes (BAR_TF) : volume, acheteurs agressifs,
  vendeurs agressifs, DELTA, et les gros ordres ("big trades").

  LA CHECKLIST (il faut les 4) - exemple a l'ACHAT :
    1. PRIX SUR LA VWAP     tendance au-dessus de la VWAP du jour, puis le
                            prix revient toucher la VWAP
    2. VENDEURS ABSORBES    sur cette bougie les vendeurs frappent fort
                            (delta tres negatif)... mais le prix ne descend
                            plus : la bougie cloture sur / au-dessus de la VWAP
    3. BIG TRADE RESPECTE   un gros ACHETEUR defend la zone (ordre >= BIG_MULT x le
                            seuil des 1 % plus gros ordres recents, au lieu de x
                            la taille habituelle) et la bougie CLOTURE AU-DESSUS
                            de son prix
    4. ACHETEURS DE RETOUR  bougie suivante : delta positif et cloture plus haute
                            -> ENTREE CONFIRMEE

  Annulation : une bougie qui cloture SOUS la VWAP (gros acheteur sur la VWAP
  mais cloture en dessous = pas de long), ou plus de SEQ_BARS bougies.
  VENTE : exactement l'inverse.

  IA D'APPRENTISSAGE (LEARN=1) : les seuils ci-dessus sont les valeurs de DEPART.
  Chaque setup (pris ou non) est suivi jusqu'a son resultat ; l'IA ajuste les seuils
  d'un cran a la fois, seulement sur preuve statistique (voir la section "IA").

  Sortie : stop SOUS LA BOUGIE d'absorption ("safe", defaut) ou SOUS LE BIG
  TRADE ("aggressive"), elargi au minimum impose par les frais Kraken.
  TP = RR x risque. A +BE_AT_R x risque, stop remonte a l'entree + frais,
  puis TRAILING STOP : le stop suit le meilleur prix a TRAIL_R x risque.
  Stop et TP poses CHEZ KRAKEN. Une seule position a la fois.

  Variables d'environnement (GitHub Secrets / Variables) :
     KRAKEN_KEY, KRAKEN_SECRET   cles Kraken FUTURES
     KRAKEN_MODE                 "paper" (defaut) ou "live"
     TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   (optionnel)
==========================================================================
"""
import base64
import datetime
import hashlib
import hmac
import html as _html
import json
import logging
import math
import os
import re
import shutil
import signal
import statistics
import threading
from collections import deque
import sys
import time
import urllib.parse

import requests

try:
    import websocket
except Exception:
    websocket = None

BASE = "https://futures.kraken.com"
TF_SEC = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400}


def envf(name, default):
    v = os.environ.get(name, "").strip()
    if v == "":
        return default
    if isinstance(default, str):
        return v
    return type(default)(float(v)) if isinstance(default, int) else type(default)(v)


CFG = {
    "MODE": envf("KRAKEN_MODE", "paper"),
    "SYMBOLS": envf("VWAP_SYMBOLS", ""),          # vide = les perpetuels les plus liquides
    # tous les perpetuels crypto avec du volume ; une bougie n'est lue que si elle a assez
    # de trades (MIN_BAR_TRADES), donc les actifs trop calmes sont ignores d'eux-memes
    "MIN_QUOTE_VOL": envf("VWAP_MIN_VOL", 1000000.0),   # volume 24 h minimum (USD)
    "TOP_N": envf("VWAP_TOP_N", 60),
    # unite de temps des bougies footprint : 15 min = intraday (1 min = trop de bruit)
    "BAR_TF": envf("BAR_TF", "15m"),
    # 1. prix sur la VWAP
    "TOUCH_PCT": envf("TOUCH_PCT", 0.30),         # "sur la VWAP" = a +/- X % de la VWAP
    "TREND_BARS": envf("TREND_BARS", 12),         # tendance : sur les X bougies precedentes (3 h)...
    "TREND_MIN": envf("TREND_MIN", 0.65),          # ...au moins X du bon cote de la VWAP
    # 2. vendeurs absorbes
    "ABS_DELTA": envf("ABS_DELTA", 0.15),         # delta de la bougie <= -X du volume (achat)
    "MIN_BAR_TRADES": envf("MIN_BAR_TRADES", 60), # bougie avec assez de trades pour etre lue
    # 3. big trade respecte
    # en crypto la taille MEDIANE d'un ordre est minuscule (particuliers) : comparer a la
    # mediane rendait "gros" presque tout ordre (x19000 observes). Reference = le seuil des
    # 1 % plus gros ordres recents (99e centile) : x1 = deja dans le top 1 %.
    "BIG_MULT": envf("BIG_MULT", 1.3),            # gros ordre = >= X fois le 99e centile
    "BIG_SAMPLE": envf("BIG_SAMPLE", 5000),       # centile calcule sur les X derniers ordres
    "BIG_WARMUP": envf("BIG_WARMUP", 200),        # ordres observes avant de juger "gros"
    # 4. acheteurs de retour
    "BUY_DELTA": envf("BUY_DELTA", 0.15),         # delta >= +X du volume (achat)
    "SEQ_BARS": envf("SEQ_BARS", 4),              # toute la sequence en X bougies maximum (1 h)
    # sortie
    "STOP_MODE": envf("STOP_MODE", "safe"),       # safe = sous la bougie ; aggressive = sous le big trade
    "STOP_BUF_PCT": envf("STOP_BUF_PCT", 0.05),   # marge sous le niveau (en % du prix)
    "SL_MIN_PCT": envf("SL_MIN_PCT", 0.25),       # risque minimum en % (frais Kraken <= 1/3 du risque)
    "SL_MAX_PCT": envf("SL_MAX_PCT", 3.0),
    "RR": envf("RR", 2.0),                        # TP = RR x risque
    "BE_AT_R": envf("BE_AT_R", 1.0),              # a +X R : stop a l'entree + frais
    "BE_EXTRA_PCT": envf("BE_EXTRA_PCT", 0.02),
    "TRAIL_R": envf("TRAIL_R", 1.0),              # trailing : stop a X R derriere le meilleur prix (0 = off)
    "TRAIL_STEP_R": envf("TRAIL_STEP_R", 0.25),   # on ne deplace le stop que par pas de X R
    "MAX_HOLD_MIN": envf("MAX_HOLD_MIN", 720),    # duree max d'un trade (minutes) : 12 h, intraday
    "ATR_PERIOD": envf("ATR_PERIOD", 14),
    "ADOPT_ORPHANS": envf("ADOPT_ORPHANS", 1),
    "FILL_TOL": envf("FILL_TOL", 1.25),
    # risque (memes valeurs prudentes que le bot precedent)
    "LEVERAGE": envf("LEVERAGE", 5),
    "SIZING": envf("SIZING", "RISK"),             # RISK = RISK_PCT du capital perdu au stop
    "RISK_PCT": envf("RISK_PCT", 0.5),
    "CAPITAL_PCT": envf("CAPITAL_PCT", 95.0),
    "LIQ_SAFETY": envf("LIQ_SAFETY", 0.75),
    "MAX_OPEN": envf("MAX_OPEN", 5),
    "ANALYSE": envf("ANALYSE", 1),                # 1 = chaque signal 4/4 analyse (10 000 simulations) sur Telegram
    "ANALYSE_DAYS": envf("ANALYSE_DAYS", 30),     # historique utilise par les simulations (jours)
    "ANALYSE_SIMS": envf("ANALYSE_SIMS", 10000),
    "FEE_TAKER": envf("FEE_TAKER", 0.0005),
    "MAX_FEE_R": envf("MAX_FEE_R", 0.35),
    "PAPER_BALANCE": envf("PAPER_BALANCE", 1000.0),
    # IA d'apprentissage (voir la section "IA" plus bas)
    "LEARN": envf("LEARN", 1),                    # 0 = criteres fixes de la video
    "LEARN_MIN": envf("LEARN_MIN", 40),           # resultats necessaires avant le 1er reglage
    "LEARN_EVERY_MIN": envf("LEARN_EVERY_MIN", 30),
    "LEARN_KEEP": envf("LEARN_KEEP", 1500),       # memoire : les X derniers setups
    # boucle
    "LOOP_MINUTES": 320,                          # lu plus bas (valeur invalide -> 320)
    "FAST_SEC": envf("FAST_SEC", 2),              # lecture du flux / cloture des bougies
    "SLOW_SEC": envf("SLOW_SEC", 15),             # positions Kraken / stops / TP
    "SUMMARY_HOURS": envf("SUMMARY_HOURS", 6.0),
}
CFG["BAR_SEC"] = TF_SEC.get(CFG["BAR_TF"], 900)
CFG["SYMS"] = []
CFG["RR_ADOPT"] = CFG["RR"]
LIVE = CFG["MODE"].lower() == "live"
try:                                               # duree d'un job : bornee sous le timeout (340 min)
    CFG["LOOP_MINUTES"] = max(5, min(330, int(float(os.environ.get("LOOP_MINUTES") or 320))))
except (ValueError, OverflowError):
    CFG["LOOP_MINUTES"] = 320
# un fichier d'etat par mode : un essai en fictif n'efface JAMAIS les positions reelles
STATE_FILE = "kraken_vwap_state.json" if LIVE else "kraken_vwap_state_paper.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                    datefmt="%H:%M:%S", stream=sys.stdout)
log = logging.getLogger("vwap")
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "vwap-flow"
FEED = None
TICK = {}



# =========================================================================
#  API KRAKEN FUTURES
# =========================================================================
def pub(path, params=None):
    last = None
    for essai in range(3):                       # 3 essais en cas de coupure reseau
        try:
            r = SESSION.get(BASE + path, params=params, timeout=10)
            r.raise_for_status()
            j = r.json()
            if isinstance(j, dict) and j.get("result") not in (None, "success"):
                raise RuntimeError("%s : %s" % (path, j.get("error") or j.get("result")))
            return j
        except Exception as e:
            last = e
            time.sleep(1 + essai)
    raise last


def priv(method, endpoint, params=None):
    """Signature Kraken Futures : b64(HMAC-SHA512(b64dec(secret), SHA256(postData+endpoint)))"""
    key = os.environ.get("KRAKEN_KEY", "")
    sec = os.environ.get("KRAKEN_SECRET", "")
    if not key or not sec:
        raise RuntimeError("cles Kraken Futures absentes")
    data = urllib.parse.urlencode(params or {})
    sha = hashlib.sha256((data + endpoint).encode()).digest()
    sig = base64.b64encode(hmac.new(base64.b64decode(sec), sha, hashlib.sha512).digest()).decode()
    headers = {"APIKey": key, "Authent": sig, "Accept": "application/json",
               "Content-Type": "application/x-www-form-urlencoded"}
    url = BASE + "/derivatives" + endpoint + (("?" + data) if data else "")
    r = SESSION.request(method, url, headers=headers, timeout=25)
    try:
        j = r.json()
    except Exception:
        raise RuntimeError("%s : reponse illisible (HTTP %s)" % (endpoint, r.status_code))
    if not isinstance(j, dict):
        raise RuntimeError("%s : reponse inattendue" % endpoint)
    if j.get("result") and j["result"] != "success":
        raise RuntimeError("%s : %s" % (endpoint, j.get("error") or j))
    if not r.ok:
        raise RuntimeError("%s HTTP %s %s" % (endpoint, r.status_code, j))
    return j


def candles(sym, tf):
    """Bougies CLOTUREES : [ts, o, h, l, c, volume]"""
    tfs = TF_SEC[tf]
    now = int(time.time())
    j = pub("/api/charts/v1/trade/%s/%s" % (sym, tf), {"from": now - 200 * tfs, "to": now})
    out = []
    for r in j.get("candles") or []:
        ts = int(r["time"]) // 1000
        if ts + tfs <= now:
            out.append([ts, float(r["open"]), float(r["high"]), float(r["low"]),
                        float(r["close"]), float(r.get("volume") or 0)])
    return out


class TradeFeed:
    """Flux en direct des trades Kraken Futures (WebSocket, thread de fond).
       Le bot voit ainsi CHAQUE trade (absorption / iceberg / agresseurs exacts) au lieu
       d'un instantane de 100 trades par minute. Reconnexion automatique ; si le flux
       tombe, le bot repasse sur l'API REST comme avant."""
    URL = "wss://futures.kraken.com/ws/v1"

    def __init__(self):
        self.lock = threading.Lock()
        self.trades = {}
        self.syms = set()
        self.acked = set()                         # symboles dont l'abonnement est CONFIRME
        self.alive = 0.0                           # derniere activite reelle du flux
        self.n = 0                                 # compteur local : ne repart jamais a zero
        self.stop = False
        self.epoch = 0                             # +1 a chaque coupure
        self.last_msg = 0.0                        # dernier message (heartbeat compris)
        self.gaps = deque(maxlen=50)               # coupures (debut, fin) : bougies incompletes
        self.gap_start = None
        self.overflowed = set()                    # symboles dont des trades ont ete perdus

    def set_symbols(self, syms):
        with self.lock:
            self.syms = set(syms)

    def ok(self, sym=None):
        """Flux vivant ET (si sym donne) abonnement confirme pour ce symbole."""
        with self.lock:
            return time.time() - self.alive < 120 and (sym is None or sym in self.acked)

    def since(self, sym, seq0):
        """Trades plus recents que seq0 (lecture depuis la fin : rapide meme liste pleine)."""
        out = []
        with self.lock:
            dq = self.trades.get(sym, ())
            for x in reversed(dq):
                if x[4] <= seq0:
                    break
                out.append(x)
            else:                                  # jamais rejoint seq0 : tampon deborde,
                if seq0 > 0 and dq and len(dq) == dq.maxlen:   # des trades sont perdus
                    self.overflowed.add(sym)
        out.reverse()
        return out

    def missing(self, t0, t1):
        """Secondes de flux manquantes entre t0 et t1 (coupures du WebSocket)."""
        with self.lock:
            gaps = list(self.gaps) + ([(self.gap_start, time.time())] if self.gap_start else [])
        return sum(max(0.0, min(e, t1) - max(s, t0)) for s, e in gaps)

    def buffered(self, sym):
        with self.lock:
            return list(self.trades.get(sym, ()))

    def recent(self, sym, secs):
        t0 = time.time() - secs
        with self.lock:
            return [x for x in self.trades.get(sym, ()) if x[0] >= t0]

    def run(self):
        wait = 2
        while not self.stop:
            ws = None
            try:
                while not self.stop:               # attendre la liste des cryptos AVANT de se
                    with self.lock:                # connecter : Kraken n'envoie rien tant qu'on
                        if self.syms:              # n'est pas abonne, et recv() ne rend pas la main
                            break
                    time.sleep(1)
                ws = websocket.create_connection(self.URL, timeout=20)
                with self.lock:
                    self.acked = set()             # nouvelle connexion : tout re-confirmer
                    self.last_msg = time.time()
                # heartbeat : un message regulier meme en marche calme -> la boucle tourne,
                # les nouvelles cryptos sont abonnees sans attendre
                ws.send(json.dumps({"event": "subscribe", "feed": "heartbeat"}))
                done = set()
                last_ping = time.time()
                wait = 2
                while not self.stop:
                    with self.lock:
                        new = sorted(self.syms - done)
                        old = sorted(done - self.syms)
                    if new:
                        ws.send(json.dumps({"event": "subscribe", "feed": "trade", "product_ids": new}))
                        done |= set(new)
                    if old:                        # cryptos sorties de la liste : desabonnement
                        ws.send(json.dumps({"event": "unsubscribe", "feed": "trade", "product_ids": old}))
                        done -= set(old)
                        with self.lock:
                            for x in old:
                                self.trades.pop(x, None)
                                self.acked.discard(x)
                    if time.time() - last_ping > 30:   # Kraken coupe une connexion sans ping
                        ws.ping()
                        last_ping = time.time()
                    try:
                        m = json.loads(ws.recv())
                    except websocket.WebSocketTimeoutException:
                        # connexion a moitie morte : plus rien, meme pas le heartbeat -> on reconnecte
                        if time.time() - self.last_msg > 60:
                            raise RuntimeError("flux muet depuis 60 s")
                        continue
                    with self.lock:
                        self.last_msg = time.time()
                    ev = m.get("event")
                    if ev == "subscribed" and m.get("feed") == "trade":
                        with self.lock:
                            first = not self.acked
                            self.acked |= set(x.upper() for x in (m.get("product_ids") or []))
                            self.alive = time.time()
                            if self.gap_start:     # fin de la coupure
                                self.gaps.append((self.gap_start, time.time()))
                                self.gap_start = None
                        if first:
                            log.info("Flux en direct des trades Kraken : CONNECTE (abonnement confirme)")
                    elif ev in ("error", "alert"):
                        log.warning("flux trades (Kraken) : %s", m)
                    elif m.get("feed") == "trade" and "price" in m and "qty" in m:
                        if (m.get("type") or "fill") in ("block", "termination"):
                            continue                   # hors carnet : ne dit rien de l'agressivite
                        sym = (m.get("product_id") or "").upper()
                        with self.lock:
                            self.n += 1
                            rec = (float(m["time"]) / 1000.0, float(m["price"]), float(m["qty"]),
                                   m.get("side"), self.n)
                            self.trades.setdefault(sym, deque(maxlen=20000)).append(rec)
                            self.alive = time.time()
            except Exception as e:
                with self.lock:
                    self.acked = set()             # plus aucun symbole considere comme vivant
                    self.epoch += 1
                    if not self.gap_start:         # debut de la coupure = dernier message recu
                        self.gap_start = self.last_msg or time.time()
                log.warning("flux trades : %s -> reconnexion dans %d s", e, wait)
                time.sleep(wait)
                wait = min(60, wait * 2)
            finally:
                try:
                    if ws is not None:
                        ws.close()                 # pas de connexion orpheline
                except Exception:
                    pass



def tickers():
    out, info = {}, {}
    for t in pub("/derivatives/api/v3/tickers").get("tickers") or []:
        s = (t.get("symbol") or "").upper()
        p = t.get("last") or t.get("markPrice")
        if s and p:
            out[s] = float(p)
            info[s] = {"rf": float(t.get("relativeFundingRate") or 0.0),
                       "oi": float(t.get("openInterest") or 0.0)}
    TICK.clear()                                   # remplace seulement si la lecture a reussi
    TICK.update(info)
    return out


PERP_EXCLUDE = ("TRADFI", "INDEX", "STOCK", "EQUIT", "COMMOD", "METAL", "GOLD", "FOREX")
NON_CRYPTO = {"XAUT", "PAXG", "XAU", "XAG"}
XSTOCKS = {"TSLAX", "CRCLX", "NVDAX", "AAPLX", "SPYX", "QQQX", "METAX", "GOOGLX", "AMZNX",
           "MSFTX", "COINX", "HOODX", "MSTRX", "AMDX", "NFLXX", "PLTRX", "GMEX", "IBITX",
           "GLDX", "SLVX", "TQQQX", "ORCLX", "AVGOX", "INTCX", "BABAX", "UBERX", "DISX",
           "ANTHROPICX", "OPENAIX", "SPACEXX", "XAIX"}           # actions pre-IPO tokenisees


def is_crypto(sym, inst):
    base = sym[3:-3]
    if base in NON_CRYPTO or base in XSTOCKS:     # or tokenise, actions tokenisees
        return False
    for f in ("category", "tags", "type", "underlying", "group"):
        v = inst.get(f)
        txt = " ".join(map(str, v)) if isinstance(v, list) else str(v or "")
        if any(x in txt.upper() for x in PERP_EXCLUDE):
            return False
    return True


def instruments():
    out = {}
    for i in pub("/derivatives/api/v3/instruments").get("instruments") or []:
        s = (i.get("symbol") or "").upper()
        if not s.startswith("PF_") or not s.endswith("USD"):
            continue
        if not i.get("tradeable", True) or i.get("isExpired") or i.get("tradfi"):
            continue
        if not is_crypto(s, i):
            continue
        cvtp = i.get("contractValueTradePrecision")
        out[s] = {"tick": float(i.get("tickSize") or 0.0001),
                  "sp": int(cvtp) if cvtp is not None else 4}
    return out


def discover(st, C):
    if C["SYMBOLS"].strip():
        return [s.strip().upper() for s in C["SYMBOLS"].split(",") if s.strip()]
    rows = []
    for t in pub("/derivatives/api/v3/tickers").get("tickers") or []:
        s = (t.get("symbol") or "").upper()
        if s not in st["inst"] or t.get("suspended") or s in st.get("blocked", []):
            continue
        qv = float(t.get("volumeQuote") or 0)
        if qv >= C["MIN_QUOTE_VOL"]:
            rows.append((qv, s))
    rows.sort(reverse=True)
    return [s for _, s in rows[:C["TOP_N"]]]


def open_positions():
    out = {}
    j = priv("GET", "/api/v3/openpositions")
    if "openPositions" not in j:                   # reponse incomplete : surtout ne pas en
        raise RuntimeError("openpositions : reponse incomplete")   # conclure "plus de position"
    for p in j["openPositions"] or []:
        out[(p.get("symbol") or "").upper()] = {
            "dir": 1 if p.get("side") == "long" else -1,
            "size": float(p.get("size") or 0),
            "price": float(p.get("price") or 0)}
    return out


def open_order_ids():
    """Identifiants des ordres encore ouverts chez Kraken (stops / TP compris)."""
    j = priv("GET", "/api/v3/openorders")
    if "openOrders" not in j:
        raise RuntimeError("openorders : reponse incomplete")
    return set(o.get("order_id") or o.get("orderId") for o in (j.get("openOrders") or []))


def equity(st):
    """(capital total, marge disponible) : le risque se calcule sur le total, la taille
    maxi sur la marge encore libre (plusieurs positions en meme temps)."""
    if not LIVE:
        return st["paper"], st["paper"]
    f = (priv("GET", "/api/v3/accounts").get("accounts") or {}).get("flex") or {}
    av = float(f.get("availableMargin") or 0)
    tot = float(f.get("marginEquity") or f.get("portfolioValue") or av or 0)
    return tot, (av if f.get("availableMargin") is not None else tot)



# =========================================================================
#  OUTILS
# =========================================================================
def tick_dec(t):
    txt = ("%.12f" % t).rstrip("0")
    return len(txt.split(".")[1]) if "." in txt else 0


def rp(st, sym, x):
    t = (st["inst"].get(sym) or {}).get("tick") or 0.0001
    return round(round(x / t) * t, tick_dec(t))


def px_str(st, sym, x):
    """Prix arrondi au tick, ecrit en decimal (jamais 1.2e-05 pour PEPE / SHIB)."""
    t = (st["inst"].get(sym) or {}).get("tick") or 0.0001
    return "%.*f" % (tick_dec(t), rp(st, sym, x))


def qty_str(st, sym, q):
    d = st["inst"].get(sym) or {}
    return "%.*f" % (max(0, d["sp"] if "sp" in d else 4), q)


def rq(st, sym, x):
    d = st["inst"].get(sym) or {}
    f = 10.0 ** (d["sp"] if "sp" in d else 4)
    return math.floor(x * f + 1e-9) / f


def nm(sym):
    return sym.replace("PF_", "").replace("USD", "").replace("XBT", "BTC").replace("XDG", "DOGE") + " PERP"


def notify(st, msg, html=False):
    # journaux GitHub publics : on masque le capital et le P&L (Telegram recoit tout)
    pub_msg = _html.unescape(re.sub(r"<[^>]+>", "", msg)) if html else msg
    pub_msg = re.sub(r"Capital du compte : [0-9.]+", "Capital du compte : (masque)", pub_msg)
    pub_msg = re.sub(r"P&L ~ [+-]?[0-9.]+ \$", "P&L ~ (masque)", pub_msg)
    pub_msg = re.sub(r"perte max ~[0-9.]+ \$", "perte max ~(masque)", pub_msg)   # = % du capital
    pub_msg = re.sub(r"qte [0-9.]+", "qte (masque)", pub_msg)          # qte x risque = capital
    pub_msg = re.sub(r"reste [0-9.]+", "reste (masque)", pub_msg)
    log.info(pub_msg)
    st["log"] = (st.get("log") or [])[-59:] + [time.strftime("%d/%m %H:%M") + " " + msg]
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return
    # envoi en arriere-plan : une panne Telegram ne retarde plus la gestion des positions
    threading.Thread(target=_send_telegram, args=(tok, chat, msg, html), daemon=True).start()


def esc(t):
    return _html.escape(str(t), quote=False)


def alert(st, txt):
    """Message d'alerte (probleme technique) : bien visible."""
    notify(st, "⚠️ <b>ALERTE · bot crypto</b>\n" + esc(txt), html=True)


def info(st, icon, title, body=""):
    notify(st, "%s <b>%s</b>%s" % (icon, title, ("\n" + body) if body else ""), html=True)


def _send_telegram(tok, chat, msg, html=False):
    for essai in range(3):                       # Telegram coupe parfois : 3 essais
        try:
            body = {"chat_id": chat, "text": msg, "disable_web_page_preview": True}
            if html:
                body["parse_mode"] = "HTML"
            r = SESSION.post("https://api.telegram.org/bot%s/sendMessage" % tok, json=body, timeout=8)
            if r.ok:
                return
            log.warning("telegram HTTP %s", r.status_code)
            if html and r.status_code == 400:    # mise en forme refusee : texte brut
                html, msg = False, _html.unescape(re.sub(r"<[^>]+>", "", msg))
                continue
        except Exception as e:
            log.warning("telegram (essai %d) : %s", essai + 1, e)
        time.sleep(2 + 3 * essai)


def side_of(dr):
    return "buy" if dr > 0 else "sell"


def order(p):
    if not LIVE:
        return "paper-%d" % time.time()
    s = priv("POST", "/api/v3/sendorder", p).get("sendStatus") or {}
    if s.get("status") and s["status"] not in (
            "placed", "attempted", "untouched", "partiallyFilled", "filled"):
        raise RuntimeError("ordre refuse : " + s["status"])
    oid = s.get("order_id") or s.get("orderId")
    if not oid:
        raise RuntimeError("ordre sans identifiant renvoye par Kraken (%s)" % (s.get("status") or s))
    return oid


def cancel(oid):
    if LIVE and oid and not str(oid).startswith("paper"):
        try:
            priv("POST", "/api/v3/cancelorder", {"order_id": oid})
        except Exception as e:
            log.warning("annulation : %s", e)



# =========================================================================
#  INDICATEURS
# =========================================================================
def atr(h, l, c, p):
    out, tr, s = [None] * len(c), [0.0] * len(c), 0.0
    for i in range(len(c)):
        a = h[i] - l[i]
        if i > 0:
            a = max(a, abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        tr[i] = a
        s += a
        if i >= p:
            s -= tr[i - p]
        if i >= p - 1:
            out[i] = s / p
    return out



# =========================================================================
#  ORDRES : STOP / TP / SORTIE
# =========================================================================
def place_exits(st, sym, p, stop=True, tp=True):
    """Pose le stop et/ou le TP. Le NOUVEL ordre est pose AVANT d'annuler l'ancien :
       la position n'est jamais sans protection, meme une seconde."""
    if p["qty"] <= 0:
        return
    old_stop, old_tp = p.get("stop_id"), p.get("tp_id")
    if stop:
        _place_stop(st, sym, p, old_stop)
    if tp:
        _place_tp(st, sym, p, old_tp)


def _place_stop(st, sym, p, old_stop):
    try:
        new = order({"orderType": "stp", "symbol": sym, "side": side_of(-p["dir"]),
                     "size": qty_str(st, sym, p["qty"]), "stopPrice": px_str(st, sym, p["sl"]),
                     "triggerSignal": "mark", "reduceOnly": "true"})
        p["stop_id"], p["stopfail"], p["stop_px"] = new, 0, p["sl"]
        p.pop("warned", None)
        if old_stop and old_stop != new:
            cancel(old_stop)
    except Exception as e:
        p["stop_id"] = old_stop                    # l'ancien stop reste en place chez Kraken
        p["stopfail"] = p.get("stopfail", 0) + 1
        if not p.get("warned"):                    # un seul message, pas un par minute
            p["warned"] = True
            alert(st, "%s : stop Kraken NON pose (%s) -> nouvel essai, fermeture si 2e echec" % (nm(sym), e))


def _place_tp(st, sym, p, old_tp):
    try:
        new = order({"orderType": "take_profit", "symbol": sym, "side": side_of(-p["dir"]),
                     "size": qty_str(st, sym, p["qty"]), "stopPrice": px_str(st, sym, p["tp"]),
                     "triggerSignal": "last", "reduceOnly": "true"})
        p["tp_id"], p["tpfail"] = new, 0
        if old_tp and old_tp != new:
            cancel(old_tp)
    except Exception as e:
        p["tp_id"] = old_tp
        p["tpfail"] = p.get("tpfail", 0) + 1
        log.warning("TP : %s", e)
        if p["tpfail"] == 1:
            alert(st, "%s : TP Kraken NON pose (%s) -> nouvel essai ; le bot surveille le TP lui-meme" % (nm(sym), e))


def cancel_all(sym):
    """Annule TOUS les ordres restants du symbole (stop / TP orphelins). True si reussi."""
    if not LIVE:
        return True
    try:
        priv("POST", "/api/v3/cancelallorders", {"symbol": sym})
        return True
    except Exception as e:
        log.warning("annulation globale %s : %s", sym, e)
        return False


def close_mkt(st, sym, p, price, why):
    CRIT[0] += 1
    try:
        return _close_mkt(st, sym, p, price, why)
    finally:
        CRIT[0] -= 1


def _close_mkt(st, sym, p, price, why):
    qty = rq(st, sym, p["qty"])
    if qty > 0:
        try:
            # on sort D'ABORD : si ca echoue, le stop / TP chez Kraken restent en place
            order({"orderType": "mkt", "symbol": sym, "side": side_of(-p["dir"]),
                   "size": qty_str(st, sym, qty), "reduceOnly": "true"})
        except Exception as e:
            alert(st, "%s : ERREUR de fermeture (stop Kraken conserve) : %s" % (nm(sym), e))
            if LIVE and not p.get("stop_id") and p.get("sl"):
                place_exits(st, sym, p)
            return False
        if LIVE:
            # un ordre au marche "place" peut n'etre execute qu'en partie (protection de prix
            # Kraken) : on n'annule le stop que si la position est VRAIMENT fermee
            rem = None
            for _ in range(4):
                time.sleep(1)
                try:
                    ex = open_positions().get(sym)
                    rem = ex["size"] if ex and ex["dir"] == p["dir"] else 0.0
                    break
                except Exception:
                    continue
            if rem is None or rem > 0:
                if rem:
                    p["qty"] = rem
                    alert(st, "%s : fermeture PARTIELLE, reste %s, stop / TP Kraken conserves" % (nm(sym), rem))
                return False                       # suivi au prochain tour (stop toujours pose)
    cancel_all(sym)
    pnl = (price - p["entry"]) * p["dir"] * qty - CFG["FEE_TAKER"] * price * qty
    if not LIVE:
        st["paper"] += pnl
    st["pos"].pop(sym, None)
    st.get("pending", {}).pop(sym, None)
    icon = "🎯" if "TP" in why else "🛑" if "STOP" in why else "⏰" if "duree" in why else "⚠️"
    title = ("TP TOUCHÉ" if "TP" in why else "SL TOUCHÉ" if why == "STOP" else why.split(" (")[0].split(" :")[0].upper())
    notify(st, "%s <b>%s · %s</b>\nSortie <code>%s</code> · P&amp;L ~ %+.2f $%s" % (
        icon, esc(title), nm(sym), rp(st, sym, price), pnl,
        ("\n<i>%s</i>" % esc(why)) if title.upper() != why.upper() and why not in ("STOP", "TP atteint") else ""),
        html=True)
    save_state(st)                                 # un redemarrage ne doit rien perdre
    return True


def adopt_orphans(st, pos_ex, px, C):
    """Position ouverte chez Kraken mais inconnue du bot (job tue, etat perdu...) :
       on la reprend avec un stop d'urgence et un TP, pour ne JAMAIS la laisser nue."""
    if not C["ADOPT_ORPHANS"]:
        return
    for sym, ex in pos_ex.items():
        if sym in st["pos"] or ex["size"] <= 0 or not sym.startswith("PF_"):
            continue
        if sym not in st["inst"]:                  # pas une crypto suivie : precision inconnue
            if sym not in st.setdefault("adopt_skip", []):
                st["adopt_skip"].append(sym)
                info(st, "ℹ️", "%s · position hors liste crypto" % nm(sym), "le bot n'y touche pas")
            continue
        price = px.get(sym) or ex["price"]
        try:
            b = candles(sym, "15m")
            a = atr([x[2] for x in b], [x[3] for x in b], [x[4] for x in b], C["ATR_PERIOD"])[-1]
        except Exception:
            a = None
        a = a or price * 0.01
        d, e = ex["dir"], ex["price"] or price
        sl = min(e, price) - 1.5 * a if d > 0 else max(e, price) + 1.5 * a
        pend = st.get("pending", {}).pop(sym, None)
        recent = pend and pend["dir"] == d and time.time() - pend["ts"] < 6 * 3600
        if recent and (price - pend["sl"]) * d <= 0:
            # entree du bot deja au-dela de SON stop prevu : on sort tout de suite
            tmp = {"dir": d, "entry": e, "qty": ex["size"], "sl": pend["sl"], "tp": pend["tp"]}
            st["pos"][sym] = tmp
            close_mkt(st, sym, tmp, price, "SORTIE : position reprise deja au-dela de son stop")
            continue
        if recent:
            sl = pend["sl"]                        # entree du bot dont la confirmation s'est perdue
        p = {"dir": d, "entry": e, "sl": sl, "tp": e + d * C["RR"] * abs(e - sl), "qty": ex["size"],
             "risk0": abs(e - sl),
             "tf": "15m", "wall": None, "wall_side": 0, "wall_key": None, "adopted": True,
             "bar_ts": int(time.time()), "stop_id": None, "tp_id": None}
        # pas de cancel_all : on ne touche pas aux ordres poses a la main ou par un autre outil
        st["pos"][sym] = p
        place_exits(st, sym, p)
        info(st, "⚠️", "%s · position Kraken inconnue reprise" % nm(sym),
             "%s qte %s\n🛑 SL d'urgence <code>%s</code> · 🎯 TP <code>%s</code>"
             % ("ACHAT" if d > 0 else "VENTE", ex["size"], rp(st, sym, sl), rp(st, sym, p["tp"])))



# =========================================================================
#  ETAT
# =========================================================================
def load_state(C):
    st = {"pos": {}, "walls": {}, "seen": [], "last": {}, "paper": C["PAPER_BALANCE"],
          "inst": {}, "lev": {}, "log": [], "mode": None, "stats": {}}
    for fn in (STATE_FILE, STATE_FILE + ".bak"):   # copie de secours si le fichier est abime
        if not os.path.exists(fn):
            continue
        try:
            with open(fn) as f:
                st.update(json.load(f))
            if fn != STATE_FILE:
                log.warning("etat principal illisible -> copie de secours utilisee")
            for p in st.get("pos", {}).values():
                p.setdefault("qty", 0.0)           # relue chez Kraken au 1er suivi
            return st
        except Exception as e:
            log.warning("etat %s illisible (%s)", fn, e)
    return st


_SAVING = [False]
STOP = [False]
CRIT = [0]                                         # > 0 : entree / sortie en cours (ne pas couper)


def save_state(st):
    """Ecriture ATOMIQUE : fichier temporaire puis remplacement. Un arret brutal au milieu
       ne peut plus laisser un fichier tronque (l'ancien reste intact + copie .bak)."""
    if _SAVING[0]:                                 # deja en cours (signal pendant la sauvegarde)
        return
    _SAVING[0] = True
    try:
        tmp = STATE_FILE + ".tmp%d" % os.getpid()
        # depot PUBLIC : ni journal des messages (capital, P&L), ni QUANTITES des positions
        # (quantite x distance du stop = capital) ; en reel la taille est relue chez Kraken
        data = {k: v for k, v in st.items() if k != "log"}
        if LIVE:
            data["pos"] = {s: {k: v for k, v in p.items() if k != "qty"} for s, p in st["pos"].items()}
        with open(tmp, "w") as f:
            json.dump(data, f, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(STATE_FILE):
            try:                                   # COPIE (pas de deplacement) : le fichier
                shutil.copy2(STATE_FILE, STATE_FILE + ".bak")   # d'etat n'est jamais absent
            except Exception:
                pass
        os.replace(tmp, STATE_FILE)
        d = os.environ.get("STATE_DIR")            # copie hors du depot pour le workflow :
        if d:                                      # jamais ecrasee par un git reset
            os.makedirs(d, exist_ok=True)
            shutil.copy2(STATE_FILE, os.path.join(d, STATE_FILE + ".tmp"))
            os.replace(os.path.join(d, STATE_FILE + ".tmp"), os.path.join(d, STATE_FILE))
    except Exception as e:
        log.warning("etat non sauvegarde : %s", e)
    finally:
        _SAVING[0] = False


def check_keys(st):
    if not (os.environ.get("KRAKEN_KEY") and os.environ.get("KRAKEN_SECRET")):
        alert(st, "aucune cle Kraken Futures -> le mode REEL ne pourra pas trader.")
        return False
    try:
        f = (priv("GET", "/api/v3/accounts").get("accounts") or {}).get("flex") or {}
        eq = float(f.get("marginEquity") or f.get("portfolioValue") or 0)
        info(st, "🔑", "Cles Kraken Futures valides", "Capital du compte : %.2f USD" % eq)
        if eq <= 0:
            alert(st, "capital a 0 sur le compte Futures -> aucun ordre possible.")
        return True
    except Exception as e:
        alert(st, "cles Kraken REFUSEES (%s)" % e)
        return False




# =========================================================================
#  FOOTPRINT 1 MINUTE + VWAP (construits a partir de chaque trade Kraken)
# =========================================================================
def event(st, txt):
    """Journal SANS donnees sensibles (pas de capital ni de P&L) : sauve dans le depot,
       pour pouvoir verifier a tout moment ce que le bot voit."""
    st["events"] = (st.get("events") or [])[-149:] + [time.strftime("%d/%m %H:%M ") + txt]
    log.info(txt)


class Flow:
    """Un actif : bougies footprint (BAR_TF, 15 min), VWAP du jour (UTC), gros ordres, et la
       checklist en cours pour chaque sens."""

    def __init__(self, sym):
        self.sym = sym
        self.bars = deque(maxlen=300)              # bougies cloturees (anciennes : sans delta)
        self.cur = None
        self.seq = 0                               # dernier trade lu dans le flux
        self.day = None
        self.pv = 0.0                              # somme prix x volume du jour
        self.vv = 0.0                              # volume du jour
        self.prints = deque(maxlen=CFG["BIG_SAMPLE"])   # tailles des derniers ordres agressifs
        self.setup = {1: None, -1: None}
        self.seed_until = 0
        self.last_px = None
        self.last_ts = 0.0
        self.first_live = True
        self.last_t = 0                            # debut de la derniere bougie cloturee
        self.epoch = getattr(FEED, "epoch", 0)     # coupures du flux deja prises en compte

    # ---------- historique : VWAP depuis minuit UTC + contexte de tendance ----------
    def seed(self, now=None):
        now = now or time.time()
        day0 = int(now // 86400 * 86400)
        B = CFG["BAR_SEC"]
        # contexte de tendance (TREND_BARS + marge) et VWAP depuis 0 h UTC ; le debut est
        # cale sur un minuit UTC pour que la VWAP de la veille soit aussi la vraie VWAP du jour
        frm = min(day0, int(now) - (CFG["TREND_BARS"] + 8) * B) // 86400 * 86400
        j = pub("/api/charts/v1/trade/%s/%s" % (self.sym, CFG["BAR_TF"]), {"from": frm, "to": int(now)})
        cur_min = int(now // B * B)
        self.bars.clear()
        self.day, self.pv, self.vv = None, 0.0, 0.0
        for r in j.get("candles") or []:
            t = int(r["time"]) // 1000
            if t >= cur_min:                       # bougie en cours : construite par le flux
                continue
            o, h, l, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
            v = float(r.get("volume") or 0)
            d = t // 86400
            if d != self.day:
                self.day, self.pv, self.vv = d, 0.0, 0.0
            self.pv += (h + l + c) / 3.0 * v
            self.vv += v
            self.bars.append({"t": t, "o": o, "h": h, "l": l, "c": c, "vol": v,
                              "vwap": self.pv / self.vv if self.vv else c, "live": False})
            self.last_px = c
            self.last_t = t
        self.seed_until = cur_min
        self.first_live = True
        self.cur = None

    def vwap(self):
        return self.pv / self.vv if self.vv else None

    # ---------- chaque trade ----------
    def ingest(self, trades, now):
        """trades : (t, prix, qte, cote_agresseur, n). Renvoie les bougies cloturees."""
        closed = []
        for t, price, qty, side, n in trades:
            self.seq = max(self.seq, n)
            if t < self.seed_until or qty <= 0:
                continue                           # deja compte dans l'historique
            minute = int(t // CFG["BAR_SEC"] * CFG["BAR_SEC"])
            if minute <= self.last_t or (self.cur and minute < self.cur["t"]):
                # trade en retard (bougie deja cloturee) : jamais de "bougie fantome",
                # il compte seulement dans la VWAP de son jour
                if int(t // 86400) == self.day:
                    self.pv += price * qty
                    self.vv += qty
                continue
            if self.cur and minute > self.cur["t"]:
                closed.append(self._close())
            d = int(t // 86400)
            if d != self.day:                      # nouveau jour UTC : VWAP remise a zero
                self.day, self.pv, self.vv = d, 0.0, 0.0
                # (les checklists de la veille sont annulees dans evaluate, APRES lecture
                #  de la bougie de 23h45)
            if not self.cur:
                self.cur = {"t": minute, "o": price, "h": price, "l": price, "c": price,
                            "buy": 0.0, "sell": 0.0, "n": 0, "fills": [],
                            "partial": self.first_live}
                self.first_live = False
            b = self.cur
            b["h"], b["l"], b["c"] = max(b["h"], price), min(b["l"], price), price
            b["n"] += 1
            if side == "buy":
                b["buy"] += qty
            else:
                b["sell"] += qty
            b["fills"].append((t, price, qty, side))
            self.pv += price * qty
            self.vv += qty
            self.last_px, self.last_ts = price, t
        # bougie terminee sans nouveau trade : on cloture quand meme
        if self.cur and now >= self.cur["t"] + CFG["BAR_SEC"] + 10:
            closed.append(self._close())
        return [b for b in closed if b]

    def _close(self):
        b, self.cur = self.cur, None
        if not b:
            return None
        if hasattr(FEED, "missing") and \
                FEED.missing(b["t"], b["t"] + CFG["BAR_SEC"]) > 0.1 * CFG["BAR_SEC"]:
            b["partial"] = True                    # plus de 10 % de la bougie sans flux : pas lue
        self.last_t = b["t"]
        vol = b["buy"] + b["sell"]
        b["vol"] = vol
        b["delta"] = b["buy"] - b["sell"]
        b["dpct"] = b["delta"] / vol if vol else 0.0
        b["vwap"] = self.vwap() or b["c"]
        # ordres agressifs : les executions d'un MEME ordre arrivent a la meme milliseconde
        agg = {}
        for t, price, qty, side in b["fills"]:
            k = (int(t * 1000), side)
            q, pq = agg.get(k, (0.0, 0.0))
            agg[k] = (q + qty, pq + price * qty)
        ref = None
        if len(self.prints) >= CFG["BIG_WARMUP"]:
            srt = sorted(self.prints)
            ref = srt[min(len(srt) - 1, int(0.99 * len(srt)))] or None   # 99e centile
        b["big"] = []
        mult = min(CFG["BIG_MULT"], GRID["BIG_MULT"][0])   # l'IA juge ensuite le multiple utile
        for (ms, side), (q, pq) in agg.items():
            if ref and q >= mult * ref:
                b["big"].append({"price": pq / q, "side": side, "size": q, "x": q / ref})
        self.prints.extend(q for q, _ in agg.values())
        b["live"] = True
        b.pop("fills", None)
        self.bars.append(b)
        return b

    def atr(self):
        bs = list(self.bars)[-(CFG["ATR_PERIOD"] + 2):]
        if len(bs) < CFG["ATR_PERIOD"] + 1:
            return None
        return atr([x["h"] for x in bs], [x["l"] for x in bs], [x["c"] for x in bs],
                   CFG["ATR_PERIOD"])[-1]


# =========================================================================
#  LA CHECKLIST EN 4 POINTS
# =========================================================================
def _big_in(b, d, lo, hi, min_x):
    """Plus gros ordre agressif DANS NOTRE SENS dans la zone [lo, hi] et RESPECTE (la bougie
       cloture au-dela de son prix) : un gros ordre casse ne masque plus un gros ordre tenu."""
    want = "buy" if d > 0 else "sell"
    c = [x for x in b.get("big") or [] if x["side"] == want and lo <= x["price"] <= hi
         and x["x"] >= min_x and (b["c"] - x["price"]) * d > 0]
    return max(c, key=lambda x: x["size"]) if c else None


def _zone_dist(price, lo, hi, v):
    """Distance (%) du big trade a la bougie d'absorption : jugee par l'IA comme le contact."""
    return max(0.0, lo - price, price - hi) / v * 100.0


def _start(fl, b, prev, d, L):
    """Points 1 + 2 (criteres SOUPLES : l'IA trie ensuite). Renvoie la checklist ou None."""
    if b.get("partial") or b["n"] < L["MIN_BAR_TRADES"] or len(prev) < L["TREND_BARS"]:
        return None
    if b["t"] % 86400 < 3600:
        return None                                # 1re heure du jour : VWAP pas encore formee
    v = b["vwap"]
    last = prev[-L["TREND_BARS"]:]
    trend = sum(1 for x in last if (x["c"] - x["vwap"]) * d > 0) / float(len(last))
    if trend < L["TREND_MIN"]:
        return None
    # distance a la VWAP en % : meche jusqu'a la VWAP + cloture pas au-dela
    touch = (max(b["l"] - v, v - b["c"]) if d > 0 else max(v - b["h"], b["c"] - v)) / v * 100.0
    a = -b["dpct"] * d                             # force de l'absorption (vendeurs pour un achat)
    if touch > L["TOUCH_PCT"] or a < L["ABS_DELTA"]:
        return None
    return {"stage": 1, "t": b["t"], "lo": b["l"], "hi": b["h"], "age": 0, "abs": b["dpct"],
            "a": a, "trend": trend, "touch": max(touch, 0.0)}


def evaluate(fl, b, st, C):
    """Appelee a chaque bougie cloturee (BAR_TF). Construit la checklist avec des criteres
       SOUPLES, puis l'IA (gate) decide avec les criteres appris si l'entree est prise.
       Renvoie un signal (avec "take") ou None."""
    if not b.get("live"):
        return None
    L = loose(C)
    v = b["vwap"]
    tol = v * L["TOUCH_PCT"] / 100.0
    prev = [x for x in list(fl.bars) if x["t"] < b["t"] and x.get("vwap")]
    for d in (1, -1):
        side_txt = "ACHAT" if d > 0 else "VENTE"
        S = fl.setup[d]
        if S and b["t"] // 86400 != S["t"] // 86400:
            fl.setup[d] = S = None                 # nouveau jour UTC : nouvelle VWAP, on oublie
        if S:
            S["age"] = (b["t"] - S["t"]) // C["BAR_SEC"]   # en temps : une coupure ne prolonge rien
            fresh = _start(fl, b, prev, d, L) if S["stage"] == 1 else None
            if fresh and fresh["a"] > S["a"]:
                fl.setup[d] = S = None             # absorption plus forte : on repart de celle-ci
            elif (b["c"] - (v - d * tol)) * d < 0:
                # cloture du mauvais cote de la VWAP : "gros acheteur sur la VWAP...
                # cloture en dessous. Pas d'absorption. Pas de long."
                event(st, "%s %s annule : cloture %s la VWAP" % (nm(fl.sym), side_txt,
                                                                "sous" if d > 0 else "au-dessus de"))
                fl.setup[d] = None
                continue
            elif S["age"] >= C["SEQ_BARS"]:
                fl.setup[d] = None                 # sequence complete en SEQ_BARS bougies maxi
                continue
            if S:
                # pire cloture sous la VWAP pendant la sequence : jugee par l'IA avec la
                # meme tolerance que le contact (0,15 % au depart en 15 min)
                pen = ((v - b["c"]) if d > 0 else (b["c"] - v)) / v * 100.0
                S["touch"] = max(S["touch"], pen)
                if b.get("partial") or b["n"] < L["MIN_BAR_TRADES"]:
                    continue                       # bougie incomplete ou trop maigre : pas lue
            if not S:
                pass
            elif S["stage"] == 1:
                big = _big_in(b, d, S["lo"] - tol, S["hi"] + tol, L["BIG_MULT"])
                if big:                                          # bougie cloture au-dela : RESPECTE
                    S.update(stage=2, big=big["price"], big_x=big["x"])
                    S["touch"] = max(S["touch"], _zone_dist(big["price"], S["lo"], S["hi"], v))
                    event(st, "%s %s 3/4 : big trade x%.1f (vs top 1 %%) respecte a %s"
                          % (nm(fl.sym), side_txt, big["x"], px_str(st, fl.sym, big["price"])))
                continue
            elif S["stage"] == 2:
                big = _big_in(b, d, S["lo"] - tol, S["hi"] + tol, L["BIG_MULT"])
                if big and big["x"] > S.get("big_x", 0) and \
                        _zone_dist(big["price"], S["lo"], S["hi"], v) <= S["touch"]:
                    S.update(big=big["price"], big_x=big["x"])   # un plus gros defenseur
                pc = prev[-1]["c"] if prev else b["o"]
                if b["dpct"] * d >= L["BUY_DELTA"] and (b["c"] - pc) * d > 0:
                    sig = {"dir": d, "close": b["c"], "vwap": v, "lo": S["lo"], "hi": S["hi"],
                           "big": S["big"], "big_x": S.get("big_x", 0), "abs": S["abs"],
                           "conf": b["dpct"], "atr": fl.atr(), "trend": S["trend"],
                           "touch": S["touch"], "sym": fl.sym, "t": b["t"], "new_sample": True}
                    ok, why = gate(sig, st, C)
                    sig["take"] = ok
                    # la 1re confirmation decide (regle "bougie suivante") : prise ou non, la
                    # checklist se termine, et c'est CE setup que l'IA apprend
                    fl.setup = {1: None, -1: None}
                    if ok:
                        event(st, "%s %s 4/4 : %s de retour (delta %+d%%) -> ENTREE CONFIRMEE"
                              % (nm(fl.sym), side_txt, "acheteurs" if d > 0 else "vendeurs",
                                 int(100 * b["dpct"])))
                    else:
                        event(st, "%s %s 4/4 souple, pas retenu par l'IA (%s) : suivi pour apprendre"
                              % (nm(fl.sym), side_txt, why))
                    return sig
                continue
        if fl.setup[d] or fl.setup[-d]:
            continue                               # une seule checklist a la fois par actif
        S = _start(fl, b, prev, d, L)
        if not S:
            continue
        event(st, "%s %s 1-2/4 : prix sur la VWAP %s, %s absorbes (delta %+d%%)"
              % (nm(fl.sym), side_txt, px_str(st, fl.sym, v), "vendeurs" if d > 0 else "acheteurs",
                 int(100 * b["dpct"])))
        big = _big_in(b, d, b["l"] - tol, b["h"] + tol, L["BIG_MULT"])  # peut etre dans la meme bougie
        if big:
            S.update(stage=2, big=big["price"], big_x=big["x"])
            S["touch"] = max(S["touch"], _zone_dist(big["price"], b["l"], b["h"], v))
            event(st, "%s %s 3/4 : big trade x%.1f (vs top 1 %%) respecte a %s"
                  % (nm(fl.sym), side_txt, big["x"], px_str(st, fl.sym, big["price"])))
        fl.setup[d] = S
    return None


# =========================================================================
#  IA D'APPRENTISSAGE  -  le bot s'ameliore seul, avec des garde-fous
# =========================================================================
#  1. Chaque checklist 4/4 trouvee avec des criteres SOUPLES devient un "setup" (pris
#     ou non). Le bot suit ensuite son resultat comme si le trade avait ete pris (stop,
#     TP, stop a zero perte, trailing, frais) : il apprend aussi des trades NON pris.
#  2. Toutes les LEARN_EVERY_MIN minutes, des que LEARN_MIN resultats sont connus, l'IA
#     cherche les criteres (force d'absorption, taille du big trade, retour des acheteurs,
#     tendance, distance a la VWAP) qui auraient donne le meilleur resultat, en jugeant
#     sur la borne BASSE de l'esperance (moyenne - incertitude) : pas sur la chance.
#     Un reglage n'est adopte que si cette borne basse est POSITIVE (preuve d'un avantage).
#  3. Garde-fous : un seul cran de reglage par parametre et par cycle, plages bornees,
#     preuve a 2,5 ecarts-types sur au moins 30 bougies DISTINCTES (10 cryptos qui sortent
#     ensemble ne font pas 10 preuves), confirmation sur les 30 % de setups les plus recents
#     (jamais vus par la selection). Teste : sur du pur hasard, 1 changement sur 200.
#     Un actif qui perd nettement est mis en pause 24 h. Si la strategie perd sur la
#     periode recente, le risque par trade est divise par 2 (jamais augmente au-dela du
#     reglage, jamais de levier en plus).
GRID = {
    "ABS_DELTA": [0.10, 0.15, 0.20, 0.25, 0.30, 0.40],   # force mini de l'absorption
    "BIG_MULT": [1.0, 1.3, 1.5, 2.0, 3.0, 5.0, 8.0],          # taille mini du big trade (x 99e centile)
    "BUY_DELTA": [0.10, 0.15, 0.20, 0.25, 0.30, 0.40],   # force mini du retour des acheteurs
    "TREND_MIN": [0.6, 0.65, 0.7, 0.8, 0.9],                   # tendance mini avant le retour a la VWAP
    "TOUCH_PCT": [0.30, 0.25, 0.20, 0.15, 0.10, 0.06],   # distance maxi a la VWAP (%)
}
FEAT = {"ABS_DELTA": 2, "BIG_MULT": 3, "BUY_DELTA": 4, "TREND_MIN": 5, "TOUCH_PCT": 6}
LEARN_NMIN = 30                                    # bougies distinctes mini par jeu de criteres
LEARN_Z = 2.5                                      # exigence de preuve (ecarts-types)
LEARN_V = 3                                        # v3 : big trade au 99e centile + IA plus exigeante


def loose(C):
    """Criteres les plus souples de la grille : le bot voit tout, l'IA trie."""
    L = dict(C)
    L["ABS_DELTA"] = min(C["ABS_DELTA"], GRID["ABS_DELTA"][0])
    L["BIG_MULT"] = min(C["BIG_MULT"], GRID["BIG_MULT"][0])
    L["BUY_DELTA"] = min(C["BUY_DELTA"], GRID["BUY_DELTA"][0])
    L["TREND_MIN"] = min(C["TREND_MIN"], GRID["TREND_MIN"][0])
    L["TOUCH_PCT"] = max(C["TOUCH_PCT"], GRID["TOUCH_PCT"][0])
    return L


def _snap(k, v):
    g = GRID[k]
    return min(g, key=lambda x: abs(x - v))


def learn_st(st, C):
    lr = st.get("learn")
    if not isinstance(lr, dict) or lr.get("tf") != C["BAR_TF"] or lr.get("v") != LEARN_V:
        lr = st["learn"] = {"tf": C["BAR_TF"], "v": LEARN_V}   # autre unite / autre mesure : on reapprend
    base = {k: _snap(k, C[k]) for k in GRID}
    if not isinstance(lr.get("params"), dict) or lr.get("base") != base:
        lr["params"] = dict(base)        # depart (ou reglage change a la main) : criteres du reglage
        lr["base"] = base                # l'historique appris est conserve
    lr.setdefault("open", [])
    lr.setdefault("done", [])
    lr.setdefault("paused", {})
    lr.setdefault("risk_mult", 1.0)
    lr.setdefault("hist", [])
    lr.setdefault("ts", 0)
    return lr


def active(st, C):
    """Criteres en vigueur (appris par l'IA, ou ceux de la video si LEARN=0)."""
    if not C["LEARN"]:
        return {k: C[k] for k in GRID}
    return learn_st(st, C)["params"]


def _feat(sig):
    d = sig["dir"]
    return [-sig["abs"] * d, sig.get("big_x") or 0.0, sig["conf"] * d, sig["trend"], sig["touch"]]


def _passes(fe, P):
    a, x, c, tr, to = fe
    return (a >= P["ABS_DELTA"] - 1e-9 and x >= P["BIG_MULT"] - 1e-9 and c >= P["BUY_DELTA"] - 1e-9
            and tr >= P["TREND_MIN"] - 1e-9 and to <= P["TOUCH_PCT"] + 1e-9)


def gate(sig, st, C):
    """L'IA decide : le setup passe-t-il les criteres appris ? (renvoie ok, raison)"""
    P = active(st, C)
    a, x, c, tr, to = _feat(sig)
    if a < P["ABS_DELTA"] - 1e-9:
        return False, "absorption %d%% < %d%%" % (100 * a, 100 * P["ABS_DELTA"])
    if x < P["BIG_MULT"] - 1e-9:
        return False, "big trade x%.1f < x%.1f" % (x, P["BIG_MULT"])
    if c < P["BUY_DELTA"] - 1e-9:
        return False, "retour %d%% < %d%%" % (100 * c, 100 * P["BUY_DELTA"])
    if tr < P["TREND_MIN"] - 1e-9:
        return False, "tendance %d%% < %d%%" % (100 * tr, 100 * P["TREND_MIN"])
    if to > P["TOUCH_PCT"] + 1e-9:
        return False, "trop loin de la VWAP (%.2f%%)" % to
    if C["LEARN"]:
        until = learn_st(st, C)["paused"].get(sig["sym"], 0)
        if time.time() < until:
            return False, "actif en pause (il perdait)"
    return True, ""


def learn_record(st, sig, C):
    """Nouveau setup : suivi virtuel de son resultat (meme stop / TP que le bot)."""
    if not C["LEARN"]:
        return
    lr = learn_st(st, C)
    pl = plan(sig, sig["close"], C)
    if not pl:
        return
    e, sl, tp = pl
    if not sl_ok(e, sl, C):
        return                                     # le bot ne le prendrait jamais : rien a apprendre
    lr["open"] = lr["open"][-299:] + [{"s": sig["sym"], "t": sig["t"], "d": sig["dir"], "e": e, "sl": sl,
                                      "tp": tp, "r": abs(e - sl), "best": e, "be": 0,
                                      "f": [round(v, 6) for v in _feat(sig)]}]


def learn_track(st, sym, b, C):
    """A chaque bougie cloturee : fait avancer les setups suivis (prudent : si le stop et
       le TP sont touches dans la meme bougie, c'est le stop qui compte)."""
    if not C["LEARN"]:
        return
    lr = learn_st(st, C)
    lt = lr.setdefault("last_t", {})
    if b["t"] <= lt.get(sym, 0):
        return                                     # bougie deja vue (rejouee au redemarrage)
    lt[sym] = b["t"]
    keep = []
    for o in lr["open"]:
        if o["s"] != sym or b["t"] <= o["t"]:
            if time.time() - o["t"] < (C["MAX_HOLD_MIN"] + 120) * 60:
                keep.append(o)                     # sans nouvelles trop longtemps : oublie
            continue
        d, e, r = o["d"], o["e"], o["r"]
        fee = 2 * C["FEE_TAKER"] * e
        res = None
        worst, fav = (b["l"], b["h"]) if d > 0 else (b["h"], b["l"])
        if (worst - o["sl"]) * d <= 0:
            res = (o["sl"] - e) * d - fee
        elif (fav - o["tp"]) * d >= 0:
            res = (o["tp"] - e) * d - fee
        else:
            o["best"] = max(o["best"], fav) if d > 0 else min(o["best"], fav)
            if not o["be"] and (o["best"] - e) * d >= C["BE_AT_R"] * r:
                o["sl"], o["be"] = e + d * e * (2 * C["FEE_TAKER"] + C["BE_EXTRA_PCT"] / 100), 1
            if o["be"] and C["TRAIL_R"] > 0:
                nsl = o["best"] - d * C["TRAIL_R"] * r
                if (nsl - o["sl"]) * d >= C["TRAIL_STEP_R"] * r:
                    o["sl"] = nsl
            if (worst - o["sl"]) * d <= 0:
                # le stop remonte (zero perte / trailing) est retouche dans la MEME bougie :
                # le vrai bot serait sorti -> on compte la sortie (jamais plus optimiste)
                res = (o["sl"] - e) * d - fee
            elif b["t"] - o["t"] >= C["MAX_HOLD_MIN"] * 60:
                res = (b["c"] - e) * d - fee
        if res is None:
            keep.append(o)
            continue
        R = res / r if r else 0.0
        lr["done"] = lr["done"][-(int(C["LEARN_KEEP"]) - 1):] + [[int(o["t"]), o["s"]] + o["f"] + [round(R, 3)]]
    lr["open"] = keep


def _sel(done, P, since=None):
    """Setups qui passent les criteres P (et plus recents que since)."""
    return [x for x in done if _passes(x[2:7], P) and (since is None or x[0] > since)]


def _stats(recs, z):
    """(moyenne, borne basse, borne haute, n_eff). n_eff = nombre de bougies DISTINCTES :
       10 cryptos qui sortent sur la meme bougie (meme mouvement du marche) ne comptent pas
       pour 10 preuves independantes."""
    n_eff = len(set(x[0] for x in recs))
    if not recs or n_eff == 0:
        return None, None, None, 0
    Rs = [x[7] for x in recs]
    m = sum(Rs) / len(Rs)
    sd = statistics.pstdev(Rs) if len(Rs) > 1 else 1.0
    w = z * sd / math.sqrt(n_eff)
    return m, m - w, m + w, n_eff


def _score(recs):
    m, lo, hi, n_eff = _stats(recs, LEARN_Z)
    return lo if n_eff >= LEARN_NMIN else None


def _Rs(done, P, paused=None):
    return [x[7] for x in done if _passes(x[2:7], P) and not (paused and x[1] in paused)]


def learn_update(st, C):
    """Le cycle d'apprentissage. Ne change rien tant qu'il n'y a pas assez de preuves :
       borne basse a 2,5 ecarts-types sur des bougies distinctes, puis CONFIRMATION sur les
       30 % de setups les plus recents, que la selection n'a pas vus."""
    if not C["LEARN"]:
        return
    lr = learn_st(st, C)
    now = time.time()
    if now - lr["ts"] < C["LEARN_EVERY_MIN"] * 60:
        return
    lr["ts"] = now
    done = lr["done"]
    if len(done) < C["LEARN_MIN"]:
        return
    cut = int(len(done) * 0.7)
    train, hold = done[:cut], done[cut:]
    P = dict(lr["params"])
    changes = []
    for k in GRID:                                 # un cran maximum par parametre et par cycle
        g = GRID[k]
        i = g.index(P[k]) if P[k] in g else g.index(_snap(k, P[k]))
        base = _score(_sel(train, P))
        best_i, best_s = i, base
        for j in (i - 1, i + 1):
            if not 0 <= j < len(g):
                continue
            Q = dict(P, **{k: g[j]})
            sc = _score(_sel(train, Q))
            if sc is None or (best_s is not None and sc <= best_s + 0.02):
                continue                           # pas d'amelioration nette
            hm, _, _, hn = _stats(_sel(hold, Q), 1.0)
            if hn < 10 or hm <= 0:
                continue                           # pas confirme sur des setups jamais vus
            # la destination doit etre PROUVEE positive : ce cran, ou le suivant dans la
            # meme direction (on peut traverser un cran intermediaire, jamais a l'aveugle)
            target = sc > 0
            j2 = j + (j - i)
            if not target and 0 <= j2 < len(g):
                Q2 = dict(P, **{k: g[j2]})
                sc2 = _score(_sel(train, Q2))
                hm2, _, _, hn2 = _stats(_sel(hold, Q2), 1.0)
                target = sc2 is not None and sc2 > 0 and hn2 >= 10 and hm2 > 0
            if target:
                best_i, best_s = j, sc
        if best_i != i:
            changes.append("%s %s -> %s" % (k, P[k], g[best_i]))
            P[k] = g[best_i]
    # pause des actifs qui perdent nettement : jugee seulement sur les setups posterieurs
    # a la pause precedente (sinon la meme histoire les remet en pause a vie)
    paused = {s: u for s, u in lr["paused"].items() if u > now}
    pend = lr.setdefault("pause_end", {})
    by = {}
    for x in done:
        if _passes(x[2:7], P) and x[0] > pend.get(x[1], 0):
            by.setdefault(x[1], []).append(x)
    for sym, recs in by.items():
        if sym in paused:
            continue
        m, lo, hi, n_eff = _stats(recs, 2.0)
        if n_eff >= 30 and m < -0.25 and hi < 0:
            paused[sym] = pend[sym] = now + 24 * 3600
            changes.append("pause 24 h %s (%+.2f R en moyenne sur %d setups)" % (nm(sym), m, len(recs)))
    lr["paused"] = paused
    # risque : divise par 2 si la strategie perd sur la periode recente, jamais augmente
    recs = _sel(done[-300:], P)
    if len(recs) < 30:
        recs = _sel(done, P)[-300:]
    if len(recs) >= 30:
        m, lo, hi, n_eff = _stats(recs, 2.0)
        rm = 0.5 if hi < 0 else 1.0 if m >= 0 else lr["risk_mult"]
        if rm != lr["risk_mult"]:
            changes.append("risque par trade x%.1f (%+.2f R en moyenne recemment)" % (rm, m))
            lr["risk_mult"] = rm
    lr["params"] = P
    if changes:
        allR = _Rs(done, P)
        txt = "IA : %s | %d setups appris, esperance avec ces criteres %+.2f R (%d setups)" % (
            " ; ".join(changes), len(done), sum(allR) / len(allR) if allR else 0.0, len(allR))
        lr["hist"] = lr["hist"][-29:] + [time.strftime("%d/%m %H:%M ") + txt]
        event(st, txt)
        info(st, "🤖", "IA · reglage mis a jour", esc(txt.replace("IA : ", "", 1)))


def learn_summary(st, C):
    if not C["LEARN"]:
        return "IA desactivee (criteres fixes de la video)"
    lr = learn_st(st, C)
    P = lr["params"]
    Rs = _Rs(lr["done"], P)
    return ("IA : %d setups appris, %d en cours | absorption >= %d%%, big trade >= x%.1f (top 1 %%), retour >= %d%%, "
            "tendance >= %d%%, VWAP +/- %.2f%% | esperance %s | risque x%.1f%s" % (
                len(lr["done"]), len(lr["open"]), 100 * P["ABS_DELTA"], P["BIG_MULT"], 100 * P["BUY_DELTA"],
                100 * P["TREND_MIN"], P["TOUCH_PCT"],
                ("%+.2f R (%d)" % (sum(Rs) / len(Rs), len(Rs))) if Rs else "pas encore de resultat",
                lr["risk_mult"], (" | en pause : " + ", ".join(nm(s) for s in lr["paused"])) if lr["paused"] else ""))


def plan(sig, price, C):
    """Stop sous la bougie d'absorption (safe) ou sous le big trade (aggressive), elargi au
       plancher impose par les frais. TP = RR x risque."""
    d, e = sig["dir"], price
    if C["STOP_MODE"].lower().startswith("agg") and sig.get("big"):
        lvl = sig["big"]
    else:
        lvl = min(sig["lo"], sig.get("big") or sig["lo"]) if d > 0 else max(sig["hi"], sig.get("big") or sig["hi"])
    sl0 = lvl - d * e * C["STOP_BUF_PCT"] / 100.0
    if (e - sl0) * d <= 0:
        return None
    fee_min = 2 * C["FEE_TAKER"] * e / C["MAX_FEE_R"] * 1.02
    r = max((e - sl0) * d, e * C["SL_MIN_PCT"] / 100.0, fee_min)
    return e, e - d * r, e + d * C["RR"] * r


def sl_ok(entry, sl, C):
    r = abs(entry - sl)
    if r <= 0:
        return False
    return (2 * C["FEE_TAKER"] * entry / r <= C["MAX_FEE_R"]
            and r / entry <= C["LIQ_SAFETY"] / C["LEVERAGE"] and r / entry * 100 <= C["SL_MAX_PCT"])


# =========================================================================
#  SIGNAL ANALYSE (Telegram) : chaque signal 4/4, pris ou non, est envoye avec une
#  simulation de Monte Carlo inspiree de "trader-timemachine" (dan-ai-lab/day-trader-fr) :
#  10 000 trajectoires reconstruites a partir des bougies 15 min des 30 derniers jours
#  du meme actif, a la meme heure (+/- 2 bougies). Stop teste avant le TP dans chaque
#  bougie (prudent). C'est une simulation statistique, pas une prevision.
# =========================================================================
import queue
import random

ANALYSIS_Q = queue.Queue()
_HIST = {}                                       # sym -> (date de lecture, bougies)


def hist_candles(sym, tf, days):
    """Bougies cloturees des `days` derniers jours (par paquets), cache 6 h."""
    c = _HIST.get(sym)
    if c and time.time() - c[0] < 6 * 3600:
        return c[1]
    tfs = TF_SEC[tf]
    now = int(time.time())
    frm, out = now - int(days * 86400), {}
    for _ in range(12):
        j = pub("/api/charts/v1/trade/%s/%s" % (sym, tf), {"from": frm, "to": now})
        rows = j.get("candles") or []
        last = frm
        for r in rows:
            ts = int(r["time"]) // 1000
            if ts + tfs <= now:
                out[ts] = (float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
            last = max(last, ts)
        if not rows or last + tfs >= now - tfs or last <= frm:
            break
        frm = last + tfs
    bars = [(ts,) + out[ts] for ts in sorted(out)]
    _HIST[sym] = (time.time(), bars)
    return bars


def monte_carlo(bars, t_entry, e, sl, tp, d, steps, tfs, sims=10000, seed=19, window=2):
    """Trajectoires : chaque bougie future = une bougie passee tiree au hasard a la meme
       heure (+/- window), appliquee en ratios OHLC a la cloture precedente."""
    per_day = 86400 // tfs
    pool = [[] for _ in range(per_day)]
    for k in range(1, len(bars)):
        ts, o, h, l, c = bars[k]
        pc = bars[k - 1][4]
        if pc <= 0 or ts - bars[k - 1][0] != tfs:
            continue                              # trou dans l'historique : ignore
        pool[(ts % 86400) // tfs].append((h / pc, l / pc, c / pc))
    if sum(len(x) for x in pool) < 20 * per_day // 2:
        return None                               # pas assez d'historique
    near = []
    for sl_i in range(per_day):
        z = []
        for w in range(-window, window + 1):
            z.extend(pool[(sl_i + w) % per_day])
        near.append(z)
    rng = random.Random(seed)
    first = ((t_entry % 86400) // tfs + 1) % per_day      # 1re bougie APRES l'entree
    risk = (e - sl) * d
    n_tp = n_sl = 0
    rs, finals = [], []
    for _ in range(sims):
        p = e
        res = None
        for st_i in range(steps):
            z = near[(first + st_i) % per_day]
            if not z:
                continue
            hr, lr, cr = z[int(rng.random() * len(z))]
            hi, lo = p * hr, p * lr
            if d > 0:
                if lo <= sl:
                    res = -1.0
                    break
                if hi >= tp:
                    res = (tp - e) / risk
                    break
            else:
                if hi >= sl:
                    res = -1.0
                    break
                if lo <= tp:
                    res = (e - tp) / risk
                    break
            p = p * cr
        if res is None:
            rs.append((p - e) * d / risk)
            finals.append(p)
        elif res < 0:
            n_sl += 1
            rs.append(res)
        else:
            n_tp += 1
            rs.append(res)
    return {"p_tp": n_tp / sims, "p_sl": n_sl / sims, "p_none": 1 - (n_tp + n_sl) / sims,
            "mean_r": sum(rs) / len(rs), "sims": sims, "seed": seed,
            "p_green": sum(1 for x in rs if x > 0) / len(rs)}


SEP = "━━━━━━━━━━━━━━━"


def badge(mc, rr):
    """Verdict lisible : objectif touche avant le stop, compare au seuil de rentabilite."""
    if not mc:
        return "⚪", "NON CALCULÉ", None, None
    hit = mc["p_tp"] + mc["p_sl"]
    if hit <= 0:
        return "⚪", "INDÉCIS", None, None
    pt, be = 100 * mc["p_tp"] / hit, 100.0 / (1.0 + rr)
    if pt - be >= 5:
        return "🟢", "FAVORABLE", pt, be
    if pt - be <= -5:
        return "🔴", "DÉFAVORABLE", pt, be
    return "🟡", "NEUTRE", pt, be


def paris_hm(ts):
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.fromtimestamp(ts, ZoneInfo("Europe/Paris")).strftime("%H:%M")
    except Exception:
        return time.strftime("%H:%M UTC", time.gmtime(ts))


def html_esc(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def analysis_text(st, sym, sig, decision, C, ia_line=""):
    d = sig["dir"]
    pl = plan(sig, sig["close"], C)
    if not pl:
        return None
    e, sl, tp = pl
    tfs = TF_SEC[C["BAR_TF"]]
    steps = max(1, int(C["MAX_HOLD_MIN"] * 60 // tfs))
    rr = C["RR"]
    try:
        bars = hist_candles(sym, C["BAR_TF"], C["ANALYSE_DAYS"])
        mc = monte_carlo(bars, int(sig.get("t") or time.time()), e, sl, tp, d, steps, tfs,
                         sims=int(C["ANALYSE_SIMS"]))
    except Exception as ex:
        log.warning("analyse %s : %s", sym, ex)
        mc = None
    if decision.startswith("PRIS"):
        dec = "✅ <b>PRIS par le bot</b> (ordre, SL et TP posés chez Kraken)"
    elif "filtre" in decision:
        dec = "👀 <b>NON PRIS</b> : écarté par le filtre de l'IA (suivi pour apprendre)"
    else:
        dec = "⛔ <b>NON PRIS</b> : " + html_esc(decision.replace("NON pris : ", ""))
    sgn = -1 if d > 0 else 1
    t_sig = int(sig.get("t") or time.time()) + tfs
    em, vtxt, pt, be = badge(mc, rr)
    L = ["%s <b>%s %s</b>" % ("🟢" if d > 0 else "🔴", "ACHAT" if d > 0 else "VENTE", nm(sym)),
         "🪙 Crypto · bot Kraken · VWAP order flow %s" % C["BAR_TF"],
         SEP,
         dec,
         "▶️ Entrée    <code>%s</code>" % px_str(st, sym, e),
         "🛑 SL        <code>%s</code>  (%+.2f %%)" % (px_str(st, sym, sl), sgn * 100 * abs(e - sl) / e),
         "🎯 TP        <code>%s</code>  (%+.2f %% · %gR)" % (px_str(st, sym, tp), -sgn * 100 * abs(tp - e) / e, rr),
         "⏰ Signal %s (Paris) · sortie max %d h" % (paris_hm(t_sig), C["MAX_HOLD_MIN"] // 60),
         SEP]
    if mc:
        L.append("%s <b>Verdict : %s</b>" % (em, vtxt))
        if pt is not None:
            L.append("TP touché avant le SL dans <b>%.0f %%</b> des cas (rentable dès %.0f %%)" % (pt, be))
        L.append("📊 %s simulations : 🎯 %.0f %% · 🛑 %.0f %% · ➖ %.0f %%" % (
            "{:,}".format(mc["sims"]).replace(",", " "), 100 * mc["p_tp"], 100 * mc["p_sl"], 100 * mc["p_none"]))
    else:
        L.append("⚪ <b>Verdict : non calculé</b> (historique Kraken indisponible)")
    L.append("🧠 Checklist : tendance %d %% · absorption %+d %% · big trade x%.1f · retour %+d %%" % (
        int(100 * sig.get("trend", 0)), int(100 * sig.get("abs", 0)), sig.get("big_x") or 0,
        int(100 * sig.get("conf", 0))))
    if ia_line:
        L.append("🤖 " + ia_line)
    L.append("<i>Simulation statistique, pas une prévision ni un conseil</i>")
    return "\n".join(L)


def analyse_async(st, sym, sig, decision, C):
    """Calcul en arriere-plan (lecture de l'historique + 10 000 trajectoires) : le
       trading n'attend jamais. Le texte est envoye par la boucle principale."""
    if not C.get("ANALYSE"):
        return
    sig = dict(sig)
    ia_line = ""
    try:                                          # lu ici (boucle principale), pas dans le thread
        rs = [x[-1] for x in (st.get("learn") or {}).get("done", [])]
        if rs:
            ia_line = "IA : %d setups suivis jusqu'au bout · moyenne %+.2f R" % (len(rs), sum(rs) / len(rs))
    except Exception:
        pass

    def work():
        try:
            txt = analysis_text(st, sym, sig, decision, C, ia_line)
            if txt:
                ANALYSIS_Q.put(txt)
        except Exception as ex:
            log.warning("analyse %s : %s", sym, ex)
    threading.Thread(target=work, daemon=True).start()


def flush_analyses(st):
    while True:
        try:
            txt = ANALYSIS_Q.get_nowait()
        except queue.Empty:
            return
        notify(st, txt, html=True)


# =========================================================================
#  ENTREE
# =========================================================================
def enter(st, sym, sig, fl, C):
    CRIT[0] += 1                                   # ordre au marche -> stop pose : pas d'arret
    try:
        return _enter(st, sym, sig, fl, C)
    finally:
        CRIT[0] -= 1


def _enter(st, sym, sig, fl, C):
    if len(st["pos"]) >= C["MAX_OPEN"] or sym in st["pos"]:
        return "une position est deja ouverte"
    if time.time() < st.get("block_until", 0):
        return "entree precedente incertaine, attente de verification"
    price = fl.last_px if (fl.last_px and time.time() - fl.last_ts < 20) else None
    if not price:
        try:
            price = tickers().get(sym)
        except Exception:
            price = None
    price = price or sig["close"]
    pl = plan(sig, price, C)
    if not pl:
        return "le prix est deja au-dela du niveau de stop"
    e, sl, tp = pl
    if not sl_ok(e, sl, C):
        return "stop trop loin (> %.1f %%)" % min(C["SL_MAX_PCT"], 100 * C["LIQ_SAFETY"] / C["LEVERAGE"])
    d = sig["dir"]
    eq, avail = equity(st)
    full = avail * C["CAPITAL_PCT"] / 100 * C["LEVERAGE"] / e
    fees = 2 * C["FEE_TAKER"] * e
    qty = rq(st, sym, full if C["SIZING"] == "FULL" else min(eq * C["RISK_PCT"] / 100 / (abs(e - sl) + fees), full))
    if qty <= 0:
        return "taille a 0 (capital insuffisant pour la taille minimale Kraken)"
    risk_plan = qty * (abs(e - sl) + fees)
    tp_dist = abs(tp - e)
    if LIVE:
        if not cancel_all(sym):
            return "anciens ordres du symbole impossibles a annuler (securite)"
        st.setdefault("pending", {})[sym] = {"dir": d, "sl": sl, "tp": tp, "ts": time.time()}
        save_state(st)
        sent, err = False, None
        for frac in (1.0, 0.84, 0.68, 0.53):      # Kraken reserve une marge de securite
            q = rq(st, sym, qty * frac)
            if q <= 0:
                break
            try:
                order({"orderType": "mkt", "symbol": sym, "side": side_of(d), "size": qty_str(st, sym, q)})
                sent = True
                break
            except Exception as ex:
                err = ex
                if "insufficient" not in str(ex).lower():
                    break
        if not sent and "insufficient" in str(err).lower():
            st["pending"].pop(sym, None)
        if not sent:
            st["block_until"] = time.time() + 180
            return "ordre refuse par Kraken : %s" % err
        ex = None
        for _ in range(5):
            time.sleep(1.5)
            try:
                ex = open_positions().get(sym)
            except Exception as er:
                log.warning("positions : %s", er)
                continue
            if ex and ex["size"] > 0 and ex["dir"] == d:
                break
        if not ex or ex["size"] <= 0 or ex["dir"] != d:
            st["block_until"] = time.time() + 180
            return "entree envoyee mais aucune position constatee (surveillee)"
        qty, e = ex["size"], ex["price"] or e
        tp = e + d * tp_dist                       # le stop reste sous la structure
    else:
        st["paper"] -= C["FEE_TAKER"] * e * qty
    if (e - sl) * d <= 0 or qty * (abs(e - sl) + 2 * C["FEE_TAKER"] * e) > C["FILL_TOL"] * risk_plan:
        p = {"dir": d, "entry": e, "qty": qty, "sl": sl, "tp": tp, "tf": CFG["BAR_TF"], "adopted": True,
             "risk0": abs(e - sl), "bar_ts": int(time.time()), "stop_id": None, "tp_id": None}
        st["pos"][sym] = p
        close_mkt(st, sym, p, price, "SORTIE immediate (prix obtenu trop loin du plan)")
        return None
    p = {"dir": d, "entry": e, "sl": sl, "tp": tp, "qty": qty, "tf": CFG["BAR_TF"], "risk0": abs(e - sl),
         "bar_ts": int(time.time()), "stop_id": None, "tp_id": None}
    st["pos"][sym] = p
    st.get("pending", {}).pop(sym, None)
    place_exits(st, sym, p)
    save_state(st)
    event(st, "%s %s ENTREE @ %s  SL %s  TP %s" % (nm(sym), "ACHAT" if d > 0 else "VENTE",
                                                  px_str(st, sym, e), px_str(st, sym, sl), px_str(st, sym, tp)))
    notify(st, "%s <b>POSITION OUVERTE · %s %s</b>\n"
               "▶️ Entrée <code>%s</code> · 🛑 SL <code>%s</code> · 🎯 TP <code>%s</code>\n"
               "📦 qte %s · perte max ~%.2f $ frais compris · levier max x%s\n"
               "<i>analyse détaillée juste après</i>"
           % ("🟢" if d > 0 else "🔴", "ACHAT" if d > 0 else "VENTE", nm(sym), px_str(st, sym, e),
              px_str(st, sym, sl), px_str(st, sym, tp), qty,
              qty * (abs(e - sl) + 2 * C["FEE_TAKER"] * e), C["LEVERAGE"]), html=True)
    return None


# =========================================================================
#  SUIVI DES POSITIONS
# =========================================================================
def manage(st, sym, price, pos_ex, C, oo=None):
    p = st["pos"].get(sym)
    if not p:
        return
    d = p["dir"]
    if LIVE:
        ex = pos_ex.get(sym)
        if not ex or ex["dir"] != d or ex["size"] <= 0:
            try:
                ex = open_positions().get(sym)
            except Exception:
                return
        if ex and ex["size"] > 0 and ex["dir"] != d:
            cancel_all(sym)                        # anciens ordres reduce-only du mauvais sens
            st["pos"].pop(sym, None)
            return
        if not ex or ex["dir"] != d or ex["size"] <= 0:
            # position absente : on CONFIRME avant d'annuler le stop (une reponse Kraken
            # ponctuellement fausse ne doit jamais retirer la protection d'une position vivante)
            p["gone"] = p.get("gone", 0) + 1
            if p["gone"] < 2:
                return
            cancel_all(sym)
            r0 = p.get("risk0", abs(p["entry"] - p["sl"])) or 1e-12
            why = ("TP" if (price - p["tp"]) * d >= -0.25 * r0
                   else "trailing stop, gain ~ %+.1f R" % ((p["sl"] - p["entry"]) * d / r0)
                   if p.get("be") and (p["sl"] - p["entry"]) * d > 0.1 * r0
                   else "stop a l'entree, ~0 $" if p.get("be") else "STOP")
            icon = "🎯" if why == "TP" else "🔒" if "trailing" in why or "entree" in why else "🛑"
            notify(st, "%s <b>%s · %s</b>\nposition fermée par Kraken (%s probable)" % (
                icon, "TP TOUCHÉ" if why == "TP" else "SL TOUCHÉ" if why == "STOP" else "SORTIE", nm(sym), esc(why)),
                html=True)
            event(st, "%s position fermee par Kraken (%s probable)" % (nm(sym), why))
            st["pos"].pop(sym, None)
            return
        p["gone"] = 0
        resized = abs(ex["size"] - (p.get("qty") or 0)) > 1e-9 * max(1.0, ex["size"])
        p["qty"] = ex["size"]
        if oo is not None:
            # l'ordre existe-t-il VRAIMENT encore chez Kraken ? (stop execute en partie,
            # annule a la main ou par Kraken...) : 2 constats de suite avant d'agir
            for key in ("stop_id", "tp_id"):
                oid = p.get(key)
                if oid and oid not in oo:
                    p[key + "_miss"] = p.get(key + "_miss", 0) + 1
                    if p[key + "_miss"] >= 2:
                        p[key + "_miss"] = 0
                        if key == "stop_id":
                            alert(st, "%s : stop ABSENT chez Kraken -> repose immediatement" % nm(sym))
                        # l'ancien id n'existe plus : on l'efface (si Kraken refuse le nouveau
                        # stop, la fermeture de secours apres 2 echecs peut alors jouer), on pose
                        # le nouveau, puis on annule l'ancien au cas ou (jamais d'empilement)
                        p[key] = None
                        p.pop("warned", None)
                        place_exits(st, sym, p, stop=key == "stop_id", tp=key == "tp_id")
                        if p.get(key) and p[key] != oid:
                            cancel(oid)
                else:
                    p[key + "_miss"] = 0
        if resized and p.get("stop_id"):
            place_exits(st, sym, p)                # taille changee : stop / TP a la bonne taille
        if not p.get("stop_id"):
            place_exits(st, sym, p)
            if not p.get("stop_id") and p.get("stopfail", 0) >= 2:
                close_mkt(st, sym, p, price, "SORTIE : stop impossible a poser chez Kraken")
                return
        if not p.get("tp_id") and p.get("tpfail", 0) < 20:
            place_exits(st, sym, p, stop=False)    # TP refuse ou absent : on le repose
    r0 = p.get("risk0", abs(p["entry"] - p["sl"]))
    if (p["sl"] - price) * d > 0.15 * r0:         # aussi APRES le passage a zero perte
        close_mkt(st, sym, p, price, "STOP (securite bot)")
        return
    # protection des gains : a +BE_AT_R x risque, stop a l'entree + frais (zero perte)
    if not p.get("be") and not p.get("adopted") and (price - p["entry"]) * d >= C["BE_AT_R"] * r0:
        be_px = p["entry"] + d * p["entry"] * (2 * C["FEE_TAKER"] + C["BE_EXTRA_PCT"] / 100)
        if (price - be_px) * d > 0:
            old = p["sl"]
            p["sl"] = be_px
            place_exits(st, sym, p, tp=False)
            if not LIVE or (p.get("stop_id") and p.get("stop_px") == p["sl"]):
                p["be"] = True
                p["best"] = price
                save_state(st)
                info(st, "🔒", "SL À L'ENTRÉE · %s" % nm(sym),
                     "+%.1f R → SL déplacé à <code>%s</code> (entrée + frais : zéro perte)"
                     % (C["BE_AT_R"], px_str(st, sym, be_px)))
            else:
                p["sl"] = old
    # trailing stop : apres le passage a zero perte, le stop suit le meilleur prix
    if p.get("be") and C["TRAIL_R"] > 0:
        best = p.get("best", price)
        best = max(best, price) if d > 0 else min(best, price)
        p["best"] = best
        new_sl = best - d * C["TRAIL_R"] * r0
        if (new_sl - p["sl"]) * d >= C["TRAIL_STEP_R"] * r0 and (price - new_sl) * d > 0:
            old = p["sl"]
            p["sl"] = new_sl
            place_exits(st, sym, p, tp=False)
            if not LIVE or (p.get("stop_id") and p.get("stop_px") == p["sl"]):
                save_state(st)
                info(st, "🔒", "TRAILING STOP · %s" % nm(sym),
                     "SL → <code>%s</code> (gain protégé ~ %+.1f R)"
                     % (px_str(st, sym, new_sl), (new_sl - p["entry"]) * d / r0))
            else:
                p["sl"] = old
    if not p.get("adopted") and time.time() - p.get("bar_ts", time.time()) > C["MAX_HOLD_MIN"] * 60:
        close_mkt(st, sym, p, price, "SORTIE duree max")
        return
    if (price <= p["sl"]) if d > 0 else (price >= p["sl"]):
        if not LIVE or not p.get("stop_id"):
            close_mkt(st, sym, p, price, "STOP")
    elif (price >= p["tp"]) if d > 0 else (price <= p["tp"]):
        if not LIVE or not p.get("tp_id"):
            close_mkt(st, sym, p, price, "TP atteint")


# =========================================================================
#  BOUCLE
# =========================================================================
FLOWS = {}


def refresh_symbols(st, C):
    inst = instruments()
    if inst:
        st["inst"] = inst                          # Kraken muet : on garde les precisions connues
    syms = discover(st, C)
    if syms or not C["SYMS"]:
        C["SYMS"] = syms
    want = list(dict.fromkeys(list(C["SYMS"]) + list(st["pos"])))
    if FEED:
        FEED.set_symbols(set(want))
    for sym in want:
        if sym not in FLOWS:
            fl = Flow(sym)
            try:
                fl.seed()
                if FEED:
                    fl.seq = max([x[4] for x in FEED.recent(sym, 3600)] or [0])
            except Exception as e:
                log.warning("historique %s : %s", sym, e)
                continue
            FLOWS[sym] = fl
            try:                                   # IA : rattrape les bougies manquees pendant un
                for b in list(fl.bars):            # redemarrage ou une coupure (stops touches...)
                    learn_track(st, sym, b, C)
            except Exception as e:
                log.warning("IA rattrapage %s : %s", sym, e)
            time.sleep(0.1)
    for sym in list(FLOWS):
        if sym not in want:
            FLOWS.pop(sym, None)
    st["disco"] = time.time()
    log.info("%d actifs suivis : %s", len(C["SYMS"]), ", ".join(nm(s) for s in C["SYMS"]))
    if LIVE:
        for sym in list(C["SYMS"]):
            if st["lev"].get(sym) != C["LEVERAGE"]:
                try:
                    priv("PUT", "/api/v3/leveragepreferences", {"symbol": sym, "maxLeverage": C["LEVERAGE"]})
                    st["lev"][sym] = C["LEVERAGE"]
                except Exception as e:
                    if "MARKET_RESTRICTED" in str(e):
                        if sym not in st.setdefault("blocked", []):
                            st["blocked"].append(sym)
                        C["SYMS"].remove(sym)
                        if sym not in st["pos"]:   # plus suivi du tout (pas d'entree possible)
                            FLOWS.pop(sym, None)
                            if FEED:
                                FEED.set_symbols(set(C["SYMS"]) | set(st["pos"]))
                    else:
                        log.warning("levier %s : %s", sym, e)


def resync(st, sym, fl, C):
    """Apres une longue coupure du flux : VWAP et bougies rechargees depuis Kraken (le volume
       de la coupure compte), checklists en cours oubliees, bougies manquees rejouees par l'IA."""
    fl.setup = {1: None, -1: None}
    fl.seed()
    buf = FEED.buffered(sym)
    later = [x[4] for x in buf if x[0] >= fl.seed_until]
    fl.seq = (min(later) - 1) if later else max([x[4] for x in buf] or [fl.seq])
    for b in list(fl.bars):
        learn_track(st, sym, b, C)


def fast_step(st, C):
    flush_analyses(st)
    """Toutes les FAST_SEC s : lecture du flux, cloture des bougies, checklist."""
    now = time.time()
    stats = st.setdefault("stats", {})
    for sym, fl in list(FLOWS.items()):
        if not FEED:
            continue
        ep = getattr(FEED, "epoch", 0)
        if fl.epoch != ep and getattr(FEED, "gap_start", None) is None:
            fl.epoch = ep                          # le flux est revenu apres une coupure
            g = FEED.gaps[-1] if getattr(FEED, "gaps", None) else None
            if g and g[1] - g[0] > 0.1 * C["BAR_SEC"]:
                try:
                    resync(st, sym, fl, C)
                    event(st, "%s resynchronise apres %d s de coupure du flux" % (nm(sym), g[1] - g[0]))
                except Exception as e:
                    log.warning("resynchronisation %s : %s", sym, e)
        new = FEED.since(sym, fl.seq)              # (coupures : jugees a la cloture de la bougie)
        if sym in getattr(FEED, "overflowed", ()):
            FEED.overflowed.discard(sym)           # trades perdus (boucle bloquee) : bougie
            if fl.cur:                             # en cours incomplete, pas lue
                fl.cur["partial"] = True
        for b in fl.ingest(new, now):
            stats["bougies"] = stats.get("bougies", 0) + 1
            try:                                   # l'IA ne doit JAMAIS bloquer le trading
                learn_track(st, sym, b, C)
            except Exception as e:
                log.warning("IA suivi %s : %s", sym, e)
            try:
                sig = evaluate(fl, b, st, C)
            except Exception as e:
                log.warning("checklist %s : %s", sym, e)
                continue
            if sig and sig.get("new_sample"):
                try:
                    learn_record(st, sig, C)
                    stats["setups appris"] = stats.get("setups appris", 0) + 1
                except Exception as e:
                    log.warning("IA enregistrement %s : %s", sym, e)
            if sig and not sig.get("take"):
                analyse_async(st, sym, sig, "NON pris : pas retenu par le filtre de l'IA", C)
            if not sig or not sig.get("take"):
                continue
            stats["entrees confirmees"] = stats.get("entrees confirmees", 0) + 1
            Ce = C
            if C["LEARN"] and learn_st(st, C)["risk_mult"] < 1.0:
                Ce = dict(C, RISK_PCT=C["RISK_PCT"] * learn_st(st, C)["risk_mult"])
            why = enter(st, sym, sig, fl, Ce)
            if why:
                motif = why.split("(")[0].split(":")[0].strip()[:40]
                stats["refus: " + motif] = stats.get("refus: " + motif, 0) + 1
                event(st, "%s %s confirme mais NON pris : %s" % (nm(sym), "ACHAT" if sig["dir"] > 0 else "VENTE", why))
                analyse_async(st, sym, sig, "NON pris : " + why, C)
            else:
                stats["trades"] = stats.get("trades", 0) + 1
                analyse_async(st, sym, sig, "PRIS (ordre, stop et TP envoyes a Kraken)", C)
    try:
        learn_update(st, C)
    except Exception as e:
        log.warning("IA : %s", e)


def slow_step(st, C):
    """Toutes les SLOW_SEC s : positions Kraken, stops, TP, protection des gains."""
    try:
        px = tickers()
    except Exception as e:                         # prix du flux en secours : le suivi continue
        log.warning("tickers : %s", e)
        px = {}
    for sym, fl in FLOWS.items():                  # prix du flux s'il est frais (plus reactif)
        if fl.last_px and (time.time() - fl.last_ts < 15 or sym not in px):
            px[sym] = fl.last_px
    pos_ex = open_positions() if LIVE else {}
    if LIVE:
        try:
            adopt_orphans(st, pos_ex, px, C)
        except Exception as e:
            log.warning("reconciliation : %s", e)
    oo = None
    if LIVE and st["pos"]:
        try:
            oo = open_order_ids()
        except Exception as e:
            log.warning("ordres ouverts : %s", e)
    for sym in list(st["pos"]):
        try:
            if px.get(sym):
                manage(st, sym, px[sym], pos_ex, C, oo)
        except Exception as e:
            log.warning("suivi %s : %s", sym, e)


FEED_DOWN = [None, False]                          # debut de la coupure, alerte envoyee


def heartbeat(st, C):
    now = time.time()
    if FEED and not FEED.ok():                     # flux coupe : le bot est aveugle -> alerte
        FEED_DOWN[0] = FEED_DOWN[0] or now
        if now - FEED_DOWN[0] > 300 and not FEED_DOWN[1]:
            FEED_DOWN[1] = True
            alert(st, "flux des trades Kraken coupe depuis plus de 5 min -> aucun "
                      "nouveau trade possible (positions ouvertes toujours protegees)")
    else:
        if FEED_DOWN[1]:
            info(st, "✅", "Flux des trades Kraken rétabli")
        FEED_DOWN[0], FEED_DOWN[1] = None, False
    stats = st.setdefault("stats", {})
    actifs = [s for s, f in FLOWS.items() if f.last_ts and now - f.last_ts < 300]
    en_cours = ["%s %s %d/4" % (nm(s), "ACHAT" if d > 0 else "VENTE", 2 if S["stage"] == 1 else 3)
                for s, f in FLOWS.items() for d, S in f.setup.items() if S]
    log.info("flux %s : %d/%d actifs vivants, %d bougies lues, checklists en cours : %s, %d position(s)",
             "OK" if FEED and FEED.ok() else "COUPE", len(actifs), len(FLOWS), stats.get("bougies", 0),
             ", ".join(en_cours) or "aucune", len(st["pos"]))
    log.info(learn_summary(st, C))
    if not st.get("stats_ts"):
        st["stats_ts"] = now
    elif now - st["stats_ts"] >= C["SUMMARY_HOURS"] * 3600:
        lignes = ["%s : %d" % (k, v) for k, v in sorted(stats.items(), key=lambda x: -x[1])
                  if k.startswith("refus")]
        info(st, "📊", "RÉSUMÉ %g h · bot crypto" % C["SUMMARY_HOURS"],
             "🕯️ %d bougies lues · 🧩 %d setups appris\n✅ %d entrées confirmées · 💼 %d trades · 📂 %d position(s) ouverte(s)\n"
             "⛔ %s\n🤖 %s"
             % (stats.get("bougies", 0), stats.get("setups appris", 0), stats.get("entrees confirmees", 0),
                stats.get("trades", 0), len(st["pos"]),
                esc(" · ".join(lignes)[:600]) or "aucun refus", esc(learn_summary(st, C))))
        st["stats"], st["stats_ts"] = {}, now


def main():
    C = CFG
    if LIVE and not (os.environ.get("KRAKEN_KEY") and os.environ.get("KRAKEN_SECRET")):
        sys.exit("MODE live sans cles Kraken Futures")
    st = load_state(C)
    end = time.time() + C["LOOP_MINUTES"] * 60     # duree du job comptee des le depart
    global FEED

    def _arret(signum, frame):
        for sg2 in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sg2, signal.SIG_IGN)
            except Exception:
                pass
        if _SAVING[0] or CRIT[0]:                  # sauvegarde, entree ou sortie en cours : la
            STOP[0] = True                         # boucle s'arretera juste apres, proprement
            return
        save_state(st)
        log.info("Arret demande (signal %s) : etat sauvegarde.", signum)
        sys.exit(0)
    for sg in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sg, _arret)
        except Exception:
            pass

    if websocket is None:
        sys.exit("module websocket-client absent : cette strategie a besoin du flux des trades")
    FEED = TradeFeed()
    threading.Thread(target=FEED.run, daemon=True).start()
    log.info("Demarrage VWAP ORDER FLOW [%s] x%s | bougies %s | stop %s | RR %s",
             "REEL" if LIVE else "PAPER", C["LEVERAGE"], C["BAR_TF"], C["STOP_MODE"], C["RR"])
    if LIVE:
        check_keys(st)
    mode = C["MODE"].lower()
    if (st.get("mode") or "").lower() != mode:     # (un fichier d'etat par mode desormais)
        if (st.get("mode") or "").lower() == "live" and st["pos"]:
            alert(st, "passage en mode fictif avec des positions REELLES ouvertes (%s) : "
                       "leurs stops/TP restent poses chez Kraken." % ", ".join(nm(x) for x in st["pos"]))
        st["pos"] = {}
        st["mode"] = mode
    for essai in range(20):                        # Kraken en panne au demarrage : on patiente
        try:                                       # ~10 min au lieu de planter le job
            refresh_symbols(st, C)
            break
        except Exception as e:
            log.warning("demarrage : liste des cryptos indisponible (%s) -> nouvel essai dans 30 s", e)
            time.sleep(30)
    else:
        save_state(st)
        sys.exit("Kraken injoignable au demarrage")
    log.info(learn_summary(st, C))
    info(st, "🤖", "Bot crypto démarré · %s" % ("RÉEL" if LIVE else "FICTIF"),
         "VWAP order flow %s · levier max x%s · %d positions max · risque %.1f %% par trade\n%d perpétuels surveillés"
         % (C["BAR_TF"], C["LEVERAGE"], int(C["MAX_OPEN"]), C["RISK_PCT"], len(C["SYMS"])))

    t_slow = t_beat = t_save = 0.0
    while True:
        t0 = time.time()
        if t0 - st.get("disco", 0) > 3600:
            st["disco"] = t0 - 3300                # en cas d'echec : nouvel essai dans 5 min
            try:
                refresh_symbols(st, C)
            except Exception as e:
                log.warning("liste des cryptos : %s (les positions restent suivies)", e)
        for nom, etape, due in (("checklist", fast_step, True),
                                ("positions", slow_step, t0 - t_slow >= C["SLOW_SEC"]),
                                ("bilan", heartbeat, t0 - t_beat >= 600)):
            if not due:
                continue
            if etape is slow_step:
                t_slow = t0
            elif etape is heartbeat:
                t_beat = t0
            try:                                   # une etape en panne n'arrete pas les autres
                etape(st, C)
            except Exception as e:
                log.warning("%s : %s", nom, e)
        if t0 - t_save >= 60 or STOP[0]:
            t_save = t0
            save_state(st)
        if STOP[0]:
            log.info("Arret demande : etat sauvegarde.")
            return
        if time.time() + 180 >= end:
            save_state(st)
            log.info("Fin de ce job (le workflow se relance automatiquement).")
            return
        time.sleep(max(0.5, C["FAST_SEC"] - (time.time() - t0)))


if __name__ == "__main__":
    main()
