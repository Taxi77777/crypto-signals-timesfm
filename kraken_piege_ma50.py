#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================================
  BOT "PIEGE PDH/PDL -> RETOUR MA50"  -  perpetuels Kraken Pro (Futures)
  Tourne en continu sur GitHub Actions (gratuit, depot public).
==========================================================================
  1) PIEGE PDH/PDL (identique a l'indicateur MT4 PIEGE_PDH_PDL) :
     meche au-dela du PDH/PDL + cloture a l'interieur + bougie de deplacement.
  2) On ne prend PAS le piege : on attend le retour du prix sur la MA50.
       PIEGE VENTE (PDH) -> ACHAT sur la MA50
       PIEGE ACHAT (PDL) -> VENTE sur la MA50
  3) Filtre ADX au moment du retour :
       ADX(14) >= ADX_MIN  et  +DI > -DI pour acheter (-DI > +DI pour vendre)
  4) Ordre LIMITE sur la zone MA50 (replace a chaque bougie).
     SL de l'autre cote de la MA50. TP1 = 1.5 R (50%) + SL au point d'entree,
     le reste en TRAILING STOP. Stop et TP1 poses CHEZ KRAKEN.
  5) Levier x10, tout le capital, UN SEUL trade a la fois.

  Variables d'environnement (GitHub Secrets) :
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
import sys
import time
import urllib.parse

import requests

BASE = "https://futures.kraken.com"
TF_SEC = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400}
STATE_FILE = "kraken_piege_state.json"


def envf(name, default):
    v = os.environ.get(name, "").strip()
    if v == "":
        return default
    return type(default)(v) if not isinstance(default, str) else v


CFG = {
    "MODE": envf("KRAKEN_MODE", "paper"),
    # vide = TOUS les perpetuels Kraken ayant assez de volume
    "SYMBOLS": envf("KRAKEN_SYMBOLS", ""),
    "MIN_QUOTE_VOL": envf("MIN_QUOTE_VOL", 1000000.0),   # volume 24 h minimum, en USD
    "TOP_N": envf("TOP_N", 100),                         # nb max d'actifs suivis
    "TF": envf("KRAKEN_TF", "15m,30m"),      # plusieurs unites de temps, separees par des virgules
    # piege
    "CONF_BARS": envf("CONF_BARS", 3),
    "DISP_ATR": envf("DISP_ATR", 0.8),
    # MA50
    "MA_PERIOD": envf("MA_PERIOD", 50),
    "MA_TYPE": envf("MA_TYPE", "SMA"),
    "MA_SLOPE": envf("MA_SLOPE", 5),
    "MA_FILTER": envf("MA_FILTER", 1),
    "ZONE_ATR": envf("ZONE_ATR", 0.20),
    "WAIT_BARS": envf("WAIT_BARS", 16),
    # ADX
    "ADX_FILTER": envf("ADX_FILTER", 1),
    "ADX_PERIOD": envf("ADX_PERIOD", 14),
    "ADX_MIN": envf("ADX_MIN", 20.0),
    "ADX_DI": envf("ADX_DI", 1),
    # sortie
    "SL_ATR": envf("SL_ATR", 1.0),
    "TP1_R": envf("TP1_R", 1.5),
    "MOVE_BE": envf("MOVE_BE", 1),
    "TRAIL_ATR": envf("TRAIL_ATR", 1.5),
    "TRAIL_AFTER_TP1": envf("TRAIL_AFTER_TP1", 1),
    "MAX_HOLD": envf("MAX_HOLD", 120),
    "ATR_PERIOD": envf("ATR_PERIOD", 14),
    # risque
    "LEVERAGE": envf("LEVERAGE", 10),
    "SIZING": envf("SIZING", "FULL"),      # FULL = tout le capital ; RISK = RISK_PCT risque
    "CAPITAL_PCT": envf("CAPITAL_PCT", 95.0),
    "RISK_PCT": envf("RISK_PCT", 5.0),
    "LIQ_SAFETY": envf("LIQ_SAFETY", 0.75),
    "MAX_OPEN": envf("MAX_OPEN", 1),
    "FEE_MAKER": envf("FEE_MAKER", 0.0002),
    "FEE_TAKER": envf("FEE_TAKER", 0.0005),
    "MAX_FEE_R": envf("MAX_FEE_R", 0.35),
    "PAPER_BALANCE": envf("PAPER_BALANCE", 1000.0),
    # boucle
    "LOOP_MINUTES": envf("LOOP_MINUTES", 320),
    "INTERVAL": envf("INTERVAL", 60),
}
CFG["SYMS"] = [s.strip().upper() for s in CFG["SYMBOLS"].split(",") if s.strip()]
CFG["TF_LIST"] = [t.strip() for t in CFG["TF"].split(",") if t.strip() in TF_SEC]
if not CFG["TF_LIST"]:
    CFG["TF_LIST"] = ["15m"]
