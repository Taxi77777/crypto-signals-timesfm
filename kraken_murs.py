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
import statistics
import sys
import time
import urllib.parse

import requests

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
    "MIN_QUOTE_VOL": envf("MIN_QUOTE_VOL", 5000000.0),
    "TOP_N": envf("TOP_N", 30),                  # carnet lu chaque minute : rester raisonnable
    "TF": envf("KRAKEN_TF", "5m,15m,30m,1h"),
    # murs (carnet d'ordres reel)
    "BUCKET_PCT": envf("BUCKET_PCT", 0.05),      # largeur d'un niveau de prix, en % du prix
    "RANGE_PCT": envf("RANGE_PCT", 1.5),         # on regarde le carnet a +/- X % du prix
    "WALL_FACTOR": envf("WALL_FACTOR", 8.0),     # mur = niveau >= X fois la mediane
    "MAX_WALLS": envf("MAX_WALLS", 3),           # seulement les X plus gros murs de chaque cote
    "GONE_SNAPS": envf("GONE_SNAPS", 2),         # absent X lectures de suite = vraiment parti
    "NOTIFY_RETIRE": envf("NOTIFY_RETIRE", 0),   # 1 = Telegram a chaque mur retire (bavard)
    "WALL_MIN_AGE": envf("WALL_MIN_AGE", 180),   # secondes de presence minimum
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
    "RR": envf("RR", 1.5),                       # TP = RR x risque
    "EXIT_IF_WALL_GONE": envf("EXIT_IF_WALL_GONE", 1),
    "ADOPT_ORPHANS": envf("ADOPT_ORPHANS", 1),   # reprendre (stop d'urgence) une position inconnue du bot
    "MIN_RR": envf("MIN_RR", 1.0),               # TP avant le mur oppose : refuse si gain/risque < X
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


def taker_delta(sym):
    """Achats agressifs - ventes agressives sur les 100 derniers trades (info)."""
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
    for t in pub("/derivatives/api/v3/tickers").get("tickers") or []:
        s = (t.get("symbol") or "").upper()
        p = t.get("last") or t.get("markPrice")
        if s and p:
            out[s] = float(p)
    return out


PERP_EXCLUDE = ("TRADFI", "INDEX", "STOCK", "EQUIT", "COMMOD", "METAL", "GOLD", "FOREX")
NON_CRYPTO = {"XAUT", "PAXG", "XAU", "XAG"}
XSTOCKS = {"TSLAX", "CRCLX", "NVDAX", "AAPLX", "SPYX", "QQQX", "METAX", "GOOGLX", "AMZNX",
           "MSFTX", "COINX", "HOODX", "MSTRX", "AMDX", "NFLXX", "PLTRX", "GMEX", "IBITX",
           "GLDX", "SLVX", "TQQQX", "ORCLX", "AVGOX", "INTCX", "BABAX", "UBERX", "DISX"}


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
        if s not in st["inst"] or t.get("suspended"):
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
def rp(st, sym, x):
    t = (st["inst"].get(sym) or {}).get("tick") or 0.0001
    d = max(0, -int(math.floor(math.log10(t)))) if t < 1 else 0
    return round(round(x / t) * t, d)


def px_str(st, sym, x):
    """Prix arrondi au tick, ecrit en decimal (jamais 1.2e-05 pour PEPE / SHIB)."""
    t = (st["inst"].get(sym) or {}).get("tick") or 0.0001
    d = max(0, -int(math.floor(math.log10(t)))) if t < 1 else 0
    return "%.*f" % (d, rp(st, sym, x))


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
    log.info(msg)
    st["log"] = (st.get("log") or [])[-59:] + [time.strftime("%d/%m %H:%M") + " " + msg]
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return
    try:
        SESSION.post("https://api.telegram.org/bot%s/sendMessage" % tok,
                     json={"chat_id": chat, "text": msg}, timeout=15)
    except Exception as e:
        log.warning("telegram : %s", e)


def side_of(dr):
    return "buy" if dr > 0 else "sell"


