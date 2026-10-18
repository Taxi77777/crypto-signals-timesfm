#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================================
  BOT "MURS & BULLES"  -  perpetuels Kraken Pro (Futures)
  Strategie de la video "La heatmap expliquee en 1 minute" (Dualis Flow),
  cette fois avec le VRAI carnet d'ordres Kraken (pas une estimation).
  Remplace l'ancien bot "PIEGE PDH/PDL -> MA50".
==========================================================================
  MUR   = gros paquet d'ordres LIMITES dans le carnet Kraken a un meme prix
          (taille >= WALL_FACTOR x la mediane des niveaux autour du prix),
          present depuis au moins WALL_MIN_AGE secondes (anti faux murs).
  BULLE = bougie dont le volume >= BUBBLE x la moyenne (ordres agressifs).

  Les 3 scenarios de la video, quand le prix arrive sur un mur :
    1. IL TIENT  -> le prix touche le mur, grosse bulle, rebond : on TRADE
                    (mur d'achat sous le prix = ACHAT, mur de vente = VENTE)
    2. MANGE     -> le mur disparait et le prix le traverse : continuation
                    (desactive par defaut : perdant au test MT4)
    3. RETIRE    -> le mur disparait AVANT l'impact : pas de trade, et si une
                    position s'appuyait sur ce mur, on SORT (EXIT_IF_WALL_GONE)

  Sortie : SL derriere le mur, TP = RR x risque. Stop et TP poses CHEZ KRAKEN.
  Unites de temps : 5m, 15m, 30m, 1h (variable KRAKEN_TF).

  Variables d'environnement (GitHub Secrets / Variables) :
     KRAKEN_KEY, KRAKEN_SECRET   cles Kraken FUTURES
     KRAKEN_MODE                 "paper" (defaut) ou "live"
     TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   (optionnel)
==========================================================================
"""
import base64
import hashlib
import hmac
import json
import logging
import math
import os
import re
import signal
import statistics
import threading
from collections import deque
import sys
import time
import urllib.parse

import requests

try:                                               # flux en direct (facultatif : repli REST sinon)
    import websocket
except Exception:
    websocket = None

BASE = "https://futures.kraken.com"
TF_SEC = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400}
STATE_FILE = "kraken_murs_state.json"


def envf(name, default):
    v = os.environ.get(name, "").strip()
    if v == "":
        return default
    if isinstance(default, str):
        return v
    return type(default)(float(v)) if isinstance(default, int) else type(default)(v)


CFG = {
    "MODE": envf("KRAKEN_MODE", "paper"),
    "SYMBOLS": envf("KRAKEN_SYMBOLS", ""),       # vide = perpetuels les plus liquides
    "MIN_QUOTE_VOL": envf("MIN_QUOTE_VOL", 500000.0),  # 0,5 M$/24 h -> ~55 cryptos (en dessous, carnets trop vides)
    "TOP_N": envf("TOP_N", 60),                  # carnet lu chaque minute (~11 s pour 55 cryptos)
    "TF": envf("KRAKEN_TF", "15m,30m,1h"),        # 5m retire : frais > 50 % du risque (perdant par construction)
    # murs (carnet d'ordres reel)
    "BUCKET_PCT": envf("BUCKET_PCT", 0.05),      # largeur d'un niveau de prix, en % du prix
    "RANGE_PCT": envf("RANGE_PCT", 1.5),         # on regarde le carnet a +/- X % du prix
    "WALL_FACTOR": envf("WALL_FACTOR", 8.0),     # mur = niveau >= X fois la mediane
    "MAX_WALLS": envf("MAX_WALLS", 3),           # seulement les X plus gros murs de chaque cote
    "GONE_SNAPS": envf("GONE_SNAPS", 2),         # absent X lectures de suite = vraiment parti
    "NOTIFY_RETIRE": envf("NOTIFY_RETIRE", 0),   # 1 = Telegram a chaque mur retire (bavard)
    "WALL_MIN_AGE": envf("WALL_MIN_AGE", 300),   # secondes de presence minimum (anti faux murs)
    "WALL_STABLE": envf("WALL_STABLE", 0.8),     # present sur >= X des lectures depuis son apparition
    "HVN_FILTER": envf("HVN_FILTER", 1),         # DOUBLE MUR : le mur du carnet doit tomber sur une zone
    "HVN_MIN": envf("HVN_MIN", 0.5),             #   de gros volume deja echange (heatmap MT4) >= X du max
    "HVN_BARS": envf("HVN_BARS", 150),
    "HVN_BINS": envf("HVN_BINS", 40),
    "DELTA_FILTER": envf("DELTA_FILTER", 1),     # les derniers trades doivent aller dans le sens du trade
    "DELTA_MIN": envf("DELTA_MIN", 0.05),        #   (acheteurs - vendeurs) / total >= X
    "WALL_COOLDOWN": envf("WALL_COOLDOWN", 60),  # minutes sans retrader le meme mur apres un trade
    "ABS_WINDOW": envf("ABS_WINDOW", 30),        # absorption comptee sur les X dernieres minutes
    "ABS_HITS": envf("ABS_HITS", 3),             # ...ou au moins X trades executes sur le mur
    # ICEBERG : mur qui encaisse plus que sa taille affichee et se recharge (ordre cache)
    "ICE_RATIO": envf("ICE_RATIO", 0.5),         # volume execute MESURE (30 min) >= X fois sa taille max
                                                 # (on ne lit que 100 trades/min : 50 % mesures ~ >= 100 % reels)
    "ICE_KEEP": envf("ICE_KEEP", 0.7),           # ...et toujours >= X de sa taille max (il se recharge)
    "NOTIFY_ICEBERG": envf("NOTIFY_ICEBERG", 1), # alerte Telegram (1 fois par mur)
    # flux en direct des trades Kraken (WebSocket) : TOUS les trades, pas 100 par minute
    "WS_TRADES": envf("WS_TRADES", 1),
    "DELTA_SECS": envf("DELTA_SECS", 300),       # acheteurs/vendeurs mesures sur les X dernieres s
    # funding : eviter d'entrer du cote deja surcharge (tout le monde long -> pas d'ACHAT)
    "FUND_FILTER": envf("FUND_FILTER", 1),
    "FUND_MAX": envf("FUND_MAX", 0.00005),       # funding relatif par heure (0,005 %/h ~ 44 %/an)
    # 2e plateforme : OKX (Binance/Bybit bloques depuis GitHub USA)
    "OKX_CONFIRM": envf("OKX_CONFIRM", 1),       # mur confirme sur OKX = preuve alternative au double mur
    "OKX_FACTOR": envf("OKX_FACTOR", 3.0),       # niveau OKX >= X fois la mediane du cote
    "SUMMARY_HOURS": envf("SUMMARY_HOURS", 6.0), # resume Telegram toutes les X heures
    # idee 1 : reputation anti-faux murs (spoofing = mur retire quand le prix approche)
    "SPOOF_MAX": envf("SPOOF_MAX", 2),           # X murs retires a l'approche sur un cote...
    "SPOOF_WIN": envf("SPOOF_WIN", 30),          # ...en Y minutes -> ce cote n'est plus cru
    # idee 2 : absorption / iceberg = volume REELLEMENT execute sur le mur
    "ABS_FILTER": envf("ABS_FILTER", 1),         # exiger que le mur ait encaisse des trades
    "ABS_MIN": envf("ABS_MIN", 0.02),            # volume execute au mur >= X de sa taille (100 trades/min lus)
    "ABS_NEAR_PCT": envf("ABS_NEAR_PCT", 0.25),  # on lit les trades si le prix est a < X % d'un mur
    "WALL_KEEP": envf("WALL_KEEP", 0.25),        # un mur qui encaisse les attaques maigrit : on le garde jusqu'a X du max
    "WALL_GONE": envf("WALL_GONE", 0.3),         # "retire / mange" si taille < X du max
    # bulles + rebond
    "BUBBLE": envf("BUBBLE", 1.8),
    "VOL_AVG": envf("VOL_AVG", 20),
    "TOL_ATR": envf("TOL_ATR", 0.15),
    "PIERCE_ATR": envf("PIERCE_ATR", 0.6),
    "ATR_PERIOD": envf("ATR_PERIOD", 14),
    "SIGNAL_MANGE": envf("SIGNAL_MANGE", 0),     # scenario 2 (desactive par defaut)
    # sortie
    "SL_BEHIND_ATR": envf("SL_BEHIND_ATR", 0.5), # SL = mur - X ATR
    "MIN_RISK_ATR": envf("MIN_RISK_ATR", 0.5),   # risque minimum (evite les SL collants)
    "SL_MIN_PCT": envf("SL_MIN_PCT", 0.3),       # risque minimum en % du prix (frais Kraken <= 1/3 du risque)
    "EXIT_IF_WALL_BROKEN": envf("EXIT_IF_WALL_BROKEN", 1),  # mur disparu ET traverse -> sortie
    "RR": envf("RR", 1.5),                       # TP = RR x risque
    "EXIT_IF_WALL_GONE": envf("EXIT_IF_WALL_GONE", 1),
    "ADOPT_ORPHANS": envf("ADOPT_ORPHANS", 1),   # reprendre (stop d'urgence) une position inconnue du bot
    "MIN_RR": envf("MIN_RR", 1.0),               # TP avant le mur oppose : refuse si gain/risque < X
    "OPP_STRENGTH": envf("OPP_STRENGTH", 1.0),   # seul un mur oppose >= X fois NOTRE mur bloque le TP
    "MAX_HOLD": envf("MAX_HOLD", 48),            # bougies max en position
    # risque (prudent par defaut : petit compte)
    "LEVERAGE": envf("LEVERAGE", 5),
    "SIZING": envf("SIZING", "RISK"),            # RISK = RISK_PCT du capital risque par trade
    "RISK_PCT": envf("RISK_PCT", 1.0),
    "CAPITAL_PCT": envf("CAPITAL_PCT", 95.0),
    "LIQ_SAFETY": envf("LIQ_SAFETY", 0.75),
    "MAX_OPEN": envf("MAX_OPEN", 1),
    "FEE_TAKER": envf("FEE_TAKER", 0.0005),
    "MAX_FEE_R": envf("MAX_FEE_R", 0.35),
    "PAPER_BALANCE": envf("PAPER_BALANCE", 1000.0),
    # boucle
    "LOOP_MINUTES": envf("LOOP_MINUTES", 320),
    "INTERVAL": envf("INTERVAL", 60),
}
CFG["TF_LIST"] = [t.strip() for t in CFG["TF"].split(",") if t.strip() in TF_SEC] or ["15m"]
CFG["SYMS"] = []
LIVE = CFG["MODE"].lower() == "live"

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                    datefmt="%H:%M:%S", stream=sys.stdout)
log = logging.getLogger("murs")
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "murs-bulles"


# =========================================================================
#  API KRAKEN FUTURES
# =========================================================================
def pub(path, params=None):
    last = None
    for essai in range(3):                       # 3 essais en cas de coupure reseau
        try:
            r = SESSION.get(BASE + path, params=params, timeout=20)
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
    j = {}
    try:
        j = r.json()
    except Exception:
        pass
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


def orderbook(sym):
    b = pub("/derivatives/api/v3/orderbook", {"symbol": sym}).get("orderBook") or {}
    bids = [(float(p), float(q)) for p, q in b.get("bids") or []]
    asks = [(float(p), float(q)) for p, q in b.get("asks") or []]
    if len(bids) < 5 or len(asks) < 5:
        raise RuntimeError("carnet vide ou incomplet")
    return bids, asks


def trade_ts(t):
    """'2026-10-16T07:07:15.517753248Z' -> secondes epoch (UTC)"""
    try:
        import calendar
        return calendar.timegm(time.strptime(str(t)[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0


def absorb_trades(st, sym, mid, now, C):
    """IDEE 2 - absorption / iceberg : quand le prix est pres d'un mur, on additionne le
       volume REELLEMENT EXECUTE au prix du mur (trades Kraken). Un mur qui encaisse des
       trades et reste en place est un vrai mur (ou un iceberg qui se recharge) ; un faux
       mur, lui, est retire avant d'etre touche."""
    book = st["walls"].get(sym) or {}
    near = [v for v in book.values() if v.get("size", 0) > 0
            and abs(v["price"] - mid) / mid * 100 <= C["ABS_NEAR_PCT"]]
    if not near:
        return
    for v in near:
        v["near_ts"] = now                         # le prix est au contact du mur maintenant
    tr_ts = st.setdefault("tr_ts", {})            # dernier trade compte (toutes sources)
    if FEED and FEED.ok(sym):                      # TOUS les trades du flux en direct
        last = st.setdefault("tr_wsseq", {}).get(sym, 0)
        t0 = max(now - 120 if not last else 0, tr_ts.get(sym, 0))
        newest = last
        for ts, px_t, q, _side, seq in FEED.since(sym, last):
            newest = max(newest, seq)
            if ts <= t0:
                continue
            tr_ts[sym] = max(tr_ts.get(sym, 0), ts)
            for v in near:
                half = (v.get("w") or mid * C["BUCKET_PCT"] / 100) / 2.0
                if abs(px_t - v["price"]) <= half:
                    v.setdefault("tr", []).append([ts, q])
        st["tr_wsseq"][sym] = newest
        for v in near:
            v["tr"] = [x for x in v.get("tr", []) if now - x[0] <= C["ABS_WINDOW"] * 60]
        return
    h = pub("/derivatives/api/v3/history", {"symbol": sym}).get("history") or []
    # anti-doublon par numero de sequence (plusieurs trades peuvent avoir la meme seconde)
    last = st.setdefault("tr_seq", {}).get(sym, 0)
    t0 = now - 120 if not last else 0
    newest = last
    for t in h:
        try:
            seq = int(t.get("sequence_id") or 0)
        except Exception:
            seq = 0
        ts = trade_ts(t.get("time"))
        if (seq and seq <= last) or ts < t0 or ts <= tr_ts.get(sym, 0):
            continue                               # (deja compte par le flux en direct)
        newest = max(newest, seq)
        px_t, q = float(t.get("price") or 0), float(t.get("size") or 0)
        for v in near:
            half = (v.get("w") or mid * C["BUCKET_PCT"] / 100) / 2.0   # le niveau du mur seulement
            if abs(px_t - v["price"]) <= half:
                v.setdefault("tr", []).append([ts, q])
    st["tr_seq"][sym] = newest
    for v in near:                                 # on ne garde que la fenetre recente
        v["tr"] = [x for x in v.get("tr", []) if now - x[0] <= C["ABS_WINDOW"] * 60]


def is_iceberg(v, C):
    """Mur qui a encaisse au moins sa taille en trades et reste en place : il se recharge
       depuis une reserve cachee (ordre iceberg) = acheteur / vendeur REEL et determine."""
    mx = v.get("max") or 0.0
    traded = sum(x[1] for x in v.get("tr", []))
    return mx > 0 and traded >= C["ICE_RATIO"] * mx and v.get("size", 0) >= C["ICE_KEEP"] * mx


def iceberg_alerts(st, sym, mid, now, C):
    for v in (st["walls"].get(sym) or {}).values():
        if v.get("ice_alert") or not is_iceberg(v, C) or now - v.get("first", now) < C["WALL_MIN_AGE"]:
            continue
        v["ice_alert"] = True
        traded = sum(x[1] for x in v.get("tr", []))
        msg = ("%s ICEBERG detecte : %s a %s (a encaisse %d%% de sa taille en %d min et se recharge)"
               % (nm(sym), "MUR D'ACHAT" if v["side"] > 0 else "MUR DE VENTE", rp(st, sym, v["price"]),
                  int(100 * traded / v["max"]), C["ABS_WINDOW"]))
        (notify if C["NOTIFY_ICEBERG"] else (lambda _s, m: log.info(m)))(st, msg)


def spoof_side(st, sym, side, now, C):
    """IDEE 1 - reputation : ce cote du carnet a-t-il retire SPOOF_MAX murs a l'approche
       du prix dans les SPOOF_WIN dernieres minutes ? (signature du spoofing)"""
    lst = [e for e in (st.get("spoof", {}).get(sym) or []) if now - e[1] <= C["SPOOF_WIN"] * 60]
    return sum(1 for e in lst if e[0] == side) >= C["SPOOF_MAX"]


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

    def set_symbols(self, syms):
        with self.lock:
            self.syms = set(syms)

    def ok(self, sym=None):
        """Flux vivant ET (si sym donne) abonnement confirme pour ce symbole."""
        with self.lock:
            return time.time() - self.alive < 120 and (sym is None or sym in self.acked)

    def since(self, sym, seq0):
        with self.lock:
            return [x for x in self.trades.get(sym, ()) if x[4] > seq0]

    def recent(self, sym, secs):
        t0 = time.time() - secs
        with self.lock:
            return [x for x in self.trades.get(sym, ()) if x[0] >= t0]

    def run(self):
        wait = 2
        while not self.stop:
            ws = None
            try:
                ws = websocket.create_connection(self.URL, timeout=20)
                with self.lock:
                    self.acked = set()             # nouvelle connexion : tout re-confirmer
                done = set()
                last_ping = time.time()
                wait = 2
                while not self.stop:
                    with self.lock:
                        new = sorted(self.syms - done)
                    if new:
                        ws.send(json.dumps({"event": "subscribe", "feed": "trade", "product_ids": new}))
                        done |= set(new)
                    if time.time() - last_ping > 30:   # Kraken coupe une connexion sans ping
                        ws.ping()
                        last_ping = time.time()
                    try:
                        m = json.loads(ws.recv())
                    except websocket.WebSocketTimeoutException:
                        continue
                    ev = m.get("event")
                    if ev == "subscribed" and m.get("feed") == "trade":
                        with self.lock:
                            self.acked |= set(x.upper() for x in (m.get("product_ids") or []))
                            self.alive = time.time()
                    elif ev in ("error", "alert"):
                        log.warning("flux trades (Kraken) : %s", m)
                    elif m.get("feed") == "trade" and "price" in m and "qty" in m:
                        sym = (m.get("product_id") or "").upper()
                        with self.lock:
                            self.n += 1
                            rec = (float(m["time"]) / 1000.0, float(m["price"]), float(m["qty"]),
                                   m.get("side"), self.n)
                            self.trades.setdefault(sym, deque(maxlen=20000)).append(rec)
                            self.alive = time.time()
            except Exception as e:
                log.warning("flux trades : %s -> reconnexion dans %d s", e, wait)
                time.sleep(wait)
                wait = min(60, wait * 2)
            finally:
                try:
                    if ws is not None:
                        ws.close()                 # pas de connexion orpheline
                except Exception:
                    pass


FEED = None
TICK = {}                                          # funding / open interest du dernier passage


def oi_note(st, sym, now):
    """Variation de l'open interest sur ~1 h (INFORMATION seulement, ne bloque rien).
       Prix + OI qui montent = nouveaux acteurs ; prix qui monte + OI qui baisse = rachats."""
    h = (st.get("oi_hist") or {}).get(sym) or []
    old = [x for x in h if 3300 <= now - x[0] <= 4500]
    cur = (TICK.get(sym) or {}).get("oi")
    if not old or not cur or not old[0][1]:
        return "open interest : historique en cours (1 h)"
    ch = (cur - old[0][1]) / old[0][1] * 100
    return "open interest %+.1f %% sur 1 h (%s)" % (ch, "argent qui ENTRE" if ch > 0.5 else
                                                    "argent qui SORT" if ch < -0.5 else "stable")


def taker_delta(sym):
    """Achats agressifs - ventes agressives (flux en direct sur DELTA_SECS s ; sinon REST)."""
    if FEED and FEED.ok(sym):
        tr = FEED.recent(sym, CFG["DELTA_SECS"])
        if len(tr) >= 5:
            buy = sum(x[2] for x in tr if x[3] == "buy")
            sell = sum(x[2] for x in tr if x[3] == "sell")
            return (buy - sell) / (buy + sell) if buy + sell else 0.0
    try:
        h = pub("/derivatives/api/v3/history", {"symbol": sym}).get("history") or []
        buy = sum(float(t["size"]) for t in h if t.get("side") == "buy")
        sell = sum(float(t["size"]) for t in h if t.get("side") == "sell")
        tot = buy + sell
        return (buy - sell) / tot if tot else 0.0
    except Exception:
        return 0.0


def tickers():
    out = {}
    TICK.clear()
    for t in pub("/derivatives/api/v3/tickers").get("tickers") or []:
        s = (t.get("symbol") or "").upper()
        p = t.get("last") or t.get("markPrice")
        if s and p:
            out[s] = float(p)
            TICK[s] = {"rf": float(t.get("relativeFundingRate") or 0.0),
                       "oi": float(t.get("openInterest") or 0.0)}
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
    for p in priv("GET", "/api/v3/openpositions").get("openPositions") or []:
        out[(p.get("symbol") or "").upper()] = {
            "dir": 1 if p.get("side") == "long" else -1,
            "size": float(p.get("size") or 0),
            "price": float(p.get("price") or 0)}
    return out


def equity(st):
    if not LIVE:
        return st["paper"]
    f = (priv("GET", "/api/v3/accounts").get("accounts") or {}).get("flex") or {}
    return float(f.get("availableMargin") or f.get("marginEquity") or f.get("portfolioValue") or 0)


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


def notify(st, msg):
    # journaux GitHub publics : on masque le capital et le P&L (Telegram recoit tout)
    log.info(re.sub(r"P&L ~ [+-]?[0-9.]+ \$", "P&L ~ (masque)",
                    re.sub(r"Capital du compte : [0-9.]+", "Capital du compte : (masque)", msg)))
    st["log"] = (st.get("log") or [])[-59:] + [time.strftime("%d/%m %H:%M") + " " + msg]
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return
    for essai in range(3):                       # Telegram coupe parfois : 3 essais
        try:
            r = SESSION.post("https://api.telegram.org/bot%s/sendMessage" % tok,
                             json={"chat_id": chat, "text": msg}, timeout=8)
            if r.ok:
                return
            log.warning("telegram HTTP %s", r.status_code)
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
#  MURS : lecture du carnet d'ordres reel
# =========================================================================
def grid_width(st, sym, mid, C):
    """Largeur de niveau FIXE pour un symbole (multiple du tick), recalculee a chaque
       decouverte (1 h) : les frontieres ne bougent plus a chaque minute."""
    g = st.setdefault("grid", {})
    if not g.get(sym):
        t = (st["inst"].get(sym) or {}).get("tick") or 0.0001
        g[sym] = max(t, round(mid * C["BUCKET_PCT"] / 100.0 / t) * t)
    return g[sym]


def find_walls(bids, asks, mid, C, w=None):
    """Regroupe le carnet par niveaux fixes et renvoie les murs :
       [(prix_centre, taille, cote)]  cote = +1 mur d'achat (sous le prix), -1 mur de vente"""
    w = w or mid * C["BUCKET_PCT"] / 100.0
    lo, hi = mid * (1 - C["RANGE_PCT"] / 100.0), mid * (1 + C["RANGE_PCT"] / 100.0)
    buckets = {}
    for side, rows in ((1, bids), (-1, asks)):
        for p, q in rows:
            if p < lo or p > hi or q <= 0:
                continue
            k = (side, int(math.floor(p / w)))
            buckets[k] = buckets.get(k, 0.0) + q
    if len(buckets) < 10:
        return [], buckets, w
    out = []
    for side in (1, -1):                           # une mediane PAR COTE (carnet asymetrique)
        vals = [q for (sd, _b), q in buckets.items() if sd == side]
        if len(vals) < 5:
            continue
        med = statistics.median(vals)
        rows = sorted(((q, b) for (sd, b), q in buckets.items()
                       if sd == side and q >= C["WALL_FACTOR"] * med), reverse=True)
        out += [((b + 0.5) * w, q, side) for q, b in rows[:C["MAX_WALLS"]]]
    return out, buckets, w


def update_walls(st, sym, walls, mid, now, C, buckets=None, w=0.0):
    """Memorise les murs dans le temps -> age, taille max, et detecte les murs
       qui disparaissent (RETIRE) avant que le prix ne les touche."""
    book = st["walls"].setdefault(sym, {})
    tol = w if w > 0 else mid * C["BUCKET_PCT"] / 100.0
    lo, hi = mid * (1 - C["RANGE_PCT"] / 100.0), mid * (1 + C["RANGE_PCT"] / 100.0)
    seen = set()
    for price, size, side in walls:
        key, dist = None, None
        for k, v in book.items():                  # le mur connu le PLUS PROCHE (< 1 niveau)
            dd = abs(v["price"] - price)
            if v["side"] == side and k not in seen and dd <= tol * 1.01 and (dist is None or dd < dist):
                key, dist = k, dd
        if key is None:
            key = "%d|%.10g" % (side, price)
            book[key] = {"price": price, "side": side, "first": now, "max": size}
        v = book[key]
        if v.get("gone"):                          # mur retire puis repose : on repart de zero
            v.update(first=now, max=size, n=0, tot=0, tr=[], near_ts=0, ice_alert=False)
        v.pop("out_ts", None)
        v.update(price=price, size=size, last=now, max=max(v["max"], size), miss=0, top=now, w=tol,
                 n=v.get("n", 0) + 1, tot=v.get("tot", 0) + 1)
        v.pop("gone", None)
        seen.add(key)
    events = []
    for k in list(book):
        v = book[k]
        if k in seen:
            continue
        if not (lo <= v["price"] <= hi):           # sorti de la fenetre (le prix s'est eloigne) :
            v.setdefault("out_ts", now)            # ni retire ni mange ; oublie au bout d'1 h
            if now - v["out_ts"] > 3600 and k != (st["pos"].get(sym) or {}).get("wall_key"):
                del book[k]                        # (sauf le mur d'une position ouverte)
            continue
        v.pop("out_ts", None)
        v["tot"] = v.get("tot", 0) + 1             # une lecture de plus pour ce mur
        # sorti du top MAX_WALLS mais toujours dans le carnet ? -> toujours la
        if buckets and w > 0:
            b = int(math.floor(v["price"] / w))
            q = buckets.get((v["side"], b), 0.0)    # meme mesure que la taille max (un seul niveau)
            if q >= C["WALL_GONE"] * v["max"]:
                v.update(size=q, last=now, miss=0, n=v.get("n", 0) + 1)
                continue
        # les teneurs de marche retirent / reposent leurs ordres en permanence :
        # un mur n'est "parti" qu'apres GONE_SNAPS lectures absentes de suite
        v["miss"] = v.get("miss", 0) + 1
        if v["miss"] < C["GONE_SNAPS"]:
            continue
        v["size"] = 0.0
        v.update(tr=[], ice_alert=False)           # un mur parti n'a plus d'absorption ni d'alerte
        old = now - v["first"] >= C["WALL_MIN_AGE"]
        if old and not v.get("gone"):
            v["gone"] = now
            # le prix n'a pas atteint le mur : il a ete RETIRE (scenario 3)
            reached = (mid <= v["price"]) if v["side"] > 0 else (mid >= v["price"])
            events.append(("MANGE" if reached else "RETIRE", v))
        if now - v.get("last", now) > 900:          # oublie au bout de 15 min
            del book[k]
    return events


def live_walls(st, sym, now, C):
    """Murs FIABLES : assez vieux, toujours presents, et stables (pas un mur qui clignote :
       les faux murs - spoofing - sont poses puis retires sans arret)."""
    return [dict(v, key=k) for k, v in (st["walls"].get(sym) or {}).items()
            if v.get("size", 0) > 0 and not v.get("miss") and now - v["first"] >= C["WALL_MIN_AGE"]
            and v.get("n", 0) >= C["WALL_STABLE"] * max(1, v.get("tot", 0))
            and now - v.get("top", 0) <= 3 * C["INTERVAL"]       # encore parmi les plus gros murs
            and v["size"] >= C["WALL_KEEP"] * v["max"]]


# =========================================================================
#  SIGNAL : rebond sur un mur qui tient (scenario 1) / mur mange (scenario 2)
# =========================================================================
def hvn_strength(bars, price, C):
    """Force (0..1) de la zone de volume DEJA ECHANGE au prix donne, sur les HVN_BARS
       bougies avant la derniere (meme calcul que la heatmap de l'indicateur MT4).
       Un ordre en attente peut etre retire ; un volume echange ne s'efface pas."""
    win = bars[-(C["HVN_BARS"] + 3):-3]
    if len(win) < 30:
        return 1.0
    lo = min(b[3] for b in win)
    hi = max(b[2] for b in win)
    nb = C["HVN_BINS"]
    if hi <= lo or not (lo <= price <= hi):
        return 0.0
    step = (hi - lo) / nb
    bins = [0.0] * nb
    for b in win:
        a = min(nb - 1, max(0, int((b[3] - lo) / step)))
        z = min(nb - 1, max(0, int((b[2] - lo) / step)))
        for k in range(a, z + 1):
            bins[k] += b[5] / (z - a + 1)
    sm = [(bins[max(0, k - 1)] + 2 * bins[k] + bins[min(nb - 1, k + 1)]) / 4 for k in range(nb)]
    mx = max(sm) or 1.0
    k = min(nb - 1, max(0, int((price - lo) / step)))
    return max(sm[max(0, k - 1):k + 2]) / mx


def okx_inst(sym):
    base = sym[3:-3].replace("XBT", "BTC").replace("XDG", "DOGE")
    return base + "-USDT-SWAP"


def okx_confirm(sym, wall, C, kmid=None):
    """Le mur Kraken correspond-il a un gros niveau dans le carnet OKX (meme cote, meme
       prix a +/- 1 niveau) ? Un mur present sur 2 plateformes n'est pas un bluff local.
       OKX cote en USDT, Kraken en USD : le prix du mur est ramene a la base OKX."""
    r = SESSION.get("https://www.okx.com/api/v5/market/books-full",
                    params={"instId": okx_inst(sym), "sz": "5000"}, timeout=5).json()
    if str(r.get("code", "0")) != "0":
        log.info("OKX %s : %s %s", okx_inst(sym), r.get("code"), r.get("msg"))
        return False
    d = (r.get("data") or [{}])[0]
    rows = d.get("bids" if wall["side"] > 0 else "asks") or []
    if len(rows) < 20 or not d.get("bids") or not d.get("asks"):
        return False
    w = wall.get("w") or wall["price"] * C["BUCKET_PCT"] / 100
    mid = (float(d["bids"][0][0]) + float(d["asks"][0][0])) / 2.0
    wall_px = wall["price"] * (mid / kmid) if kmid else wall["price"]
    buckets = {}
    for r_ in rows:
        px_, q = float(r_[0]), float(r_[1])
        if abs(px_ - mid) / mid * 100 > C["RANGE_PCT"]:
            continue
        b = int(math.floor(px_ / w))
        buckets[b] = buckets.get(b, 0.0) + q
    if len(buckets) < 5:
        return False
    med = statistics.median(buckets.values())
    b0 = int(math.floor(wall_px / w))
    best = max(buckets.get(b0 + i, 0.0) for i in (-1, 0, 1))
    return med > 0 and best >= C["OKX_FACTOR"] * med


def detect(bars, walls, C):
    o = [b[1] for b in bars]
    h = [b[2] for b in bars]
    l = [b[3] for b in bars]
    c = [b[4] for b in bars]
    v = [b[5] for b in bars]
    k = len(bars) - 1
    if k < max(C["VOL_AVG"], C["ATR_PERIOD"]) + 3:
        return None
    a = atr(h, l, c, C["ATR_PERIOD"])[k - 1]
    avgv = sum(v[k - C["VOL_AVG"]:k]) / C["VOL_AVG"]
    if not a or avgv <= 0:
        return None
    rng = h[k] - l[k]
    if rng <= 0:
        return None
    bub0, bub1 = v[k] >= C["BUBBLE"] * avgv, v[k - 1] >= C["BUBBLE"] * avgv
    tol = C["TOL_ATR"] * a
    best = None
    for w in walls:
        near = w.get("near_ts", 0)
        if not near or near < bars[k - 1][0] or w["first"] + C["WALL_MIN_AGE"] > near:
            continue                               # le mur doit etre confirme AVANT le contact
        W, d = w["price"], 0
        ice = is_iceberg(w, C)                     # iceberg : l'interet reel est deja prouve
        if bub0 or bub1 or ice:
            lowmin, highmax = min(l[k], l[k - 1]), max(h[k], h[k - 1])
            gw = max(w.get("w") or 0.0, 0.2 * a)   # une petite meche sous le mur est toleree
            if (w["side"] > 0 and c[k - 2] > W and W - gw <= lowmin <= W + tol and c[k] > W
                    and c[k] > o[k] and (c[k] - l[k]) >= 0.5 * rng and c[k] > c[k - 1]):
                d = 1                              # mur d'achat qui TIENT -> ACHAT
            if (w["side"] < 0 and c[k - 2] < W and W - tol <= highmax <= W + gw and c[k] < W
                    and c[k] < o[k] and (h[k] - c[k]) >= 0.5 * rng and c[k] < c[k - 1]):
                d = -1                             # mur de vente qui TIENT -> VENTE
        trs = w.get("tr", [])
        ab = sum(x[1] for x in trs) / w["max"] if w.get("max") else 0.0
        if d and C["ABS_FILTER"] and ab < C["ABS_MIN"] and len(trs) < C["ABS_HITS"]:
            d = 0                                  # le mur n'a encaisse aucun trade : pas prouve
        hv = hvn_strength(bars, W, C) if d else 0.0
        need_okx = False
        if d and C["HVN_FILTER"] and hv < C["HVN_MIN"] and not ice and C["OKX_CONFIRM"]:
            need_okx = True                        # pas de zone de volume : la preuve viendra d'OKX
        elif d and C["HVN_FILTER"] and hv < C["HVN_MIN"] and not ice:
            d = 0                                  # mur du carnet seul : pas de volume echange derriere
        if d and (best is None or (not need_okx, w["max"]) > (not best.get("need_okx"), best["wall"]["max"])):
            best = {"dir": d, "type": "ICEBERG" if ice else "REBOND", "wall": w, "atr": a, "close": c[k],
                    "vol": max(v[k], v[k - 1]) / avgv, "hvn": hv, "abs": ab, "need_okx": need_okx}
    return best


def mange_signal(bars, ev, C):
    """Scenario 2 : mur mange + bougie qui traverse avec une bulle -> continuation"""
    if not C["SIGNAL_MANGE"]:
        return None
    o, h, l, c, v = ([b[i] for b in bars] for i in range(1, 6))
    k = len(bars) - 1
    a = atr(h, l, c, C["ATR_PERIOD"])[k - 1]
    avgv = sum(v[k - C["VOL_AVG"]:k]) / C["VOL_AVG"]
    if not a or avgv <= 0 or v[k] < C["BUBBLE"] * avgv:
        return None
    W, side = ev["price"], ev["side"]
    if side > 0 and c[k] < W - C["TOL_ATR"] * a and c[k] < o[k]:
        return {"dir": -1, "type": "MANGE", "wall": ev, "atr": a, "close": c[k], "vol": v[k] / avgv}
    if side < 0 and c[k] > W + C["TOL_ATR"] * a and c[k] > o[k]:
        return {"dir": 1, "type": "MANGE", "wall": ev, "atr": a, "close": c[k], "vol": v[k] / avgv}
    return None


def plan(sig, C):
    """Entree / SL derriere le mur, elargi au plancher (ATR, % du prix, frais) / TP.
       La taille est calculee sur ce risque : la perte reste 1 % du capital."""
    d, W, a, e = sig["dir"], sig["wall"]["price"], sig["atr"], sig["close"]
    fee_min = 2 * C["FEE_TAKER"] * e / C["MAX_FEE_R"] * 1.02   # garantit que les frais passent
    r = max((e - (W - d * C["SL_BEHIND_ATR"] * a)) * d,
            C["MIN_RISK_ATR"] * a, e * C["SL_MIN_PCT"] / 100.0, fee_min)
    return e, e - d * r, e + d * C["RR"] * r


def sl_ok(entry, sl, C):
    r = abs(entry - sl)
    if r <= 0:
        return False
    fee_r = 2 * C["FEE_TAKER"] * entry / r
    return fee_r <= C["MAX_FEE_R"] and r / entry <= C["LIQ_SAFETY"] / C["LEVERAGE"]


# =========================================================================
#  ORDRES
# =========================================================================
def place_exits(st, sym, p):
    """Pose le stop et le TP. Le NOUVEL ordre est pose AVANT d'annuler l'ancien :
       la position n'est jamais sans protection, meme une seconde."""
    if p["qty"] <= 0:
        return
    old_stop, old_tp = p.get("stop_id"), p.get("tp_id")
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
            notify(st, "%s stop Kraken NON pose (%s) -> nouvel essai, fermeture si 2e echec" % (nm(sym), e))
    try:
        new = order({"orderType": "take_profit", "symbol": sym, "side": side_of(-p["dir"]),
                     "size": qty_str(st, sym, p["qty"]), "stopPrice": px_str(st, sym, p["tp"]),
                     "triggerSignal": "last", "reduceOnly": "true"})
        p["tp_id"] = new
        if old_tp and old_tp != new:
            cancel(old_tp)
    except Exception as e:
        p["tp_id"] = old_tp
        log.warning("TP : %s", e)


def cancel_all(sym):
    """Annule TOUS les ordres restants du symbole (stop / TP orphelins)."""
    if LIVE:
        try:
            priv("POST", "/api/v3/cancelallorders", {"symbol": sym})
        except Exception as e:
            log.warning("annulation globale %s : %s", sym, e)


def close_mkt(st, sym, p, price, why):
    qty = rq(st, sym, p["qty"])
    if qty > 0:
        try:
            # on sort D'ABORD : si ca echoue, le stop / TP chez Kraken restent en place
            order({"orderType": "mkt", "symbol": sym, "side": side_of(-p["dir"]),
                   "size": qty_str(st, sym, qty), "reduceOnly": "true"})
        except Exception as e:
            notify(st, "%s ERREUR fermeture (stop Kraken conserve) : %s" % (nm(sym), e))
            if LIVE and not p.get("stop_id") and p.get("sl"):
                place_exits(st, sym, p)
            return False
    cancel_all(sym)
    pnl = (price - p["entry"]) * p["dir"] * qty - CFG["FEE_TAKER"] * price * qty
    if not LIVE:
        st["paper"] += pnl
    st["pos"].pop(sym, None)
    st.get("pending", {}).pop(sym, None)
    notify(st, "%s %s @ %s  P&L ~ %+.2f $" % (nm(sym), why, rp(st, sym, price), pnl))
    save_state(st)                                 # un redemarrage ne doit rien perdre
    return True


def enter(st, sym, tf, sig, px, C):
    if len(st["pos"]) >= C["MAX_OPEN"] or sym in st["pos"]:
        return "position deja ouverte"
    if time.time() < st.get("block_until", 0):
        return "entree precedente incertaine, attente de verification"
    try:
        price = tickers().get(sym) or px.get(sym) or sig["close"]   # prix frais
    except Exception:
        price = px.get(sym) or sig["close"]
    if (price - sig["wall"]["price"]) * sig["dir"] <= 0:
        return "le prix a deja franchi le mur"
    e, sl, tp = plan(dict(sig, close=price), C)
    if not sl_ok(e, sl, C):
        return "SL trop loin (> %.1f %% : risque de liquidation x%s)" % (100 * C["LIQ_SAFETY"] / C["LEVERAGE"], C["LEVERAGE"])
    d = sig["dir"]
    # le prochain mur OPPOSE est la resistance suivante (video) : TP juste avant
    # (un mur oppose plus FAIBLE que le notre sera vraisemblablement mange par le rebond :
    #  seul un mur au moins aussi fort arrete le trajet)
    ours = sig["wall"].get("max") or 0.0
    opp = [v["price"] for v in live_walls(st, sym, time.time(), C)
           if v["side"] == -d and (v["price"] - e) * d > 0
           and v.get("max", 0.0) >= C["OPP_STRENGTH"] * ours]
    if opp:
        cap = min(opp, key=lambda x: abs(x - e)) - d * (st.get("grid", {}).get(sym) or 0)
        if (cap - e) * d < (tp - e) * d:
            tp = cap
    if (tp - e) * d < C["MIN_RR"] * abs(e - sl):
        return "mur oppose trop proche (gain/risque < %g)" % C["MIN_RR"]
    rf = (TICK.get(sym) or {}).get("rf", 0.0)
    sig["rf"] = rf
    if C["FUND_FILTER"] and rf * d > C["FUND_MAX"]:
        return "funding extreme : trop de %s deja en position (%+.4f %%/h)" % (
            "acheteurs" if d > 0 else "vendeurs", 100 * rf)
    delta = taker_delta(sym)
    sig["delta"] = delta
    if C["DELTA_FILTER"] and delta * d < C["DELTA_MIN"]:
        return "les %s ne frappent pas encore (delta %+d%%)" % ("acheteurs" if d > 0 else "vendeurs", int(100 * delta))
    eq = equity(st)
    full = eq * C["CAPITAL_PCT"] / 100 * C["LEVERAGE"] / e
    qty = rq(st, sym, full if C["SIZING"] == "FULL" else min(eq * C["RISK_PCT"] / 100 / abs(e - sl), full))
    risk_plan = qty * abs(e - sl)                  # perte maximale prevue, en USD
    tp_dist = abs(tp - e)
    if qty <= 0:
        return "taille a 0 (capital insuffisant pour la taille minimale Kraken)"
    if LIVE:
        # plan memorise AVANT l'ordre : si la confirmation se perd, la position reprise
        # recevra CE stop (et pas un stop d'urgence plus large)
        st.setdefault("pending", {})[sym] = {"dir": d, "sl": sl, "tp": tp, "ts": time.time()}
        save_state(st)
        sent, err = False, None
        for frac in (1.0, 0.84, 0.68):
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
            st["pending"].pop(sym, None)           # refus net de Kraken : rien n'a ete execute
        if not sent:
            st["block_until"] = time.time() + 180   # l'ordre a peut-etre quand meme ete execute
            return "ordre refuse par Kraken : %s" % err
        ex = None
        for _ in range(5):                       # l'ordre peut mettre un peu de temps
            time.sleep(1.5)
            try:
                ex = open_positions().get(sym)
            except Exception as err:            # (ne pas ecraser e = prix d'entree)
                log.warning("positions : %s", err)
                continue
            if ex and ex["size"] > 0 and ex["dir"] == d:
                break
        if not ex or ex["size"] <= 0 or ex["dir"] != d:
            # la reconciliation (adopt_orphans) la reprendra si elle apparait plus tard
            st["block_until"] = time.time() + 180
            return "entree envoyee mais aucune position constatee (surveillee)"
        qty, e = ex["size"], ex["price"] or e
        # le SL reste DERRIERE LE MUR (jamais elargi) ; seul le TP suit le prix obtenu
        tp = e + d * tp_dist
    else:
        st["paper"] -= C["FEE_TAKER"] * e * qty
    # remplissage trop loin du plan : entree au-dela du SL, ou perte possible > 1,35 x prevu
    # (1,2 faisait sortir sur un simple glissement de ~0,06 % en payant les frais pour rien)
    if (e - sl) * d <= 0 or qty * abs(e - sl) > 1.35 * risk_plan:
        a = sig["atr"]
        p = {"dir": d, "entry": e, "qty": qty, "sl": e - d * a, "tp": e + d * a, "tf": tf,
             "wall": None, "wall_side": 0, "wall_key": None, "adopted": True,
             "bar_ts": int(time.time()), "stop_id": None, "tp_id": None}
        st["pos"][sym] = p                        # si la sortie echoue : stop d'urgence pose au cycle suivant
        close_mkt(st, sym, p, price, "SORTIE immediate (prix obtenu trop loin du plan)")
        return None
    p = {"dir": d, "entry": e, "sl": sl, "tp": tp, "qty": qty, "tf": tf, "risk0": abs(e - sl),
         "wall": sig["wall"]["price"], "wall_side": sig["wall"]["side"],
         "wall_key": sig["wall"].get("key"),
         "bar_ts": int(time.time() // TF_SEC[tf] * TF_SEC[tf]), "stop_id": None, "tp_id": None}
    st["pos"][sym] = p
    st.get("pending", {}).pop(sym, None)
    place_exits(st, sym, p)
    save_state(st)
    notify(st, "%s %s %s (%s) levier max x%s @ %s  qte %s\nDOUBLE MUR %s %s : carnet (taille %.4g) + volume echange %d%%\n"
               "bulle x%.1f, agresseurs %+d%%, %s\n%s, funding %+.4f %%/h\n%s\nTP %s   SL %s (derriere le mur)"
           % (nm(sym), tf, "ACHAT" if d > 0 else "VENTE",
              {"REBOND": "le mur tient", "ICEBERG": "ICEBERG : le mur se recharge"}.get(sig["type"], "mur mange"),
              C["LEVERAGE"], rp(st, sym, e), qty,
              "D'ACHAT" if sig["wall"]["side"] > 0 else "DE VENTE", rp(st, sym, sig["wall"]["price"]),
              sig["wall"]["max"], int(100 * sig.get("hvn", 1.0)), sig["vol"], int(100 * sig.get("delta", 0)),
              ("ICEBERG (a encaisse %d%% de sa taille et se recharge)" if sig["type"] == "ICEBERG" else
               "absorption %d%%") % int(100 * sig.get("abs", 0)),
              "preuve : mur CONFIRME sur OKX" if sig.get("okx") else "preuve : zone de volume / iceberg",
              100 * sig.get("rf", 0.0), oi_note(st, sym, time.time()),
              rp(st, sym, tp), rp(st, sym, sl)))
    return None


def adopt_orphans(st, pos_ex, px, C):
    """Position ouverte chez Kraken mais inconnue du bot (job tue, etat perdu...) :
       on la reprend avec un stop d'urgence et un TP, pour ne JAMAIS la laisser nue."""
    if not C["ADOPT_ORPHANS"]:
        return
    for sym, ex in pos_ex.items():
        if sym in st["pos"] or ex["size"] <= 0 or not sym.startswith("PF_"):
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
        if pend and pend["dir"] == d and time.time() - pend["ts"] < 1800 and (price - pend["sl"]) * d > 0:
            sl = pend["sl"]                        # entree du bot dont la confirmation s'est perdue
        p = {"dir": d, "entry": e, "sl": sl, "tp": e + d * C["RR"] * abs(e - sl), "qty": ex["size"],
             "risk0": abs(e - sl),
             "tf": "15m", "wall": None, "wall_side": 0, "wall_key": None, "adopted": True,
             "bar_ts": int(time.time()), "stop_id": None, "tp_id": None}
        # pas de cancel_all : on ne touche pas aux ordres poses a la main ou par un autre outil
        st["pos"][sym] = p
        place_exits(st, sym, p)
        notify(st, "%s position Kraken inconnue reprise (%s qte %s) -> stop d'urgence %s, TP %s"
               % (nm(sym), "ACHAT" if d > 0 else "VENTE", ex["size"], rp(st, sym, sl), rp(st, sym, p["tp"])))


def manage(st, sym, price, pos_ex, C):
    p = st["pos"].get(sym)
    if not p:
        return
    d = p["dir"]
    # 1) d'abord : Kraken a-t-il deja ferme la position (stop / TP) ?
    if LIVE:
        ex = pos_ex.get(sym)
        if not ex or ex["dir"] != d or ex["size"] <= 0:
            try:                                  # 2e verification avant de conclure
                ex = open_positions().get(sym)
            except Exception:
                return                            # dans le doute, on ne touche a rien
        if ex and ex["size"] > 0 and ex["dir"] != d:
            # etat perime : la vraie position est dans l'autre sens -> on la laisse a
            # adopt_orphans (stop d'urgence) SANS annuler son stop actuel
            st["pos"].pop(sym, None)
            return
        if not ex or ex["dir"] != d or ex["size"] <= 0:
            cancel_all(sym)
            why = ("stop au prix d'entree, ~0 $" if p.get("be") and abs(price - p["entry"]) < p.get("risk0", 0)
                   else "TP" if (price - p["entry"]) * d > 0 else "STOP")
            notify(st, "%s position fermee par Kraken (%s probable)" % (nm(sym), why))
            st["pos"].pop(sym, None)
            return
        p["qty"] = ex["size"]
        if not p.get("stop_id"):                  # stop jamais pose -> on reessaie
            place_exits(st, sym, p)
            if not p.get("stop_id") and p.get("stopfail", 0) >= 2:
                close_mkt(st, sym, p, price, "SORTIE : stop impossible a poser chez Kraken")
                return
    # securite : prix nettement au-dela du SL et toujours en position -> on sort
    if not p.get("be") and (p["sl"] - price) * d > 0.3 * p.get("risk0", abs(p["entry"] - p["sl"])):
        close_mkt(st, sym, p, price, "STOP (securite bot)")
        return
    # 2) scenario 3 en position : le mur a ete RETIRE alors que le prix ne l'a pas
    #    traverse -> on sort. S'il a ete mange (prix au-dela), c'est le SL qui joue.
    if (C["EXIT_IF_WALL_GONE"] or C["EXIT_IF_WALL_BROKEN"]) and p.get("wall_key") and not p.get("adopted"):
        v = (st["walls"].get(sym) or {}).get(p["wall_key"])
        still_our_side = (price - p["wall"]) * d > 0
        gone = not v or v.get("size", 0) <= 0
        if gone and not still_our_side and C["EXIT_IF_WALL_BROKEN"]:
            # le stop est plus loin que le mur : si le mur est tombe ET traverse, l'idee
            # du trade est fausse -> on sort sans attendre le stop complet
            close_mkt(st, sym, p, price, "SORTIE : mur traverse (l'idee du trade est fausse)")
            return
        if C["EXIT_IF_WALL_GONE"] and gone and still_our_side and not p.get("be"):
            if (price - p["entry"]) * d > 0:
                # en gain : on ne sort pas sur un retrait (souvent du bluff) -> stop au prix
                # d'entree (plus de perte possible) et on laisse aller au TP
                old_sl = p["sl"]
                p["sl"] = p["entry"]
                place_exits(st, sym, p)
                if p.get("stop_id") and p.get("stop_px") == p["sl"]:   # Kraken a bien accepte le nouveau stop
                    p["be"] = True
                    save_state(st)
                    notify(st, "%s mur retire, trade en gain -> stop deplace au prix d'entree %s (zero perte)"
                           % (nm(sym), rp(st, sym, p["entry"])))
                else:                              # refuse : on garde l'ancien stop, nouvel essai
                    p["sl"] = old_sl
                    log.info("%s stop au prix d'entree refuse par Kraken -> nouvel essai au prochain cycle", sym)
            else:
                close_mkt(st, sym, p, price, "SORTIE : le mur a ete retire (trade en perte)")
            return
    # 3) duree max
    if not p.get("adopted") and time.time() - p.get("bar_ts", time.time()) > C["MAX_HOLD"] * TF_SEC.get(p.get("tf"), 900):
        close_mkt(st, sym, p, price, "SORTIE duree max")
        return
    if (price <= p["sl"]) if d > 0 else (price >= p["sl"]):
        if not LIVE or not p.get("stop_id"):
            close_mkt(st, sym, p, price, "STOP")
    elif (price >= p["tp"]) if d > 0 else (price <= p["tp"]):
        if not LIVE or not p.get("tp_id"):
            close_mkt(st, sym, p, price, "TP atteint")


# =========================================================================
#  ETAT + BOUCLE
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
            return st
        except Exception as e:
            log.warning("etat %s illisible (%s)", fn, e)
    return st


def save_state(st):
    """Ecriture ATOMIQUE : fichier temporaire puis remplacement. Un arret brutal au milieu
       ne peut plus laisser un fichier tronque (l'ancien reste intact + copie .bak)."""
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            # depot PUBLIC : on n'ecrit pas le journal des messages (capital, P&L...)
            json.dump({k: v for k, v in st.items() if k != "log"}, f, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(STATE_FILE):
            try:
                os.replace(STATE_FILE, STATE_FILE + ".bak")
            except Exception:
                pass
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        log.warning("etat non sauvegarde : %s", e)


def check_keys(st):
    if not (os.environ.get("KRAKEN_KEY") and os.environ.get("KRAKEN_SECRET")):
        notify(st, "ATTENTION : aucune cle Kraken Futures -> le mode REEL ne pourra pas trader.")
        return False
    try:
        f = (priv("GET", "/api/v3/accounts").get("accounts") or {}).get("flex") or {}
        eq = float(f.get("marginEquity") or f.get("portfolioValue") or 0)
        notify(st, "Cles Kraken FUTURES valides. Capital du compte : %.2f USD" % eq)
        if eq <= 0:
            notify(st, "ATTENTION : capital a 0 sur le compte Futures -> aucun ordre possible.")
        return True
    except Exception as e:
        notify(st, "ATTENTION : cles Kraken REFUSEES (%s)" % e)
        return False


def cycle(st, C):
    if time.time() - st.get("disco", 0) > 3600 or not C["SYMS"]:
        st["inst"] = instruments()
        C["SYMS"] = discover(st, C)
        if FEED:
            FEED.set_symbols(set(C["SYMS"]) | set(st["pos"]))
        st["disco"] = time.time()
        st["grid"] = {}                           # grille recalculee 1x par heure
        keep = set(C["SYMS"]) | set(st["pos"]) | {k for k, v in (st.get("pending") or {}).items()
                                                  if time.time() - v.get("ts", 0) < 1800}
        for key in ("walls", "spoof", "tr_seq", "pending", "oi_hist"):
            st[key] = {k: v for k, v in (st.get(key) or {}).items() if k in keep}
        st["last"] = {k: v for k, v in (st.get("last") or {}).items() if k.split("|")[0] in keep}
        st["cooldown"] = {k: v for k, v in (st.get("cooldown") or {}).items() if k.split("|")[0] in keep}
        log.info("%d actifs suivis : %s", len(C["SYMS"]), ", ".join(nm(s) for s in C["SYMS"][:15]))
    if st.get("mode") != C["MODE"]:
        if st.get("mode") != "live":              # positions fictives : on repart a zero
            st["pos"] = {}
        st["mode"] = C["MODE"]
        notify(st, "Bot MURS & BULLES (carnet d'ordres reel Kraken) [%s] x%s %s %s%% | %s | %d perpetuels"
               % ("REEL" if LIVE else "PAPER", C["LEVERAGE"], C["SIZING"], C["RISK_PCT"],
                  "/".join(C["TF_LIST"]), len(C["SYMS"])))
    if LIVE:
        for sym in list(C["SYMS"]):
            if st["lev"].get(sym) != C["LEVERAGE"]:
                try:
                    priv("PUT", "/api/v3/leveragepreferences",
                         {"symbol": sym, "maxLeverage": C["LEVERAGE"]})
                    st["lev"][sym] = C["LEVERAGE"]
                except Exception as e:
                    if "MARKET_RESTRICTED" in str(e):   # interdit pour ce compte : on l'ecarte
                        if sym not in st.setdefault("blocked", []):
                            st["blocked"].append(sym)
                        C["SYMS"].remove(sym)
                        log.info("%s interdit pour ce compte Kraken -> retire de la liste", sym)
                    else:
                        log.warning("levier %s : %s", sym, e)

    px = tickers()
    oih = st.setdefault("oi_hist", {})             # open interest : 1 point / 5 min, 2 h gardees
    for sym in C["SYMS"]:
        oi = (TICK.get(sym) or {}).get("oi")
        if oi:
            lst = oih.setdefault(sym, [])
            if not lst or time.time() - lst[-1][0] >= 300:
                lst.append([time.time(), oi])
            oih[sym] = [x for x in lst if time.time() - x[0] <= 7200]
    pos_ex = open_positions() if LIVE else {}
    now = time.time()
    events = {}
    # 1) photo du carnet de chaque actif -> murs (age, taille), murs retires / manges
    n_retire = 0
    for sym in C["SYMS"]:
        if not px.get(sym):
            continue
        try:
            bids, asks = orderbook(sym)
            walls, buckets, bw = find_walls(bids, asks, px[sym], C, grid_width(st, sym, px[sym], C))
            ev = update_walls(st, sym, walls, px[sym], now, C, buckets, bw)
            events[sym] = ev
            for kind, v in ev:
                dist = abs(px[sym] - v["price"]) / px[sym] * 100
                if kind == "RETIRE" and dist <= 0.3:
                    # retire alors que le prix approchait = signature du faux mur -> reputation
                    sp = st.setdefault("spoof", {}).setdefault(sym, [])
                    sp.append([v["side"], now])
                    st["spoof"][sym] = [e for e in sp if now - e[1] <= C["SPOOF_WIN"] * 60]
                    n_retire += 1                  # resume en 1 ligne par cycle (journal lisible)
                    if C["NOTIFY_RETIRE"]:
                        notify(st, "%s MUR %s RETIRE juste avant l'impact (%s, a %.2f%% du prix) -> pas de trade"
                               % (nm(sym), "D'ACHAT" if v["side"] > 0 else "DE VENTE",
                                  rp(st, sym, v["price"]), dist))
            try:
                absorb_trades(st, sym, px[sym], now, C)
                iceberg_alerts(st, sym, px[sym], now, C)
            except Exception as e:
                log.warning("trades %s : %s", sym, e)
            time.sleep(0.15)
        except Exception as e:
            log.warning("carnet %s : %s", sym, e)

    # 2) positions : reprise des orphelines, stop / TP / sortie si le mur disparait
    if LIVE:
        try:
            adopt_orphans(st, pos_ex, px, C)
        except Exception as e:
            log.warning("reconciliation : %s", e)
    for sym in list(st["pos"]):
        try:
            if px.get(sym):
                manage(st, sym, px[sym], pos_ex, C)
        except Exception as e:
            log.warning("suivi %s : %s", sym, e)

    # 3) a chaque bougie cloturee : le prix a-t-il rebondi sur un mur qui tient ?
    stats = st.setdefault("stats", {})
    cd = st.setdefault("cooldown", {})
    for tf in sorted(C["TF_LIST"], key=lambda x: TF_SEC[x], reverse=True):   # 1h d'abord
        tfs = TF_SEC[tf]
        last_closed = int(now // tfs * tfs) - tfs
        if now - (last_closed + tfs) < 5:
            continue
        for sym in C["SYMS"]:
            akey = "%s|%s" % (sym, tf)
            if st["last"].get(akey) == last_closed:
                continue
            walls = [w for w in live_walls(st, sym, now, C)
                     if not spoof_side(st, sym, w["side"], now, C)     # cote qui bluffe : ignore
                     and cd.get("%s|%s" % (sym, w["key"]), 0) < now]   # mur deja trade : pause
            gone = [v for kind, v in events.get(sym, []) if kind == "MANGE"]
            if not walls and not gone:
                st["last"][akey] = last_closed
                continue
            try:
                bars = candles(sym, tf)
                time.sleep(0.08)
                if len(bars) < 40 or bars[-1][0] != last_closed:
                    continue
                st["last"][akey] = last_closed
                sig = detect(bars, walls, C)
                for v in gone:
                    sig = sig or mange_signal(bars, v, C)
                if not sig:
                    continue
                key = "%s|%s|%s" % (sym, tf, last_closed)
                if key in st["seen"]:
                    continue
                st["seen"].append(key)
                stats["signaux"] = stats.get("signaux", 0) + 1
                sig["okx"] = None                  # None = OKX pas consulte (pas necessaire)
                if sig.get("need_okx"):
                    try:
                        sig["okx"] = okx_confirm(sym, sig["wall"], C, px.get(sym))
                    except Exception as e:
                        log.warning("OKX %s : %s", sym, e)
                        sig["okx"] = False
                if sig.get("need_okx") and not sig["okx"]:
                    stats["refus: ni zone de volume ni mur OKX"] = stats.get("refus: ni zone de volume ni mur OKX", 0) + 1
                    log.info("%s %s signal sur mur %s ignore : ni zone de volume ni mur sur OKX"
                             % (nm(sym), tf, rp(st, sym, sig["wall"]["price"])))
                    continue
                why = enter(st, sym, tf, sig, px, C)
                if not why:
                    stats["trades"] = stats.get("trades", 0) + 1
                    if sig["wall"].get("key"):
                        cd["%s|%s" % (sym, sig["wall"]["key"])] = now + C["WALL_COOLDOWN"] * 60
                if why:                            # journal seulement (pas de Telegram a chaque refus)
                    motif = why.split("(")[0].strip()[:40]
                    stats["refus: " + motif] = stats.get("refus: " + motif, 0) + 1
                    log.info("%s %s signal %s sur mur %s ignore : %s"
                           % (nm(sym), tf, "ACHAT" if sig["dir"] > 0 else "VENTE",
                              rp(st, sym, sig["wall"]["price"]), why))
            except Exception as e:
                log.warning("%s %s : %s", sym, tf, e)
    st["seen"] = st["seen"][-600:]
    st["cooldown"] = {k: v for k, v in cd.items() if v > now}
    # resume regulier : l'utilisateur sait ce que le bot voit, meme sans trade
    if not st.get("stats_ts"):
        st["stats_ts"] = now
    elif now - st["stats_ts"] >= C["SUMMARY_HOURS"] * 3600:
        lignes = ["%s : %d" % (k, v) for k, v in sorted(stats.items(), key=lambda x: -x[1])]
        notify(st, "Resume %dh : %d signaux detectes, %d trades\n%s"
               % (C["SUMMARY_HOURS"], stats.get("signaux", 0), stats.get("trades", 0),
                  "\n".join(l for l in lignes if l.startswith("refus"))[:900] or "aucun refus"))
        st["stats"], st["stats_ts"] = {}, now
    nw = sum(len(live_walls(st, s, now, C)) for s in C["SYMS"])
    log.info("cycle ok : %d murs confirmes sur %d actifs, %d position(s), %d mur(s) retire(s) a l'approche",
             nw, len(C["SYMS"]), len(st["pos"]), n_retire)


def main():
    C = CFG
    if LIVE and not (os.environ.get("KRAKEN_KEY") and os.environ.get("KRAKEN_SECRET")):
        sys.exit("MODE live sans cles Kraken Futures")
    st = load_state(C)
    st.pop("tr_wsseq", None)                       # compteur du flux : propre a chaque job
    global FEED
    if C["WS_TRADES"] and websocket is not None:
        FEED = TradeFeed()
        threading.Thread(target=FEED.run, daemon=True).start()
        log.info("Flux en direct des trades Kraken : demarre")
    elif C["WS_TRADES"]:
        log.warning("module websocket absent : trades lus par l'API REST (100 par minute)")
    log.info("Demarrage MURS & BULLES [%s] x%s %s | %s | boucle %s min, scan toutes les %s s",
             "REEL" if LIVE else "PAPER", C["LEVERAGE"], C["SIZING"],
             "/".join(C["TF_LIST"]), C["LOOP_MINUTES"], C["INTERVAL"])
    if LIVE:
        check_keys(st)

    def _arret(signum, frame):                     # job annule / redemarre : on ne perd rien
        for sg2 in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sg2, signal.SIG_IGN)     # un 2e signal n'interrompt pas la sauvegarde
            except Exception:
                pass
        save_state(st)
        log.info("Arret demande (signal %s) : etat sauvegarde.", signum)
        sys.exit(0)
    for sg in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sg, _arret)
        except Exception:
            pass
    end = time.time() + C["LOOP_MINUTES"] * 60
    while True:
        t0 = time.time()
        try:
            cycle(st, C)
        except Exception as e:
            log.warning("cycle : %s", e)
        save_state(st)
        if time.time() + 180 >= end:
            log.info("Fin de ce job (le workflow se relance automatiquement).")
            return
        time.sleep(max(5, C["INTERVAL"] - (time.time() - t0)))


if __name__ == "__main__":
    main()
