#!/usr/bin/env python3
"""
BOT PRO  -  signaux intraday "niveau Wall Street / Bloomberg"  (Telegram, AUCUN ordre envoye)
=============================================================================================
Trois strategies intraday parmi les mieux documentees par la recherche publiee, un filtre de
regime de marche facon terminal Bloomberg, une validation statistique de chaque signal, un suivi
automatique de chaque signal jusqu'a son resultat (bilan public en R, frais estimes inclus).

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

3. DERNIERE DEMI-HEURE  (ETF indiciels US)
   Gao, Han, Li & Zhou (2018) "Market intraday momentum" (JFE) et Baltussen, Da, Lammers &
   Martens (2021) "Hedging demand and market intraday momentum" (JFE) : le rendement depuis la
   cloture de la veille jusqu'a 15 h 30 (New York) predit le sens de la derniere demi-heure,
   effet renforce par la couverture des dealers d'options (gamma). Sharpe 0.87 a 1.73 selon
   les classes d'actifs, 1974-2020.

COUCHE DESKS INSTITUTIONNELS (facon BlackRock / JPMorgan)
   - ciblage de volatilite (Harvey et al. 2018, Man Group) : risque divise par 2 si VIX >= 25 ;
   - derive pre-FOMC (Lucca & Moench, Fed de New York 2015) : alerte les veilles / jours de Fed ;
   - courbe des taux 10 ans - 3 mois, Fear & Greed actions et crypto, funding Kraken Futures ;
   - pre-ouverture : les plus gros gaps du jour avant l'ouverture de Wall Street.

COUCHE BLOOMBERG
   - briefing du matin : indices (S&P 500, Nasdaq, Dow, DAX, CAC, Nikkei), VIX, dollar (DXY),
     taux 10 ans US, or, petrole, BTC, rotation des secteurs, regime risk-on / risk-off ;
   - filtre de regime : chaque signal est note selon le regime (tendance du marche, peur VIX) ;
   - contexte OpenBB / Yahoo : actualites, resultats d'entreprise, calendrier economique.

VALIDATION DE CHAQUE SIGNAL
   - score de confluence /100 (cassure, tendance 1 h, force relative, volume relatif, regime,
     backtest ; la VWAP est un filtre) ; les actualites sont affichees, pas notees ;
   - backtest de la MEME regle sur l'actif (60 derniers jours de bougies 5 min, frais inclus,
     execution prudente) + intervalle a 90 % du R moyen (bootstrap) ;
   - rejeu : 10 000 tirages de 20 trades passes (information, pas une probabilite du futur).

Donnees : Yahoo Finance (gratuit, non officiel, retard possible). Ce n'est pas un conseil
financier : les resultats passes et les etudes ne garantissent rien.
"""
import datetime
import hashlib
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
        x = float(v.replace(",", "."))
        if not math.isfinite(x):
            raise ValueError("non fini")
        return type(default)(x)
    except (ValueError, OverflowError):
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
MOM_CRYPTO = ("BTC,ETH,SOL,XRP,BNB,DOGE,ADA,AVAX,LINK,LTC,DOT,TRX,BCH,NEAR,XLM,HBAR,ICP,ATOM,ETC,FIL,XMR,ALGO,"
              "VET,EOS,XTZ,AAVE,MKR,INJ,SAND,MANA,AXS,THETA,EGLD,CHZ,CRV,LDO,KAS,SHIB,QNT,SNX,ZEC,"
              "DASH,NEO,KSM,1INCH,BAT,ENJ,ZIL,FET,BSV,HNT,KAVA,ROSE,CELO,ONE,QTUM,"
              "SUI,APT,ARB,OP,TIA,SEI,TON,PEPE,WIF,BONK,JUP,RENDER,ENA,WLD,ONDO,TAO,PYTH,STX,IMX,POL")


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
    "SIMPLE": envf("SIMPLE", 1),                # 1 = messages en francais simple (debutant), 0 = version detaillee
    "MAX_SAME": envf("MAX_SAME", 3),            # garde-fou de correlation : max signaux meme sens / classe / passage
    "CAPITAL": envf("CAPITAL", 0.0),            # capital (devise du compte) pour la taille de position (0 = formule)
    "RISK_PCT": envf("RISK_PCT", 1.0),          # risque par trade en % du capital
    "LH_STOCKS": _lst("LH_STOCKS", "SPY,QQQ,IWM,DIA"),
    "LH_MIN": envf("LH_MIN", 0.004),            # derniere 1/2 h : mouvement mini depuis la cloture de la veille
    "VOL_CUT": envf("VOL_CUT", 25.0),           # ciblage de volatilite : risque / 2 au-dessus de ce VIX
    "LH_MAX": envf("LH_MAX", 1),                # derniere 1/2 h : SPY/QQQ/IWM/DIA sont correles a ~0.9 -> 1 seul
    "COST_BPS_STOCKS": envf("COST_BPS_STOCKS", 3.0),   # frais + ecart achat/vente aller-retour (points de base)
    "COST_BPS_FX": envf("COST_BPS_FX", 1.5),
    "COST_BPS_CR": envf("COST_BPS_CR", 10.0),
    "ACCOUNT_CCY": envf("ACCOUNT_CCY", "EUR"),  # devise du capital (EUR ou USD)
    "MAX_LEV": envf("MAX_LEV", 4.0),            # exposition maxi par signal = 4 x capital (comme l'etude)
    "LOOP_MINUTES": min(envf("LOOP_MINUTES", 320), 320),
    "STATE_DIR": envf("STATE_DIR", "."),
    "PAUSE": envf("PAUSE", 0.2),
}
for _k, _lo in (("SIP_ATR_STOP", 0.01), ("RISK_PCT", 0.01), ("LOOP_MINUTES", 5), ("MAX_SAME", 1), ("SIMS", 1000),
                ("LH_MAX", 1), ("MAX_LEV", 0.1)):
    CFG[_k] = max(CFG[_k], type(CFG[_k])(_lo))
CFG["CAPITAL"] = max(0.0, CFG["CAPITAL"])
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
# jours d'annonce du FOMC (communique 14 h New York) - calendrier officiel 2026, provisoire 2027
FOMC_DAYS = {
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28",
    "2026-12-09", "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09", "2027-07-28", "2027-09-15",
    "2027-10-27", "2027-12-08",
}


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
    if sym in KR_SYM:
        return "%s (Kraken perp %s)" % (sym, KR_SYM[sym])
    return {"XAUUSD": "XAUUSD (or)", "XAGUSD": "XAGUSD (argent)"}.get(sym, sym)


KR_SYM = {}          # crypto -> contrat perpetuel Kraken (rempli au demarrage : PF_XBTUSD, PF_SOLUSD...)


ETF_US = {"SPY", "QQQ", "IWM", "DIA", "SMH", "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE", "XLC"}


def cat_tag(kind, sym=""):
    """Bandeau de categorie, toujours en 1re ligne : on voit tout de suite crypto / action / forex."""
    if kind == "cr":
        return "🪙 <b>CRYPTO</b>"
    if kind == "fx":
        return "🥇 <b>FOREX · MÉTAUX</b>" if sym in ("XAUUSD", "XAGUSD") else "💱 <b>FOREX</b>"
    return "📈 <b>ETF US</b>" if sym.upper() in ETF_US else "📈 <b>ACTION US</b>"


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
        if None in (o[i], h[i], l[i], c[i]) or min(o[i], h[i], l[i], c[i]) <= 0:
            continue
        out[int(ts)] = (int(ts), float(o[i]), float(h[i]), float(l[i]), float(c[i]),
                        float((v[i] if i < len(v) else 0) or 0))
    return [out[k] for k in sorted(out)]