LIVE = CFG["MODE"].lower() == "live"

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                    datefmt="%H:%M:%S", stream=sys.stdout)
log = logging.getLogger("piege")
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "piege-ma50"


# =========================================================================
#  INDICATEURS
# =========================================================================
def sma(v, p):
    out, s = [None] * len(v), 0.0
    for i, x in enumerate(v):
        s += x
        if i >= p:
            s -= v[i - p]
        if i >= p - 1:
            out[i] = s / p
    return out


def ema(v, p):
    out, e, k = [None] * len(v), None, 2.0 / (p + 1)
    for i, x in enumerate(v):
        if i == p - 1:
            e = sum(v[:p]) / p
        elif i >= p:
            e = x * k + e * (1 - k)
        out[i] = e
    return out


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


def adx(h, l, c, p):
    """ADX de Wilder -> (adx, +DI, -DI)"""
    n = len(c)
    A = [None] * n
    P = [None] * n
    M = [None] * n
    trS = pS = mS = dxSum = 0.0
    a = None
    for i in range(1, n):
        up, dn = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm = up if (up > dn and up > 0) else 0.0
        mdm = dn if (dn > up and dn > 0) else 0.0
        tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        if i <= p:
            trS += tr
            pS += pdm
            mS += mdm
        else:
            trS = trS - trS / p + tr
            pS = pS - pS / p + pdm
            mS = mS - mS / p + mdm
        if i < p:
            continue
        pdi = 100 * pS / trS if trS else 0.0
        mdi = 100 * mS / trS if trS else 0.0
        dx = 100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) else 0.0
        P[i], M[i] = pdi, mdi
        if i < 2 * p - 1:
            dxSum += dx
            continue
        a = (dxSum + dx) / p if i == 2 * p - 1 else (a * (p - 1) + dx) / p
        A[i] = a
    return A, P, M


def series(bars, C):
    o = [b[1] for b in bars]
    h = [b[2] for b in bars]
    l = [b[3] for b in bars]
    c = [b[4] for b in bars]
    A, P, M = adx(h, l, c, C["ADX_PERIOD"])
    return {
        "o": o, "h": h, "l": l, "c": c,
        "a": atr(h, l, c, C["ATR_PERIOD"]),
        "m": (ema if C["MA_TYPE"].upper() == "EMA" else sma)(c, C["MA_PERIOD"]),
        "adx": A, "pdi": P, "mdi": M,
    }