def order(p):
    if not LIVE:
        return "paper-%d" % time.time()
    s = priv("POST", "/api/v3/sendorder", p).get("sendStatus") or {}
    if s.get("status") and s["status"] not in (
            "placed", "attempted", "untouched", "partiallyFilled", "filled"):
        raise RuntimeError("ordre refuse : " + s["status"])
    return s.get("order_id") or s.get("orderId")


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
            v.update(first=now, max=size)
        v.update(price=price, size=size, last=now, max=max(v["max"], size), miss=0, top=now, w=tol)
        v.pop("gone", None)
        seen.add(key)
    events = []
    for k in list(book):
        v = book[k]
        if k in seen:
            continue
        if not (lo <= v["price"] <= hi):           # sorti de la fenetre (le prix s'est eloigne) :
            v["last"] = now                        # ni retire ni mange, on n'en sait rien
            continue
        # sorti du top MAX_WALLS mais toujours dans le carnet ? -> toujours la
        if buckets and w > 0:
            b = int(math.floor(v["price"] / w))
            q = sum(buckets.get((v["side"], b + i), 0.0) for i in (-1, 0, 1))
            if q >= C["WALL_GONE"] * v["max"]:
                v.update(size=q, last=now, miss=0)
                continue
        # les teneurs de marche retirent / reposent leurs ordres en permanence :
        # un mur n'est "parti" qu'apres GONE_SNAPS lectures absentes de suite
        v["miss"] = v.get("miss", 0) + 1
        if v["miss"] < C["GONE_SNAPS"]:
            continue
        v["size"] = 0.0
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
    """Murs confirmes (assez vieux) et toujours presents."""
    return [dict(v, key=k) for k, v in (st["walls"].get(sym) or {}).items()
            if v.get("size", 0) > 0 and not v.get("miss") and now - v["first"] >= C["WALL_MIN_AGE"]
            and now - v.get("top", 0) <= 3 * C["INTERVAL"]       # encore parmi les plus gros murs
            and v["size"] >= C["WALL_KEEP"] * v["max"]]