def chart(sym, rng, interval, prepost=False, meta=False):
    last = None
    for _ in range(2):
        for host in HOSTS:
            try:
                STATS["requetes"] += 1
                prm = {"range": rng, "interval": interval}
                if prepost:
                    prm["includePrePost"] = "true"
                r = SESSION.get("%s/v8/finance/chart/%s" % (host, sym), params=prm, timeout=20)
                if r.status_code == 429 or r.status_code >= 500:
                    last = "HTTP %d" % r.status_code
                    if r.status_code == 429:
                        backoff(r)
                    continue
                res = ((r.json().get("chart") or {}).get("result") or [None])[0]
                if not res:
                    raise ValueError("symbole inconnu ou sans donnees")
                bars = parse_result(res)
                if interval == "5m":
                    bars = [b for b in bars if b[0] % BAR == 0]   # bougie en cours (horodatage non aligne)
                return (bars, res.get("meta") or {}) if meta else bars
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
    """Reechantillonnage (bootstrap) du backtest : 20 trades passes rejoues au hasard, `sims` fois,
       + intervalle a 90 % du R moyen. C'est un rejeu du passe, pas une probabilite du futur."""
    if len(rs) < 8:
        return None
    rng = random.Random(seed)
    tots, dds, means = [], [], []
    for _ in range(int(sims)):
        eq = peak = dd = 0.0
        for _ in range(n_next):
            eq += rs[int(rng.random() * len(rs))]
            peak = max(peak, eq)
            dd = max(dd, peak - eq)
        tots.append(eq)
        dds.append(dd)
    for _ in range(max(1000, int(sims) // 5)):
        means.append(sum(rs[int(rng.random() * len(rs))] for _ in range(len(rs))) / len(rs))
    tots.sort()
    dds.sort()
    means.sort()
    return {"p_win": sum(1 for x in tots if x > 0) / len(tots), "med": tots[len(tots) // 2],
            "p5": tots[int(0.05 * len(tots))], "dd95": dds[int(0.95 * len(dds))], "n": n_next,
            "sims": int(sims), "m5": means[int(0.05 * len(means))], "m95": means[int(0.95 * len(means))]}


def cost_r(kind, entry, risk):
    """Frais + ecart achat/vente aller-retour, exprimes en R."""
    bps = {"stocks": CFG["COST_BPS_STOCKS"], "fx": CFG["COST_BPS_FX"], "cr": CFG["COST_BPS_CR"]}.get(kind, 5.0)
    return entry * bps / 1e4 / risk if risk > 0 else 0.0


def nb(n):
    return "{:,}".format(int(n)).replace(",", " ")


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
    """{decalage depuis le debut du jour (s): bougie} pour la journee x (cache par contenu, plafonne)."""
    bs = days[x]
    key = (kind, x, bs[0], bs[-1], len(bs)) if bs else (kind, x)
    m = _OFF.get(key)
    if m is None:
        if len(_OFF) > 6000:
            _OFF.clear()
        tz, start_t = MOM_MARKETS[kind][0], MOM_MARKETS[kind][1]
        t0 = at(x, start_t, tz)
        m = {b[0] - t0: b for b in bs}
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
    if kind == "stocks" and stock_close_t(d) != hm(16):          # demi-seance (clôture 13 h)
        last_t, flat_t = hm(12, 30), hm(12, 55)
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
                if (px - stop) * dr <= px * max(0.0003, 4 * cost_r(kind, 1.0, 1.0)):
                    continue                      # stop trop serre : les frais depasseraient 0.25 R
                seen.add(dr)
                out.append({"dir": dr, "k": k, "ts": close_ts, "price": px, "upper": up, "lower": lo,
                            "vwap": vw, "stop": stop, "sigma": sig, "t_flat": t_flat, "kind": kind,
                            "hi0": max(o, pc), "lo0": min(o, pc)})
    return out


def trail_stop(days, d, kind, rec, j, stop, dr):
    """Stop suiveur de l'etude : max(borne SUPERIEURE ACTUELLE, VWAP) a l'achat (min(borne inf., VWAP)
       a la vente), recalcule a chaque HH:00 / HH:30 ; il ne recule jamais."""
    bs = days[d]
    tz, start_t = MOM_MARKETS[kind][0], MOM_MARKETS[kind][1]
    sig = noise_sigma(days, d, bs[j][0] - at(d, start_t, tz), kind)
    up = rec["hi0"] * (1 + sig) if sig and rec.get("hi0") else rec["upper"]
    lo = rec["lo0"] * (1 - sig) if sig and rec.get("lo0") else rec["lower"]
    vw = vwap(bs[:j + 1])
    new = max(up, vw) if dr > 0 else min(lo, vw)
    return max(stop, new) if dr > 0 else min(stop, new)


def mom_exit(days, d, s):
    """Sortie de la regle : stop verifie sur chaque bougie 5 min (gap : execution a l'ouverture),
       stop suiveur mis a jour toutes les 30 min (si le nouveau stop est deja franchi : sortie a la
       cloture de la bougie), sortie en fin de seance. Resultat en R (risque initial)."""
    bs = days[d]
    dr, e, stop = s["dir"], s["price"], s["stop"]
    kind = s.get("kind", "stocks")
    risk = (e - stop) * dr
    tz = MOM_MARKETS[kind][0]
    last = e
    for j in range(s["k"] + 1, len(bs)):
        b = bs[j]
        if b[0] + BAR > s["t_flat"]:
            break
        last = b[4]
        if dr > 0 and b[3] <= stop:
            return (min(b[1], stop) - e) / risk
        if dr < 0 and b[2] >= stop:
            return (e - max(b[1], stop)) / risk
        if local_dt(b[0] + BAR, tz).minute % 30 == 0:
            stop = trail_stop(days, d, kind, s, j, stop, dr)
            if (b[4] - stop) * dr <= 0:
                return ((b[4] - e) * dr) / risk
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
                r = max(-1.5, mom_exit(days, d, s))          # gap au-dela du stop borne a -1.5 R
                rs.append(r - cost_r(kind, s["price"], abs(s["price"] - s["stop"])))
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


SIP_ORDER_END = hm(15, 30)        # ordres stop valables jusqu'a 15 h 30 New York (suivis en direct)


def sip_backtest(days, d, atr_frac):
    """La regle sur les jours precedents (volume relatif >= seuil ce jour-la), en R, frais inclus.
       Prudent : si la bougie d'entree touche aussi le stop, on compte -1 R ; gap : execution a l'ouverture."""
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
        vo = [days[y][0][5] for y in p14 if local_dt(days[y][0][0], NY).time() == hm(9, 30)]
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
        c = stock_close_t(x)
        t_exit = at(x, hm(15, 55) if c == hm(16) else hm(12, 55), NY)
        t_order = at(x, SIP_ORDER_END if c == hm(16) else hm(12, 30), NY)
        filled, res = False, None
        for b in bs[1:]:
            if b[0] + BAR > t_exit:
                break
            if not filled:
                if b[0] >= t_order:
                    break
                if (dr > 0 and b[2] >= e) or (dr < 0 and b[3] <= e):
                    filled = True
                    if (dr > 0 and b[3] <= stop) or (dr < 0 and b[2] >= stop):
                        res = -1.0                # ordre des prix inconnu dans la bougie : prudent
                        break
                    res = (b[4] - e) * dr / risk
                continue
            if dr > 0 and b[3] <= stop:
                res = (min(b[1], stop) - e) / risk
                break
            if dr < 0 and b[2] >= stop:
                res = (e - max(b[1], stop)) / risk
                break
            res = (b[4] - e) * dr / risk
        if filled and res is not None:
            rs.append(max(-1.5, res) - cost_r("stocks", e, risk))
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
        if not spy:
            REGIME.update(label="inconnu (données S&P indisponibles)", spy=0, vix=v, ts=time.time() - 3000)
            return REGIME
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


# --------------------------------------------------------------- DESKS INSTITUTIONNELS
def fomc_note(now_ts):
    """Derive pre-FOMC (Lucca & Moench) : le S&P a capte l'essentiel de sa prime dans les 24 h
       precedant les annonces de la Fed (1994-2011). Renvoie un texte la veille et le jour J."""
    d = local_dt(now_ts, NY).date()
    for k, txt in ((0, "aujourd'hui"), (1, "demain")):
        dd = d + datetime.timedelta(days=k)
        if dd.isoformat() in FOMC_DAYS:
            t_ann = at(dd, hm(14), NY)
            h = local_dt(t_ann, PARIS).strftime("%H:%M")
            if now_ts >= t_ann:
                return "🏦 FOMC (Fed) : annonce faite à %s Paris, volatilité possible jusqu'à la clôture" % h
            return "🏦 FOMC (Fed) %s à %s Paris : forte volatilité autour de l'annonce ; le S&P a historiquement monté dans les 24 h avant (Lucca & Moench, Fed de New York)" % (txt, h)
    return None


def _get_json(url, ttl, headers=None):
    def f():
        r = SESSION.get(url, timeout=15, headers=headers or {})
        r.raise_for_status()
        return r.json()
    return _cached("json|" + url, ttl, f)


def fear_greed():
    """Indices Fear & Greed : actions (CNN) et crypto (alternative.me). 0 = peur extreme, 100 = euphorie."""
    out = {}
    j = _get_json("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", 3600,
                  {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                   "Referer": "https://edition.cnn.com/", "Origin": "https://edition.cnn.com"})
    try:
        fg = j["fear_and_greed"]
        out["actions"] = (int(round(float(fg["score"]))), str(fg.get("rating", "")))
    except Exception:
        pass
    j = _get_json("https://api.alternative.me/fng/?limit=1", 3600)
    try:
        x = j["data"][0]
        out["crypto"] = (int(x["value"]), str(x.get("value_classification", "")))
    except Exception:
        pass
    return out


FG_FR = {"extreme fear": "peur extrême", "fear": "peur", "neutral": "neutre", "greed": "avidité",
         "extreme greed": "avidité extrême"}


def kraken_funding():
    """Funding et open interest des perpetuels Kraken Futures (API publique, sans cle)."""
    j = _get_json("https://futures.kraken.com/derivatives/api/v3/tickers", 900)
    out = {}
    for t in ((j or {}).get("tickers") or []) if isinstance(j, dict) else []:
        try:
            sym = str(t.get("symbol", "")).upper()
            if not (sym.startswith("PF_") and sym.endswith("USD")):
                continue
            coin = {"XBT": "BTC", "XDG": "DOGE"}.get(sym[3:-3], sym[3:-3])
            rel = t.get("relativeFundingRate")
            mark = t.get("markPrice") or 0
            oi = t.get("openInterest") or 0
            out[coin] = {"rel": float(rel) if rel is not None else None, "oi_usd": float(oi) * float(mark)}
        except Exception as ex:
            OBB["errors"]["kraken"] = str(ex)[:160]
    return out


# ---- Rapport COT (CFTC) : positions des grands speculateurs (hedge funds, CTA) sur les contrats a terme
COT_CODES = {"EUR": "099741", "JPY": "097741", "GBP": "096742", "CHF": "092741", "CAD": "090741",
             "AUD": "232741", "NZD": "112741", "XAU": "088691", "XAG": "084691"}
COT_NAMES = {"EUR": "euro", "JPY": "yen", "GBP": "livre", "CHF": "franc suisse", "CAD": "dollar canadien",
             "AUD": "dollar australien", "NZD": "dollar néo-zélandais", "XAU": "or", "XAG": "argent"}


def cot_data():
    """{devise: {"net": % de l'open interest, "chg": variation sur 1 semaine (points), "date"}} (cache 6 h).
       Source gratuite : API publique de la CFTC (rapport « Legacy », speculateurs non commerciaux)."""
    codes = ",".join("'%s'" % c for c in COT_CODES.values())
    j = _get_json("https://publicreporting.cftc.gov/resource/6dca-aqww.json?$where=cftc_contract_market_code%20in("
                  + codes + ")&$order=report_date_as_yyyy_mm_dd%20DESC&$limit=60", 6 * 3600)
    rows = j if isinstance(j, list) else []
    by = {}
    for r in rows:
        try:
            code = str(r.get("cftc_contract_market_code"))
            ccy = next(k for k, v in COT_CODES.items() if v == code)
            oi = float(r.get("open_interest_all") or 0)
            net = float(r.get("noncomm_positions_long_all") or 0) - float(r.get("noncomm_positions_short_all") or 0)
            if oi > 0:
                by.setdefault(ccy, []).append((str(r.get("report_date_as_yyyy_mm_dd", ""))[:10], 100 * net / oi))
        except (StopIteration, ValueError, TypeError):
            continue
    out = {}
    for ccy, v in by.items():
        v.sort(reverse=True)
        out[ccy] = {"net": v[0][1], "chg": v[0][1] - v[1][1] if len(v) > 1 else 0.0, "date": v[0][0]}
    return out


def cot_bias(sym):
    """Sens favorise par les grands fonds pour une paire : +1 achat, -1 vente, 0 neutre / inconnu."""
    c = cot_data()
    if sym in ("XAUUSD", "XAGUSD"):
        x = c.get(sym[:3])
        return (1 if x["net"] > 5 else -1 if x["net"] < -5 else 0, x) if x else (0, None)
    if len(sym) != 6:
        return 0, None
    b, q = sym[:3], sym[3:]
    sb = 0.0 if b == "USD" else (c.get(b) or {}).get("net")
    sq = 0.0 if q == "USD" else (c.get(q) or {}).get("net")
    if sb is None or sq is None:
        return 0, None
    d_ = sb - sq
    return (1 if d_ > 5 else -1 if d_ < -5 else 0), {"net": d_}


def cot_line(sym, dr):
    bias, x = cot_bias(sym)
    if not x:
        return None
    if bias == 0:
        return "🏦 Grands fonds (rapport COT) : pas de sens net sur %s" % label(sym)
    return "🏦 Grands fonds (rapport COT) : plutôt %s %s → %s" % (
        "acheteurs" if bias > 0 else "vendeurs", label(sym), "dans ton sens ✅" if bias == dr else "CONTRE toi ⚠️")


def cot_summary():
    c = cot_data()
    if not c:
        return None
    buy = [COT_NAMES[k] for k in COT_CODES if k in c and c[k]["net"] > 5]
    sell = [COT_NAMES[k] for k in COT_CODES if k in c and c[k]["net"] < -5]
    date = max(v["date"] for v in c.values())
    return "🏦 Grands fonds (rapport COT du %s) : ils achètent %s · ils vendent %s" % (
        date, ", ".join(buy) or "rien", ", ".join(sell) or "rien")


# ---- Achats des dirigeants (formulaire 4 de la SEC), via OpenInsider (gratuit)
def insider_buys(sym, days=30):
    """Nombre d'achats d'actions par les dirigeants sur `days` jours et montant total ($). None si indisponible."""
    def f():
        r = SESSION.get("http://openinsider.com/screener", timeout=15, params={
            "s": sym, "fd": days, "xp": 1, "cnt": 100, "sortcol": 0})
        if not r.ok:
            return None
        return parse_openinsider(r.text)
    return _cached("ins|%s|%d" % (sym, days), 6 * 3600, f)


def parse_openinsider(html_text):
    m = re.search(r'<table[^>]*class="tinytable"[^>]*>(.*?)</table>', html_text, re.S)
    if not m:
        return {"n": 0, "usd": 0.0}
    n, usd = 0, 0.0
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.S)[1:]:
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if not any("P - Purchase" in c for c in cells):
            continue
        n += 1
        for c in cells:
            if c.startswith("+$") or c.startswith("$") and "," in c:
                try:
                    usd += float(c.replace("+", "").replace("$", "").replace(",", ""))
                    break
                except ValueError:
                    pass
    return {"n": n, "usd": usd}


def funding_line(coin, dr):
    f = kraken_funding().get(coin)
    if not f or f["rel"] is None:
        return None
    r = f["rel"] * 100
    crowd = ""
    if abs(f["rel"]) >= 5e-5:
        side = 1 if f["rel"] > 0 else -1
        crowd = " · ⚠️ positions %s déjà très chargées" % ("acheteuses" if side > 0 else "vendeuses")
        if side != dr:
            crowd += " (contrarien : favorable)"
    return "💸 Funding Kraken %+.4f %%/h · open interest %.0f M$%s" % (r, f["oi_usd"] / 1e6, crowd)


def yield_curve():
    """Courbe des taux US 10 ans - 3 mois : negative = inversee (signal de recession suivi par les desks)."""
    try:
        t10, t3m = daily_perf("^TNX"), daily_perf("^IRX")
        if t10 and t3m:
            sp = t10["last"] - t3m["last"]
            return "📈 Courbe des taux 10 ans − 3 mois : <b>%+.2f pt</b> %s" % (
                sp, "(INVERSÉE : signal de ralentissement)" if sp < 0 else "(normale)")
    except Exception as ex:
        log.warning("courbe : %s", ex)
    return None


def premarket_gaps(st, now_ts):
    """Pre-ouverture facon desk : les plus gros gaps avant 9 h 30 New York (candidats Stocks in Play)."""
    d = local_dt(now_ts, NY).date()
    if st["done"].get("pre") == d.isoformat():
        return
    st["done"]["pre"] = d.isoformat()
    t_open = at(d, hm(9, 30), NY)
    rows = []
    deadline = time.time() + 300
    for sym in CFG["SIP"]:
        if STOP[0] or time.time() > deadline:
            break
        try:
            bars, meta = chart(ysym(sym, "stocks"), "1d", "5m", prepost=True, meta=True)
        except Exception:
            continue
        finally:
            time.sleep(CFG["PAUSE"])
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        pre = [b for b in bars if at(d, hm(4), NY) <= b[0] < t_open]
        if not prev or not pre:
            continue
        vol = sum(b[5] for b in pre)
        g = pre[-1][4] / prev - 1
        if abs(g) >= 0.02 and vol >= 20000:
            rows.append((g, sym, pre[-1][4], vol))
    st["univers_pre"] = {"lus": len(CFG["SIP"]), "gaps": len(rows)}
    if not rows:
        return
    rows.sort()
    up = [r for r in reversed(rows) if r[0] > 0][:6]
    dn = [r for r in rows if r[0] < 0][:6]
    FMT[0] = 2
    L = ["📈 <b>ACTIONS US</b>", "🌅 <b>AVANT L'OUVERTURE DE WALL STREET</b> · %s" % local_dt(now_ts, PARIS).strftime("%d/%m %H:%M"),
         "Les actions qui ont le plus bougé depuis hier soir (à surveiller, pas encore un signal)", SEP]
    if up:
        L += ["🟢 <b>En forte hausse</b>"] + ["<code>%-5s</code> %s · <code>%s</code> · vol. pré-marché %s" % (
            esc(s_), pct(g), fmt(px), "{:,}".format(int(v)).replace(",", " ")) for g, s_, px, v in up]
    if dn:
        L += ["🔴 <b>En forte baisse</b>"] + ["<code>%-5s</code> %s · <code>%s</code> · vol. pré-marché %s" % (
            esc(s_), pct(g), fmt(px), "{:,}".format(int(v)).replace(",", " ")) for g, s_, px, v in dn]
    fn = fomc_note(now_ts)
    if fn:
        L.append(esc(fn))
    L += [SEP, "<i>Les plus actives seront souvent dans la liste « actions du jour » de %s. Pas un conseil.</i>"
          % local_dt(at(d, hm(9, 36), NY), PARIS).strftime("%H:%M")]
    telegram("\n".join(L))
    save_state(st)


def pct(x):
    return "-" if x is None else "%+.2f %%" % (100 * x)


def briefing():
    FMT[0] = 2
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
    inst = []
    yc = yield_curve()
    if yc:
        inst.append(yc)
    fg = fear_greed()
    if fg:
        inst.append("😨 Fear &amp; Greed : " + " · ".join("%s <b>%d</b> (%s)" % (
            k, v[0], esc(FG_FR.get(v[1].lower(), v[1]))) for k, v in sorted(fg.items())))
    try:
        cs = cot_summary()
    except Exception:
        cs = None
    if cs:
        inst.append(esc(cs))
    fu = kraken_funding()
    fl = ["%s %+.4f %%/h" % (c, fu[c]["rel"] * 100) for c in ("BTC", "ETH", "SOL") if c in fu and fu[c]["rel"] is not None]
    if fl:
        inst.append("💸 Funding perpétuels Kraken : " + esc(" · ".join(fl)))
    fn = fomc_note(time.time())
    if fn:
        inst.append(esc(fn))
    if reg.get("vix") is not None and reg["vix"] >= CFG["VOL_CUT"]:
        inst.append("🛡 VIX ≥ %.0f : risque par trade divisé par 2 (ciblage de volatilité)" % CFG["VOL_CUT"])
    if inst:
        L += [SEP, "🏦 <b>Vue desks institutionnels</b>"] + inst
    try:
        ev = econ_calendar_today()
    except Exception:
        ev = []
    if ev:
        L += [SEP, "📅 <b>Annonces économiques du jour</b> (🔴 fort impact : pas de signal 30 min avant / après)"] + [esc(x) for x in ev[:10]]
    else:
        ev = econ_today()
        if ev:
            L += [SEP, "🏦 <b>Agenda économique (Paris)</b>"] + [esc(x) for x in ev[:8]]
    if CFG["SIMPLE"]:
        L += [SEP, "🧭 <b>Aujourd'hui : %s</b>" % esc(market_simple(reg["label"])),
              "<i>Marché en hausse : les achats sont favorisés. En baisse : les ventes. Pas un conseil.</i>"]
    else:
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
    OBB["cache"][key] = (time.time() - ttl + 120 if val is None else time.time(), val)
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


# ---- Calendrier economique (ForexFactory, gratuit) : annonces a fort impact + pause des signaux
FLAGS = {"USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵", "CHF": "🇨🇭", "CAD": "🇨🇦", "AUD": "🇦🇺",
         "NZD": "🇳🇿", "CNY": "🇨🇳"}


def ff_events():
    """Annonces de la semaine : [(ts, devise, titre, impact, prevu, precedent)] (cache 1 h)."""
    j = _get_json("https://nfs.faireconomy.media/ff_calendar_thisweek.json", 3600)
    out = []
    for e in j if isinstance(j, list) else []:
        try:
            ts = int(datetime.datetime.fromisoformat(str(e["date"])).timestamp())
            out.append((ts, str(e.get("country", "")).upper(), str(e.get("title", ""))[:60], str(e.get("impact", "")),
                        str(e.get("forecast") or ""), str(e.get("previous") or "")))
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(out)


def sym_ccys(kind, sym):
    if kind == "fx" and len(sym) == 6 and not sym.startswith("XA"):
        return {sym[:3], sym[3:]}
    return {"USD"}                                # actions US, or, argent, crypto : surtout les annonces US


def news_pause(kind, sym, ts, before=1800, after=1800):
    """Annonce a FORT impact sur une devise concernee entre ts-30 min et ts+30 min -> pas de signal."""
    cc = sym_ccys(kind, sym)
    for t, c, title, imp, _f, _p in ff_events():
        if imp == "High" and c in cc and ts - after <= t <= ts + before:
            return "%s %s %s" % (local_dt(t, PARIS).strftime("%H:%M"), c, title)
    return None


def next_event_line(kind, sym, ts, horizon=3 * 3600):
    cc = sym_ccys(kind, sym)
    for t, c, title, imp, _f, _p in ff_events():
        if imp == "High" and c in cc and ts < t <= ts + horizon:
            return "📅 Annonce importante à %s : %s %s · prudence, le prix peut bouger fort" % (
                local_dt(t, PARIS).strftime("%H:%M"), c, title)
    return None


def econ_calendar_today():
    d = local_dt(time.time(), PARIS).date()
    out = []
    for t, c, title, imp, fc, pv in ff_events():
        if local_dt(t, PARIS).date() != d or imp not in ("High", "Medium"):
            continue
        out.append("%s %s %s %s%s%s" % (local_dt(t, PARIS).strftime("%H:%M"), "🔴" if imp == "High" else "🟠",
                                        FLAGS.get(c, c), title, " · prévu %s" % fc if fc else "",
                                        " · avant %s" % pv if pv else ""))
    return out


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


CHAT_RES = {"id": None, "how": "", "ts": 0, "pair_ts": 0, "offset": 0}
# Appairage : envoyer ce code (prive, groupe ou canal ou le bot est admin) fixe la destination des
# messages. Seule l'empreinte SHA-256 du code est dans le depot public.
PAIR_HASH = "421400dea6994bedd848d60bc1b10ef0794b39c306ad35ee34487422a8b35fe1"


def _pair_key():
    tok = os.environ.get("TELEGRAM_BOT_TOKEN") or ""
    return int(hashlib.sha256(("pair|" + tok).encode()).hexdigest()[:16], 16)


def pair_encode(cid):
    n = int(cid)
    return ("-" if n < 0 else "") + "%x" % (abs(n) ^ _pair_key())


def pair_decode(v):
    try:
        neg = v.startswith("-")
        n = int(v.lstrip("-"), 16) ^ _pair_key()
        return str(-n if neg else n)
    except Exception:
        return None


def pair_check(st):
    """Cherche le code d'appairage dans les derniers messages recus par le bot (toutes les minutes)."""
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not tok or time.time() - CHAT_RES["pair_ts"] < 60:
        return False
    CHAT_RES["pair_ts"] = time.time()
    try:
        up = SESSION.get("https://api.telegram.org/bot%s/getUpdates" % tok,
                         params={"limit": 100, "offset": CHAT_RES["offset"]}, timeout=15).json()
    except Exception as ex:
        STATS["appairage"] = "erreur : %s" % str(ex).replace(tok, "***")[:80]
        return False
    if not up.get("ok"):
        STATS["appairage"] = "%s refuse : %s" % (time.strftime("%H:%M", time.gmtime()), str(up.get("description", ""))[:100])
        if "webhook" in str(up.get("description", "")).lower():
            SESSION.get("https://api.telegram.org/bot%s/deleteWebhook" % tok, timeout=10)   # webhook oublie : on le retire
        return False
    res = up.get("result") or []
    kinds = sorted({(u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}).get("chat", {}).get("type", "?")
                    for u in res})
    STATS["appairage"] = "%s : %d message(s) recu(s) %s" % (time.strftime("%H:%M", time.gmtime()), len(res), ",".join(kinds))
    for u in res:
        CHAT_RES["offset"] = max(CHAT_RES["offset"], int(u.get("update_id", 0)) + 1)
        m = u.get("message") or u.get("channel_post") or u.get("edited_message") or u.get("edited_channel_post") or {}
        txt = str(m.get("text") or "").upper()
        for w in re.findall(r"PRO-[A-Z0-9]{8}", txt):
            if hashlib.sha256(w.encode()).hexdigest() == PAIR_HASH and (m.get("chat") or {}).get("id"):
                chat = m["chat"]
                CHAT_RES.update(id=str(chat["id"]), how="appaire par code (%s %s)" % (
                    chat.get("type", ""), str(chat.get("title") or "")[:30]))
                st["tg_pair"] = pair_encode(chat["id"])
                return True
    return False


def resolve_chat():
    """Si TELEGRAM_CHAT_ID est un nom '@xxx' que Telegram refuse, retrouve le numero du chat dans les
       derniers messages recus par le bot (getUpdates), UNIQUEMENT si le nom d'utilisateur ou de canal
       correspond exactement au secret. Le numero reste en memoire, jamais dans l'etat public."""
    tok, c = os.environ.get("TELEGRAM_BOT_TOKEN"), _raw_chat()
    if not tok or not c.startswith("@") or CHAT_RES["id"] or time.time() - CHAT_RES["ts"] < 240:
        return CHAT_RES["id"]
    CHAT_RES["ts"] = time.time()
    name = c[1:].lower()
    try:
        ch = SESSION.get("https://api.telegram.org/bot%s/getChat" % tok, params={"chat_id": c}, timeout=10).json()
        if ch.get("ok"):
            CHAT_RES.update(id=str(ch["result"]["id"]), how="canal %s" % ch["result"].get("type", ""))
            return CHAT_RES["id"]
        up = SESSION.get("https://api.telegram.org/bot%s/getUpdates" % tok, params={"limit": 100}, timeout=15).json()
        for u in reversed(up.get("result") or []):
            m = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
            chat = m.get("chat") or {}
            who = (m.get("from") or {}).get("username") or ""
            if str(chat.get("username") or "").lower() == name or (chat.get("type") == "private" and who.lower() == name):
                CHAT_RES.update(id=str(chat["id"]), how="retrouve (%s)" % chat.get("type", ""))
                break
        else:
            CHAT_RES["how"] = "pas encore : le canal / compte %s est introuvable pour Telegram" % (
                c[:2] + "***" + c[-1:])            # nom masque : le depot est public
    except Exception as ex:
        CHAT_RES["how"] = "erreur : %s" % str(ex).replace(tok, "***")[:60]
    return CHAT_RES["id"]


def _raw_chat():
    raw = os.environ.get("TELEGRAM_CHAT_ID") or ""
    c = raw.strip().strip("\"'`").strip()
    return c.split()[0] if c.startswith("@") else c


def chat_id():
    """TELEGRAM_CHAT_ID nettoye : espaces, guillemets, texte autour (ex. 'chat_id: 123'). Un nom '@xxx'
       est remplace par le numero retrouve (resolve_chat) des qu'il est connu."""
    if CHAT_RES["id"]:
        return CHAT_RES["id"]
    raw = os.environ.get("TELEGRAM_CHAT_ID") or ""
    c = raw.strip().strip("\"'`").strip()
    if c.startswith("@"):
        return c.split()[0]
    m = re.search(r"-?\d{5,}", c)
    return m.group(0) if m else c


def chat_shape():
    raw = os.environ.get("TELEGRAM_CHAT_ID") or ""
    bits = ["%d caracteres" % len(raw)]
    if raw != raw.strip():
        bits.append("espaces / retour a la ligne")
    if any(q in raw for q in "\"'`"):
        bits.append("guillemets")
    if raw.strip().startswith("@"):
        bits.append("commence par @")
    if re.search(r"[A-Za-z]", raw.strip().lstrip("@")):
        bits.append("contient des lettres")
    bits.append("nombre trouve" if re.search(r"-?\d{5,}", raw) else "aucun nombre")
    return ", ".join(bits)


def kraken_check():
    if "BTC" not in KR_SYM:
        return "BTC absent"
    try:
        n = len(kraken_candles(KR_SYM["BTC"], time.time() - 86400, time.time()))
        return "ok : %d bougies 5 min sur 24 h" % n
    except Exception as ex:
        return "ERREUR : %s" % str(ex)[:100]


def telegram_check():
    """Diagnostic sans exposer le jeton ni l'identifiant : nom du bot et acces au chat."""
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), chat_id()
    if not tok or not chat:
        return "secret TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID absent"
    out = []
    try:
        me = SESSION.get("https://api.telegram.org/bot%s/getMe" % tok, timeout=10).json()
        out.append("bot @%s" % (me.get("result") or {}).get("username", "?") if me.get("ok")
                   else "jeton refuse : %s" % str(me.get("description", ""))[:80])
        if me.get("ok"):
            os.environ["BOT_NAME"] = str((me.get("result") or {}).get("username", ""))
        if chat.startswith("@") and resolve_chat():
            chat = CHAT_RES["id"]
            out.append("nom @ remplace par le numero : %s" % CHAT_RES["how"])
        elif chat.startswith("@"):
            out.append("resolution : %s" % CHAT_RES["how"])
        ch = SESSION.get("https://api.telegram.org/bot%s/getChat" % tok, params={"chat_id": chat}, timeout=10).json()
        out.append("chat ok (%s)" % (ch.get("result") or {}).get("type", "?") if ch.get("ok")
                   else "chat refuse : %s" % str(ch.get("description", ""))[:80])
        out.append("identifiant nettoye : %s" % ("groupe/canal (-)" if chat.startswith("-") else "prive"
                                                  if chat.isdigit() else "@canal" if chat.startswith("@") else "INVALIDE"))
        out.append("secret brut : " + chat_shape())
    except Exception as ex:
        out.append("erreur : %s" % str(ex).replace(tok, "***")[:80])
    return " · ".join(out)