# =========================================================================
#  STRATEGIE
# =========================================================================
def find_setups(bars, C, S):
    """Un setup par piege valide : {i, ts, trap, dir, level}"""
    o, h, l, c, a, m = S["o"], S["h"], S["l"], S["c"], S["a"], S["m"]
    n = len(bars)
    warm = max(C["ATR_PERIOD"], C["MA_PERIOD"]) + C["MA_SLOPE"] + 1
    out = []
    cur = PH = PL = None
    dh = dl = 0.0
    used = {1: False, -1: False}
    for i in range(n):
        d = bars[i][0] // 86400
        if d != cur:
            if cur is not None:
                PH, PL = dh, dl
            cur, dh, dl = d, h[i], l[i]
            used = {1: False, -1: False}
        else:
            dh, dl = max(dh, h[i]), min(dl, l[i])
        if PH is None or i < warm or not a[i] or PH <= PL:
            continue
        for tdir in (-1, 1):                     # -1 = piege au PDH, +1 = piege au PDL
            if used[tdir]:
                continue
            sweep = (h[i] > PH and c[i] < PH) if tdir < 0 else (l[i] < PL and c[i] > PL)
            if not sweep:
                continue
            j = -1
            for k in range(i, min(i + C["CONF_BARS"], n - 1) + 1):
                brk = True if k == i else ((c[k] < l[i]) if tdir < 0 else (c[k] > h[i]))
                if (c[k] - o[k]) * tdir >= C["DISP_ATR"] * a[i] and brk:
                    j = k
                    break
            if j < 0:
                continue
            used[tdir] = True
            dr = -tdir                           # on trade le RETOUR, dans l'autre sens
            if (c[j] - m[j]) * dr <= 0:
                continue
            if C["MA_FILTER"] and (m[j] - m[j - C["MA_SLOPE"]]) * dr <= 0:
                continue
            out.append({"i": j, "ts": bars[j][0], "trap": "VENTE" if tdir < 0 else "ACHAT",
                        "dir": dr, "level": PH if tdir < 0 else PL})
    return out


def levels(m_last, a_last, dr, C):
    return m_last + dr * C["ZONE_ATR"] * a_last, m_last - dr * C["SL_ATR"] * a_last


def fee_r(entry, sl, C):
    r = abs(entry - sl)
    return 99.0 if r <= 0 else (C["FEE_MAKER"] + C["FEE_TAKER"]) * entry / r


def sl_ok(entry, sl, C):
    return (fee_r(entry, sl, C) <= C["MAX_FEE_R"]
            and abs(entry - sl) / entry <= C["LIQ_SAFETY"] / C["LEVERAGE"])


def adx_ok(S, i, dr, C):
    if not C["ADX_FILTER"]:
        return True, ""
    A, P, M = S["adx"][i], S["pdi"][i], S["mdi"][i]
    if A is None:
        return False, "ADX indisponible"
    txt = "ADX %.1f +DI %.1f -DI %.1f" % (A, P, M)
    if A < C["ADX_MIN"]:
        return False, txt + " < %g" % C["ADX_MIN"]
    if C["ADX_DI"] and (P - M) * dr <= 0:
        return False, txt + " DI contre"
    return True, txt


# =========================================================================
#  API KRAKEN FUTURES
# =========================================================================
def pub(path, params=None):
    r = SESSION.get(BASE + path, params=params, timeout=25)
    r.raise_for_status()
    return r.json()


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
    tfs = TF_SEC[tf]
    now = int(time.time())
    j = pub("/api/charts/v1/trade/%s/%s" % (sym, tf), {"from": now - 300 * tfs, "to": now})
    out = []
    for r in j.get("candles") or []:
        ts = int(r["time"]) // 1000
        if ts + tfs <= now:                      # bougies CLOTUREES seulement
            out.append([ts, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])])
    return out


def tickers():
    out = {}
    for t in pub("/derivatives/api/v3/tickers").get("tickers") or []:
        s = (t.get("symbol") or "").upper()
        p = t.get("last") or t.get("markPrice")
        if s and p:
            out[s] = float(p)
    return out


PERP_EXCLUDE = ("TRADFI", "INDEX")


