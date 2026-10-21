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
    3. BIG TRADE RESPECTE   un gros ACHETEUR defend la zone (ordre >= BIG_MULT x
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

try:
    import websocket
except Exception:
    websocket = None

BASE = "https://futures.kraken.com"
TF_SEC = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400}
STATE_FILE = "kraken_vwap_state.json"


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
    "TOUCH_PCT": envf("TOUCH_PCT", 0.15),         # "sur la VWAP" = a +/- X % de la VWAP
    "TREND_BARS": envf("TREND_BARS", 12),         # tendance : sur les X bougies precedentes (3 h)...
    "TREND_MIN": envf("TREND_MIN", 0.7),          # ...au moins X du bon cote de la VWAP
    # 2. vendeurs absorbes
    "ABS_DELTA": envf("ABS_DELTA", 0.15),         # delta de la bougie <= -X du volume (achat)
    "MIN_BAR_TRADES": envf("MIN_BAR_TRADES", 60), # bougie avec assez de trades pour etre lue
    # 3. big trade respecte
    "BIG_MULT": envf("BIG_MULT", 15.0),           # gros ordre = >= X fois la taille mediane
    "BIG_SAMPLE": envf("BIG_SAMPLE", 3000),       # mediane calculee sur les X derniers ordres
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
    "RISK_PCT": envf("RISK_PCT", 1.0),
    "CAPITAL_PCT": envf("CAPITAL_PCT", 95.0),
    "LIQ_SAFETY": envf("LIQ_SAFETY", 0.75),
    "MAX_OPEN": envf("MAX_OPEN", 1),
    "FEE_TAKER": envf("FEE_TAKER", 0.0005),
    "MAX_FEE_R": envf("MAX_FEE_R", 0.35),
    "PAPER_BALANCE": envf("PAPER_BALANCE", 1000.0),
    # IA d'apprentissage (voir la section "IA" plus bas)
    "LEARN": envf("LEARN", 1),                    # 0 = criteres fixes de la video
    "LEARN_MIN": envf("LEARN_MIN", 40),           # resultats necessaires avant le 1er reglage
    "LEARN_EVERY_MIN": envf("LEARN_EVERY_MIN", 30),
    "LEARN_KEEP": envf("LEARN_KEEP", 1500),       # memoire : les X derniers setups
    # boucle
    "LOOP_MINUTES": envf("LOOP_MINUTES", 320),
    "FAST_SEC": envf("FAST_SEC", 2),              # lecture du flux / cloture des bougies
    "SLOW_SEC": envf("SLOW_SEC", 15),             # positions Kraken / stops / TP
    "SUMMARY_HOURS": envf("SUMMARY_HOURS", 6.0),
}
CFG["BAR_SEC"] = TF_SEC.get(CFG["BAR_TF"], 900)
CFG["SYMS"] = []
CFG["RR_ADOPT"] = CFG["RR"]
LIVE = CFG["MODE"].lower() == "live"

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
                while not self.stop:               # attendre la liste des cryptos AVANT de se
                    with self.lock:                # connecter : Kraken n'envoie rien tant qu'on
                        if self.syms:              # n'est pas abonne, et recv() ne rend pas la main
                            break
                    time.sleep(1)
                ws = websocket.create_connection(self.URL, timeout=20)
                with self.lock:
                    self.acked = set()             # nouvelle connexion : tout re-confirmer
                # heartbeat : un message regulier meme en marche calme -> la boucle tourne,
                # les nouvelles cryptos sont abonnees sans attendre
                ws.send(json.dumps({"event": "subscribe", "feed": "heartbeat"}))
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
                            first = not self.acked
                            self.acked |= set(x.upper() for x in (m.get("product_ids") or []))
                            self.alive = time.time()
                        if first:
                            log.info("Flux en direct des trades Kraken : CONNECTE (abonnement confirme)")
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
    pub_msg = re.sub(r"Capital du compte : [0-9.]+", "Capital du compte : (masque)", msg)
    pub_msg = re.sub(r"P&L ~ [+-]?[0-9.]+ \$", "P&L ~ (masque)", pub_msg)
    pub_msg = re.sub(r"perte max ~[0-9.]+ \$", "perte max ~(masque)", pub_msg)   # = % du capital
    log.info(pub_msg)
    st["log"] = (st.get("log") or [])[-59:] + [time.strftime("%d/%m %H:%M") + " " + msg]
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return
    # envoi en arriere-plan : une panne Telegram ne retarde plus la gestion des positions
    threading.Thread(target=_send_telegram, args=(tok, chat, msg), daemon=True).start()