def telegram(msg, html=True):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), chat_id()
    log.info("\n" + re.sub(r"📦[^\n]*", "📦 taille : (masquée dans le journal public)", strip_tags(msg)))
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
                try:
                    STATS["telegram_html_refuse"] = str(r.json().get("description", ""))[:120]
                except Exception:
                    pass
                html, msg = False, strip_tags(msg)
                continue
            STATS["telegram_erreurs"] = STATS.get("telegram_erreurs", 0) + 1
            try:
                desc = str(r.json().get("description", ""))[:120]
            except Exception:
                desc = ""
            STATS["telegram_derniere_erreur"] = "%s HTTP %d %s · %s" % (
                time.strftime("%d/%m %H:%M", time.gmtime()), r.status_code, desc, strip_tags(msg)[:40].replace("\n", " "))
            log.warning("telegram : HTTP %d %s", r.status_code, desc)
            if r.status_code in (401, 403, 404):
                return
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
    for k, t in (("alerts", dict), ("done", dict), ("sip", dict), ("signals", list), ("day", dict),
                 ("open", list), ("track", list)):
        if not isinstance(st.get(k), t):
            st[k] = t()
    st["alerts"] = {k: v for k, v in st["alerts"].items() if isinstance(v, (int, float))}
    st["open"] = [o for o in st["open"] if isinstance(o, dict)]
    st["track"] = [x for x in st["track"] if isinstance(x, dict) and isinstance(x.get("r"), (int, float))]
    return st