def instruments(C):
    """Tous les perpetuels PF_...USD negociables, avec leurs precisions."""
    out = {}
    for i in pub("/derivatives/api/v3/instruments").get("instruments") or []:
        s = (i.get("symbol") or "").upper()
        if not s.startswith("PF_") or not s.endswith("USD"):
            continue
        if not i.get("tradeable", True) or i.get("isExpired") or i.get("tradfi"):
            continue
        if str(i.get("category") or "").upper() in PERP_EXCLUDE:
            continue
        # contractValueTradePrecision = nb de decimales autorisees sur la TAILLE
        # (c'est bien ce champ, pas contractValuePrecision, qui vaut souvent 0)
        cvtp = i.get("contractValueTradePrecision")
        out[s] = {"tick": float(i.get("tickSize") or 0.0001),
                  "sp": int(cvtp) if cvtp is not None else 4}
    return out


def discover(st, C):
    """Liste des actifs suivis : ceux choisis a la main, sinon tous les
       perpetuels Kraken classes par volume 24 h."""
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
    return float(f.get("marginEquity") or f.get("portfolioValue") or f.get("availableMargin") or 0)


# =========================================================================
#  OUTILS
# =========================================================================
def rp(st, sym, x):
    t = (st["inst"].get(sym) or {}).get("tick") or 0.0001
    d = max(0, -int(math.floor(math.log10(t)))) if t < 1 else 0
    return round(round(x / t) * t, d)


def rq(st, sym, x):
    d = st["inst"].get(sym) or {}
    p = d["sp"] if "sp" in d else 4
    f = 10.0 ** p
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
    if LIVE and oid:
        try:
            priv("POST", "/api/v3/cancelorder", {"order_id": oid})
        except Exception as e:
            log.warning("annulation : %s", e)


def place_stop(st, sym, p):
    cancel(p.get("stop_id"))
    p["stop_id"] = None
    if p["qty"] <= 0:
        return
    try:
        p["stop_id"] = order({"orderType": "stp", "symbol": sym, "side": side_of(-p["dir"]),
                              "size": p["qty"], "stopPrice": rp(st, sym, p["sl"]),
                              "triggerSignal": "mark", "reduceOnly": "true"})
    except Exception as e:
        notify(st, "%s stop Kraken NON pose (%s) -> gere par le bot" % (nm(sym), e))


def place_tp1(st, sym, p):
    half = rq(st, sym, p["qty0"] / 2)
    if half <= 0:
        return
    try:
        p["tp_id"] = order({"orderType": "take_profit", "symbol": sym, "side": side_of(-p["dir"]),
                            "size": half, "stopPrice": rp(st, sym, p["tp1"]),
                            "triggerSignal": "last", "reduceOnly": "true"})
    except Exception as e:
        p["tp_id"] = None
        log.warning("TP1 : %s", e)


def close_mkt(st, sym, p, qty, price, why):
    qty = rq(st, sym, qty)
    if qty > 0:
        try:
            order({"orderType": "mkt", "symbol": sym, "side": side_of(-p["dir"]),
                   "size": qty, "reduceOnly": "true"})
        except Exception as e:
            notify(st, "%s ERREUR fermeture : %s" % (nm(sym), e))
            return False
    pnl = (price - p["entry"]) * p["dir"] * qty - CFG["FEE_TAKER"] * price * qty
    if not LIVE:
        st["paper"] += pnl
    p["qty"] = rq(st, sym, p["qty"] - qty)
    notify(st, "%s %s @ %s  qte %s  P&L ~ %+.2f $" % (nm(sym), why, rp(st, sym, price), qty, pnl))
    return True