def _send_telegram(tok, chat, msg):
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
            notify(st, "%s stop Kraken NON pose (%s) -> nouvel essai, fermeture si 2e echec" % (nm(sym), e))


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
            notify(st, "%s TP Kraken NON pose (%s) -> nouvel essai ; le bot surveille le TP lui-meme" % (nm(sym), e))


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
        recent = pend and pend["dir"] == d and time.time() - pend["ts"] < 1800
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
        notify(st, "%s position Kraken inconnue reprise (%s qte %s) -> stop d'urgence %s, TP %s"
               % (nm(sym), "ACHAT" if d > 0 else "VENTE", ex["size"], rp(st, sym, sl), rp(st, sym, p["tp"])))



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
            return st
        except Exception as e:
            log.warning("etat %s illisible (%s)", fn, e)
    return st


_SAVING = [False]


def save_state(st):
    """Ecriture ATOMIQUE : fichier temporaire puis remplacement. Un arret brutal au milieu
       ne peut plus laisser un fichier tronque (l'ancien reste intact + copie .bak)."""
    if _SAVING[0]:                                 # deja en cours (signal pendant la sauvegarde)
        return
    _SAVING[0] = True
    try:
        tmp = STATE_FILE + ".tmp%d" % os.getpid()
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
    finally:
        _SAVING[0] = False


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

    # ---------- historique : VWAP depuis minuit UTC + contexte de tendance ----------
    def seed(self, now=None):
        now = now or time.time()
        day0 = int(now // 86400 * 86400)
        B = CFG["BAR_SEC"]
        # contexte de tendance (TREND_BARS + marge) et VWAP depuis 0 h UTC
        frm = min(day0, int(now) - (CFG["TREND_BARS"] + 8) * B)
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
            if self.cur and minute > self.cur["t"]:
                closed.append(self._close())
            d = int(t // 86400)
            if d != self.day:                      # nouveau jour UTC : VWAP remise a zero
                self.day, self.pv, self.vv = d, 0.0, 0.0
                self.setup = {1: None, -1: None}
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
        if self.cur and now >= self.cur["t"] + CFG["BAR_SEC"] + 3:
            closed.append(self._close())
        return [b for b in closed if b]

    def _close(self):
        b, self.cur = self.cur, None
        if not b:
            return None
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
        med = statistics.median(self.prints) if len(self.prints) >= CFG["BIG_WARMUP"] else None
        b["big"] = []
        mult = min(CFG["BIG_MULT"], GRID["BIG_MULT"][0])   # l'IA juge ensuite le multiple utile
        for (ms, side), (q, pq) in agg.items():
            if med and q >= mult * med:
                b["big"].append({"price": pq / q, "side": side, "size": q, "x": q / med})
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
    """Gros ordre agressif DANS NOTRE SENS (acheteur pour un achat) dans la zone [lo, hi]."""
    want = "buy" if d > 0 else "sell"
    c = [x for x in b.get("big") or [] if x["side"] == want and lo <= x["price"] <= hi and x["x"] >= min_x]
    return max(c, key=lambda x: x["size"]) if c else None


def _start(fl, b, prev, d, L):
    """Points 1 + 2 (criteres SOUPLES : l'IA trie ensuite). Renvoie la checklist ou None."""
    if b.get("partial") or b["n"] < L["MIN_BAR_TRADES"] or len(prev) < L["TREND_BARS"]:
        return None
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
    prev = [x for x in list(fl.bars)[:-1] if x.get("vwap")]
    for d in (1, -1):
        side_txt = "ACHAT" if d > 0 else "VENTE"
        S = fl.setup[d]
        if S:
            S["age"] += 1
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
            elif S["age"] > C["SEQ_BARS"]:
                fl.setup[d] = None
                continue
            if S:
                # pire cloture sous la VWAP pendant la sequence : jugee par l'IA avec la
                # meme tolerance que le contact (0,15 % au depart en 15 min)
                pen = ((v - b["c"]) if d > 0 else (b["c"] - v)) / v * 100.0
                S["touch"] = max(S["touch"], pen)
            if not S:
                pass
            elif S["stage"] == 1:
                big = _big_in(b, d, S["lo"] - tol, S["hi"] + tol, L["BIG_MULT"])
                if big and (b["c"] - big["price"]) * d > 0:      # bougie cloture au-dela : RESPECTE
                    S.update(stage=2, big=big["price"], big_x=big["x"])
                    event(st, "%s %s 3/4 : big trade x%.0f respecte a %s"
                          % (nm(fl.sym), side_txt, big["x"], px_str(st, fl.sym, big["price"])))
                continue
            elif S["stage"] == 2:
                big = _big_in(b, d, S["lo"] - tol, S["hi"] + tol, L["BIG_MULT"])
                if big and big["x"] > S.get("big_x", 0) and (b["c"] - big["price"]) * d > 0:
                    S.update(big=big["price"], big_x=big["x"])   # un plus gros defenseur
                pc = prev[-1]["c"] if prev else b["o"]
                if b["dpct"] * d >= L["BUY_DELTA"] and (b["c"] - pc) * d > 0:
                    sig = {"dir": d, "close": b["c"], "vwap": v, "lo": S["lo"], "hi": S["hi"],
                           "big": S["big"], "big_x": S.get("big_x", 0), "abs": S["abs"],
                           "conf": b["dpct"], "atr": fl.atr(), "trend": S["trend"],
                           "touch": S["touch"], "sym": fl.sym, "t": b["t"],
                           "new_sample": not S.get("sampled")}
                    S["sampled"] = True
                    ok, why = gate(sig, st, C)
                    sig["take"] = ok
                    if ok:
                        event(st, "%s %s 4/4 : %s de retour (delta %+d%%) -> ENTREE CONFIRMEE"
                              % (nm(fl.sym), side_txt, "acheteurs" if d > 0 else "vendeurs",
                                 int(100 * b["dpct"])))
                        fl.setup = {1: None, -1: None}
                    elif sig["new_sample"]:
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
        if big and (b["c"] - big["price"]) * d > 0:
            S.update(stage=2, big=big["price"], big_x=big["x"])
            event(st, "%s %s 3/4 : big trade x%.0f respecte a %s"
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
#     amelioration minimale exigee, au moins 20 setups par jeu de criteres.
#     Un actif qui perd nettement est mis en pause 24 h. Si la strategie perd sur la
#     periode recente, le risque par trade est divise par 2 (jamais augmente au-dela du
#     reglage, jamais de levier en plus).
GRID = {
    "ABS_DELTA": [0.10, 0.15, 0.20, 0.25, 0.30, 0.40],   # force mini de l'absorption
    "BIG_MULT": [7.0, 10.0, 15.0, 20.0, 30.0, 50.0],      # taille mini du big trade (x mediane)
    "BUY_DELTA": [0.10, 0.15, 0.20, 0.25, 0.30, 0.40],   # force mini du retour des acheteurs
    "TREND_MIN": [0.6, 0.7, 0.8, 0.9],                   # tendance mini avant le retour a la VWAP
    "TOUCH_PCT": [0.30, 0.25, 0.20, 0.15, 0.10, 0.06],   # distance maxi a la VWAP (%)
}
FEAT = {"ABS_DELTA": 2, "BIG_MULT": 3, "BUY_DELTA": 4, "TREND_MIN": 5, "TOUCH_PCT": 6}
LEARN_NMIN = 30


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
    if not isinstance(lr, dict) or lr.get("tf") != C["BAR_TF"]:
        lr = st["learn"] = {"tf": C["BAR_TF"]}     # autre unite de temps : on reapprend
    if not isinstance(lr.get("params"), dict):
        lr["params"] = {k: _snap(k, C[k]) for k in GRID}   # depart : criteres de la video
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
        return False, "big trade x%.0f < x%.0f" % (x, P["BIG_MULT"])
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
    lr["open"] = lr["open"][-299:] + [{"s": sig["sym"], "t": sig["t"], "d": sig["dir"], "e": e, "sl": sl,
                                      "tp": tp, "r": abs(e - sl), "best": e, "be": 0,
                                      "f": [round(v, 4) for v in _feat(sig)]}]


def learn_track(st, sym, b, C):
    """A chaque bougie cloturee : fait avancer les setups suivis (prudent : si le stop et
       le TP sont touches dans la meme bougie, c'est le stop qui compte)."""
    if not C["LEARN"]:
        return
    lr = learn_st(st, C)
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
            if b["t"] - o["t"] >= C["MAX_HOLD_MIN"] * 60:
                res = (b["c"] - e) * d - fee
        if res is None:
            keep.append(o)
            continue
        R = res / r if r else 0.0
        lr["done"] = lr["done"][-(int(C["LEARN_KEEP"]) - 1):] + [[int(o["t"]), o["s"]] + o["f"] + [round(R, 3)]]
    lr["open"] = keep


def _score(Rs):
    n = len(Rs)
    if n < LEARN_NMIN:
        return None
    m = sum(Rs) / n
    sd = statistics.pstdev(Rs) if n > 1 else 1.0
    return m - sd / math.sqrt(n)                   # borne basse : on ne croit pas a la chance


def _Rs(done, P, paused=None):
    return [x[7] for x in done if _passes(x[2:7], P) and not (paused and x[1] in paused)]


def learn_update(st, C):
    """Le cycle d'apprentissage. Ne change rien tant qu'il n'y a pas assez de preuves."""
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
    P = dict(lr["params"])
    changes = []
    for k in GRID:                                 # un cran maximum par parametre et par cycle
        g = GRID[k]
        i = g.index(P[k]) if P[k] in g else g.index(_snap(k, P[k]))
        base = _score(_Rs(done, P))
        best_i, best_s = i, base
        for j in (i - 1, i + 1):
            if 0 <= j < len(g):
                Q = dict(P, **{k: g[j]})
                sc = _score(_Rs(done, Q))
                # on ne bouge que si le nouveau reglage a une esperance POSITIVE meme dans le
                # pire cas (borne basse > 0) : sur du bruit, l'IA ne touche a rien
                if sc is not None and sc > 0 and (best_s is None or sc > best_s + 0.02):
                    best_i, best_s = j, sc
        if best_i != i:
            changes.append("%s %s -> %s" % (k, P[k], g[best_i]))
            P[k] = g[best_i]
    # pause des actifs qui perdent nettement (re-evalues a chaque cycle)
    paused = {s: u for s, u in lr["paused"].items() if u > now}
    by = {}
    for x in done:
        if _passes(x[2:7], P):
            by.setdefault(x[1], []).append(x[7])
    for sym, Rs in by.items():
        if len(Rs) >= 12 and sym not in paused:
            m = sum(Rs) / len(Rs)
            if m < -0.25 and m + statistics.pstdev(Rs) / math.sqrt(len(Rs)) < 0:
                paused[sym] = now + 24 * 3600
                changes.append("pause 24 h %s (%+.2f R en moyenne sur %d setups)" % (nm(sym), m, len(Rs)))
    lr["paused"] = paused
    # risque : divise par 2 si la strategie perd sur la periode recente, jamais augmente
    Rs = _Rs(done[-300:], P)
    if len(Rs) < 30:
        Rs = _Rs(done, P)[-300:]
    if len(Rs) >= 30:
        m = sum(Rs) / len(Rs)
        up = m + statistics.pstdev(Rs) / math.sqrt(len(Rs))
        rm = 0.5 if up < 0 else 1.0 if m >= 0 else lr["risk_mult"]
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
        notify(st, txt)


def learn_summary(st, C):
    if not C["LEARN"]:
        return "IA desactivee (criteres fixes de la video)"
    lr = learn_st(st, C)
    P = lr["params"]
    Rs = _Rs(lr["done"], P)
    return ("IA : %d setups appris, %d en cours | absorption >= %d%%, big trade >= x%.0f, retour >= %d%%, "
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
#  ENTREE
# =========================================================================
def enter(st, sym, sig, fl, C):
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
    eq = equity(st)
    full = eq * C["CAPITAL_PCT"] / 100 * C["LEVERAGE"] / e
    fees = 2 * C["FEE_TAKER"] * e
    qty = rq(st, sym, full if C["SIZING"] == "FULL" else min(eq * C["RISK_PCT"] / 100 / (abs(e - sl) + fees), full))
    if qty <= 0:
        return "taille a 0 (capital insuffisant pour la taille minimale Kraken)"
    risk_plan = qty * (abs(e - sl) + fees)
    tp_dist = abs(tp - e)
    if LIVE:
        cancel_all(sym)
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
    notify(st, "%s %s @ %s  qte %s  (levier max x%s)\n"
               "CHECKLIST VWAP 4/4 :\n"
               " 1. prix sur la VWAP %s\n"
               " 2. %s absorbes (delta %+d%%)\n"
               " 3. big trade x%.0f respecte a %s\n"
               " 4. %s de retour (delta %+d%%)\n"
               "SL %s (%s)   TP %s (%.1f R)\nperte max ~%.2f $ frais compris"
           % (nm(sym), "ACHAT" if d > 0 else "VENTE", px_str(st, sym, e), qty, C["LEVERAGE"],
              px_str(st, sym, sig["vwap"]),
              "vendeurs" if d > 0 else "acheteurs", int(100 * sig["abs"]),
              sig.get("big_x", 0), px_str(st, sym, sig["big"]) if sig.get("big") else "?",
              "acheteurs" if d > 0 else "vendeurs", int(100 * sig["conf"]),
              px_str(st, sym, sl), "sous le big trade" if C["STOP_MODE"].lower().startswith("agg")
              else ("sous la bougie" if d > 0 else "au-dessus de la bougie"),
              px_str(st, sym, tp), C["RR"], qty * (abs(e - sl) + 2 * C["FEE_TAKER"] * e)))
    return None


# =========================================================================
#  SUIVI DES POSITIONS
# =========================================================================
def manage(st, sym, price, pos_ex, C):
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
            st["pos"].pop(sym, None)
            return
        if not ex or ex["dir"] != d or ex["size"] <= 0:
            cancel_all(sym)
            r0 = p.get("risk0", abs(p["entry"] - p["sl"])) or 1e-12
            why = ("TP" if (price - p["tp"]) * d >= -0.25 * r0
                   else "trailing stop, gain ~ %+.1f R" % ((p["sl"] - p["entry"]) * d / r0)
                   if p.get("be") and (p["sl"] - p["entry"]) * d > 0.1 * r0
                   else "stop a l'entree, ~0 $" if p.get("be") else "STOP")
            notify(st, "%s position fermee par Kraken (%s probable)" % (nm(sym), why))
            event(st, "%s position fermee par Kraken (%s probable)" % (nm(sym), why))
            st["pos"].pop(sym, None)
            return
        p["qty"] = ex["size"]
        if not p.get("stop_id"):
            place_exits(st, sym, p)
            if not p.get("stop_id") and p.get("stopfail", 0) >= 2:
                close_mkt(st, sym, p, price, "SORTIE : stop impossible a poser chez Kraken")
                return
        if not p.get("tp_id") and p.get("tpfail", 0) < 20:
            place_exits(st, sym, p, stop=False)    # TP refuse ou absent : on le repose
    r0 = p.get("risk0", abs(p["entry"] - p["sl"]))
    if not p.get("be") and (p["sl"] - price) * d > 0.15 * r0:
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
                notify(st, "%s +%.1f R -> stop deplace a %s (entree + frais : zero perte)"
                       % (nm(sym), C["BE_AT_R"], px_str(st, sym, be_px)))
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
                notify(st, "%s trailing stop -> %s (gain protege ~ %+.1f R)"
                       % (nm(sym), px_str(st, sym, new_sl), (new_sl - p["entry"]) * d / r0))
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
    st["inst"] = instruments()
    C["SYMS"] = discover(st, C)
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
                        st.setdefault("blocked", []).append(sym)
                        C["SYMS"].remove(sym)
                    else:
                        log.warning("levier %s : %s", sym, e)


def fast_step(st, C):
    """Toutes les FAST_SEC s : lecture du flux, cloture des bougies, checklist."""
    now = time.time()
    stats = st.setdefault("stats", {})
    for sym, fl in list(FLOWS.items()):
        if not FEED:
            continue
        new = FEED.since(sym, fl.seq)
        if not FEED.ok(sym) and fl.cur:
            fl.cur["partial"] = True               # flux coupe : bougie incomplete, pas lue
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
            else:
                stats["trades"] = stats.get("trades", 0) + 1
    try:
        learn_update(st, C)
    except Exception as e:
        log.warning("IA : %s", e)


def slow_step(st, C):
    """Toutes les SLOW_SEC s : positions Kraken, stops, TP, protection des gains."""
    px = tickers()
    for sym, fl in FLOWS.items():                  # prix du flux s'il est frais (plus reactif)
        if fl.last_px and time.time() - fl.last_ts < 15:
            px[sym] = fl.last_px
    pos_ex = open_positions() if LIVE else {}
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


def heartbeat(st, C):
    now = time.time()
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
        notify(st, "Resume %gh VWAP : %d bougies lues, %d setups appris, %d entrees confirmees, %d trades\n%s\n%s"
               % (C["SUMMARY_HOURS"], stats.get("bougies", 0), stats.get("setups appris", 0),
                  stats.get("entrees confirmees", 0), stats.get("trades", 0),
                  "\n".join(lignes)[:900] or "aucun refus", learn_summary(st, C)))
        st["stats"], st["stats_ts"] = {}, now


def main():
    C = CFG
    if LIVE and not (os.environ.get("KRAKEN_KEY") and os.environ.get("KRAKEN_SECRET")):
        sys.exit("MODE live sans cles Kraken Futures")
    st = load_state(C)
    global FEED
    if websocket is None:
        sys.exit("module websocket-client absent : cette strategie a besoin du flux des trades")
    FEED = TradeFeed()
    threading.Thread(target=FEED.run, daemon=True).start()
    log.info("Demarrage VWAP ORDER FLOW [%s] x%s %s %s%% | stop %s | RR %s",
             "REEL" if LIVE else "PAPER", C["LEVERAGE"], C["SIZING"], C["RISK_PCT"], C["STOP_MODE"], C["RR"])
    if LIVE:
        check_keys(st)
    if st.get("mode") != C["MODE"]:
        if st.get("mode") != "live":
            st["pos"] = {}
        elif st["pos"]:
            notify(st, "ATTENTION : passage en mode fictif avec des positions REELLES ouvertes (%s) : "
                       "leurs stops/TP restent poses chez Kraken." % ", ".join(nm(x) for x in st["pos"]))
            st["pos"] = {}
        st["mode"] = C["MODE"]
    refresh_symbols(st, C)
    log.info(learn_summary(st, C))
    notify(st, "Bot VWAP ORDER FLOW [%s] x%s %s %s%% | %d perpetuels : %s"
           % ("REEL" if LIVE else "PAPER", C["LEVERAGE"], C["SIZING"], C["RISK_PCT"],
              len(C["SYMS"]), ", ".join(nm(s) for s in C["SYMS"])))

    def _arret(signum, frame):
        for sg2 in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sg2, signal.SIG_IGN)
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
    t_slow = t_beat = t_save = 0.0
    while True:
        t0 = time.time()
        try:
            if t0 - st.get("disco", 0) > 3600:
                refresh_symbols(st, C)
            fast_step(st, C)
            if t0 - t_slow >= C["SLOW_SEC"]:
                t_slow = t0
                slow_step(st, C)
            if t0 - t_beat >= 600:
                t_beat = t0
                heartbeat(st, C)
            if t0 - t_save >= 60:
                t_save = t0
                save_state(st)
        except Exception as e:
            log.warning("boucle : %s", e)
        if time.time() + 180 >= end:
            save_state(st)
            log.info("Fin de ce job (le workflow se relance automatiquement).")
            return
        time.sleep(max(0.5, C["FAST_SEC"] - (time.time() - t0)))


if __name__ == "__main__":
    main()