def save_state(st):
    cut = time.time() - 4 * 86400
    st["alerts"] = {k: v for k, v in st["alerts"].items() if v > cut}
    st["signals"] = st["signals"][-80:]
    st["track"] = st["track"][-1000:]
    st["stats"] = dict(STATS, maj=time.strftime("%d/%m %H:%M UTC", time.gmtime()),
                       symboles_ko=sorted(k for k, v in BAD.items() if v >= 3)[:80])
    os.makedirs(CFG["STATE_DIR"], exist_ok=True)
    p = os.path.join(CFG["STATE_DIR"], STATE_FILE)     # uniquement STATE_DIR (le workflow le copie dans le repo)
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
        L.append("📜 Backtest de la règle sur l'actif (frais inclus) : %d trades · %.0f %% gagnants · moy. %+.2f R · PF %.2f" % (
            st_["n"], 100 * st_["win"], st_["avg"], min(st_["pf"], 99)))
    else:
        L.append("📜 Backtest : pas assez d'historique")
    if mc:
        L.append("📏 R moyen, intervalle 90 %% : [%+.2f ; %+.2f] → %s" % (
            mc["m5"], mc["m95"], "avantage statistique probable" if mc["m5"] > 0 else "avantage NON démontré (échantillon court)"))
        L.append("🎲 %s rejeux de 20 trades passés : positif dans %.0f %% · médiane %+.1f R · pire 5 %% %+.1f R <i>(rejeu du passé, pas une prévision)</i>" % (
            nb(mc["sims"]), 100 * mc["p_win"], mc["med"], mc["p5"]))
    return L


def usd_per(ccy):
    """Valeur de 1 unite de `ccy` en USD (cours Yahoo en cache 1 h)."""
    ccy = ccy.upper()
    if ccy == "USD":
        return 1.0
    direct = ccy in ("EUR", "GBP", "AUD", "NZD")
    y = ("%sUSD=X" % ccy) if direct else ("%s=X" % ccy)

    def f():
        b = chart(y, "5d", "1d")
        return b[-1][4] if b else None
    px = _cached("fx|" + y, 3600, f)
    if not px:
        return None
    return px if direct else 1.0 / px


def size_line(entry, stop, kind, sym=""):
    """Taille de position pour risquer RISK_PCT % du capital (ou la formule si capital non renseigne).
       Ciblage de volatilite : risque / 2 si VIX >= VOL_CUT (actions). Exposition plafonnee a MAX_LEV x capital."""
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    unit = {"stocks": "actions", "fx": "unités de devise", "cr": "unités"}.get(kind, "unités")
    rp = CFG["RISK_PCT"]
    vix = REGIME.get("vix")
    cut = kind == "stocks" and vix is not None and vix >= CFG["VOL_CUT"]
    if cut:
        rp /= 2.0
    note = " · risque ÷ 2 (VIX %.0f, ciblage de volatilité)" % vix if cut else ""
    quote = sym[3:6] if kind == "fx" and len(sym) == 6 else "USD"
    rk = ("%.2f" % risk) if kind == "stocks" else fmt(risk)
    formula = "📦 Taille = (capital × %.1f %%) ÷ %s %s de risque par %s%s" % (
        rp, rk, quote, unit[:-1] if unit.endswith("s") else unit, note)
    if CFG["CAPITAL"] <= 0:
        return formula
    acc = str(CFG["ACCOUNT_CCY"]).upper()
    acc_usd, q_usd = usd_per(acc), usd_per(quote)
    if not acc_usd or not q_usd:
        return formula
    amount = CFG["CAPITAL"] * rp / 100.0                       # en devise du compte
    q = amount * acc_usd / (risk * q_usd)
    cap = CFG["MAX_LEV"] * CFG["CAPITAL"] * acc_usd / (entry * q_usd)
    capped = q > cap
    q = min(q, cap)
    if kind == "stocks":
        if q < 1:
            return "📦 Taille : moins d'1 action pour ce risque (capital trop petit pour ce signal)"
        qs = "%d" % int(q)
    else:
        qs = ("%d" % q) if q >= 10 else ("%.3f" % q if q >= 0.01 else "%.6f" % q)
    lot = "  (%.2f lot)" % (q / 100000.0) if kind == "fx" and q >= 1000 else ""
    sym_ccy = {"EUR": "€", "USD": "$"}.get(acc, acc)
    real = q * risk * q_usd / acc_usd
    return "📦 Taille : <b>%s %s</b>%s pour risquer %.0f %s (%.2f %% du capital)%s%s" % (
        qs, unit, lot, real, sym_ccy, 100 * real / CFG["CAPITAL"], note,
        " · plafonné à %.0f× le capital" % CFG["MAX_LEV"] if capped else "")


def stars(score):
    n = 5 if score >= 90 else 4 if score >= 80 else 3 if score >= 70 else 2 if score >= 60 else 1
    return "⭐" * n + "☆" * (5 - n)


def market_simple(label_):
    l_ = label_.upper()
    if "PANIQUE" in l_:
        return "marché mondial en panique (très risqué)"
    if l_.startswith("RISK-ON"):
        return "marché mondial plutôt en hausse"
    if l_.startswith("RISK-OFF"):
        return "marché mondial plutôt en baisse"
    if l_.startswith("NEUTRE"):
        return "marché mondial hésitant"
    return "marché mondial : inconnu"


def history_simple(sym, stt, mc):
    if not stt.get("n"):
        return ["📊 Pas assez d'historique pour juger cette règle sur %s" % esc(label(sym))]
    won = int(round(stt["win"] * stt["n"]))
    L = ["📊 Ces 60 derniers jours, la même règle sur %s : %d essais, %d gagnés, au total %s" % (
        esc(label(sym)), stt["n"], won, "en GAIN" if stt["avg"] > 0 else "en PERTE")]
    if mc and mc["m5"] > 0:
        L.append("✅ Résultat solide sur l'historique")
    else:
        L.append("⚠️ Avantage pas encore prouvé (peu d'historique) : prudence, petite taille")
    return L