# =========================================================================
#  CYCLE
# =========================================================================
def on_bar(st, sym, tf, bars, C):
    tfs = TF_SEC[tf]
    akey = "%s|%s" % (sym, tf)
    S = series(bars, C)
    k = len(bars) - 1
    mL, aL = S["m"][k], S["a"][k]

    # 1) nouveau piege sur la derniere bougie cloturee
    for s in find_setups(bars, C, S):
        if s["i"] != k:
            continue
        key = "%s|%s|%s|%s" % (sym, tf, s["ts"], s["dir"])
        if key in st["seen"]:
            continue
        st["seen"].append(key)
        side = "ACHAT" if s["dir"] > 0 else "VENTE"
        msg = ("%s %s PIEGE %s au %s %s -> attente retour MA50 pour %s"
               % (nm(sym), tf, s["trap"], "PDH" if s["trap"] == "VENTE" else "PDL",
                  rp(st, sym, s["level"]), side))
        if sym in st["pos"] or len(st["pos"]) + len(st["arm"]) >= C["MAX_OPEN"]:
            msg += "  (ignore : un trade deja en cours)"
        else:
            st["arm"][akey] = {"sym": sym, "tf": tf, "dir": s["dir"],
                               "expire": s["ts"] + (C["WAIT_BARS"] + 1) * tfs,
                               "order_id": None, "target": None, "sl": None, "qty": 0, "adx": ""}
        notify(st, msg)

    # 2) ordre limite qui suit la MA50 (+ filtre ADX)
    ar = st["arm"].get(akey)
    if ar:
        cancel(ar.get("order_id"))
        ar.update(order_id=None, target=None, qty=0)
        if bars[k][0] + tfs >= ar["expire"]:
            del st["arm"][akey]
            notify(st, "%s %s pas de retour MA50 a temps -> setup annule" % (nm(sym), tf))
        else:
            target, sl = levels(mL, aL, ar["dir"], C)
            ok, why = adx_ok(S, k, ar["dir"], C)
            if not ok:
                log.info("%s filtre ADX : %s -> pas d'ordre cette bougie", sym, why)
            elif not sl_ok(target, sl, C):
                log.info("%s SL hors limites (frais / liquidation x%s) -> pas d'ordre",
                         sym, C["LEVERAGE"])
            else:
                eq = equity(st)
                full = eq * C["CAPITAL_PCT"] / 100 * C["LEVERAGE"] / target
                qty = rq(st, sym, full if C["SIZING"] == "FULL"
                         else min(eq * C["RISK_PCT"] / 100 / abs(target - sl), full))
                if qty > 0:
                    try:
                        ar["order_id"] = order({"orderType": "lmt", "symbol": sym,
                                                "side": side_of(ar["dir"]), "size": qty,
                                                "limitPrice": rp(st, sym, target)})
                        ar.update(target=target, sl=sl, qty=qty, adx=why)
                        log.info("%s ordre limite MA50 %s qte %s SL %s | %s",
                                 sym, rp(st, sym, target), qty, rp(st, sym, sl), why)
                    except Exception as e:
                        notify(st, "%s ordre MA50 refuse : %s" % (nm(sym), e))
                else:
                    notify(st, "%s taille calculee a 0 (capital %.2f USD) -> aucun ordre. "
                               "Compte Futures vide ou capital trop faible pour cet actif."
                           % (nm(sym), eq))

    # 3) trailing stop + duree max (sur l'unite de temps qui a ouvert la position)
    p = st["pos"].get(sym)
    if p and p.get("tf", tf) == tf:
        if p["tp1_done"] or not C["TRAIL_AFTER_TP1"]:
            since = [b for b in bars if b[0] >= p["bar_ts"]]
            if since:
                best = max(b[2] for b in since) if p["dir"] > 0 else min(b[3] for b in since)
                t = best - p["dir"] * C["TRAIL_ATR"] * aL
                if (t - p["sl"]) * p["dir"] > 0:
                    p["sl"] = t
                    place_stop(st, sym, p)
                    log.info("%s trailing stop -> %s", sym, rp(st, sym, t))
        if (bars[k][0] - p["bar_ts"]) / tfs > C["MAX_HOLD"]:
            cancel(p.get("stop_id"))
            cancel(p.get("tp_id"))
            close_mkt(st, sym, p, p["qty"], bars[k][4], "SORTIE duree max")
            st["pos"].pop(sym, None)


