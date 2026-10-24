#!/usr/bin/env python3
"""
BOT PRO  -  signaux intraday "niveau Wall Street / Bloomberg"  (Telegram, AUCUN ordre envoye)
=============================================================================================
Deux strategies intraday parmi les mieux documentees par la recherche publiee, un filtre de
regime de marche facon terminal Bloomberg, une validation statistique de chaque signal.

1. STOCKS IN PLAY  (actions US)
   Zarattini, Barbon & Aziz (2024) "A Profitable Day Trading Strategy For The U.S. Equity
   Market" (SSRN 4729284) : cassure de la plage d'ouverture 5 min sur les seules actions
   "en jeu" (volume relatif d'ouverture >= 100 % de la moyenne 14 jours, top 20 du jour,
   prix > 5 $, volume moyen >= 1 M, ATR 14 j > 0.50 $). Sens = couleur de la 1re bougie
   5 min ; ordre stop au plus haut (achat) / plus bas (vente) de cette bougie ; stop a
   10 % de l'ATR 14 jours ; sortie en fin de seance. Etude : Sharpe 2.81 (filtre volume)
   contre 0.48 sans filtre, 2016-2023, frais inclus, sans glissement modelise.

2. MOMENTUM INTRADAY  (ETF/grandes actions, forex, or, crypto)
   Zarattini, Aziz & Barbon (2024) "Beat the Market - An Effective Intraday Momentum
   Strategy for S&P500 ETF (SPY)" + Gao, Han, Li & Zhou (2018) "Market intraday momentum" :
   "zone de bruit" = ouverture du jour x (1 +/- deplacement moyen a la meme heure sur
   14 jours, corrige des gaps). Decision toutes les 30 min : achat si le prix sort AU-DESSUS
   de la zone ET de la VWAP du jour, vente si EN DESSOUS des deux. Stop suiveur = max(borne,
   VWAP) (achat), sortie en fin de seance. Adapte au forex et a la crypto (jour = 0 h UTC).

COUCHE BLOOMBERG
   - briefing du matin : indices (S&P 500, Nasdaq, Dow, DAX, CAC, Nikkei), VIX, dollar (DXY),
     taux 10 ans US, or, petrole, BTC, rotation des secteurs, regime risk-on / risk-off ;
   - filtre de regime : chaque signal est note selon le regime (tendance du marche, peur VIX) ;
   - contexte OpenBB / Yahoo : actualites, resultats d'entreprise, calendrier economique.

VALIDATION DE CHAQUE SIGNAL
   - score de confluence /100 (cassure, tendance 1 h, VWAP, force relative, volume relatif,
     regime, backtest, actualites) ;
   - backtest de la MEME regle sur l'actif (60 derniers jours de bougies 5 min) ;
   - Monte Carlo : 10 000 tirages des resultats du backtest sur les 20 prochains trades
     (probabilite d'etre gagnant, mediane, pire 5 %).

Donnees : Yahoo Finance (gratuit, non officiel, retard possible). Ce n'est pas un conseil
financier : les resultats passes et les etudes ne garantissent rien.
"""
import datetime
import html as _html
import json
import logging
import math
import os
import random
import re
import signal
import time
from zoneinfo import ZoneInfo

import requests

NY = ZoneInfo("America/New_York")
PARIS = ZoneInfo("Europe/Paris")
LDN = ZoneInfo("Europe/London")
UTC = datetime.timezone.utc
log = logging.getLogger("botpro")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")


def envf(name, default):
    v = os.environ.get(name, "").strip()
    if v == "":
        return default
    if isinstance(default, str):
        return v
    try:
        return type(default)(float(v.replace(",", ".")))
    except ValueError:
        logging.warning("variable %s=%r invalide : %s par defaut", name, v, default)
        return default


SIP_STOCKS = (
    "AAPL,MSFT,NVDA,AMZN,META,GOOGL,TSLA,AVGO,AMD,NFLX,ORCL,CRM,ADBE,INTC,QCOM,TXN,MU,AMAT,LRCX,"
    "KLAC,ADI,MRVL,NXPI,ON,MCHP,ARM,SMCI,DELL,HPQ,IBM,CSCO,ANET,PANW,CRWD,FTNT,ZS,NET,DDOG,SNOW,"
    "MDB,PLTR,NOW,INTU,WDAY,TEAM,SHOP,UBER,LYFT,ABNB,DASH,BKNG,EXPE,PYPL,XYZ,COIN,HOOD,SOFI,AFRM,UPST,"
    "MSTR,MARA,RIOT,CLSK,HUT,IREN,CIFR,RBLX,U,SNAP,PINS,SPOT,ROKU,TTD,APP,DUOL,CVNA,CHWY,ETSY,EBAY,"
    "BABA,JD,PDD,BIDU,NIO,XPEV,LI,RIVN,LCID,F,GM,TM,STLA,AI,SOUN,BBAI,IONQ,RGTI,QBTS,OKLO,SMR,"
    "MRNA,BNTX,PFE,LLY,NVO,JNJ,ABBV,MRK,BMY,AMGN,GILD,REGN,VRTX,UNH,CVS,HUM,ISRG,DXCM,"
    "JPM,BAC,WFC,C,GS,MS,SCHW,BLK,AXP,V,MA,COF,USB,PNC,"
    "WMT,COST,TGT,HD,LOW,MCD,SBUX,CMG,NKE,LULU,KO,PEP,PG,DIS,CMCSA,T,VZ,TMUS,"
    "BA,CAT,DE,GE,HON,LMT,RTX,NOC,UPS,FDX,XOM,CVX,COP,OXY,SLB,HAL,DVN,FCX,NEM,AA,CLF,"
    "DAL,UAL,AAL,CCL,RCL,NCLH")
MOM_STOCKS = "SPY,QQQ,IWM,DIA,SMH,NVDA,TSLA,AAPL,MSFT,AMZN,META,GOOGL,AMD,AVGO,NFLX,COIN,MSTR,PLTR"
MOM_FX = "EURUSD,GBPUSD,USDJPY,USDCHF,USDCAD,AUDUSD,NZDUSD,EURJPY,GBPJPY,EURGBP,AUDJPY,XAUUSD,XAGUSD"
MOM_CRYPTO = "BTC,ETH,SOL,XRP,BNB,DOGE,ADA,AVAX,LINK,LTC,DOT,TRX,BCH,NEAR,XLM,HBAR,ICP,ATOM"


def _lst(name, default):
    return [s.strip().upper() for s in envf(name, default).split(",") if s.strip()]