def simple_signal(kind, sym, dr, entry, stop, tp, valid_ts, flat_ts, score, stt, mc, reg, title=None,
                  trailing=False, extra=()):
    """Message pour debutant : que faire, a quel prix, quand sortir. Pas de jargon."""
    risk = abs(entry - stop)
    L = [cat_tag(kind, sym)]
    if title:
        L.append(title)
    L.append("%s <b>%s %s</b>%s" % ("🟢" if dr > 0 else "🔴", "ACHETER" if dr > 0 else "VENDRE", esc(label(sym)),
                                   "" if dr > 0 else "  <i>(tu gagnes si le prix baisse)</i>"))
    L.append(SEP)
    L.append("💵 Prix d'entrée : <code>%s</code>%s" % (
        fmt(entry), "  ·  à faire avant %s" % local_dt(valid_ts, PARIS).strftime("%H:%M") if valid_ts else ""))
    L.append("🛑 Sortie de sécurité (stop) : <code>%s</code>  (perte max %.2f %%)" % (fmt(stop), 100 * risk / entry))
    if tp is not None:
        L.append("🎯 Prendre le gain (TP) : <code>%s</code>  (gain %+.2f %%)" % (fmt(tp), 100 * abs(tp - entry) / entry))
    L.append("⏰ Fermer de toute façon à %s" % local_dt(flat_ts, PARIS).strftime("%H:%M"))
    if trailing:
        L.append("🔁 Je t'envoie un message quand il faut %s ta sortie de sécurité" % ("remonter" if dr > 0 else "descendre"))
    sz = size_line(entry, stop, kind, sym)
    if sz and CFG["CAPITAL"] > 0:
        L.append(sz.replace("📦 Taille :", "📦 Quantité à prendre :"))
    L.append(SEP)
    L.append("Fiabilité : %s  (%d/100)" % (stars(score), score))
    L += history_simple(sym, stt, mc)
    L.append("🧭 " + market_simple(reg.get("label", "")))
    L += [esc(x) for x in extra if x]
    L.append("<i>Signal seulement : c'est toi qui passes l'ordre. Pas un conseil.</i>")
    return "\n".join(L)


def verdict(score):
    if score >= 75:
        return "🟢", "SIGNAL FORT"
    if score >= CFG["MIN_SCORE"]:
        return "🟡", "SIGNAL MOYEN"
    return "🔴", "FAIBLE"


# --------------------------------------------------------------- MOMENTUM : scan
HIST = {}


BAD = {}


def kraken_perps():
    """{crypto: contrat perpetuel Kraken} d'apres l'API publique Kraken Futures (cache 15 min)."""
    j = _get_json("https://futures.kraken.com/derivatives/api/v3/tickers", 900)
    out = {}
    for t in ((j or {}).get("tickers") or []) if isinstance(j, dict) else []:
        sym = str(t.get("symbol", "")).upper()
        if sym.startswith("PF_") and sym.endswith("USD") and not t.get("suspended"):
            out[{"XBT": "BTC", "XDG": "DOGE"}.get(sym[3:-3], sym[3:-3])] = sym
    return out


def kraken_candles(pf, start, end):
    """Bougies 5 min du perpetuel Kraken (prix des transactions), pagine, horodatage en secondes."""
    out, frm = {}, int(start)
    for _ in range(14):
        STATS["requetes"] += 1
        r = SESSION.get("https://futures.kraken.com/api/charts/v1/trade/%s/5m" % pf,
                        params={"from": frm, "to": int(end)}, timeout=20)
        r.raise_for_status()
        j = r.json()
        cs = j.get("candles") or []
        for c in cs:
            ts = int(c["time"]) // 1000
            o, h, l, cl = (float(c[k]) for k in ("open", "high", "low", "close"))
            if ts % BAR == 0 and min(o, h, l, cl) > 0:
                out[ts] = (ts, o, h, l, cl, float(c.get("volume") or 0))
        if not j.get("more_candles") or not cs:
            break
        nxt = int(cs[-1]["time"]) // 1000 + BAR
        if nxt <= frm:
            break
        frm = nxt
    if not out:
        raise RuntimeError("Kraken %s : aucune bougie" % pf)
    return out


def _hist_kraken(sym):
    pf = KR_SYM[sym]
    key = "KR:" + pf
    c = HIST.get(key)
    now = time.time()
    if not c or now - c["full"] > 12 * 3600:
        HIST[key] = {"bars": kraken_candles(pf, now - 60 * 86400, now), "full": now}
    else:
        c["bars"].update(kraken_candles(pf, now - 2 * 86400, now))
    bars = HIST[key]["bars"]
    return [bars[k] for k in sorted(bars)]


def hist(kind, sym):
    """Bougies 5 min (60 jours) en cache, completees a chaque passage par la journee en cours.
       Crypto : prix des PERPETUELS KRAKEN (ce que le trader voit chez Kraken), Yahoo en secours.
       Un symbole introuvable 3 fois de suite est ignore pour le reste du passage (liste dans diag)."""
    if kind == "cr" and sym in KR_SYM:
        try:
            return _hist_kraken(sym)
        except Exception as ex:
            STATS["kraken_secours_yahoo"] = "%s : %s" % (sym, str(ex)[:80])
    y = ysym(sym, kind)
    if BAD.get(y, 0) >= 3 and y not in HIST:
        raise RuntimeError("symbole ignore (introuvable sur Yahoo)")
    try:
        return _hist(kind, y)
    except Exception:
        BAD[y] = BAD.get(y, 0) + 1
        raise


def _hist(kind, y):
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
    if kind == "cr" and KR_SYM:
        syms = [x for x in syms if x in KR_SYM]   # seulement les cryptos tradables en perpetuel chez Kraken
    reg = regime_update()
    sent = 0
    pending = []
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
            try:
                pz = news_pause(kind, sym, s["ts"])
            except Exception:
                pz = None
            if pz:
                count(st, "pause_annonce")
                log.info("%s : pas de signal, annonce economique %s", sym, pz)
                continue
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
            cot_l = None
            if kind == "fx":
                try:
                    cb, _x = cot_bias(sym)
                    cot_l = cot_line(sym, dr)
                except Exception:
                    cb = 0
                if cb == dr:
                    pts += 5
                elif cb == -dr:
                    pts -= 10                     # contre les grands fonds : on penalise
            nw = news(ysym(sym, kind))
            score = max(0, min(100, pts))
            if score < CFG["MIN_SCORE"] or (CFG["NEED_BT"] and (stt.get("n", 0) < 12 or stt["avg"] <= 0)):
                count(st, "mom_rejetes")
                log.info("%s %s : score %d, backtest %s -> non envoye", sym, dr, score, stt)
                continue
            FMT[0] = 2 if kind == "stocks" else 5
            risk = abs(px - s["stop"])
            tp = px + dr * 2 * risk                   # objectif indicatif : la regle etudiee n'a pas de TP
            em, vt = verdict(score)
            flat_p = local_dt(s["t_flat"], PARIS).strftime("%H:%M")
            L = [cat_tag(kind, sym),
                 "%s <b>%s %s</b>" % ("🟢" if dr > 0 else "🔴", "ACHAT" if dr > 0 else "VENTE", esc(label(sym))),
                 "⚡ Momentum intraday (zone de bruit + VWAP)",
                 SEP,
                 "▶️ Entrée    <code>%s</code>" % fmt(px),
                 "🛑 SL        <code>%s</code>  (%+.2f %%)" % (fmt(s["stop"]), -dr * 100 * risk / px),
                 "🎯 TP (take profit) <code>%s</code>  (%+.2f %% · 2× le risque)" % (fmt(tp), dr * 200 * risk / px),
                 "   <i>facultatif : la règle étudiée laisse courir avec le stop suiveur</i>",
                 "🔁 Stop suiveur : toutes les 30 min, SL = %s(borne de la zone, VWAP) ; il ne recule jamais (borne actuelle %s)" % (
                     "max" if dr > 0 else "min", fmt(s["upper"] if dr > 0 else s["lower"])),
                 "⏰ Sortie au plus tard %s · ⌛ valable jusqu'à %s (Paris)" % (
                     flat_p, local_dt(s["ts"] + 600, PARIS).strftime("%H:%M"))]
            sz = size_line(px, s["stop"], kind, sym)
            if sz:
                L.append(sz)
            L += [SEP,
                  "%s <b>%s · score %d/100</b> %s" % (em, vt, score, bar10(score))]
            L += ["%s %s" % ("✅" if p_ >= mx * 0.7 else "➖" if p_ > 0 else "❌", esc(n_)) for n_, p_, mx in why]
            L += bt_lines(stt, mc)
            L.append("🧭 Régime : %s" % esc(reg["label"]))
            if kind == "cr":
                try:
                    fl_ = funding_line(sym.split("-")[0], dr)
                except Exception:
                    fl_ = None
                if fl_:
                    L.append(esc(fl_))
            if cot_l:
                L.append(esc(cot_l))
            fn = fomc_note(now_ts) if kind != "cr" else None
            if fn:
                L.append(esc(fn))
            for t in nw:
                L.append("📰 " + esc(t[:110]))
            L.append("<i>Signal seulement, aucun ordre envoyé · pas un conseil</i>")
            if CFG["SIMPLE"]:
                extra = []
                if kind == "cr":
                    try:
                        f_ = kraken_funding().get(sym.split("-")[0])
                        if f_ and f_["rel"] is not None and abs(f_["rel"]) >= 5e-5 and (1 if f_["rel"] > 0 else -1) == dr:
                            extra.append("⚠️ Beaucoup de traders sont déjà dans ce sens : risque de retournement")
                    except Exception:
                        pass
                if cot_l:
                    extra.append(cot_l)
                try:
                    ne_ = next_event_line(kind, sym, now_ts)
                except Exception:
                    ne_ = None
                if ne_:
                    extra.append(ne_)
                fn_ = fomc_note(now_ts) if kind != "cr" else None
                if fn_:
                    extra.append("🏦 Annonce de la banque centrale US (Fed) proche : mouvements violents possibles")
                L = [simple_signal(kind, sym, dr, px, s["stop"], tp, s["ts"] + 600, s["t_flat"], score, stt, mc, reg,
                                   trailing=True, extra=extra)]
            pending.append((score, dr, sym, "\n".join(L), {
                "strat": "momentum", "kind": kind, "sym": sym, "dir": dr, "entry": px, "stop": s["stop"],
                "upper": s["upper"], "lower": s["lower"], "hi0": s["hi0"], "lo0": s["lo0"], "t_flat": s["t_flat"],
                "day": d.isoformat(), "sent": s["ts"], "score": score, "tp": tp}))
    # garde-fou de correlation : au plus MAX_SAME positions dans le meme sens par classe d'actifs,
    # positions encore ouvertes comprises (hors liste Stocks in Play)
    pending.sort(key=lambda x: -x[0])
    per_dir = {dd: sum(1 for o in st["open"] if o.get("kind") == kind and o.get("dir") == dd
                       and o.get("strat") != "stocks in play") for dd in (1, -1)}
    for score, dr, sym, msg, rec in pending:
        if per_dir[dr] >= CFG["MAX_SAME"]:
            count(st, "mom_correles")
            log.info("%s : ecarte (deja %d signaux %s correles sur %s)", sym, per_dir[dr], dr, kind)
            continue
        per_dir[dr] += 1
        telegram(msg)
        sent += 1
        count(st, "mom_envoyes")
        st["open"].append(rec)
        st["signals"].append({"t": time.strftime("%d/%m %H:%M", time.gmtime()), "strat": "momentum",
                              "sym": sym, "sens": "ACHAT" if dr > 0 else "VENTE", "entree": rec["entry"],
                              "sl": rec["stop"], "score": score})
        save_state(st)
    return sent