C_TF_FALLBACK = "15m"


def check_fill(st, akey, px, pos_ex, C):
    ar = st["arm"].get(akey)
    if not isinstance(ar, dict) or "sym" not in ar or "tf" not in ar:
        st["arm"].pop(akey, None)                # entree d'une ancienne version
        return
    if not ar.get("target") or ar["qty"] <= 0:
        return
    sym, tf = ar["sym"], ar["tf"]
    price = px.get(sym)
    if not price:
        return
    dr = ar["dir"]
    if LIVE:
        ex = pos_ex.get(sym)
        if not ex or ex["size"] <= 0 or ex["dir"] != dr:
            return
        qty, fill = ex["size"], ex["price"] or ar["target"]
        cancel(ar.get("order_id"))
    else:
        if not ((price <= ar["target"]) if dr > 0 else (price >= ar["target"])):
            return
        qty, fill = ar["qty"], ar["target"]
        st["paper"] -= C["FEE_MAKER"] * fill * qty
    risk = (fill - ar["sl"]) * dr
    now = time.time()
    p = {"dir": dr, "entry": fill, "sl": ar["sl"], "qty": qty, "qty0": qty,
         "tp1": fill + dr * C["TP1_R"] * risk, "tp1_done": False,
         "bar_ts": int(now // TF_SEC[tf] * TF_SEC[tf]), "tf": tf,
         "stop_id": None, "tp_id": None}
    del st["arm"][akey]
    for k2 in [k3 for k3, v in st["arm"].items() if v["sym"] == sym]:
        cancel(st["arm"][k2].get("order_id"))      # plus d'ordre concurrent sur cet actif
        del st["arm"][k2]
    st["pos"][sym] = p
    place_stop(st, sym, p)
    place_tp1(st, sym, p)
    notify(st, "%s %s %s x%s sur MA50 @ %s  qte %s\nSL %s  TP1 %s (50%%) puis trailing\n%s"
           % (nm(sym), tf, "ACHAT" if dr > 0 else "VENTE", C["LEVERAGE"], rp(st, sym, fill), qty,
              rp(st, sym, p["sl"]), rp(st, sym, p["tp1"]), ar.get("adx", "")))


def manage(st, sym, price, pos_ex, C):
    p = st["pos"].get(sym)
    if not p:
        return
    dr = p["dir"]
    if LIVE:
        ex = pos_ex.get(sym)
        size = ex["size"] if (ex and ex["dir"] == dr) else 0
        if size <= 0:                            # ferme chez Kraken
            cancel(p.get("stop_id"))
            cancel(p.get("tp_id"))
            out = ("BE" if abs(p["sl"] - p["entry"]) < 1e-12 else "TRAILING STOP") \
                if p["tp1_done"] else "STOP"
            notify(st, "%s position fermee par Kraken (%s ~ %s)" % (nm(sym), out, rp(st, sym, p["sl"])))
            st["pos"].pop(sym, None)
            return
        if not p["tp1_done"] and size < p["qty0"] * 0.75:     # TP1 execute par Kraken
            p.update(tp1_done=True, qty=size, tp_id=None)
            if C["MOVE_BE"] and (p["entry"] - p["sl"]) * dr > 0:
                p["sl"] = p["entry"]
            place_stop(st, sym, p)
            notify(st, "%s TP1 touche (50%%) -> SL au point d'entree, reste en trailing" % nm(sym))
            return
        p["qty"] = size
        if not p.get("stop_id") and ((price <= p["sl"]) if dr > 0 else (price >= p["sl"])):
            cancel(p.get("tp_id"))
            if close_mkt(st, sym, p, p["qty"], price, "STOP (bot)"):
                st["pos"].pop(sym, None)
        elif not p["tp1_done"] and not p.get("tp_id") and \
                ((price >= p["tp1"]) if dr > 0 else (price <= p["tp1"])):
            close_mkt(st, sym, p, p["qty0"] / 2, price, "TP1 (50%)")
            p["tp1_done"] = True
            if C["MOVE_BE"] and (p["entry"] - p["sl"]) * dr > 0:
                p["sl"] = p["entry"]
            place_stop(st, sym, p)
        return
    # ---- paper ----
    if (price <= p["sl"]) if dr > 0 else (price >= p["sl"]):
        why = "STOP" if not p["tp1_done"] else (
            "BE" if abs(p["sl"] - p["entry"]) < 1e-12 else "TRAILING STOP")
        close_mkt(st, sym, p, p["qty"], price, why)
        st["pos"].pop(sym, None)
    elif not p["tp1_done"] and ((price >= p["tp1"]) if dr > 0 else (price <= p["tp1"])):
        close_mkt(st, sym, p, p["qty0"] / 2, price, "TP1 (50%)")
        p["tp1_done"] = True
        if C["MOVE_BE"] and (p["entry"] - p["sl"]) * dr > 0:
            p["sl"] = p["entry"]


# =========================================================================
#  ETAT + BOUCLE
# =========================================================================
def load_state(C):
    st = {"arm": {}, "pos": {}, "seen": [], "last": {}, "paper": C["PAPER_BALANCE"],
          "inst": {}, "lev": {}, "log": [], "mode": None}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                st.update(json.load(f))
        except Exception as e:
            log.warning("etat illisible (%s) -> repart a zero", e)
    return st


def migrate_state(st):
    """Les anciennes versions indexaient les armements par simple symbole et
       sans les champs sym/tf. On les annule proprement pour ne pas bloquer
       le bot (MAX_OPEN) ni laisser un ordre limite orphelin chez Kraken."""
    for k in [k for k, v in list(st.get("arm", {}).items())
              if not isinstance(v, dict) or "sym" not in v or "tf" not in v]:
        v = st["arm"].pop(k)
        if isinstance(v, dict) and v.get("order_id"):
            cancel(v["order_id"])
        log.info("ancien armement %s supprime (format obsolete)", k)
    for k in [k for k, v in list(st.get("pos", {}).items())
              if not isinstance(v, dict) or "tf" not in v]:
        st["pos"][k]["tf"] = st.get("mode") and C_TF_FALLBACK or C_TF_FALLBACK
    st["last"] = {k: v for k, v in (st.get("last") or {}).items() if "|" in k}


def save_state(st):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(st, f, indent=1)
    except Exception as e:
        log.warning("etat non sauvegarde : %s", e)


def check_keys(st):
    """Verification des cles au demarrage (lecture seule, aucun ordre)."""
    if not (os.environ.get("KRAKEN_KEY") and os.environ.get("KRAKEN_SECRET")):
        notify(st, "ATTENTION : aucune cle Kraken Futures -> le mode REEL ne pourra pas trader.")
        return False
    try:
        f = (priv("GET", "/api/v3/accounts").get("accounts") or {}).get("flex") or {}
        eq = float(f.get("marginEquity") or f.get("portfolioValue") or 0)
        notify(st, "Cles Kraken FUTURES valides. Capital du compte : %.2f USD" % eq)
        if eq <= 0:
            notify(st, "ATTENTION : capital a 0 sur le compte Futures. En mode REEL, "
                       "aucun ordre ne pourra etre passe tant que le compte n'est pas approvisionne.")
        return True
    except Exception as e:
        notify(st, "ATTENTION : cles Kraken REFUSEES (%s). Ce sont peut-etre des cles SPOT : "
                   "il faut des cles creees dans l'onglet Futures." % e)
        return False


def cycle(st, C):
    # liste des actifs : rafraichie au demarrage puis toutes les heures
    if time.time() - st.get("disco", 0) > 3600 or not C["SYMS"]:
        st["inst"] = instruments(C)
        C["SYMS"] = discover(st, C)
        st["disco"] = time.time()
        log.info("%d actifs suivis (volume >= %s USD) : %s", len(C["SYMS"]),
                 int(C["MIN_QUOTE_VOL"]), ", ".join(nm(s) for s in C["SYMS"][:12])
                 + (" ..." if len(C["SYMS"]) > 12 else ""))
    if st.get("mode") != C["MODE"]:
        st.update(arm={}, pos={}, mode=C["MODE"])
        notify(st, "Bot PIEGE -> MA50 [%s] x%s %s | unites de temps %s | ADX>=%g | "
                   "%d perpetuels Kraken (volume 24h >= %s USD), un seul trade a la fois"
               % ("REEL" if LIVE else "PAPER", C["LEVERAGE"], C["SIZING"],
                  "/".join(C["TF_LIST"]), C["ADX_MIN"], len(C["SYMS"]), int(C["MIN_QUOTE_VOL"])))
    if LIVE:
        for sym in C["SYMS"]:
            if st["lev"].get(sym) == C["LEVERAGE"]:
                continue
            try:
                priv("PUT", "/api/v3/leveragepreferences",
                     {"symbol": sym, "maxLeverage": C["LEVERAGE"]})
            except Exception as e:
                log.warning("levier %s : %s", sym, e)
            st["lev"][sym] = C["LEVERAGE"]

    px = tickers()
    pos_ex = open_positions() if LIVE else {}
    now = time.time()
    for tf in C["TF_LIST"]:
        tfs = TF_SEC[tf]
        last_closed = int(now // tfs * tfs) - tfs
        if now - (last_closed + tfs) < 5:
            continue
        for sym in C["SYMS"]:
            akey = "%s|%s" % (sym, tf)
            try:
                if st["last"].get(akey) != last_closed:
                    bars = candles(sym, tf)
                    time.sleep(0.08)             # ne pas saturer l'API Kraken
                    if len(bars) > 120 and bars[-1][0] == last_closed:
                        st["last"][akey] = last_closed
                        on_bar(st, sym, tf, bars, C)
            except Exception as e:
                log.warning("%s %s : %s", sym, tf, e)

    for akey in list(st["arm"]):
        try:
            check_fill(st, akey, px, pos_ex, C)
        except Exception as e:
            log.warning("remplissage %s : %s", akey, e)
    for sym in list(st["pos"]):
        try:
            if px.get(sym):
                manage(st, sym, px[sym], pos_ex, C)
        except Exception as e:
            log.warning("suivi %s : %s", sym, e)
    st["seen"] = st["seen"][-600:]


def main():
    C = CFG
    if LIVE and not (os.environ.get("KRAKEN_KEY") and os.environ.get("KRAKEN_SECRET")):
        sys.exit("MODE live sans cles Kraken Futures")
    st = load_state(C)
    globals()["C_TF_FALLBACK"] = C["TF_LIST"][0]
    migrate_state(st)
    log.info("Demarrage [%s] x%s %s | %s | ADX>=%g | boucle %s min, scan toutes les %s s",
             "REEL" if LIVE else "PAPER", C["LEVERAGE"], C["SIZING"],
             "/".join(C["TF_LIST"]), C["ADX_MIN"], C["LOOP_MINUTES"], C["INTERVAL"])
    check_keys(st)
    end = time.time() + C["LOOP_MINUTES"] * 60
    while True:
        t0 = time.time()
        try:
            cycle(st, C)
        except Exception as e:
            log.warning("cycle : %s", e)
        save_state(st)
        if time.time() >= end:
            log.info("Fin de ce job (le workflow se relance automatiquement).")
            return
        time.sleep(max(5, C["INTERVAL"] - (time.time() - t0)))


if __name__ == "__main__":
    main()