CFG = {
    "SIP": _lst("SIP_STOCKS", SIP_STOCKS),
    "MOM_STOCKS": _lst("MOM_STOCKS", MOM_STOCKS),
    "MOM_FX": _lst("MOM_FX", MOM_FX),
    "MOM_CRYPTO": _lst("MOM_CRYPTO", MOM_CRYPTO),
    "SIP_TOP": envf("SIP_TOP", 20),             # etude : les 20 plus forts volumes relatifs
    "SIP_SEND": envf("SIP_SEND", 10),           # combien en envoyer (les meilleurs scores)
    "SIP_RVOL": envf("SIP_RVOL", 1.0),          # volume relatif mini (1.0 = 100 %)
    "SIP_ATR_STOP": envf("SIP_ATR_STOP", 0.10),  # stop = 10 % de l'ATR 14 jours
    "MIN_SCORE": envf("MIN_SCORE", 60),         # score de confluence mini pour envoyer
    "NEED_BT": envf("NEED_BT", 1),              # 1 = backtest de l'actif gagnant en moyenne exige
    "SIMS": max(1000, envf("SIMS", 10000)),
    "LOOP_MINUTES": min(envf("LOOP_MINUTES", 320), 320),
    "STATE_DIR": envf("STATE_DIR", "."),
    "PAUSE": envf("PAUSE", 0.2),
}
BAR = 300
STATE_FILE = "bot_pro_state.json"
SEP = "━━━━━━━━━━━━━━━"

NYSE_HOLIDAYS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19",
    "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
    "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}
NYSE_EARLY_CLOSE = {"2026-11-27", "2026-12-24", "2027-11-26"}


def hm(h, m=0):
    return datetime.time(h, m)


def stock_open(d):
    return d.weekday() < 5 and d.isoformat() not in NYSE_HOLIDAYS


def stock_close_t(d):
    return hm(13) if d.isoformat() in NYSE_EARLY_CLOSE else hm(16)


def at(d, t, tz):
    return int(datetime.datetime.combine(d, t, tzinfo=tz).timestamp())


def local_dt(ts, tz):
    return datetime.datetime.fromtimestamp(ts, tz)


# --------------------------------------------------------------- symboles
def ysym(sym, kind):
    if kind == "cr":
        return sym if "-" in sym else sym + "-USD"
    if kind == "fx":
        return {"XAUUSD": "GC=F", "XAGUSD": "SI=F"}.get(sym, sym + "=X")
    return sym.replace(".", "-")


def label(sym):
    return {"XAUUSD": "XAUUSD (or)", "XAGUSD": "XAGUSD (argent)"}.get(sym, sym)


def esc(t):
    return _html.escape(str(t), quote=False)


FMT = [2]


def fmt(x):
    if x is None:
        return "-"
    if 0 < x < 1:
        return "%.*f" % (max(4, 4 - int(math.floor(math.log10(x)))), x)
    if FMT[0] == 2 or x >= 1000:
        return "%.2f" % x
    return "%.3f" % x if x >= 20 else "%.5f" % x