# --------------------------------------------------------------- STOCKS IN PLAY : selection
def sip_select(st, now_ts):
    d = local_dt(now_ts, NY).date()
    if st["done"].get("sip") == d.isoformat():
        return
    reg = regime_update()
    cands = []
    fails = 0
    deadline = time.time() + 420
    for sym in CFG["SIP"]:
        if STOP[0] or time.time() > deadline:
            break
        try:
            bars = chart(ysym(sym, "stocks"), "60d", "5m")
        except Exception as ex:
            fails += 1
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
    if fails > len(CFG["SIP"]) // 2 and local_dt(time.time(), NY).time() < hm(10, 15):
        log.warning("SIP : %d echecs Yahoo, nouvel essai plus tard", fails)
        return                                    # panne de donnees : on reessaie (done non pose)
    st["done"]["sip"] = d.isoformat()
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
        try:
            ins = insider_buys(a["sym"])
        except Exception:
            ins = None
        if ins and ins["n"] and a["dir"] > 0:
            pts += 5                              # les dirigeants achetent : signal de confiance
        a.update(score=min(100, pts), bt=stt, mc=mc, rank=rank, earn=earn, ins=ins)
        if a["score"] < CFG["MIN_SCORE"] or (CFG["NEED_BT"] and (stt.get("n", 0) < 12 or stt["avg"] <= 0)):
            count(st, "sip_rejetes")
            continue
        picks.append(a)
    picks.sort(key=lambda x: -x["score"])
    picks = picks[:int(CFG["SIP_SEND"])]
    st["sip"] = {"date": d.isoformat(), "sent": now_ts,
                 "picks": [{"sym": a["sym"], "dir": a["dir"], "entry": a["entry"], "stop": a["stop"],
                            "hit": False, "score": a["score"]} for a in picks]}
    st["univers_sip"] = {"lus": len(CFG["SIP"]), "en_jeu": len(cands), "envoyes": len(picks)}
    if not picks:
        telegram("🇺🇸 <b>STOCKS IN PLAY</b> · %s\naucune action « en jeu » qui passe tous les filtres aujourd'hui"
                 % d.strftime("%d/%m"))
        save_state(st)
        return
    FMT[0] = 2
    flat = local_dt(at(d, hm(15, 55) if stock_close_t(d) == hm(16) else hm(12, 55), NY), PARIS).strftime("%H:%M")
    L = ["📈 <b>ACTIONS US</b>", "🇺🇸 <b>STOCKS IN PLAY · ordres du jour</b> · %s" % d.strftime("%d/%m"),
         "Cassure de la 1re bougie 5 min sur les actions au plus fort volume relatif (étude Zarattini-Barbon-Aziz)",
         "🧭 Régime : %s" % esc(reg["label"]), SEP]
    for a in picks:
        dr = a["dir"]
        em, vt = verdict(a["score"])
        risk = abs(a["entry"] - a["stop"])
        L.append("%s <b>%s %s</b> · score %d/100 %s" % ("🟢" if dr > 0 else "🔴", "ACHAT" if dr > 0 else "VENTE",
                                                       esc(a["sym"]), a["score"], bar10(a["score"])))
        L.append("   ordre %s <code>%s</code> · SL <code>%s</code> · TP <code>%s</code> (3× le risque, facultatif)" % (
            "stop achat" if dr > 0 else "stop vente", fmt(a["entry"]), fmt(a["stop"]), fmt(a["entry"] + dr * 3 * risk)))
        sz = size_line(a["entry"], a["stop"], "stocks")
        if sz:
            L.append("   " + sz)
        bt = a["bt"]
        line = "   volume x%.1f · ATR %.2f $" % (a["rvol"], a["atr"])
        if bt.get("n"):
            line += " · backtest %d trades, %.0f %% gagnants, moy. %+.2f R (frais inclus)" % (bt["n"], 100 * bt["win"], bt["avg"])
        if a["mc"]:
            line += " · R moyen IC 90 %% [%+.2f ; %+.2f]" % (a["mc"]["m5"], a["mc"]["m95"])
        L.append(esc(line))
        if a["earn"]:
            L.append("   📅 ⚠️ résultats de l'entreprise aujourd'hui/demain")
    t_ord = local_dt(at(d, SIP_ORDER_END if stock_close_t(d) == hm(16) else hm(12, 30), NY), PARIS).strftime("%H:%M")
    if CFG["SIMPLE"]:
        L = ["📈 <b>ACTIONS US</b>", "🇺🇸 <b>ACTIONS DU JOUR</b> · %s" % d.strftime("%d/%m"),
             "Les actions qui bougent le plus ce matin à Wall Street.",
             "Place un <b>ordre qui se déclenche seulement si le prix atteint le niveau</b> indiqué (ordre « stop »).",
             "🧭 " + market_simple(reg["label"])]
        try:
            ne_ = next_event_line("stocks", "SPY", now_ts)
        except Exception:
            ne_ = None
        if ne_:
            L.append(esc(ne_))
        L.append(SEP)
        for a in picks:
            dr = a["dir"]
            risk = abs(a["entry"] - a["stop"])
            L.append("%s <b>%s %s</b>  %s" % ("🟢" if dr > 0 else "🔴", "ACHETER" if dr > 0 else "VENDRE", esc(a["sym"]),
                                             stars(a["score"])))
            L.append("   si le prix %s à <code>%s</code> · sortie de sécurité <code>%s</code> · prendre le gain <code>%s</code>" % (
                "MONTE" if dr > 0 else "DESCEND", fmt(a["entry"]), fmt(a["stop"]), fmt(a["entry"] + dr * 3 * risk)))
            sz = size_line(a["entry"], a["stop"], "stocks")
            if sz and CFG["CAPITAL"] > 0:
                L.append("   " + sz.replace("📦 Taille :", "📦 Quantité :"))
            bt = a["bt"]
            if bt.get("n"):
                L.append("   📊 60 derniers jours : %d essais, au total %s%s" % (
                    bt["n"], "en GAIN" if bt["avg"] > 0 else "en PERTE",
                    "" if a["mc"] and a["mc"]["m5"] > 0 else " (pas encore prouvé)"))
            if a.get("ins") and a["ins"]["n"]:
                L.append("   👔 Les dirigeants ont acheté des actions : %d achat(s) en 30 jours (%s $)" % (
                    a["ins"]["n"], nb(a["ins"]["usd"])))
            if a["earn"]:
                L.append("   ⚠️ l'entreprise publie ses résultats aujourd'hui ou demain : très risqué")
        L += [SEP, "⏰ Ordres à annuler s'ils ne sont pas déclenchés à %s · tout fermer à %s (Paris)" % (t_ord, flat),
              "🔔 Je t'envoie un message dès qu'un ordre se déclenche.",
              "<i>Signaux seulement : c'est toi qui passes les ordres. Pas un conseil.</i>"]
        telegram("\n".join(L))
        count(st, "sip_envoyes")
        save_state(st)
        return
    L += [SEP, "⏰ Ordres valables jusqu'à %s · sortie de toutes les positions à %s (Paris) · pas de TP dans l'étude" % (t_ord, flat),
          "🔔 Tu reçois une alerte quand un ordre se déclenche.",
          "<i>Signaux seulement, aucun ordre envoyé · pas un conseil</i>"]
    telegram("\n".join(L))
    count(st, "sip_envoyes")
    save_state(st)


def sip_triggers(st, now_ts):
    """Alerte quand le prix touche l'ordre d'une action en jeu (jusqu'a 15 h 30 NY). Chaque ordre
       declenche est suivi jusqu'au resultat, y compris s'il est deja stoppe (-1 R compte au bilan)."""
    s = st.get("sip") or {}
    d = local_dt(now_ts, NY).date()
    if s.get("date") != d.isoformat():
        return
    t_order = at(d, SIP_ORDER_END if stock_close_t(d) == hm(16) else hm(12, 30), NY)
    for p in s.get("picks", []):
        if p["hit"] or STOP[0]:
            continue
        try:
            bars = [b for b in chart(ysym(p["sym"], "stocks"), "1d", "5m") if b[0] + BAR <= now_ts]
        except Exception:
            continue
        finally:
            time.sleep(CFG["PAUSE"])
        after = [b for b in bars if b[0] >= at(d, hm(9, 35), NY) and b[0] < t_order]   # seance du jour seulement
        dr = p["dir"]
        trig = [b for b in after if (dr > 0 and b[2] >= p["entry"]) or (dr < 0 and b[3] <= p["entry"])]
        if not trig:
            if now_ts >= t_order:
                p["hit"] = True                   # ordre expire sans declenchement
            continue
        p["hit"] = True
        k0 = after.index(trig[0])
        stopped = any((dr > 0 and b[3] <= p["stop"]) or (dr < 0 and b[2] >= p["stop"]) for b in after[k0:])
        missed = trig[0][0] + BAR <= s.get("sent", 0)        # declenche avant l'envoi de la liste
        if not missed:
            st["open"].append({"strat": "stocks in play", "kind": "stocks", "sym": p["sym"], "dir": dr,
                               "entry": p["entry"], "stop": p["stop"], "first_bar_stop": True,
                               "tp": p["entry"] + dr * 3 * abs(p["entry"] - p["stop"]),
                               "t_flat": at(d, hm(15, 55) if stock_close_t(d) == hm(16) else hm(12, 55), NY),
                               "day": d.isoformat(), "sent": trig[0][0], "score": p.get("score", 0)})
        FMT[0] = 2
        extra = ""
        if missed:
            extra = "\nℹ️ déclenché avant l'envoi de la liste : entrée manquée, non comptée au bilan"
        elif stopped:
            extra = "\n⚠️ le SL a déjà été touché après l'entrée : ne pas entrer maintenant (compté −1 R au bilan)"
        if CFG["SIMPLE"]:
            extra = ""
            if missed:
                extra = "\nℹ️ C'est arrivé avant ma liste : trop tard pour entrer, on ignore."
            elif stopped:
                extra = "\n⚠️ Le prix est déjà revenu toucher la sortie de sécurité : n'entre PAS maintenant."
            telegram(cat_tag("stocks", p["sym"]) + "\n🔔 <b>%s : %s déclenché</b>\nLe prix a atteint <code>%s</code>. "
                     "Sortie de sécurité : <code>%s</code>. Prix actuel : <code>%s</code>.%s"
                     % (esc(p["sym"]), "achat" if dr > 0 else "vente", fmt(p["entry"]), fmt(p["stop"]),
                        fmt(after[-1][4]), extra))
            save_state(st)
            continue
        telegram(cat_tag("stocks", p["sym"]) + "\n🔔 <b>DÉCLENCHÉ · %s %s</b>\nentrée <code>%s</code> · SL <code>%s</code> · prix actuel <code>%s</code>%s"
                 % ("ACHAT" if dr > 0 else "VENTE", esc(p["sym"]), fmt(p["entry"]), fmt(p["stop"]),
                    fmt(after[-1][4]), extra))
        save_state(st)


# --------------------------------------------------------------- DERNIERE DEMI-HEURE
def lh_times(d):
    c = stock_close_t(d)
    dec = (datetime.datetime.combine(d, c) - datetime.timedelta(minutes=30)).time()
    return at(d, dec, NY), at(d, c, NY)


def lh_trade(day_bars, prev_close, d, mean_lh, k_stop=2.0):
    """Une seance : rendement veille -> 15 h 30 (rROD) puis derniere demi-heure, stop a k x |move moyen|."""
    t_dec, t_close = lh_times(d)
    before = [b for b in day_bars if b[0] + BAR <= t_dec]
    last = [b for b in day_bars if t_dec <= b[0] and b[0] + BAR <= t_close]
    if not before or not prev_close or not mean_lh:
        return None
    entry = before[-1][4]
    rod = entry / prev_close - 1
    if abs(rod) < CFG["LH_MIN"]:
        return None
    dr = 1 if rod > 0 else -1
    risk = entry * mean_lh * k_stop
    stop = entry - dr * risk
    out = {"dir": dr, "entry": entry, "stop": stop, "rod": rod, "risk": risk, "r": None,
           "last_ts": before[-1][0] + BAR}
    for b in last:
        if dr > 0 and b[3] <= stop:
            out["r"] = (min(b[1], stop) - entry) / risk - cost_r("stocks", entry, risk)
            return out
        if dr < 0 and b[2] >= stop:
            out["r"] = (entry - max(b[1], stop)) / risk - cost_r("stocks", entry, risk)
            return out
    if last and len(last) >= 5:
        out["r"] = max(-1.5, (last[-1][4] - entry) * dr / risk) - cost_r("stocks", entry, risk)
    return out