# =========================================================================
#  SIGNAL : rebond sur un mur qui tient (scenario 1) / mur mange (scenario 2)
# =========================================================================
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
    tol, pierce = C["TOL_ATR"] * a, C["PIERCE_ATR"] * a
    best = None
    for w in walls:
        if w["first"] + C["WALL_MIN_AGE"] > bars[k - 1][0]:
            continue                               # mur apparu apres le contact : ignore
        W, d = w["price"], 0
        if bub0 or bub1:
            lowmin, highmax = min(l[k], l[k - 1]), max(h[k], h[k - 1])
            gw = w.get("w") or pierce               # le prix ne doit pas TRAVERSER le mur (sinon il est mange)
            if (w["side"] > 0 and c[k - 2] > W and W - gw <= lowmin <= W + tol and c[k] > W
                    and c[k] > o[k] and (c[k] - l[k]) >= 0.5 * rng and c[k] > c[k - 1]):
                d = 1                              # mur d'achat qui TIENT -> ACHAT
            if (w["side"] < 0 and c[k - 2] < W and W - tol <= highmax <= W + gw and c[k] < W
                    and c[k] < o[k] and (h[k] - c[k]) >= 0.5 * rng and c[k] < c[k - 1]):
                d = -1                             # mur de vente qui TIENT -> VENTE
        if d and (best is None or w["max"] > best["wall"]["max"]):
            best = {"dir": d, "type": "REBOND", "wall": w, "atr": a, "close": c[k],
                    "vol": max(v[k], v[k - 1]) / avgv}
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
    """Entree / SL derriere le mur / TP"""
    d, W, a, e = sig["dir"], sig["wall"]["price"], sig["atr"], sig["close"]
    sl = W - d * C["SL_BEHIND_ATR"] * a
    if (e - sl) * d < C["MIN_RISK_ATR"] * a:
        sl = e - d * C["MIN_RISK_ATR"] * a
    risk = (e - sl) * d
    return e, sl, e + d * C["RR"] * risk


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
    for k in ("stop_id", "tp_id"):
        cancel(p.get(k))
        p[k] = None
    if p["qty"] <= 0:
        return
    try:
        p["stop_id"] = order({"orderType": "stp", "symbol": sym, "side": side_of(-p["dir"]),
                              "size": qty_str(st, sym, p["qty"]), "stopPrice": px_str(st, sym, p["sl"]),
                              "triggerSignal": "mark", "reduceOnly": "true"})
    except Exception as e:
        p["stopfail"] = p.get("stopfail", 0) + 1
        if not p.get("warned"):                    # un seul message, pas un par minute
            p["warned"] = True
            notify(st, "%s stop Kraken NON pose (%s) -> nouvel essai, fermeture si 2e echec" % (nm(sym), e))
    try:
        p["tp_id"] = order({"orderType": "take_profit", "symbol": sym, "side": side_of(-p["dir"]),
                            "size": qty_str(st, sym, p["qty"]), "stopPrice": px_str(st, sym, p["tp"]),
                            "triggerSignal": "last", "reduceOnly": "true"})
    except Exception as e:
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
    notify(st, "%s %s @ %s  P&L ~ %+.2f $" % (nm(sym), why, rp(st, sym, price), pnl))
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
    e, sl, tp = plan(dict(sig, close=price), C)
    if not sl_ok(e, sl, C):
        return "SL hors limites (frais / liquidation x%s)" % C["LEVERAGE"]
    d = sig["dir"]
    # le prochain mur OPPOSE est la resistance suivante (video) : TP juste avant
    opp = [v["price"] for v in live_walls(st, sym, time.time(), C)
           if v["side"] == -d and (v["price"] - e) * d > 0]
    if opp:
        cap = min(opp, key=lambda x: abs(x - e)) - d * (st.get("grid", {}).get(sym) or 0)
        if (cap - e) * d < (tp - e) * d:
            tp = cap
    if (tp - e) * d < C["MIN_RR"] * abs(e - sl):
        return "mur oppose trop proche (gain/risque < %g)" % C["MIN_RR"]
    eq = equity(st)
    full = eq * C["CAPITAL_PCT"] / 100 * C["LEVERAGE"] / e
    qty = rq(st, sym, full if C["SIZING"] == "FULL" else min(eq * C["RISK_PCT"] / 100 / abs(e - sl), full))
    risk_plan = qty * abs(e - sl)                  # perte maximale prevue, en USD
    tp_dist = abs(tp - e)
    if qty <= 0:
        return "taille a 0 (capital %.2f USD)" % eq
    if LIVE:
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
        if not sent:
            st["block_until"] = time.time() + 180   # l'ordre a peut-etre quand meme ete execute
            return "ordre refuse par Kraken : %s" % err
        ex = None
        for _ in range(5):                       # l'ordre peut mettre un peu de temps
            time.sleep(1.5)
            try:
                ex = open_positions().get(sym)
            except Exception as e:
                log.warning("positions : %s", e)
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
    # remplissage trop loin du plan : entree au-dela du SL, ou perte possible > 1,5 x prevu
    if (e - sl) * d <= 0 or qty * abs(e - sl) > 1.5 * risk_plan:
        a = sig["atr"]
        p = {"dir": d, "entry": e, "qty": qty, "sl": e - d * a, "tp": e + d * a, "tf": tf,
             "wall": None, "wall_side": 0, "wall_key": None, "adopted": True,
             "bar_ts": int(time.time()), "stop_id": None, "tp_id": None}
        st["pos"][sym] = p                        # si la sortie echoue : stop d'urgence pose au cycle suivant
        close_mkt(st, sym, p, price, "SORTIE immediate (prix obtenu trop loin du plan)")
        return None
    p = {"dir": d, "entry": e, "sl": sl, "tp": tp, "qty": qty, "tf": tf,
         "wall": sig["wall"]["price"], "wall_side": sig["wall"]["side"],
         "wall_key": sig["wall"].get("key"),
         "bar_ts": int(time.time() // TF_SEC[tf] * TF_SEC[tf]), "stop_id": None, "tp_id": None}
    st["pos"][sym] = p
    place_exits(st, sym, p)
    notify(st, "%s %s %s (%s) x%s @ %s  qte %s\nMur %s %s (taille %.4g, bulle x%.1f, delta %+d%%)\n"
               "TP %s   SL %s (derriere le mur)"
           % (nm(sym), tf, "ACHAT" if d > 0 else "VENTE",
              "le mur tient" if sig["type"] == "REBOND" else "mur mange",
              C["LEVERAGE"], rp(st, sym, e), qty,
              "d'achat" if sig["wall"]["side"] > 0 else "de vente", rp(st, sym, sig["wall"]["price"]),
              sig["wall"]["max"], sig["vol"], int(100 * taker_delta(sym)),
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
        p = {"dir": d, "entry": e, "sl": sl, "tp": e + d * C["RR"] * abs(e - sl), "qty": ex["size"],
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
            why = "TP" if (price - p["entry"]) * d > 0 else "STOP"
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
    if (p["sl"] - price) * d > 0.3 * abs(p["entry"] - p["sl"]):
        close_mkt(st, sym, p, price, "STOP (securite bot)")
        return
    # 2) scenario 3 en position : le mur a ete RETIRE alors que le prix ne l'a pas
    #    traverse -> on sort. S'il a ete mange (prix au-dela), c'est le SL qui joue.
    if C["EXIT_IF_WALL_GONE"] and p.get("wall_key") and not p.get("adopted"):
        v = (st["walls"].get(sym) or {}).get(p["wall_key"])
        still_our_side = (price - p["wall"]) * d > 0
        if (not v or v.get("size", 0) <= 0) and still_our_side:
            close_mkt(st, sym, p, price, "SORTIE : le mur a ete retire")
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
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                st.update(json.load(f))
        except Exception as e:
            log.warning("etat illisible (%s) -> repart a zero", e)
    return st


def save_state(st):
    try:
        with open(STATE_FILE, "w") as f:
            # depot PUBLIC : on n'ecrit pas le journal des messages (capital, P&L...)
            json.dump({k: v for k, v in st.items() if k != "log"}, f, indent=1)
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
        st["disco"] = time.time()
        st["grid"] = {}                           # grille recalculee 1x par heure
        log.info("%d actifs suivis : %s", len(C["SYMS"]), ", ".join(nm(s) for s in C["SYMS"][:15]))
    if st.get("mode") != C["MODE"]:
        if st.get("mode") != "live":              # positions fictives : on repart a zero
            st["pos"] = {}
        st["mode"] = C["MODE"]
        notify(st, "Bot MURS & BULLES (carnet d'ordres reel Kraken) [%s] x%s %s %s%% | %s | %d perpetuels"
               % ("REEL" if LIVE else "PAPER", C["LEVERAGE"], C["SIZING"], C["RISK_PCT"],
                  "/".join(C["TF_LIST"]), len(C["SYMS"])))
    if LIVE:
        for sym in C["SYMS"]:
            if st["lev"].get(sym) != C["LEVERAGE"]:
                try:
                    priv("PUT", "/api/v3/leveragepreferences",
                         {"symbol": sym, "maxLeverage": C["LEVERAGE"]})
                    st["lev"][sym] = C["LEVERAGE"]
                except Exception as e:
                    log.warning("levier %s : %s", sym, e)

    px = tickers()
    pos_ex = open_positions() if LIVE else {}
    now = time.time()
    events = {}
    # 1) photo du carnet de chaque actif -> murs (age, taille), murs retires / manges
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
                    (notify if C["NOTIFY_RETIRE"] else (lambda _s, m: log.info(m)))(st, "%s MUR %s RETIRE juste avant l'impact (%s, a %.2f%% du prix) -> pas de trade"
                           % (nm(sym), "D'ACHAT" if v["side"] > 0 else "DE VENTE",
                              rp(st, sym, v["price"]), dist))
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
    for tf in C["TF_LIST"]:
        tfs = TF_SEC[tf]
        last_closed = int(now // tfs * tfs) - tfs
        if now - (last_closed + tfs) < 5:
            continue
        for sym in C["SYMS"]:
            akey = "%s|%s" % (sym, tf)
            if st["last"].get(akey) == last_closed:
                continue
            walls = live_walls(st, sym, now, C)
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
                why = enter(st, sym, tf, sig, px, C)
                if why:                            # journal seulement (pas de Telegram a chaque refus)
                    log.info("%s %s signal %s sur mur %s ignore : %s"
                           % (nm(sym), tf, "ACHAT" if sig["dir"] > 0 else "VENTE",
                              rp(st, sym, sig["wall"]["price"]), why))
            except Exception as e:
                log.warning("%s %s : %s", sym, tf, e)
    st["seen"] = st["seen"][-600:]
    nw = sum(len(live_walls(st, s, now, C)) for s in C["SYMS"])
    log.info("cycle ok : %d murs confirmes sur %d actifs, %d position(s)", nw, len(C["SYMS"]), len(st["pos"]))


def main():
    C = CFG
    if LIVE and not (os.environ.get("KRAKEN_KEY") and os.environ.get("KRAKEN_SECRET")):
        sys.exit("MODE live sans cles Kraken Futures")
    st = load_state(C)
    log.info("Demarrage MURS & BULLES [%s] x%s %s | %s | boucle %s min, scan toutes les %s s",
             "REEL" if LIVE else "PAPER", C["LEVERAGE"], C["SIZING"],
             "/".join(C["TF_LIST"]), C["LOOP_MINUTES"], C["INTERVAL"])
    if LIVE:
        check_keys(st)
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