# --------------------------------------------------------------- Yahoo
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) bot-pro/1.0", "Accept": "*/*"})
HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
STATS = {"requetes": 0, "erreurs": 0}


def backoff(r):
    try:
        wait = float(r.headers.get("Retry-After") or 5)
    except ValueError:
        wait = 5.0
    STATS["limites_429"] = STATS.get("limites_429", 0) + 1
    time.sleep(min(30.0, max(2.0, wait)))


def parse_result(res):
    t = res.get("timestamp") or []
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0] or {}
    o, h, l, c, v = (q.get(k) or [None] * len(t) for k in ("open", "high", "low", "close", "volume"))
    out = {}
    for i, ts in enumerate(t):
        if i >= min(len(o), len(h), len(l), len(c)):
            break
        if None in (o[i], h[i], l[i], c[i]) or c[i] <= 0:
            continue
        out[int(ts)] = (int(ts), float(o[i]), float(h[i]), float(l[i]), float(c[i]),
                        float((v[i] if i < len(v) else 0) or 0))
    return [out[k] for k in sorted(out)]


def chart(sym, rng, interval):
    last = None
    for _ in range(2):
        for host in HOSTS:
            try:
                STATS["requetes"] += 1
                r = SESSION.get("%s/v8/finance/chart/%s" % (host, sym),
                                params={"range": rng, "interval": interval}, timeout=20)
                if r.status_code == 429 or r.status_code >= 500:
                    last = "HTTP %d" % r.status_code
                    if r.status_code == 429:
                        backoff(r)
                    continue
                res = ((r.json().get("chart") or {}).get("result") or [None])[0]
                if not res:
                    raise ValueError("symbole inconnu ou sans donnees")
                return parse_result(res)
            except Exception as ex:
                last = str(ex)[:120]
        time.sleep(2)
    STATS["erreurs"] += 1
    raise RuntimeError("Yahoo %s : %s" % (sym, last))


# --------------------------------------------------------------- indicateurs
def ema(vals, n):
    if not vals:
        return []
    k = 2.0 / (n + 1)
    out, e = [], vals[0]
    for x in vals:
        e = x * k + e * (1 - k)
        out.append(e)
    return out


def resample(bars, sec, t0=0):
    """Bougies agregees (ex. 1 h) a partir des 5 min."""
    out = {}
    for b in bars:
        k = (b[0] - t0) // sec
        if k not in out:
            out[k] = [k * sec + t0, b[1], b[2], b[3], b[4], b[5]]
        else:
            x = out[k]
            x[2], x[3], x[4], x[5] = max(x[2], b[2]), min(x[3], b[3]), b[4], x[5] + b[5]
    return [tuple(out[k]) for k in sorted(out)]


def trend_1h(bars, upto_ts):
    """+1 tendance haussiere 1 h (EMA20 > EMA50 et prix > EMA50), -1 baissiere, 0 neutre."""
    h = [b for b in resample([b for b in bars if b[0] < upto_ts], 3600) if b[0] + 3600 <= upto_ts + 300]
    if len(h) < 55:
        return 0
    c = [b[4] for b in h]
    e20, e50 = ema(c, 20)[-1], ema(c, 50)[-1]
    if e20 > e50 and c[-1] > e50:
        return 1
    if e20 < e50 and c[-1] < e50:
        return -1
    return 0


def vwap(bars):
    pv = vv = s = 0.0
    for b in bars:
        tp = (b[2] + b[3] + b[4]) / 3.0
        pv += tp * b[5]
        vv += b[5]
        s += tp
    if vv > 0:
        return pv / vv
    return s / len(bars) if bars else None


def wilson_low(k, n, z=1.28):
    if n <= 0:
        return 0.0
    p = k / n
    den = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - r) / den


def bt_stats(rs):
    if not rs:
        return {"n": 0}
    wins = [x for x in rs if x > 0]
    losses = [-x for x in rs if x < 0]
    pf = (sum(wins) / sum(losses)) if losses else (99.0 if wins else 0.0)
    return {"n": len(rs), "win": len(wins) / len(rs), "avg": sum(rs) / len(rs), "pf": pf, "tot": sum(rs)}


def mc_trades(rs, n_next=20, sims=10000, seed=7):
    """Monte Carlo : 10 000 sequences des 20 prochains trades tirees dans le backtest."""
    if len(rs) < 8:
        return None
    rng = random.Random(seed)
    tots, dds = [], []
    for _ in range(int(sims)):
        eq = peak = dd = 0.0
        for _ in range(n_next):
            eq += rs[int(rng.random() * len(rs))]
            peak = max(peak, eq)
            dd = max(dd, peak - eq)
        tots.append(eq)
        dds.append(dd)
    tots.sort()
    dds.sort()
    return {"p_win": sum(1 for x in tots if x > 0) / len(tots), "med": tots[len(tots) // 2],
            "p5": tots[int(0.05 * len(tots))], "dd95": dds[int(0.95 * len(dds))], "n": n_next}


# --------------------------------------------------------------- jours / sessions de bougies
def by_day(bars, tz, start_t):
    """{date: [bougies du jour]} ; un jour commence a start_t (heure locale tz)."""
    out = {}
    for b in bars:
        dt = local_dt(b[0], tz)
        d = dt.date()
        if dt.time() < start_t:
            continue
        out.setdefault(d, []).append(b)
    return out


# =============================================================== 2. MOMENTUM (zone de bruit)
MOM_MARKETS = {
    # cle : (fuseau, debut du jour, premiere decision, derniere decision, sortie, jours ouvres ?)
    "stocks": (NY, hm(9, 30), hm(10, 0), hm(15, 30), hm(15, 55), True),
    "fx": (UTC, hm(0), hm(7, 0), hm(19, 30), hm(20, 55), True),
    "cr": (UTC, hm(0), hm(6, 30), hm(20, 0), hm(20, 55), False),
}


def mom_day_ok(kind, d):
    if kind == "stocks":
        return stock_open(d)
    if kind == "fx":
        return d.weekday() < 5 and (d.month, d.day) not in ((12, 25), (1, 1))
    return True


_OFF = {}


def day_offsets(days, x, kind):
    """{decalage depuis le debut du jour (s): bougie} pour la journee x (cache)."""
    key = (id(days), x)
    m = _OFF.get(key)
    if m is None:
        tz, start_t = MOM_MARKETS[kind][0], MOM_MARKETS[kind][1]
        t0 = at(x, start_t, tz)
        m = {b[0] - t0: b for b in days[x]}
        _OFF[key] = m
    return m


def noise_sigma(days, d, off, kind, look=14):
    """Deplacement moyen |close/ouverture - 1| au MEME horaire sur les `look` jours precedents."""
    prev = [x for x in sorted(days) if x < d and mom_day_ok(kind, x)][-look:]
    vals = []
    for x in prev:
        bs = days[x]
        if not bs:
            continue
        b = day_offsets(days, x, kind).get(off)
        if b:
            vals.append(abs(b[4] / bs[0][1] - 1))
    return (sum(vals) / len(vals)) if len(vals) >= 10 else None


def mom_eval_day(days, d, kind, upto_ts=None, prev_close=None):
    """Applique la regle a la journee d. Retourne la liste des signaux (au plus 1 par sens) :
       [{dir, k, ts, price, upper, lower, vwap, stop, sigma}] et pour le backtest leur sortie."""
    tz, start_t, first_t, last_t, flat_t, _ = MOM_MARKETS[kind]
    bs = days.get(d) or []
    if len(bs) < 6:
        return []
    o = bs[0][1]
    pc = prev_close if prev_close else o
    t_first, t_last, t_flat = at(d, first_t, tz), at(d, last_t, tz), at(d, flat_t, tz)
    t0 = at(d, start_t, tz)
    out, seen = [], set()
    for k, b in enumerate(bs):
        close_ts = b[0] + BAR
        if upto_ts is not None and close_ts > upto_ts:
            break
        if close_ts < t_first or close_ts > t_last:
            continue
        if local_dt(close_ts, tz).minute % 30 != 0:
            continue                              # decisions a HH:00 et HH:30 (comme l'etude)
        sig = noise_sigma(days, d, b[0] - t0, kind)
        if not sig:
            continue
        up, lo = max(o, pc) * (1 + sig), min(o, pc) * (1 - sig)
        vw = vwap(bs[:k + 1])
        px = b[4]
        for dr in (1, -1):
            if dr in seen:
                continue
            if (dr > 0 and px > up and px > vw) or (dr < 0 and px < lo and px < vw):
                stop = max(up, vw) if dr > 0 else min(lo, vw)
                if (px - stop) * dr <= px * 0.0003:
                    continue                      # stop collé au prix : signal ignore
                seen.add(dr)
                out.append({"dir": dr, "k": k, "ts": close_ts, "price": px, "upper": up, "lower": lo,
                            "vwap": vw, "stop": stop, "sigma": sig, "t_flat": t_flat})
    return out


def mom_exit(days, d, s):
    """Sortie de la regle : stop suiveur max(borne, VWAP) verifie toutes les 30 min, fin de seance.
       Retourne le resultat en R (risque initial = entree - stop initial)."""
    bs = days[d]
    dr, e, stop = s["dir"], s["price"], s["stop"]
    risk = (e - stop) * dr
    tz = MOM_MARKETS["stocks"][0]
    last = e
    for j in range(s["k"] + 1, len(bs)):
        b = bs[j]
        if b[0] + BAR > s["t_flat"]:
            break
        last = b[4]
        if (b[3] <= stop and dr > 0) or (b[2] >= stop and dr < 0):
            return ((stop - e) * dr) / risk       # stop touche dans la bougie
        if local_dt(b[0] + BAR, tz).minute % 30 == 0:  # mise a jour du stop suiveur
            vw = vwap(bs[:j + 1])
            new = max(s["upper"], vw) if dr > 0 else min(s["lower"], vw)
            stop = max(stop, new) if dr > 0 else min(stop, new)
    return ((last - e) * dr) / risk


def mom_backtest(days, kind, before, dr):
    rs = []
    keys = sorted(days)
    for i, d in enumerate(keys):
        if d >= before or not mom_day_ok(kind, d):
            continue
        pc = days[keys[i - 1]][-1][4] if i > 0 and days[keys[i - 1]] else None
        for s in mom_eval_day(days, d, kind, None, pc):
            if s["dir"] == dr:
                rs.append(max(-1.5, mom_exit(days, d, s)))   # glissement de stop borne a -1.5 R
    return rs


# =============================================================== 1. STOCKS IN PLAY
def sip_analyse(sym, bars, d):
    """Volume relatif d'ouverture, ATR 14 j, filtres de l'etude, sens de la 1re bougie 5 min."""
    days = by_day([b for b in bars if hm(9, 30) <= local_dt(b[0], NY).time() < hm(16)], NY, hm(9, 30))
    if d not in days:
        return None
    today = days[d]
    first = today[0]
    if local_dt(first[0], NY).time() != hm(9, 30):
        return None
    prev = [x for x in sorted(days) if x < d]
    if len(prev) < 14:
        return None
    p14 = prev[-14:]
    v_open = [days[x][0][5] for x in p14 if days[x] and local_dt(days[x][0][0], NY).time() == hm(9, 30)]
    if len(v_open) < 10 or sum(v_open) <= 0:
        return None
    rvol = first[5] / (sum(v_open) / len(v_open))
    trs = []
    for i, x in enumerate(prev[-15:]):
        bs = days[x]
        hi, lo = max(b[2] for b in bs), min(b[3] for b in bs)
        if i > 0:
            pc = days[prev[-15:][i - 1]][-1][4]
            trs.append(max(hi - lo, abs(hi - pc), abs(lo - pc)))
    atr = sum(trs[-14:]) / len(trs[-14:]) if trs else 0
    adv = sum(sum(b[5] for b in days[x]) for x in p14) / 14.0
    o, h, l, c = first[1], first[2], first[3], first[4]
    dr = 1 if c > o else -1 if c < o else 0
    return {"sym": sym, "rvol": rvol, "atr": atr, "adv": adv, "price": o, "dir": dr,
            "entry": h if dr > 0 else l, "stop": (h - CFG["SIP_ATR_STOP"] * atr) if dr > 0 else (l + CFG["SIP_ATR_STOP"] * atr),
            "days": days, "first": first}


def sip_backtest(days, d, atr_frac):
    """La regle sur les jours precedents (volume relatif >= seuil ce jour-la), en R."""
    rs = []
    keys = sorted(x for x in days if x < d)
    for i, x in enumerate(keys):
        if i < 15:
            continue
        bs = days[x]
        f = bs[0]
        if local_dt(f[0], NY).time() != hm(9, 30):
            continue
        p14 = keys[i - 14:i]
        vo = [days[y][0][5] for y in p14]
        if not vo or sum(vo) <= 0 or f[5] / (sum(vo) / len(vo)) < CFG["SIP_RVOL"]:
            continue
        trs = []
        for j in range(i - 14, i):
            b2 = days[keys[j]]
            hi, lo = max(b[2] for b in b2), min(b[3] for b in b2)
            pc = days[keys[j - 1]][-1][4]
            trs.append(max(hi - lo, abs(hi - pc), abs(lo - pc)))
        atr = sum(trs) / len(trs)
        dr = 1 if f[4] > f[1] else -1 if f[4] < f[1] else 0
        if not dr or atr <= 0:
            continue
        e = f[2] if dr > 0 else f[3]
        stop = e - dr * atr_frac * atr
        risk = abs(e - stop)
        filled, res = False, None
        for b in bs[1:]:
            if local_dt(b[0] + BAR, NY).time() > hm(15, 55):
                break
            if not filled:
                if (dr > 0 and b[2] >= e) or (dr < 0 and b[3] <= e):
                    filled = True
                    # bougie d'entree : l'ordre des prix dans les 5 min est inconnu ; stop compte
                    # seulement si la bougie CLOTURE au-dela (ni optimiste ni pessimiste)
                    if (dr > 0 and b[4] <= stop) or (dr < 0 and b[4] >= stop):
                        res = -1.0
                        break
                    res = (b[4] - e) * dr / risk
                continue
            if (dr > 0 and b[3] <= stop) or (dr < 0 and b[2] >= stop):
                res = -1.0
                break
            res = (b[4] - e) * dr / risk
        if filled and res is not None:
            rs.append(max(-1.5, res))
    return rs


# =============================================================== COUCHE BLOOMBERG
MACRO = [("^GSPC", "S&P 500"), ("^IXIC", "Nasdaq"), ("^DJI", "Dow Jones"), ("^GDAXI", "DAX"),
         ("^FCHI", "CAC 40"), ("^N225", "Nikkei"), ("^VIX", "VIX (peur)"), ("DX-Y.NYB", "Dollar DXY"),
         ("^TNX", "Taux 10 ans US"), ("GC=F", "Or"), ("CL=F", "Pétrole WTI"), ("BTC-USD", "Bitcoin")]
SECTORS = [("XLK", "Tech"), ("XLF", "Finance"), ("XLE", "Énergie"), ("XLV", "Santé"), ("XLY", "Conso. cyclique"),
           ("XLP", "Conso. de base"), ("XLI", "Industrie"), ("XLU", "Services publics"), ("XLB", "Matériaux"),
           ("XLRE", "Immobilier"), ("XLC", "Télécoms")]
REGIME = {"label": "inconnu", "spy": 0, "vix": None, "ts": 0}


def daily_perf(sym):
    b = chart(sym, "3mo", "1d")
    c = [x[4] for x in b]
    if len(c) < 2:
        return None
    ma50 = sum(c[-50:]) / len(c[-50:])
    return {"last": c[-1], "d1": c[-1] / c[-2] - 1, "w1": c[-1] / c[-6] - 1 if len(c) > 6 else None,
            "above50": c[-1] > ma50}


def regime_update(force=False):
    """Regime de marche facon desk : SPY au-dessus de sa MM50 + VIX. risk-on / neutre / risk-off."""
    if not force and time.time() - REGIME["ts"] < 3600:
        return REGIME
    try:
        spy = daily_perf("SPY")
        vix = daily_perf("^VIX")
        v = vix["last"] if vix else None
        trend = 1 if spy and spy["above50"] else -1
        if v is not None and v >= 30:
            lab = "RISK-OFF (VIX ≥ 30 : marché en panique)"
            trend = -1
        elif trend > 0 and (v is None or v < 20):
            lab = "RISK-ON (S&P au-dessus de sa MM50, VIX calme)"
        elif trend > 0:
            lab = "NEUTRE (S&P haussier mais VIX nerveux)"
        else:
            lab = "RISK-OFF (S&P sous sa MM50)"
        REGIME.update(label=lab, spy=trend, vix=v, ts=time.time())
    except Exception as ex:
        log.warning("regime : %s", ex)
    return REGIME


def pct(x):
    return "-" if x is None else "%+.2f %%" % (100 * x)


def briefing():
    reg = regime_update(force=True)
    L = ["🏛 <b>BRIEFING MARCHÉ</b> · %s" % local_dt(time.time(), PARIS).strftime("%d/%m %H:%M"), SEP]
    for s, name in MACRO:
        try:
            p = daily_perf(s)
            if p:
                arrow = "🟢" if p["d1"] > 0 else "🔴"
                L.append("%s %s <code>%s</code> %s (5 j %s)" % (arrow, esc(name), fmt(p["last"]), pct(p["d1"]), pct(p["w1"])))
        except Exception as ex:
            log.warning("briefing %s : %s", s, ex)
        time.sleep(CFG["PAUSE"])
    sect = []
    for s, name in SECTORS:
        try:
            p = daily_perf(s)
            if p and p["w1"] is not None:
                sect.append((p["w1"], name))
        except Exception:
            pass
        time.sleep(CFG["PAUSE"])
    if sect:
        sect.sort(reverse=True)
        L += [SEP, "🔄 <b>Rotation sectorielle (5 jours)</b>",
              "en tête : " + esc(", ".join("%s %s" % (n, pct(w)) for w, n in sect[:3])),
              "en queue : " + esc(", ".join("%s %s" % (n, pct(w)) for w, n in sect[-3:]))]
    ev = econ_today()
    if ev:
        L += [SEP, "🏦 <b>Agenda économique (Paris)</b>"] + [esc(x) for x in ev[:8]]
    L += [SEP, "🧭 <b>Régime : %s</b>" % esc(reg["label"]),
          "<i>Achats favorisés en risk-on, ventes en risk-off. Pas un conseil.</i>"]
    return "\n".join(L)


# --------------------------------------------------------------- OpenBB / Yahoo : contexte
OBB = {"mod": None, "err": None, "cache": {}, "errors": {}}
MAJOR = ("nonfarm", "non-farm", "payroll", "cpi", "inflation", "interest rate", "rate decision", "fomc",
         "ecb", "boe", "boj", "snb", "gdp", "unemployment", "retail sales", "pmi", "pce", "jobless",
         "press conference", "monetary policy")


def openbb():
    if OBB["mod"] is None and OBB["err"] is None:
        try:
            from openbb import obb as _o
            OBB["mod"] = _o
        except Exception as ex:
            OBB["err"] = str(ex)[:150]
    return OBB["mod"]


def _cached(key, ttl, fn):
    c = OBB["cache"].get(key)
    if c and time.time() - c[0] < ttl:
        return c[1]
    try:
        val = fn()
    except Exception as ex:
        OBB["errors"][key.split("|")[0]] = str(ex)[:160]
        val = None
    OBB["cache"][key] = (time.time(), val)
    return val


def news(sym, n=2):
    def yahoo():
        for host in HOSTS:
            try:
                r = SESSION.get("%s/v1/finance/search" % host, params={"q": sym, "newsCount": 6, "quotesCount": 0},
                                timeout=15)
                if r.ok:
                    return [str(x["title"]).strip() for x in r.json().get("news") or [] if x.get("title")]
            except Exception:
                pass
        return []
    return (_cached("news|" + sym, 1800, yahoo) or [])[:n]


def earnings_soon(sym, d):
    o = openbb()
    if not o:
        return None

    def f():
        rows = getattr(o.equity.calendar.earnings(start_date=d.isoformat(),
                                                  end_date=(d + datetime.timedelta(days=1)).isoformat(),
                                                  provider="nasdaq"), "results", []) or []
        return {str(getattr(x, "symbol", "")).upper() for x in rows}
    s = _cached("earn|" + d.isoformat(), 6 * 3600, f) or set()
    return sym.upper() in s


def econ_today():
    o = openbb()
    if not o:
        return []
    d = datetime.datetime.now(NY).date()

    def f():
        rows = getattr(o.economy.calendar(start_date=d.isoformat(), end_date=d.isoformat(), provider="nasdaq"),
                       "results", []) or []
        out = []
        for x in rows:
            ev = str(getattr(x, "event", "") or "")
            if not any(k in ev.lower() for k in MAJOR):
                continue
            dt = getattr(x, "date", None)
            t = ""
            if isinstance(dt, datetime.datetime):
                t = (dt if dt.tzinfo else dt.replace(tzinfo=NY)).astimezone(PARIS).strftime("%H:%M")
            out.append("%s %s · %s" % (t, getattr(x, "country", ""), ev[:60]))
        return out
    return _cached("econ|" + d.isoformat(), 3600, f) or []


# --------------------------------------------------------------- Telegram / etat
LAST_TG = [0.0]


def strip_tags(t):
    return _html.unescape(re.sub(r"</?(b|i|code)>", "", t))


def telegram(msg, html=True):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    log.info("\n" + strip_tags(msg))
    if not tok or not chat:
        return
    if len(msg) > 4000:
        msg = msg[:3990].rsplit("\n", 1)[0] + "\n…"
    gap = 3.1 if str(chat).startswith("-") else 1.1
    for essai in range(4):
        time.sleep(max(0.0, LAST_TG[0] + gap - time.time()))
        LAST_TG[0] = time.time()
        try:
            body = {"chat_id": chat, "text": msg, "disable_web_page_preview": True}
            if html:
                body["parse_mode"] = "HTML"
            r = SESSION.post("https://api.telegram.org/bot%s/sendMessage" % tok, json=body, timeout=10)
            if r.ok:
                return
            if r.status_code == 429:
                try:
                    wait = float((r.json().get("parameters") or {}).get("retry_after") or 5)
                except Exception:
                    wait = 5.0
                time.sleep(min(60.0, wait + 1))
                continue
            if html and r.status_code == 400:
                html, msg = False, strip_tags(msg)
                continue
        except Exception as ex:
            log.warning("telegram : %s", str(ex).replace(tok, "***"))
        time.sleep(2 + 3 * essai)


def load_state():
    for p in (os.path.join(CFG["STATE_DIR"], STATE_FILE), STATE_FILE):
        try:
            with open(p) as f:
                st = json.load(f)
            if isinstance(st, dict):
                break
        except Exception:
            st = None
    st = st if isinstance(st, dict) else {}
    for k in ("alerts", "done", "sip", "signals", "day"):
        st.setdefault(k, {} if k != "signals" else [])
    return st


def save_state(st):
    cut = time.time() - 4 * 86400
    st["alerts"] = {k: v for k, v in st["alerts"].items() if v > cut}
    st["signals"] = st["signals"][-80:]
    st["stats"] = dict(STATS, maj=time.strftime("%d/%m %H:%M UTC", time.gmtime()))
    os.makedirs(CFG["STATE_DIR"], exist_ok=True)
    for p in {os.path.join(CFG["STATE_DIR"], STATE_FILE), STATE_FILE}:
        with open(p + ".tmp", "w") as f:
            json.dump(st, f, indent=1)
        os.replace(p + ".tmp", p)


def count(st, key):
    d = local_dt(time.time(), PARIS).date().isoformat()
    day = st["day"] if st["day"].get("date") == d else {"date": d}
    day[key] = day.get(key, 0) + 1
    st["day"] = day


# --------------------------------------------------------------- score / messages
def bar10(score):
    n = int(round(score / 10.0))
    return "▰" * n + "▱" * (10 - n)


def bt_points(st_):
    if st_.get("n", 0) < 12:
        return 0
    pf = st_["pf"]
    return 20 if pf >= 1.5 else 14 if pf >= 1.2 else 7 if pf >= 1.0 else 0


def bt_lines(st_, mc):
    L = []
    if st_.get("n"):
        L.append("📜 Backtest de la règle sur l'actif : %d trades · %.0f %% gagnants · moy. %+.2f R · PF %.2f" % (
            st_["n"], 100 * st_["win"], st_["avg"], min(st_["pf"], 99)))
    else:
        L.append("📜 Backtest : pas assez d'historique")
    if mc:
        L.append("🎲 10 000 simulations des %d prochains trades : gagnant dans <b>%.0f %%</b> des cas · médiane %+.1f R · pire 5 %% %+.1f R" % (
            mc["n"], 100 * mc["p_win"], mc["med"], mc["p5"]))
    return L


def verdict(score):
    if score >= 75:
        return "🟢", "SIGNAL FORT"
    if score >= CFG["MIN_SCORE"]:
        return "🟡", "SIGNAL MOYEN"
    return "🔴", "FAIBLE"


# --------------------------------------------------------------- MOMENTUM : scan
HIST = {}


def hist(kind, sym):
    """Bougies 5 min (60 jours) en cache, completees a chaque passage par la journee en cours."""
    y = ysym(sym, kind)
    c = HIST.get(y)
    if not c or time.time() - c["full"] > 12 * 3600:
        HIST[y] = {"bars": {b[0]: b for b in chart(y, "60d", "5m")}, "full": time.time()}
    else:
        for b in chart(y, "1d" if kind == "stocks" else "2d", "5m"):
            c["bars"][b[0]] = b
    return [HIST[y]["bars"][k] for k in sorted(HIST[y]["bars"])]


def benchmark_ret(kind, d, upto):
    ref = {"stocks": "SPY", "cr": "BTC"}.get(kind)
    if not ref:
        return None
    try:
        bars = hist(kind, ref)
        tz, start_t = MOM_MARKETS[kind][0], MOM_MARKETS[kind][1]
        day = by_day(bars, tz, start_t).get(d) or []
        day = [b for b in day if b[0] + BAR <= upto]
        return day[-1][4] / day[0][1] - 1 if day else None
    except Exception:
        return None


def mom_scan(st, kind, now_ts):
    tz, start_t, first_t, last_t, flat_t, _ = MOM_MARKETS[kind]
    d = local_dt(now_ts, tz).date()
    if not mom_day_ok(kind, d) or not (at(d, first_t, tz) <= now_ts <= at(d, last_t, tz) + 240):
        return 0
    syms = {"stocks": CFG["MOM_STOCKS"], "fx": CFG["MOM_FX"], "cr": CFG["MOM_CRYPTO"]}[kind]
    reg = regime_update()
    sent = 0
    for sym in syms:
        if STOP[0]:
            break
        keys = ["mom|%s|%s|%d" % (d, sym, dr) for dr in (1, -1)]
        if all(k in st["alerts"] for k in keys):
            continue
        try:
            bars = hist(kind, sym)
        except Exception as ex:
            log.warning("%s : %s", sym, ex)
            continue
        finally:
            time.sleep(CFG["PAUSE"])
        days = by_day(bars, tz, start_t)
        dk = sorted(days)
        if d not in days:
            continue
        i = dk.index(d)
        pc = days[dk[i - 1]][-1][4] if i > 0 else None
        for s in mom_eval_day(days, d, kind, now_ts, pc):
            key = "mom|%s|%s|%d" % (d, sym, s["dir"])
            if key in st["alerts"]:
                continue
            st["alerts"][key] = time.time()
            if now_ts - s["ts"] > 600:
                continue                          # decision deja ancienne : jamais en retard
            count(st, "mom_vus")
            dr, px = s["dir"], s["price"]
            rs = mom_backtest(days, kind, d, dr)
            stt, mc = bt_stats(rs), mc_trades(rs, sims=CFG["SIMS"])
            # ---- score de confluence
            pts, why = 0, []
            ex_ = ((px / (s["upper"] if dr > 0 else s["lower"])) - 1) * dr / max(s["sigma"], 1e-9)
            p = 20 if ex_ >= 0.5 else 12 if ex_ >= 0.2 else 6
            pts += p
            why.append(("Sortie de la zone de bruit", p, 20))
            tr = trend_1h(bars, s["ts"])
            p = 20 if tr == dr else 8 if tr == 0 else 0
            pts += p
            why.append(("Tendance 1 h (EMA 20/50)", p, 20))
            bret = benchmark_ret(kind, d, s["ts"])
            own = px / days[d][0][1] - 1
            if bret is None or sym in ("SPY", "BTC"):
                p = 8
            else:
                p = 15 if (own - bret) * dr > 0 else 0
            pts += p
            why.append(("Force relative vs %s" % ({"stocks": "S&P", "cr": "BTC"}.get(kind, "marché")), p, 15))
            if kind == "fx":
                p = 8
            else:
                k = s["k"]
                cur = sum(b[5] for b in days[d][max(0, k - 5):k + 1])
                off_end = days[d][k][0] - at(d, start_t, tz)
                hist_v = []
                for x in dk[max(0, i - 14):i]:
                    mm = day_offsets(days, x, kind)
                    v_ = [mm[o_][5] for o_ in range(off_end - 5 * BAR, off_end + 1, BAR) if o_ in mm]
                    if v_:
                        hist_v.append(sum(v_))
                rv = cur / (sum(hist_v) / len(hist_v)) if hist_v and sum(hist_v) > 0 else 1
                p = 15 if rv >= 1.5 else 8 if rv >= 1.0 else 0
            pts += p
            why.append(("Volume relatif 30 min", p, 15))
            p = 10 if (reg["spy"] == dr or kind == "fx") else 0
            pts += p
            why.append(("Régime de marché", p, 10))
            p = bt_points(stt)
            pts += p
            why.append(("Backtest de l'actif", p, 20))
            nw = news(ysym(sym, kind))
            score = min(100, pts)
            if score < CFG["MIN_SCORE"] or (CFG["NEED_BT"] and (stt.get("n", 0) < 12 or stt["avg"] <= 0)):
                count(st, "mom_rejetes")
                log.info("%s %s : score %d, backtest %s -> non envoye", sym, dr, score, stt)
                continue
            FMT[0] = 2 if kind == "stocks" else 5
            risk = abs(px - s["stop"])
            tp = px + dr * 2 * risk
            em, vt = verdict(score)
            flat_p = local_dt(s["t_flat"], PARIS).strftime("%H:%M")
            L = ["%s <b>%s %s</b> · %s" % ("🟢" if dr > 0 else "🔴", "ACHAT" if dr > 0 else "VENTE", esc(label(sym)),
                                          {"stocks": "🇺🇸 Actions/ETF", "fx": "💱 Forex", "cr": "🪙 Crypto"}[kind]),
                 "⚡ Momentum intraday (zone de bruit + VWAP)",
                 SEP,
                 "▶️ Entrée    <code>%s</code>" % fmt(px),
                 "🛑 SL        <code>%s</code>  (%+.2f %%)" % (fmt(s["stop"]), -dr * 100 * risk / px),
                 "🎯 TP        <code>%s</code>  (2R indicatif)" % fmt(tp),
                 "🔁 Stop suiveur : remonter le SL à max(borne %s, VWAP) toutes les 30 min" % fmt(s["upper"] if dr > 0 else s["lower"]),
                 "⏰ Sortie au plus tard %s · ⌛ valable jusqu'à %s (Paris)" % (
                     flat_p, local_dt(s["ts"] + 600, PARIS).strftime("%H:%M")),
                 SEP,
                 "%s <b>%s · score %d/100</b> %s" % (em, vt, score, bar10(score))]
            L += ["%s %s" % ("✅" if p_ >= mx * 0.7 else "➖" if p_ > 0 else "❌", esc(n_)) for n_, p_, mx in why]
            L += bt_lines(stt, mc)
            L.append("🧭 Régime : %s" % esc(reg["label"]))
            for t in nw:
                L.append("📰 " + esc(t[:110]))
            L.append("<i>Signal seulement, aucun ordre envoyé · pas un conseil</i>")
            telegram("\n".join(L))
            sent += 1
            count(st, "mom_envoyes")
            st["signals"].append({"t": time.strftime("%d/%m %H:%M", time.gmtime()), "strat": "momentum",
                                  "sym": sym, "sens": "ACHAT" if dr > 0 else "VENTE", "entree": px,
                                  "sl": s["stop"], "score": score})
            save_state(st)
    return sent


# --------------------------------------------------------------- STOCKS IN PLAY : selection
def sip_select(st, now_ts):
    d = local_dt(now_ts, NY).date()
    if st["done"].get("sip") == d.isoformat():
        return
    st["done"]["sip"] = d.isoformat()
    reg = regime_update()
    cands = []
    for sym in CFG["SIP"]:
        if STOP[0]:
            break
        try:
            bars = chart(ysym(sym, "stocks"), "60d", "5m")
        except Exception as ex:
            log.warning("SIP %s : %s", sym, ex)
            continue
        finally:
            time.sleep(CFG["PAUSE"])
        a = sip_analyse(sym, bars, d)
        if not a or a["dir"] == 0:
            continue
        if a["price"] < 5 or a["adv"] < 1e6 or a["atr"] < 0.5 or a["rvol"] < CFG["SIP_RVOL"]:
            continue
        cands.append(a)
    cands.sort(key=lambda x: -x["rvol"])
    top = cands[:int(CFG["SIP_TOP"])]
    picks = []
    for rank, a in enumerate(top, 1):
        rs = sip_backtest(a["days"], d, CFG["SIP_ATR_STOP"])
        stt, mc = bt_stats(rs), mc_trades(rs, sims=CFG["SIMS"])
        pts = 30 if a["rvol"] >= 3 else 22 if a["rvol"] >= 2 else 12
        tr = trend_1h([b for x in sorted(a["days"]) for b in a["days"][x]], a["first"][0] + BAR)
        pts += 20 if tr == a["dir"] else 8 if tr == 0 else 0
        pts += 10 if reg["spy"] == a["dir"] else 0
        pts += bt_points(stt)
        pts += 10 if rank <= 5 else 5
        earn = earnings_soon(a["sym"], d)
        pts += 0 if earn else 10
        a.update(score=min(100, pts), bt=stt, mc=mc, rank=rank, earn=earn)
        if CFG["NEED_BT"] and stt.get("n", 0) >= 8 and stt["avg"] <= 0:
            continue
        picks.append(a)
    picks.sort(key=lambda x: -x["score"])
    picks = picks[:int(CFG["SIP_SEND"])]
    st["sip"] = {"date": d.isoformat(), "picks": [{"sym": a["sym"], "dir": a["dir"], "entry": a["entry"],
                                                   "stop": a["stop"], "hit": False} for a in picks]}
    st["univers_sip"] = {"lus": len(CFG["SIP"]), "en_jeu": len(cands), "envoyes": len(picks)}
    if not picks:
        telegram("🇺🇸 <b>STOCKS IN PLAY</b> · %s\naucune action « en jeu » qui passe tous les filtres aujourd'hui"
                 % d.strftime("%d/%m"))
        save_state(st)
        return
    FMT[0] = 2
    flat = local_dt(at(d, hm(15, 55) if stock_close_t(d) == hm(16) else hm(12, 55), NY), PARIS).strftime("%H:%M")
    L = ["🇺🇸 <b>STOCKS IN PLAY · ordres du jour</b> · %s" % d.strftime("%d/%m"),
         "Cassure de la 1re bougie 5 min sur les actions au plus fort volume relatif (étude Zarattini-Barbon-Aziz)",
         "🧭 Régime : %s" % esc(reg["label"]), SEP]
    for a in picks:
        dr = a["dir"]
        em, vt = verdict(a["score"])
        risk = abs(a["entry"] - a["stop"])
        L.append("%s <b>%s %s</b> · score %d/100 %s" % ("🟢" if dr > 0 else "🔴", "ACHAT" if dr > 0 else "VENTE",
                                                       esc(a["sym"]), a["score"], bar10(a["score"])))
        L.append("   ordre %s <code>%s</code> · SL <code>%s</code> · TP 3R <code>%s</code>" % (
            "stop achat" if dr > 0 else "stop vente", fmt(a["entry"]), fmt(a["stop"]), fmt(a["entry"] + dr * 3 * risk)))
        bt = a["bt"]
        line = "   volume x%.1f · ATR %.2f $" % (a["rvol"], a["atr"])
        if bt.get("n"):
            line += " · backtest %d trades, %.0f %% gagnants, moy. %+.2f R" % (bt["n"], 100 * bt["win"], bt["avg"])
        if a["mc"]:
            line += " · 10 000 sim. : gagnant %.0f %%" % (100 * a["mc"]["p_win"])
        L.append(esc(line))
        if a["earn"]:
            L.append("   📅 ⚠️ résultats de l'entreprise aujourd'hui/demain")
    L += [SEP, "⏰ Ordres valables jusqu'à 17h00 · sortie de toutes les positions avant %s (Paris)" % flat,
          "🔔 Tu reçois une alerte quand un ordre se déclenche.",
          "<i>Signaux seulement, aucun ordre envoyé · pas un conseil</i>"]
    telegram("\n".join(L))
    count(st, "sip_envoyes")
    save_state(st)


def sip_triggers(st, now_ts):
    """Alerte quand le prix touche l'ordre d'une action en jeu (jusqu'a 11 h NY)."""
    s = st.get("sip") or {}
    d = local_dt(now_ts, NY).date()
    if s.get("date") != d.isoformat():
        return
    for p in s.get("picks", []):
        if p["hit"] or STOP[0]:
            continue
        try:
            bars = [b for b in chart(ysym(p["sym"], "stocks"), "1d", "5m") if b[0] + BAR <= now_ts]
        except Exception:
            continue
        finally:
            time.sleep(CFG["PAUSE"])
        after = [b for b in bars if local_dt(b[0], NY).time() >= hm(9, 35)]
        dr = p["dir"]
        if any((dr > 0 and b[2] >= p["entry"]) or (dr < 0 and b[3] <= p["entry"]) for b in after):
            p["hit"] = True
            stopped = any((dr > 0 and b[3] <= p["stop"]) or (dr < 0 and b[2] >= p["stop"]) for b in after)
            FMT[0] = 2
            telegram("🔔 <b>DÉCLENCHÉ · %s %s</b>\nentrée <code>%s</code> · SL <code>%s</code> · prix actuel <code>%s</code>%s"
                     % ("ACHAT" if dr > 0 else "VENTE", esc(p["sym"]), fmt(p["entry"]), fmt(p["stop"]),
                        fmt(after[-1][4]), "\n⚠️ le SL a déjà été touché : ne pas entrer" if stopped else ""))
            save_state(st)


def day_recap(st):
    d = local_dt(time.time(), PARIS).date().isoformat()
    if st["done"].get("recap") == d:
        return
    st["done"]["recap"] = d
    day = st["day"] if st["day"].get("date") == d else {}
    telegram("📊 <b>RÉSUMÉ DU JOUR</b> · %s\n⚡ Momentum : %d cassures vues, %d envoyées, %d écartées (score / backtest)\n"
             "🇺🇸 Stocks in Play : %s\n🧭 Régime : %s" % (
                 local_dt(time.time(), PARIS).strftime("%d/%m"), day.get("mom_vus", 0), day.get("mom_envoyes", 0),
                 day.get("mom_rejetes", 0), "liste envoyée" if day.get("sip_envoyes") else "pas de séance / rien",
                 esc(REGIME["label"])))
    save_state(st)


# --------------------------------------------------------------- boucle
STOP = [False]


def _stop(*_):
    STOP[0] = True


def in_hours(now):
    g = time.gmtime(now)
    return 6 <= g.tm_hour < 21 or (g.tm_hour == 21 and g.tm_min < 15)


def main():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    if not in_hours(time.time()):
        log.info("hors des heures (6 h - 21 h 15 UTC)")
        return
    st = load_state()
    end = time.time() + CFG["LOOP_MINUTES"] * 60
    try:
        st["diag"] = {"yahoo": "ok : %d bougies SPY" % len(chart("SPY", "5d", "5m")),
                      "openbb": "ok" if openbb() else "absent : %s" % OBB["err"],
                      "regime": regime_update(force=True)["label"]}
    except Exception as ex:
        st["diag"] = {"yahoo": "ERREUR : %s" % str(ex)[:150]}
    log.info("diagnostic : %s", st["diag"])
    save_state(st)
    done_mom = done_5 = 0
    while not STOP[0] and time.time() < end and in_hours(time.time()):
        now = time.time()
        ny = local_dt(now, NY)
        par = local_dt(now, PARIS)
        try:                                      # briefing facon terminal, chaque matin 8 h (Paris)
            if par.time() >= hm(8) and par.time() < hm(11) and st["done"].get("brief") != par.date().isoformat():
                st["done"]["brief"] = par.date().isoformat()
                telegram(briefing())
                save_state(st)
            if stock_open(ny.date()) and hm(9, 36) <= ny.time() < hm(10, 30):
                sip_select(st, now)
            if stock_open(ny.date()) and hm(9, 40) <= ny.time() <= hm(11) and int(now // BAR) != done_5 \
                    and now % BAR >= 40:
                done_5 = int(now // BAR)
                sip_triggers(st, now)
            slot = int(now // 1800)
            if slot != done_mom and now % 1800 >= 45:    # 45 s apres HH:00 / HH:30
                done_mom = slot
                for kind in ("stocks", "fx", "cr"):
                    if STOP[0]:
                        break
                    try:
                        mom_scan(st, kind, time.time())
                    except Exception as ex:
                        log.warning("momentum %s : %s", kind, ex)
                save_state(st)
            if par.time() >= hm(22, 5):
                day_recap(st)
        except Exception as ex:
            log.warning("boucle : %s", ex)
        time.sleep(5)
    if STOP[0] is False and time.time() >= end and in_hours(time.time() + 600):
        try:
            with open(os.path.join(CFG["STATE_DIR"], "relance"), "w") as f:
                f.write("1")
        except OSError:
            pass
    save_state(st)
    log.info("fin (%d requetes, %d erreurs)", STATS["requetes"], STATS["erreurs"])


if __name__ == "__main__":
    main()