def lh_moves(days, keys):
    out = {}
    for x in keys:
        t_dec, t_close = lh_times(x)
        a = [b for b in days[x] if b[0] + BAR <= t_dec]
        z = [b for b in days[x] if b[0] + BAR <= t_close]
        if a and z and len(z) - len(a) >= 5:
            out[x] = abs(z[-1][4] / a[-1][4] - 1)
    return out


def lh_mean_before(moves, d, look=20):
    v = [moves[x] for x in sorted(moves) if x < d][-look:]
    return sum(v) / len(v) if len(v) >= 10 else None


def lh_history(days, before_d):
    """Backtest de la derniere demi-heure sur les seances precedentes. La largeur du stop de chaque
       seance n'utilise que les 20 seances d'AVANT (aucun regard vers le futur)."""
    keys = [x for x in sorted(days) if x < before_d and stock_open(x)]
    moves = lh_moves(days, keys)
    rs = []
    for i in range(1, len(keys)):
        m = lh_mean_before(moves, keys[i])
        tr = lh_trade(days[keys[i]], days[keys[i - 1]][-1][4], keys[i], m)
        if tr and tr["r"] is not None:
            rs.append(tr["r"])
    return rs, lh_mean_before(moves, before_d)


def lh_scan(st, now_ts):
    """Derniere demi-heure : decision a 15 h 30 NY (12 h 30 en demi-seance), essais repetes jusqu'a
       ce que la bougie de 15 h 30 soit publiee (fenetre 9 min). Un seul ETF par sens (correles ~0.9)."""
    d = local_dt(now_ts, NY).date()
    t_dec, t_close = lh_times(d)
    if not (t_dec + 45 <= now_ts < t_dec + 540) or st["done"].get("lh") == d.isoformat():
        return 0
    if int(now_ts // 60) == LHT[0]:
        return 0
    LHT[0] = int(now_ts // 60)                    # au plus un essai par minute
    reg = regime_update()
    FMT[0] = 2
    cands, ready = [], 0
    for sym in CFG["LH_STOCKS"]:
        if STOP[0]:
            break
        try:
            days = by_day([b for b in hist("stocks", sym) if b[0] + BAR <= now_ts], NY, hm(9, 30))
        except Exception as ex:
            log.warning("1/2 h %s : %s", sym, ex)
            continue
        finally:
            time.sleep(CFG["PAUSE"])
        prevs = [x for x in sorted(days) if x < d]
        if d not in days or not prevs:
            continue
        if days[d][-1][0] + BAR < t_dec:
            continue                              # bougie de 15 h 30 pas encore publiee
        ready += 1
        rs, mean_lh = lh_history(days, d)
        tr = lh_trade(days[d], days[prevs[-1]][-1][4], d, mean_lh)
        if not tr or tr["last_ts"] != t_dec:
            continue
        count(st, "lh_vus")
        try:
            if news_pause("stocks", sym, t_dec + 900, before=900, after=1800):
                count(st, "pause_annonce")
                continue
        except Exception:
            pass
        stt, mc = bt_stats(rs), mc_trades(rs, sims=CFG["SIMS"])
        vix = reg.get("vix")
        why = [("Mouvement depuis hier %s" % pct(tr["rod"]), 30 if abs(tr["rod"]) >= 2 * CFG["LH_MIN"] else 18, 30),
               ("Volatilité (couverture gamma des dealers plus forte)", 20 if vix and vix >= 20 else 8, 20),
               ("Sens du régime de marché", 15 if reg.get("spy") == tr["dir"] else 0, 15),
               ("Backtest de l'actif", bt_points(stt) + (15 if stt.get("n", 0) >= 12 and stt["avg"] > 0 else 0), 35)]
        score = min(100, sum(p_ for _, p_, _ in why))
        if score < CFG["MIN_SCORE"] or (CFG["NEED_BT"] and not (stt.get("n", 0) >= 12 and stt["avg"] > 0)):
            count(st, "lh_rejetes")
            continue
        cands.append((score, sym, tr, why, stt, mc))
    if ready < len(CFG["LH_STOCKS"]) and now_ts < t_dec + 420:
        return 0                                  # on retente a la minute suivante
    st["done"]["lh"] = d.isoformat()
    cands.sort(key=lambda x: -x[0])
    per_dir = {dd: sum(1 for o in st["open"] if o.get("strat") == "dernière 1/2 h" and o.get("dir") == dd
                       and o.get("day") == d.isoformat()) for dd in (1, -1)}
    sent = 0
    for score, sym, tr, why, stt, mc in cands:
        dr, e = tr["dir"], tr["entry"]
        if per_dir[dr] >= CFG["LH_MAX"]:
            count(st, "lh_correles")
            continue
        per_dir[dr] += 1
        em, vt = verdict(score)
        if CFG["SIMPLE"]:
            telegram(simple_signal("stocks", sym, dr, e, tr["stop"], None, None, t_close, score, stt, mc, reg,
                                   title="⏱ <b>Dernière demi-heure de Wall Street</b> : entrer maintenant",
                                   extra=["🏦 Annonce de la Fed proche : mouvements violents possibles"] if fomc_note(now_ts) else []))
            sent += 1
            count(st, "lh_envoyes")
            st["open"].append({"strat": "dernière 1/2 h", "kind": "stocks", "sym": sym, "dir": dr, "entry": e,
                               "stop": tr["stop"], "t_flat": t_close, "day": d.isoformat(), "sent": t_dec,
                               "score": score})
            continue
        L = [cat_tag("stocks", sym), "⏱ <b>DERNIÈRE DEMI-HEURE · %s %s</b>" % ("🟢 ACHAT" if dr > 0 else "🔴 VENTE", esc(sym)), SEP,
             "💵 Entrée  <code>%s</code>  (maintenant)" % fmt(e),
             "🛑 SL        <code>%s</code>" % fmt(tr["stop"]),
             "⏰ Sortie à la clôture : %s (Paris) · pas de TP" % local_dt(t_close, PARIS).strftime("%H:%M")]
        sz = size_line(e, tr["stop"], "stocks")
        if sz:
            L.append(sz)
        L += [SEP, "%s <b>%s · score %d/100</b> %s" % (em, vt, score, bar10(score))]
        L += ["%s %s" % ("✅" if p_ >= mx * 0.7 else "➖" if p_ > 0 else "❌", esc(n_)) for n_, p_, mx in why]
        L += bt_lines(stt, mc)
        L.append("📚 Baltussen et al. 2021 (Gao et al. 2018) : le sens de la journée se prolonge souvent dans la dernière demi-heure")
        fn = fomc_note(now_ts)
        if fn:
            L.append(esc(fn))
        L.append("<i>Signal seulement, aucun ordre envoyé · pas un conseil</i>")
        telegram("\n".join(L))
        sent += 1
        count(st, "lh_envoyes")
        st["open"].append({"strat": "dernière 1/2 h", "kind": "stocks", "sym": sym, "dir": dr, "entry": e,
                           "stop": tr["stop"], "t_flat": t_close, "day": d.isoformat(), "sent": t_dec,
                           "score": score})
        st["signals"].append({"t": time.strftime("%d/%m %H:%M", time.gmtime()), "strat": "derniere 1/2 h",
                              "sym": sym, "sens": "ACHAT" if dr > 0 else "VENTE", "entree": e,
                              "sl": tr["stop"], "score": score})
    save_state(st)
    return sent


def _track_one(st, o, now_ts):
    """Suit un signal. Retourne True si le resultat est connu (publie), False s'il reste ouvert."""
    kind, dr = o["kind"], o["dir"]
    allb = hist(kind, o["sym"])
    tz, start_t = MOM_MARKETS[kind][0], MOM_MARKETS[kind][1]
    d = datetime.date.fromisoformat(o["day"])
    days = by_day(allb, tz, start_t)
    bs = days.get(d) or []
    e, stop = o["entry"], o["stop"]
    risk = abs(e - o["stop"]) or 1e-12
    res = why = None
    seen = False
    for j, b in enumerate(bs):
        if b[0] < o["sent"] or b[0] + BAR > now_ts:
            continue
        if b[0] + BAR > o["t_flat"]:
            break
        first = not seen
        seen = True
        if (dr > 0 and b[3] <= stop) or (dr < 0 and b[2] >= stop):
            fill = stop if (first and o.get("first_bar_stop")) else (min(b[1], stop) if dr > 0 else max(b[1], stop))
            res = max(-1.5, (fill - e) * dr / risk)
            why = "stop suiveur" if (stop - o["stop"]) * dr > 0 else "SL"
            break
        tp = o.get("tp")
        if tp and not o.get("tp_told") and ((dr > 0 and b[2] >= tp) or (dr < 0 and b[3] <= tp)):
            o["tp_told"] = True
            if b[0] + BAR >= now_ts - 900:        # seulement en direct, pas en rattrapage
                FMT[0] = 2 if kind == "stocks" else 5
                telegram(cat_tag(kind, o["sym"]) + "\n🎯 <b>%s a touché ton prix de gain (TP) <code>%s</code></b>\n"
                         "Si tu avais placé le TP : gain encaissé (%+.2f %%, 2 à 3 fois le risque). "
                         "Sinon, tu peux encaisser maintenant ou laisser courir avec la sortie de sécurité."
                         % (esc(label(o["sym"])), fmt(tp), 100 * abs(tp - e) / e))
        if o["strat"] == "momentum" and local_dt(b[0] + BAR, tz).minute % 30 == 0:
            stop = trail_stop(days, d, kind, o, j, stop, dr)
            if (b[4] - stop) * dr <= 0:
                res, why = (b[4] - e) * dr / risk, "stop suiveur"
                break
            told = o.get("told", o["stop"])
            if (stop - told) * dr >= 0.25 * risk and b[0] + BAR >= now_ts - 900:
                o["told"] = stop                  # message seulement pour une bougie recente (pas de rattrapage)
                FMT[0] = 2 if kind == "stocks" else 5
                gain = (stop - e) * dr / e * 100
                telegram(cat_tag(kind, o["sym"]) + "\n🔁 <b>%s : %s ta sortie de sécurité</b>\nNouvelle sortie de sécurité (stop) : <code>%s</code>%s"
                         % (esc(label(o["sym"])), "remonte" if dr > 0 else "descends", fmt(stop),
                            "\n🔒 Gain déjà protégé : %+.2f %%" % gain if gain > 0 else "\nPerte max réduite"))
    if res is None and now_ts >= o["t_flat"]:
        last = [b for b in bs if o["sent"] <= b[0] and b[0] + BAR <= o["t_flat"]]
        if last:
            res, why = (last[-1][4] - e) * dr / risk, "fin de séance"
    if res is None:
        o["stop_now"] = stop
        return False
    res -= cost_r(kind, e, risk)
    icon = "✅" if res > 0.05 else "🛑" if res < -0.05 else "➖"
    st["track"].append({"day": o["day"], "strat": o["strat"], "kind": kind, "sym": o["sym"], "dir": dr,
                        "r": round(res, 3), "why": why, "score": o.get("score", 0)})
    tot = [x["r"] for x in st["track"]]
    if CFG["SIMPLE"]:
        verdict_ = "GAGNANT" if res > 0.05 else "PERDANT" if res < -0.05 else "à peu près nul"
        why_s = {"SL": "sortie de sécurité touchée", "stop suiveur": "sortie de sécurité (remontée) touchée",
                 "fin de séance": "fermé à l'heure prévue"}.get(why, why)
        nw_ = sum(1 for x in tot if x > 0)
        telegram(cat_tag(kind, o["sym"]) + "\n%s <b>%s terminé : %s</b>\nRésultat : <b>%+.1f fois le risque</b> "
                 "(ex. %+.0f € si tu risquais 10 €)\nRaison : %s\n📒 Bilan du bot : %d trades · %d gagnants · total %+.0f € pour 10 € risqués par trade"
                 % (icon, esc(label(o["sym"])), verdict_, res, 10 * res, esc(why_s), len(tot), nw_, 10 * sum(tot)))
        count(st, "fermes")
        return True
    telegram(cat_tag(kind, o["sym"]) + "\n%s <b>RÉSULTAT · %s %s</b> (%s)\n%s : <b>%+.2f R</b> (frais estimés inclus)\n📒 Bilan : %d trades · %.0f %% gagnants · total %+.1f R"
             % (icon, "ACHAT" if dr > 0 else "VENTE", esc(label(o["sym"])), esc(o["strat"]), esc(why), res,
                len(tot), 100 * sum(1 for x in tot if x > 0) / len(tot), sum(tot)))
    count(st, "fermes")
    return True


def track_update(st, now_ts):
    """Suit chaque signal envoye jusqu'a sa sortie (SL / stop suiveur / fin de seance, comme les
       backtests : pas de TP) et annonce le resultat en R. Un enregistrement defectueux est ecarte,
       un signal sans donnees est abandonne 3 jours apres sa fin."""
    still = []
    try:
        for o in st["open"]:
            if STOP[0]:
                still.append(o)
                continue
            try:
                done = _track_one(st, o, now_ts)
            except Exception as ex:
                log.warning("suivi %s : %s", o.get("sym"), ex)
                done = None
            finally:
                time.sleep(CFG["PAUSE"])
            if done:
                continue
            try:
                expired = now_ts - float(o.get("t_flat") or 0) > 3 * 86400
            except (TypeError, ValueError):
                expired = True
            if done is None and not isinstance(o.get("day"), str):
                expired = True
            if expired:
                count(st, "suivi_abandonne")
                log.warning("suivi abandonne : %s", o.get("sym"))
                continue
            still.append(o)
    finally:
        st["open"] = still


def scorecard(st, days=None):
    tr = st["track"]
    if days:
        cut = (local_dt(time.time(), PARIS).date() - datetime.timedelta(days=days)).isoformat()
        tr = [x for x in tr if x["day"] >= cut]
    if not tr:
        return "📒 Bilan : aucun trade terminé pour l'instant"
    rs = [x["r"] for x in tr]
    if CFG["SIMPLE"]:
        return "📒 Bilan%s : %d trades · %d gagnants · %d perdants · total %+.0f € pour 10 € risqués par trade" % (
            " des %d derniers jours" % days if days else "", len(rs), sum(1 for x in rs if x > 0),
            sum(1 for x in rs if x < 0), 10 * sum(rs))
    out = "📒 Bilan%s : %d trades · %.0f %% gagnants · total %+.1f R · moy. %+.2f R" % (
        " %d j" % days if days else "", len(rs), 100 * sum(1 for x in rs if x > 0) / len(rs), sum(rs), sum(rs) / len(rs))
    by = {}
    for x in tr:
        by.setdefault(x["strat"], []).append(x["r"])
    for k, v in by.items():
        out += "\n   %s : %d trades, %+.1f R" % (k, len(v), sum(v))
    return out


def day_recap(st):
    d = local_dt(time.time(), PARIS).date().isoformat()
    if st["done"].get("recap") == d:
        return
    st["done"]["recap"] = d
    if st["open"]:
        track_update(st, time.time())             # resultats des sorties de fin de seance d'abord
    day = st["day"] if st["day"].get("date") == d else {}
    weekly = local_dt(time.time(), PARIS).weekday() == 6
    if CFG["SIMPLE"]:
        telegram("📊 <b>RÉSUMÉ DU JOUR</b> · %s\n📨 Signaux envoyés : %d · écartés car pas assez fiables : %d · "
                 "en pause pour une annonce économique : %d\n📈 Liste des actions du jour : %s\n🧭 %s\n%s%s" % (
                     local_dt(time.time(), PARIS).strftime("%d/%m"),
                     day.get("mom_envoyes", 0) + day.get("lh_envoyes", 0),
                     day.get("mom_rejetes", 0) + day.get("mom_correles", 0) + day.get("lh_rejetes", 0),
                     day.get("pause_annonce", 0), "envoyée" if day.get("sip_envoyes") else "pas de bourse / rien",
                     esc(market_simple(REGIME["label"])), esc(scorecard(st, 7 if weekly else None)),
                     "\n<i>(bilan des 7 derniers jours)</i>" if weekly else ""))
        save_state(st)
        return
    telegram("📊 <b>RÉSUMÉ DU JOUR</b> · %s\n⚡ Momentum : %d cassures vues, %d envoyées, %d écartées (score / backtest), "
             "%d écartées (corrélation), %d en pause (annonce économique)\n⏱ Dernière 1/2 h : %d envoyées\n🇺🇸 Stocks in Play : %s\n🧭 Régime : %s\n%s%s" % (
                 local_dt(time.time(), PARIS).strftime("%d/%m"), day.get("mom_vus", 0), day.get("mom_envoyes", 0),
                 day.get("mom_rejetes", 0), day.get("mom_correles", 0), day.get("pause_annonce", 0), day.get("lh_envoyes", 0),
                 "liste envoyée" if day.get("sip_envoyes") else "pas de séance / rien", esc(REGIME["label"]),
                 esc(scorecard(st, 7 if weekly else None)), "\n<i>(bilan de la semaine)</i>" if weekly else ""))
    save_state(st)


# --------------------------------------------------------------- boucle
STOP = [False]
TRK = [0]
LHT = [0]


def _stop(*_):
    STOP[0] = True


def in_hours(now):
    g = time.gmtime(now)
    return 6 <= g.tm_hour < 21 or (g.tm_hour == 21 and g.tm_min < 30)


def main():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    # Les crons GitHub sont tres irreguliers (parfois 1 seul declenchement en 2 h) : le bot tourne donc
    # en CHAINE 24 h/24 (chaque job de 5 h 20 relance le suivant) et attend simplement la nuit.
    st = load_state()
    try:
        KR_SYM.update({k: v for k, v in kraken_perps().items() if k in CFG["MOM_CRYPTO"]})
    except Exception as ex:
        log.warning("perpetuels Kraken : %s", ex)
    if st.get("tg_pair") and os.environ.get("TELEGRAM_BOT_TOKEN"):
        cid = pair_decode(str(st["tg_pair"]))
        if cid:
            CHAT_RES.update(id=cid, how="appaire")
    end = time.time() + CFG["LOOP_MINUTES"] * 60
    try:
        st["diag"] = {"yahoo": "ok : %d bougies SPY" % len(chart("SPY", "5d", "5m")),
                      "openbb": "ok" if openbb() else "absent : %s" % OBB["err"],
                      "regime": regime_update(force=True)["label"],
                      "kraken_funding": "%d perpetuels" % len(kraken_funding()),
                      "fear_greed": fear_greed() or "indisponible",
                      "telegram": telegram_check(),
                      "cot": "%d marches" % len(cot_data()),
                      "calendrier_eco": "%d annonces cette semaine" % len(ff_events()),
                      "crypto_kraken_perp": "%d / %d cryptos en perpetuel Kraken (absentes : %s)" % (
                          len(KR_SYM), len(CFG["MOM_CRYPTO"]),
                          ",".join(x for x in CFG["MOM_CRYPTO"] if x not in KR_SYM)[:120] or "aucune"),
                      "kraken_bougies_btc": kraken_check()}
    except Exception as ex:
        st["diag"] = {"yahoo": "ERREUR : %s" % str(ex)[:150]}
    log.info("diagnostic : %s", st["diag"])
    today = local_dt(time.time(), PARIS).date().isoformat()
    if "chat ok" in str(st["diag"].get("telegram")) and st["done"].get("hello") != today:
        st["done"]["hello"] = today
        telegram("✅ <b>Bot Pro connecté</b> · %s\nSignaux actions, forex et crypto actifs. Aucun ordre envoyé : tu trades toi-même."
                 % local_dt(time.time(), PARIS).strftime("%d/%m %H:%M"))
    save_state(st)
    done_mom = done_5 = 0
    while not STOP[0] and time.time() < end:
        if not in_hours(time.time()):
            st["stats"] = dict(STATS, veille=time.strftime("%d/%m %H:%M UTC", time.gmtime()))
            time.sleep(30)                        # nuit : on attend 6 h UTC sans rien faire
            continue
        now = time.time()
        ny = local_dt(now, NY)
        par = local_dt(now, PARIS)
        try:                                      # briefing facon terminal, chaque matin 8 h (Paris)
            if par.time() >= hm(8) and par.time() < hm(11) and st["done"].get("brief") != par.date().isoformat():
                st["done"]["brief"] = par.date().isoformat()
                telegram(briefing())
                save_state(st)
            if stock_open(ny.date()) and hm(9, 15) <= ny.time() < hm(9, 29):
                premarket_gaps(st, now)
            if stock_open(ny.date()):
                lh_scan(st, now)
            if stock_open(ny.date()) and hm(9, 36) <= ny.time() < hm(10, 30):
                sip_select(st, now)
            if stock_open(ny.date()) and hm(9, 40) <= ny.time() <= hm(15, 35) and int(now // BAR) != done_5 \
                    and now % BAR >= 40:
                done_5 = int(now // BAR)
                sip_triggers(st, now)
            if "chat ok" not in str(st["diag"].get("telegram")) and CHAT_RES["how"] != "appaire" \
                    and not CHAT_RES["how"].startswith("appaire par code") and pair_check(st):
                st["diag"]["telegram"] = "destination : %s" % CHAT_RES["how"]
                telegram("✅ <b>Bot Pro connecté à ce chat</b> · %s\nTu recevras ici tous les signaux (actions, forex, crypto). "
                         "Aucun ordre envoyé : tu trades toi-même.\n<i>Tu peux supprimer le message avec le code.</i>"
                         % par.strftime("%d/%m %H:%M"))
                save_state(st)
            if _raw_chat().startswith("@") and not CHAT_RES["id"] and resolve_chat():
                st["diag"]["telegram"] = "chat retrouve : %s" % CHAT_RES["how"]
                st["done"]["hello"] = par.date().isoformat()
                telegram("✅ <b>Bot Pro connecté</b> · %s\nSignaux actions, forex et crypto actifs. Aucun ordre envoyé : tu trades toi-même."
                         % par.strftime("%d/%m %H:%M"))
                save_state(st)
            if st["open"] and int(now // BAR) != TRK[0] and now % BAR >= 50:
                TRK[0] = int(now // BAR)
                track_update(st, now)
                save_state(st)
            slot = int(now // 1800) * 2 + (1 if now % 1800 >= 180 else 0)
            if slot != done_mom and now % 1800 >= 45:    # 45 s puis 3 min apres HH:00 / HH:30
                done_mom = slot
                for kind in ("stocks", "fx", "cr"):
                    if STOP[0]:
                        break
                    try:
                        mom_scan(st, kind, time.time())
                    except Exception as ex:
                        log.warning("momentum %s : %s", kind, ex)
                save_state(st)
            g = time.gmtime(now)
            if g.tm_hour == 21 and g.tm_min >= 2:      # 21 h 02 UTC = 22 h 02 / 23 h 02 Paris
                day_recap(st)
        except Exception as ex:
            log.warning("boucle : %s", ex)
        time.sleep(5)
    if STOP[0] is False and time.time() >= end:
        try:
            with open(os.path.join(CFG["STATE_DIR"], "relance"), "w") as f:
                f.write("1")
        except OSError:
            pass
    save_state(st)
    log.info("fin (%d requetes, %d erreurs)", STATS["requetes"], STATS["erreurs"])


if __name__ == "__main__":
    main()
