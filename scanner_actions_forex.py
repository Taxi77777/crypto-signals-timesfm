#!/usr/bin/env python3
"""
SCANNER ACTIONS + FOREX + CRYPTO  -  strategies de la video (pack "day-trader-fr")
=================================================================================
SIGNAUX UNIQUEMENT : ce scanner n'envoie AUCUN ordre, il previent sur Telegram.

Regles reprises du pack open-source dan-ai-lab/day-trader-fr (adaptation francaise
de Jakeschincariol/day-trader-skill, licence MIT) :

  1. CASSURE DE LA PLAGE D'OUVERTURE (ORB 15 min, bougies 5 min)       trader-scanner/entry
     plage = plus haut / plus bas des 15 premieres minutes de la seance ;
     signal = 1re bougie 5 min TERMINEE qui cloture strictement au-dessus du plus haut
     (achat) ou sous le plus bas (vente, active par defaut ; SHORTS=0 = achats seulement
     comme le pack d'origine). Une seule fois par jour et par sens.
  2. PLAN DE SORTIE                                                      trader-exit
     stop = milieu de la plage (defaut du pack), objectif = 2R, sortie a l'heure limite.
     Refus si le risque est < 0.05 % du prix (trop serre face au spread).
  3. TIME MACHINE                                                         trader-timemachine
     10 000 fins de seance reconstruites a partir des bougies 5 min des 60 derniers jours
     (meme heure +/- 2 bougies), stop teste avant l'objectif ; puis la MEME regle rejouee
     sur chaque seance passee. Verdict compare a l'equilibre 1/(1+R).
     FAVORABLE (seul envoye par defaut) = simulation >= equilibre + 5 points, resultat moyen
     simule > 0, moins de 40 % de trajectoires sans issue ET historique reel de la regle sur
     ce symbole d'au moins 15 signaux, resultat moyen > 0, et part de TP parmi TP+SL dont la
     borne basse (Wilson, 90 %) depasse l'equilibre (le pack previent qu'une simulation seule
     n'est pas un avantage demontre).
  4. WATCHLIST DU JOUR (avant l'ouverture)                                trader-scanner daily
     cassure 20 jours, drapeau haussier, reprise de la moyenne 20 j, survente (RSI 14 < 30),
     rebond de survente - calcule sur les clotures quotidiennes.

Marches :
  ACTIONS US : seance New York 9 h 30 - 16 h (calendrier NYSE 2026-2027, demi-seances),
               entrees jusqu'a 15 h 30, sortie 15 h 55.
  FOREX      : adaptation de l'ORB aux ouvertures de Londres (8 h heure de Londres) et de
               New York (8 h heure de New York) ; entrees jusqu'a 11 h, sortie 16 h locale.
  CRYPTO     : 7 j/7, meme regle aux ouvertures de Londres (8 h) et de Wall Street (9 h 30 NY) ;
               entrees jusqu'a 11 h / 12 h, sortie 16 h locale.

Donnees : API graphiques Yahoo Finance (gratuite, non officielle, retard possible).
Ce n'est pas un conseil financier ; un signal correspond a une regle, pas a un achat.
"""
import datetime
import html as _html
import json
import math
import logging
import os
import random
import re
import signal
import time
from zoneinfo import ZoneInfo

import requests

NY = ZoneInfo("America/New_York")
LDN = ZoneInfo("Europe/London")
UTC = datetime.timezone.utc

log = logging.getLogger("scanner")
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
        logging.warning("variable %s=%r invalide : valeur par defaut %s", name, v, default)
        return default


DEFAULT_STOCKS = (
    # geants tech et croissance
    "AAPL,MSFT,NVDA,AMZN,META,GOOGL,GOOG,TSLA,AVGO,AMD,NFLX,ORCL,CRM,ADBE,INTC,QCOM,TXN,MU,AMAT,LRCX,"
    "KLAC,ADI,MRVL,NXPI,ON,MCHP,ARM,SMCI,DELL,HPQ,IBM,CSCO,ANET,PANW,CRWD,FTNT,ZS,NET,DDOG,SNOW,"
    "MDB,PLTR,NOW,INTU,WDAY,TEAM,SHOP,UBER,LYFT,ABNB,DASH,BKNG,EXPE,PYPL,XYZ,COIN,HOOD,SOFI,AFRM,UPST,"
    "MSTR,MARA,RIOT,CLSK,HUT,IREN,CIFR,RBLX,U,SNAP,PINS,SPOT,ROKU,TTD,APP,DUOL,CVNA,CHWY,ETSY,EBAY,"
    # chine, VE, IA, biotech
    "BABA,JD,PDD,BIDU,NIO,XPEV,LI,RIVN,LCID,F,GM,TM,STLA,AI,SOUN,BBAI,IONQ,RGTI,QBTS,OKLO,SMR,"
    "MRNA,BNTX,PFE,LLY,NVO,JNJ,ABBV,MRK,BMY,AMGN,GILD,REGN,VRTX,UNH,CVS,HUM,ISRG,DXCM,"
    # finance
    "JPM,BAC,WFC,C,GS,MS,SCHW,BLK,AXP,V,MA,COF,USB,PNC,"
    # consommation, industrie, energie
    "WMT,COST,TGT,HD,LOW,MCD,SBUX,CMG,NKE,LULU,KO,PEP,PG,DIS,CMCSA,T,VZ,TMUS,"
    "BA,CAT,DE,GE,HON,LMT,RTX,NOC,UPS,FDX,XOM,CVX,COP,OXY,SLB,HAL,DVN,FCX,NEM,AA,CLF,"
    "DAL,UAL,AAL,CCL,RCL,NCLH,"
    # ETF tres liquides
    "SPY,QQQ,IWM,DIA,SMH,XLF,XLE,TLT,GLD,SLV,ARKK,SOXL,TQQQ")
DEFAULT_FX = (
    # majeures
    "EURUSD,GBPUSD,USDJPY,USDCHF,USDCAD,AUDUSD,NZDUSD,"
    # croisees
    "EURGBP,EURJPY,EURCHF,EURAUD,EURCAD,EURNZD,GBPJPY,GBPCHF,GBPAUD,GBPCAD,GBPNZD,"
    "AUDJPY,AUDCAD,AUDCHF,AUDNZD,CADJPY,CADCHF,CHFJPY,NZDJPY,NZDCAD,NZDCHF,"
    # exotiques
    "USDMXN,USDZAR,USDTRY,USDSEK,USDNOK,USDDKK,USDPLN,USDHUF,USDCZK,USDSGD,USDHKD,USDCNH,"
    "USDTHB,USDINR,EURTRY,EURPLN,EURSEK,EURNOK,EURHUF,EURCZK,GBPZAR,"
    # metaux
    "XAUUSD,XAGUSD")
DEFAULT_CRYPTO = ("BTC,ETH,XRP,BNB,SOL,DOGE,ADA,TRX,LINK,AVAX,XLM,BCH,DOT,LTC,HBAR,SHIB,NEAR,ICP,ETC,"
                  "FIL,ATOM,ALGO,VET,AAVE,XMR,INJ,MKR,QNT,EGLD,SAND,MANA,AXS,CHZ,CRV,KAVA,ZEC,DASH,"
                  "XTZ,EOS,NEO,THETA,FLOW,GALA,ENJ,BAT,ZIL,SNX,YFI,SUSHI,KSM,RUNE,LRC,1INCH")
STABLES = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "PYUSD", "USDD", "BUSD", "USDS", "USD1",
           "WBTC", "WETH", "STETH", "WSTETH", "WEETH", "CBBTC", "RETH", "WBETH", "EETH", "LBTC",
           "USDG", "RLUSD", "USDF", "USDX", "EURC", "XAUT", "PAXG"}

CFG = {
    "STOCKS": [s.strip().upper() for s in envf("SCAN_STOCKS", DEFAULT_STOCKS).split(",") if s.strip()],
    "FX": [s.strip().upper() for s in envf("SCAN_FX", DEFAULT_FX).split(",") if s.strip()],
    "CRYPTO": [s.strip().upper() for s in envf("SCAN_CRYPTO", DEFAULT_CRYPTO).split(",") if s.strip()],
    "OR_MIN": envf("OR_MIN", 15),               # plage d'ouverture (minutes)
    "RR": envf("RR", 2.0),                      # objectif = RR x risque
    "SHORTS": envf("SHORTS", 1),                # 1 = aussi les cassures du plus bas (vente)
    "ONLY_FAVORABLE": envf("ONLY_FAVORABLE", 1),  # 1 = n'envoyer QUE les signaux au verdict FAVORABLE
    "CRYPTO_TOP": envf("CRYPTO_TOP", 80),       # + les plus grosses cryptos du classement Yahoo
    "MAX_AGE_BARS": max(1, envf("MAX_AGE_BARS", 1)),  # alerte seulement si la cassure date de <= X bougies
    "MAX_CHASE_R": envf("MAX_CHASE_R", 0.25),   # prix deja parti de plus de X R dans le sens : trop tard
    "MAX_AGAINST_R": envf("MAX_AGAINST_R", 0.5),  # prix deja revenu de plus de X R vers le SL : annule
    "SIMS": max(1000, envf("SIMS", 10000)),
    "MIN_HIST": envf("MIN_HIST", 15),           # FAVORABLE : au moins X signaux passes de la meme regle
    "Z": envf("Z", 1.28),                       # exigence statistique sur l'historique (1.28 = 90 %)
    "SEED": envf("SEED", 19),
    "WATCHLIST": envf("WATCHLIST", 1),          # message "watchlist du jour" avant l'ouverture
    "LOOP_MINUTES": min(envf("LOOP_MINUTES", 320), 320),
    "STATE_DIR": envf("STATE_DIR", "."),
    "PAUSE": envf("PAUSE", 0.25),               # politesse envers Yahoo entre deux requetes
}
BAR = 300                                         # bougies 5 min
STATE_FILE = "scanner_af_state.json"

# --------------------------------------------------------------- calendriers
NYSE_HOLIDAYS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19",
    "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
    "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}
NYSE_EARLY_CLOSE = {"2026-11-27", "2026-12-24", "2027-11-26"}   # cloture 13 h


def hm(h, m=0):
    return datetime.time(h, m)


def stock_session(d):
    """(ouverture, derniere entree, sortie) heure de New York, ou None si ferme."""
    if d.weekday() >= 5 or d.isoformat() in NYSE_HOLIDAYS:
        return None
    if d.isoformat() in NYSE_EARLY_CLOSE:
        return hm(9, 30), hm(12, 30), hm(12, 55)
    return hm(9, 30), hm(15, 30), hm(15, 55)


def fx_session(d):
    if d.weekday() >= 5 or (d.month, d.day) in ((12, 25), (1, 1)):
        return None
    return hm(8), hm(11), hm(16)


def crypto_ldn(d):
    """Crypto (7 j/7) : meme regle a l'ouverture de Londres."""
    return hm(8), hm(11), hm(16)


def crypto_ny(d):
    """Crypto (7 j/7) : meme regle a l'ouverture de Wall Street (9 h 30 NY, pic de volume)."""
    return hm(9, 30), hm(12), hm(16)


MARKETS = [
    # nom, fuseau, fonction de seance, liste de symboles, prefixe des messages
    {"key": "stocks", "name": "ACTIONS US", "tz": NY, "sess": stock_session, "list": "STOCKS",
     "tag": "[ACTIONS]", "watch_at": hm(9, 0)},
    {"key": "fx_ldn", "name": "FOREX ouverture Londres", "tz": LDN, "sess": fx_session, "list": "FX",
     "tag": "[FOREX]", "watch_at": hm(7, 30)},
    {"key": "fx_ny", "name": "FOREX ouverture New York", "tz": NY, "sess": fx_session, "list": "FX",
     "tag": "[FOREX]", "watch_at": None},
    {"key": "cr_ldn", "name": "CRYPTO ouverture Londres", "tz": LDN, "sess": crypto_ldn, "list": "CRYPTO",
     "tag": "[CRYPTO]", "watch_at": hm(7, 30)},
    {"key": "cr_ny", "name": "CRYPTO ouverture New York", "tz": NY, "sess": crypto_ny, "list": "CRYPTO",
     "tag": "[CRYPTO]", "watch_at": None},
]


def yahoo_symbol(sym, market_key):
    if market_key == "stocks":
        return sym.replace(".", "-")
    if market_key.startswith("cr"):
        return sym if "-" in sym else sym + "-USD"
    if sym in ("XAUUSD", "GOLD"):
        return "GC=F"                             # or : contrat a terme COMEX (proche du spot)
    if sym in ("XAGUSD", "SILVER"):
        return "SI=F"
    return sym + "=X"


def esc(t):
    return _html.escape(str(t), quote=False)


def label(sym):
    if sym.endswith("-USD"):                      # crypto Yahoo (ex. SUI20947-USD -> SUI)
        return re.sub(r"\d+$", "", sym[:-4]) or sym
    return {"XAUUSD": "XAUUSD (or)", "XAGUSD": "XAGUSD (argent)"}.get(sym, sym)


ICON = {"stocks": "🇺🇸", "fx": "💱", "cr": "🪙"}


def icon(market_key):
    return ICON["cr"] if market_key.startswith("cr") else ICON["fx"] if market_key.startswith("fx") else ICON["stocks"]


# --------------------------------------------------------------- Yahoo
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) scanner-actions-forex/1.0",
                        "Accept": "*/*"})
HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
STATS = {"requetes": 0, "erreurs": 0}


def yahoo_chart(ysym, rng, interval):
    """[(ts, o, h, l, c, v)] tries, sans bougie incomplete (valeur nulle)."""
    last = None
    for attempt in range(2):
        for host in HOSTS:
            try:
                STATS["requetes"] += 1
                r = SESSION.get("%s/v8/finance/chart/%s" % (host, ysym),
                                params={"range": rng, "interval": interval}, timeout=15)
                if r.status_code == 429 or r.status_code >= 500:
                    last = "HTTP %d" % r.status_code
                    if r.status_code == 429:
                        backoff(r)
                    continue
                j = r.json()
                res = ((j.get("chart") or {}).get("result") or [None])[0]
                if not res:
                    raise ValueError(str((j.get("chart") or {}).get("error"))[:120])
                return parse_result(res)
            except Exception as e:                # reseau, JSON, symbole inconnu
                last = str(e)[:120]
        time.sleep(3)
    STATS["erreurs"] += 1
    raise RuntimeError("Yahoo %s : %s" % (ysym, last))


def backoff(r):
    """Yahoo limite le debit (429) : on respecte Retry-After (max 30 s)."""
    try:
        wait = float(r.headers.get("Retry-After") or 5)
    except ValueError:
        wait = 5.0
    STATS["limites_429"] = STATS.get("limites_429", 0) + 1
    time.sleep(min(30.0, max(2.0, wait)))


def yahoo_spark(ysyms, rng, interval, failed=None):
    """Clotures de 20 symboles par requete (comme trader-scanner) -> {ysym: [(ts, c)]}.
       Les symboles des lots en echec sont ajoutes a `failed` (ils ne sont pas "inconnus")."""
    out = {}
    for i in range(0, len(ysyms), 20):
        batch = ysyms[i:i + 20]
        got = None
        for host in HOSTS:
            try:
                STATS["requetes"] += 1
                r = SESSION.get("%s/v7/finance/spark" % host,
                                params={"symbols": ",".join(batch), "range": rng, "interval": interval}, timeout=20)
                if r.status_code == 429 or r.status_code >= 500:
                    if r.status_code == 429:
                        backoff(r)
                    continue
                got = (r.json().get("spark") or {}).get("result") or []
                break
            except Exception as e:
                log.warning("spark : %s", str(e)[:100])
        if got is None:
            STATS["erreurs"] += 1
            if failed is not None:
                failed.update(batch)
        for item in got or []:
            resp = item.get("response") or []
            if not resp:
                continue
            ys = item.get("symbol") or (resp[0].get("meta") or {}).get("symbol")
            t = resp[0].get("timestamp") or []
            c = (((resp[0].get("indicators") or {}).get("quote") or [{}])[0] or {}).get("close") or []
            out[ys] = [(int(t[j]), float(c[j])) for j in range(min(len(t), len(c))) if c[j]]
        time.sleep(CFG["PAUSE"])
    return out


def crypto_universe():
    """Liste crypto : la liste de base + les plus grosses du classement Yahoo (sans stablecoins)."""
    syms = list(CFG["CRYPTO"])
    try:
        for host in HOSTS:
            r = SESSION.get("%s/v1/finance/screener/predefined/saved" % host,
                            params={"scrIds": "all_cryptocurrencies_us", "count": int(CFG["CRYPTO_TOP"])},
                            timeout=20)
            if r.ok:
                q = ((r.json().get("finance") or {}).get("result") or [{}])[0].get("quotes") or []
                for x in q:
                    ys = x.get("symbol") or ""
                    if ys.endswith("-USD") and label(ys).upper() not in STABLES and ys[:-4].upper() not in STABLES:
                        base = ys[:-4]
                        if base not in syms and ys not in syms:
                            syms.append(ys)
                break
    except Exception as e:
        log.warning("classement crypto : %s", str(e)[:100])
    return syms


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


# --------------------------------------------------------------- outils seances
def local_dt(ts, tz):
    return datetime.datetime.fromtimestamp(ts, tz)


def at(d, t, tz):
    """timestamp UTC d'une heure locale (fuseau avec changement d'heure)."""
    return int(datetime.datetime.combine(d, t, tzinfo=tz).timestamp())


def session_bars(bars, d, tz, open_t, end_t):
    """Bougies 5 min de la journee d entre open_t (inclus) et end_t (exclu), indexees par
       creneau depuis l'ouverture. Retourne {k: bar}."""
    t0, t1 = at(d, open_t, tz), at(d, end_t, tz)
    out = {}
    for b in bars:
        if t0 <= b[0] < t1 and (b[0] - t0) % BAR == 0:
            out[(b[0] - t0) // BAR] = b
    return out


def orb_rule(sb, n_or, last_k, now_ts, t0, d_want=None, shorts=True):
    """Regle ORB sur une seance {k: bar}. n_or = bougies de la plage ; last_k = dernier
       creneau autorise pour la bougie de declenchement (sa cloture <= derniere entree).
       Retourne la liste des declenchements [(dir, k, bar, orh, orl)] (un par sens)."""
    if any(k not in sb for k in range(n_or)):
        return None                               # plage incomplete : graphique inutilisable
    orh = max(sb[k][2] for k in range(n_or))
    orl = min(sb[k][3] for k in range(n_or))
    out, seen = [], set()
    for k in range(n_or, last_k + 1):
        b = sb.get(k)
        if b is None:
            if any(j > k for j in sb):
                continue                          # bougie vide (aucun echange) : on passe
            break                                 # pas encore publiee
        if b[0] + BAR > now_ts:
            break                                 # bougie pas terminee
        for d in ((1, -1) if shorts else (1,)):
            if d in seen or (d_want and d != d_want):
                continue
            if (d > 0 and b[4] > orh) or (d < 0 and b[4] < orl):
                seen.add(d)
                out.append((d, k, b, orh, orl))
    return out, orh, orl


def exit_plan(e, orh, orl, d, rr, cents=False):
    """trader-exit : stop au milieu de la plage, objectif RR x risque. Actions (cents=True) :
       stop arrondi au cent en s'eloignant de l'entree, objectif au cent le plus proche."""
    sl = (orh + orl) / 2.0
    if cents:
        e = math.floor(e * 100 + 0.5) / 100       # entree au cent (prix de cloture affiche)
        sl = math.floor(sl * 100 + 1e-9) / 100 if d > 0 else math.ceil(sl * 100 - 1e-9) / 100
    risk = (e - sl) * d
    if cents and risk > 0 and risk >= e * 0.0005:
        return sl, math.floor((e + d * rr * risk) * 100 + 0.5) / 100
    if risk <= 0 or risk < e * 0.0005:
        return None
    return sl, e + d * rr * risk


# --------------------------------------------------------------- time machine
def full_sessions(bars, tz, sess_fn, before_day):
    """Seances passees COMPLETES (toutes les bougies de l'ouverture a la sortie)."""
    days = sorted({local_dt(b[0], tz).date() for b in bars})
    out = []
    for d in days:
        if d >= before_day:
            continue
        s = sess_fn(d)
        if not s or d.isoformat() in NYSE_EARLY_CLOSE and sess_fn is stock_session:
            continue
        open_t, last_t, flat_t = s
        sb = session_bars(bars, d, tz, open_t, flat_t)
        n = (at(d, flat_t, tz) - at(d, open_t, tz)) // BAR
        if len(sb) == n and n > 0:
            out.append((d, sb, n))
    return out


def simulate(sessions, e, sl, tp, d, k0, n_end, sims, seed, window=2):
    """Trajectoires de k0+1 a n_end-1 : chaque bougie = une bougie passee tiree au hasard
       au meme creneau (+/- window), en ratios de la cloture precedente. Stop d'abord."""
    pool = {}
    for _, sb, n in sessions:
        for k in range(1, n):
            pc = sb[k - 1][4]
            b = sb[k]
            pool.setdefault(k, []).append((b[2] / pc, b[3] / pc, b[4] / pc))
    near = {}
    for k in range(k0 + 1, n_end):
        z = []
        for w in range(-window, window + 1):
            z.extend(pool.get(k + w, ()))
        near[k] = z
    if not near or any(not z for z in near.values()):
        return None
    rng = random.Random(seed)
    risk = (e - sl) * d
    n_tp = n_sl = 0
    rs = []
    for _ in range(sims):
        p, res = e, None
        for k in range(k0 + 1, n_end):
            z = near[k]
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
            p *= cr
        if res is None:
            rs.append((p - e) * d / risk)
        else:
            rs.append(res)
            if res < 0:
                n_sl += 1
            else:
                n_tp += 1
    return {"p_tp": n_tp / sims, "p_sl": n_sl / sims, "p_none": 1 - (n_tp + n_sl) / sims,
            "mean_r": sum(rs) / len(rs), "p_green": sum(1 for x in rs if x > 0) / len(rs)}


def history_replay(sessions, tz, sess_fn, n_or, d_want, rr):
    """La MEME regle (meme sens) rejouee sur chaque seance passee complete."""
    res = {"tp": 0, "sl": 0, "flat": 0, "none": 0, "rs": []}
    for d, sb, n in sessions:
        open_t, last_t, flat_t = sess_fn(d)
        t0 = at(d, open_t, tz)
        last_k = (at(d, last_t, tz) - t0) // BAR - 1
        r = orb_rule(sb, n_or, last_k, 10 ** 12, t0, d_want=d_want, shorts=True)
        if not r or not r[0]:
            res["none"] += 1
            continue
        trig, orh, orl = r
        _, k, b, _, _ = trig[0]
        e = b[4]
        pl = exit_plan(e, orh, orl, d_want, rr)
        if not pl:
            res["none"] += 1
            continue
        sl, tp = pl
        risk = (e - sl) * d_want
        out = None
        for j in range(k + 1, n):
            hi, lo = sb[j][2], sb[j][3]
            if (d_want > 0 and lo <= sl) or (d_want < 0 and hi >= sl):
                out = ("sl", -1.0)
                break
            if (d_want > 0 and hi >= tp) or (d_want < 0 and lo <= tp):
                out = ("tp", rr)
                break
        if out is None:
            out = ("flat", (sb[n - 1][4] - e) * d_want / risk)
        res[out[0]] += 1
        res["rs"].append(out[1])
    return res


def verdict(mc, rr):
    hit = mc["p_tp"] + mc["p_sl"]
    if hit <= 0:
        return "aucune trajectoire ne touche le stop ou l'objectif avant la sortie"
    pt = mc["p_tp"] / hit
    be = 1.0 / (1.0 + rr)
    gap = 100 * (pt - be)
    if abs(gap) < 5:
        v = "proche de l'equilibre"
    elif abs(gap) < 15:
        v = "biais vers %s" % ("l'objectif" if gap > 0 else "le stop")
    else:
        v = "biais MARQUE vers %s" % ("l'objectif" if gap > 0 else "le stop")
    return "%s : objectif d'abord dans %.0f %% des cas qui touchent un niveau (equilibre a %gR = %.0f %%)" % (
        v, 100 * pt, rr, 100 * be)


# --------------------------------------------------------------- watchlist (clotures)
def rsi(c, n=14):
    if len(c) < n + 1:
        return None
    g = [max(0.0, c[i] - c[i - 1]) for i in range(1, len(c))]
    lo = [max(0.0, c[i - 1] - c[i]) for i in range(1, len(c))]
    ag, al = sum(g[:n]) / n, sum(lo[:n]) / n
    for i in range(n, len(g)):
        ag = (ag * (n - 1) + g[i]) / n
        al = (al * (n - 1) + lo[i]) / n
    return 100 - 100 / (1 + ag / al) if al else 100.0


SETUP_FR = [("breakout_20d", "Cassure du plus haut 20 jours"), ("bull_flag", "Drapeau haussier"),
            ("reclaim_ma20", "Reprise de la moyenne 20 jours"), ("oversold", "Survente (RSI 14 sous 30)"),
            ("oversold_bounce", "Rebond de survente")]


def find_setups(c):
    """trader-scanner daily : clotures (anciennes d'abord, >= 30)."""
    if len(c) < 30:
        return [], {}
    last = c[-1]
    hi20 = max(c[-21:-1])
    ma20 = sum(c[-20:]) / 20.0
    ma20_prev = sum(c[-21:-1]) / 20.0
    run = c[-6] / c[-16] - 1
    rng5 = (max(c[-5:]) - min(c[-5:])) / last
    r, r_prev = rsi(c), rsi(c[:-1])
    tags = []
    if last > hi20:
        tags.append("breakout_20d")
    if run > 0.10 and rng5 < 0.05:
        tags.append("bull_flag")
    if c[-2] < ma20_prev and last > ma20:
        tags.append("reclaim_ma20")
    if r is not None and r < 30:
        tags.append("oversold")
    if r_prev is not None and r_prev < 30 and last > c[-2]:
        tags.append("oversold_bounce")
    return tags, {"rsi": r, "ma20": ma20, "hi20": hi20}


def daily_closes(bars, tz, today, now_ts=None):
    """Clotures quotidiennes COMPLETES (la bougie du jour en cours est exclue, y compris pour
       la crypto et le forex dont la bougie quotidienne commence a minuit UTC)."""
    out = {}
    now_ts = now_ts or time.time()
    for b in bars:
        d = local_dt(b[0], tz).date()
        if d < today and b[0] + 86400 <= now_ts:
            out[d] = b[4]
    return [out[d] for d in sorted(out)]


# --------------------------------------------------------------- Telegram / etat
def strip_tags(t):
    return _html.unescape(re.sub(r"</?(b|i|code)>", "", t))


LAST_TG = [0.0]


def telegram(msg, html=True):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    log.info("\n" + strip_tags(msg))
    if not tok or not chat:
        return
    if len(msg) > 4000:                           # limite Telegram : 4096 caracteres
        msg = msg[:3990].rsplit("\n", 1)[0] + "\n…"
    gap = 3.1 if str(chat).startswith("-") else 1.1   # groupe : 20 messages / min maximum
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
            log.warning("telegram HTTP %s", r.status_code)
            if r.status_code == 429:              # trop de messages : on attend ce que Telegram demande
                try:
                    wait = float((r.json().get("parameters") or {}).get("retry_after") or 5)
                except Exception:
                    wait = 5.0
                time.sleep(min(60.0, wait + 1))
                continue
            if html and r.status_code == 400:     # mise en forme refusee : texte brut
                html, msg = False, strip_tags(msg)
                continue
        except Exception as e:
            log.warning("telegram : %s", str(e).replace(tok, "***"))
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
    st.setdefault("alerts", {})
    st.setdefault("watch", {})
    st.setdefault("signals", [])
    return st


def save_state(st):
    cut = time.time() - 4 * 86400
    st["alerts"] = {k: v for k, v in st["alerts"].items() if v > cut}
    st["signals"] = st["signals"][-60:]
    st["stats"] = dict(STATS, maj=time.strftime("%d/%m %H:%M UTC", time.gmtime()))
    os.makedirs(CFG["STATE_DIR"], exist_ok=True)
    for p in {os.path.join(CFG["STATE_DIR"], STATE_FILE), STATE_FILE}:
        tmp = p + ".tmp"
        with open(tmp, "w") as f:
            json.dump(st, f, indent=1)
        os.replace(tmp, p)


# --------------------------------------------------------------- analyse d'un signal
_HIST = {}


def hist_5m(ysym):
    c = _HIST.get(ysym)
    if c and time.time() - c[0] < 3 * 3600:
        return c[1]
    bars = yahoo_chart(ysym, "60d", "5m")
    _HIST[ysym] = (time.time(), bars)
    return bars


FMT = [2]


def fmt(x):
    """actions et or : 2 decimales ; paires JPY : 3 ; autres paires : 5 ; tres petits prix
       (SHIB, PEPE...) : assez de chiffres significatifs pour distinguer entree, SL et TP."""
    if 0 < x < 1:
        return "%.*f" % (max(4 if FMT[0] == 2 else 5, 4 - int(math.floor(math.log10(x)))), x)
    if FMT[0] == 2 or x >= 1000:
        return "%.2f" % x
    return "%.3f" % x if x >= 20 else "%.5f" % x


SEP = "━━━━━━━━━━━━━━━"
PARIS = ZoneInfo("Europe/Paris")


def badge(mc, rr):
    """Verdict de la simulation : objectif touche avant le stop, compare a l'equilibre."""
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


def wilson_low(k, n, z):
    """Borne basse de l'intervalle de Wilson d'une proportion k/n."""
    if n <= 0:
        return 0.0
    p = k / n
    den = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - r) / den


def decide(mc, hist, rr):
    """Verdict final + avertissements. FAVORABLE seulement si la simulation ET l'historique
       reel de la regle sur ce symbole vont dans le meme sens (sinon : NON CONFIRMÉ)."""
    em, v, pt, be = badge(mc, rr)
    warn = []
    nt = (hist["tp"] + hist["sl"] + hist["flat"]) if hist else 0
    hm_r = (sum(hist["rs"]) / nt) if nt else None
    if mc:
        if mc["p_none"] > 0.40:
            warn.append("plus de 40 % des trajectoires ne touchent ni le SL ni le TP : le prix de sortie compte")
        if v == "FAVORABLE" and mc["mean_r"] <= 0:
            warn.append("résultat moyen simulé négatif malgré le verdict")
        if v == "DÉFAVORABLE" and mc["mean_r"] > 0:
            warn.append("résultat moyen simulé positif malgré le verdict")
    if nt and hm_r is not None and v in ("FAVORABLE", "DÉFAVORABLE") and (hm_r > 0) != (v == "FAVORABLE"):
        warn.append("l'historique réel de la règle dit l'inverse de la simulation")
    if v == "FAVORABLE":
        why = []
        if mc["mean_r"] <= 0:
            why.append("résultat moyen simulé ≤ 0")
        if mc["p_none"] > 0.40:
            why.append("trop de trajectoires sans issue")
        if nt < CFG["MIN_HIST"]:
            why.append("historique trop court (%d signaux)" % nt)
        elif hm_r <= 0:
            why.append("historique réel négatif")
        else:                                     # l'historique doit battre l'equilibre de facon
            hits = hist["tp"] + hist["sl"]        # statistiquement nette (borne basse de Wilson)
            lb = wilson_low(hist["tp"], hits, CFG["Z"]) if hits else 0.0
            if 100 * lb < be:
                why.append("historique pas assez net (%d TP / %d SL)" % (hist["tp"], hist["sl"]))
        if why:
            return "🟡", "NON CONFIRMÉ", pt, be, warn, why
    return em, v, pt, be, warn, []


def signal_message(m, sym, d, k, b, orh, orl, sl, tp, day, sess, now_ts, last_px=None, age=1):
    tz = m["tz"]
    open_t, last_t, flat_t = sess
    ysym = yahoo_symbol(sym, m["key"])
    FMT[0] = 2 if m["key"] == "stocks" else 5
    e = b[4]
    rr = CFG["RR"]
    sig_paris = local_dt(b[0] + BAR, PARIS).strftime("%H:%M")
    flat_paris = datetime.datetime.combine(day, flat_t, tzinfo=tz).astimezone(PARIS).strftime("%H:%M")
    mc, hist, ctx, n_ses, ok = None, None, "", 0, True
    try:
        bars = hist_5m(ysym)
        sessions = full_sessions(bars, tz, m["sess"], day)
        n_ses = len(sessions)
        t0 = at(day, open_t, tz)
        n_end = (at(day, flat_t, tz) - t0) // BAR
        n_or = CFG["OR_MIN"] * 60 // BAR
        if n_ses >= 20:
            mc = simulate(sessions, e, sl, tp, d, k, n_end, int(CFG["SIMS"]), int(CFG["SEED"]))
        hist = history_replay(sessions, tz, m["sess"], n_or, d, rr)
    except Exception as ex:
        ok = False                                # erreur reseau : on reessaiera au prochain passage
        log.warning("analyse %s : %s", sym, ex)
    try:
        daily = yahoo_chart(ysym, "3mo", "1d")
        tags, info = find_setups(daily_closes(daily, tz, day, now_ts))
        lbls = [lbl for key, lbl in SETUP_FR if key in tags]
        if info.get("rsi") is not None:
            ctx = "RSI %.0f" % info["rsi"] + ("".join(" · " + x for x in lbls))
    except Exception as ex:
        log.warning("contexte %s : %s", sym, ex)
    sgn = -1 if d > 0 else 1
    em, vtxt, pt, be, warn, why = decide(mc, hist, rr)
    L = ["%s <b>%s %s</b>" % ("🟢" if d > 0 else "🔴", "ACHAT" if d > 0 else "VENTE", esc(label(sym))),
         "%s %s · cassure de la plage d'ouverture %d min" % (icon(m["key"]), m["name"], CFG["OR_MIN"]),
         SEP,
         "▶️ Entrée    <code>%s</code>" % fmt(e),
         "🛑 SL        <code>%s</code>  (%+.2f %%)" % (fmt(sl), sgn * 100 * abs(e - sl) / e),
         "🎯 TP        <code>%s</code>  (%+.2f %% · %gR)" % (fmt(tp), -sgn * 100 * abs(tp - e) / e, rr),
         "⏰ Signal %s · sortie avant %s (heure de Paris)" % (sig_paris, flat_paris)]
    valid = local_dt(b[0] + 2 * BAR, PARIS).strftime("%H:%M")
    L.append("⌛ <b>Valable jusqu'à %s</b> (Paris) · après, ne pas entrer" % valid)
    if last_px:
        L.append("⏱️ Envoyé à %s · prix actuel <code>%s</code>" % (
            local_dt(time.time(), PARIS).strftime("%H:%M:%S"), fmt(last_px)))
    L.append(SEP)
    if mc:
        L.append("%s <b>Verdict : %s</b>" % (em, vtxt))
        if pt is not None:
            L.append("TP touché avant le SL dans <b>%.0f %%</b> des cas (rentable dès %.0f %%)" % (pt, be))
        L.append("📊 %s simulations : 🎯 %.0f %% · 🛑 %.0f %% · ➖ %.0f %% · moy. %+.2f R" % (
            "{:,}".format(int(CFG["SIMS"])).replace(",", " "), 100 * mc["p_tp"], 100 * mc["p_sl"],
            100 * mc["p_none"], mc["mean_r"]))
    else:
        L.append("⚪ <b>Verdict : non calculé</b> (historique insuffisant : %d séances)" % n_ses)
    if hist:
        nt = hist["tp"] + hist["sl"] + hist["flat"]
        if nt:
            L.append("📜 %d dernières séances : %d signaux → %d 🎯 · %d 🛑 · %d ⏰ (moy. %+.2f R)" % (
                n_ses, nt, hist["tp"], hist["sl"], hist["flat"], sum(hist["rs"]) / nt))
    for w in why:
        L.append("🟡 non confirmé : " + esc(w))
    for w in warn:
        L.append("⚠️ " + esc(w))
    if ctx:
        L.append("📈 Contexte : " + esc(ctx))
    L.append("<i>Signal seulement, aucun ordre envoyé · pas un conseil</i>")
    return "\n".join(L), vtxt, ok and mc is not None


# --------------------------------------------------------------- scans
def scan_market(m, st, now_ts):
    tz = m["tz"]
    now = local_dt(now_ts, tz)
    day = now.date()
    sess = m["sess"](day)
    if not sess:
        return 0
    open_t, last_t, flat_t = sess
    t0 = at(day, open_t, tz)
    n_or = CFG["OR_MIN"] * 60 // BAR
    if now_ts < t0 + n_or * BAR + 60 or now_ts > at(day, last_t, tz) + 2 * BAR:
        return 0                                  # hors de la fenetre d'entree
    last_k = (at(day, last_t, tz) - t0) // BAR - 1
    found = 0
    scanned = 0
    hidden = 0
    rng_ = "1d" if m["key"] == "stocks" else "2d"
    ymap = {yahoo_symbol(x, m["key"]): x for x in CFG[m["list"]]}
    failed = set()
    spark = yahoo_spark(list(ymap), rng_, "5m", failed)
    if not spark:
        log.warning("%s : Yahoo ne repond pas (spark vide), passage saute", m["key"])
        return 0                                  # pas de verification symbole par symbole (429)
    cands = []
    kdir = lambda sym, d: "%s|%s|%s|%d" % (m["key"], day.isoformat(), sym, d)  # noqa: E731
    for ysym, sym in ymap.items():
        if STOP[0]:
            break
        need_l = kdir(sym, 1) not in st["alerts"]
        need_s = bool(CFG["SHORTS"]) and kdir(sym, -1) not in st["alerts"]
        if not (need_l or need_s):
            continue
        cl = spark.get(ysym)
        if cl is None:
            continue
        scanned += 1
        byk = {(ts - t0) // BAR: c for ts, c in cl if t0 <= ts and (ts - t0) % BAR == 0}
        if any(k not in byk for k in range(n_or)):
            continue
        top = max(byk[k] for k in range(n_or))
        bot = min(byk[k] for k in range(n_or))
        done = [k for k in sorted(byk) if n_or <= k <= last_k and t0 + (k + 1) * BAR <= now_ts]
        recent = done[-int(CFG["MAX_AGE_BARS"]):]
        # necessaire pour une cassure recente : une cloture recente au-dela des clotures de la plage
        # (seulement pour un sens pas encore traite aujourd'hui)
        if (need_l and any(byk[k] >= top for k in recent)) or (need_s and any(byk[k] <= bot for k in recent)):
            cands.append(sym)
    for sym in cands:
        if STOP[0]:
            break
        ysym = yahoo_symbol(sym, m["key"])
        try:
            bars = yahoo_chart(ysym, rng_, "5m")
        except Exception as ex:
            log.warning("%s : %s", sym, ex)
            continue
        finally:
            time.sleep(CFG["PAUSE"])
        sb = session_bars(bars, day, tz, open_t, flat_t)
        r = orb_rule(sb, n_or, last_k, now_ts, t0, shorts=bool(CFG["SHORTS"]))
        if not r:
            continue
        trig, orh, orl = r
        for d, k, b, _, _ in trig:
            key = kdir(sym, d)
            if key in st["alerts"]:
                continue
            age = (now_ts - (b[0] + BAR)) // BAR + 1
            if age > CFG["MAX_AGE_BARS"]:
                st["alerts"][key] = time.time()   # 1re cassure du jour deja ancienne : definitif
                log.info("%s %s : cassure ancienne (%d bougies), pas d'alerte", sym, d, age)
                continue
            pl = exit_plan(b[4], orh, orl, d, CFG["RR"], cents=(m["key"] == "stocks"))
            if not pl:
                st["alerts"][key] = time.time()   # plage trop etroite : definitif
                continue
            sl, tp = pl
            later = [sb[j] for j in range(k + 1, last_k + 40) if j in sb and sb[j][0] + BAR <= now_ts]
            if any((d > 0 and (x[3] <= sl or x[2] >= tp)) or (d < 0 and (x[2] >= sl or x[3] <= tp)) for x in later):
                st["alerts"][key] = time.time()   # deja au stop / a l'objectif : plus jouable
                continue
            last_px = next((x[4] for x in reversed(bars) if x[0] <= now_ts), None)
            risk = abs(b[4] - sl)
            if last_px and risk > 0:
                move = (last_px - b[4]) * d / risk   # > 0 : parti dans le sens du trade
                if move > CFG["MAX_CHASE_R"] or move < -CFG["MAX_AGAINST_R"]:
                    st["alerts"][key] = time.time()
                    log.info("%s %s : prix deja a %+.2f R de l'entree, signal trop tard / annule", sym, d, move)
                    continue
            msg, vtxt, analysed = signal_message(m, sym, d, k, b, orh, orl, sl, tp, day, sess, now_ts,
                                                 last_px=last_px, age=age)
            if not analysed:
                log.info("%s %s : analyse impossible (reseau), nouvel essai au prochain passage", sym, d)
                continue                          # la cle n'est PAS posee : on reessaie
            st["alerts"][key] = time.time()       # une seule fois par jour et par sens
            if CFG["ONLY_FAVORABLE"] and vtxt != "FAVORABLE":
                hidden += 1
                log.info("%s %s : verdict %s, signal non envoye (favorables seulement)", sym, d, vtxt)
                continue
            telegram(msg)
            found += 1
            st["signals"].append({"t": time.strftime("%d/%m %H:%M", time.gmtime()), "marche": m["key"],
                                  "sym": sym, "sens": "ACHAT" if d > 0 else "VENTE", "entree": b[4],
                                  "stop": sl, "objectif": tp})
            save_state(st)
    sc = st.setdefault("scans", {}).get(m["key"]) or {}
    if sc.get("jour") != day.isoformat():
        sc = {"jour": day.isoformat(), "envoyes": 0, "non_favorables": 0}
    sc.update(heure=now.strftime("%d/%m %H:%M"), lus=scanned, symboles=len(CFG[m["list"]]),
              candidats=len(cands), envoyes=sc["envoyes"] + found, non_favorables=sc["non_favorables"] + hidden)
    st["scans"][m["key"]] = sc
    return found


def watchlist(m, st, now_ts):
    if not CFG["WATCHLIST"] or not m.get("watch_at"):
        return
    tz = m["tz"]
    now = local_dt(now_ts, tz)
    day = now.date()
    if not m["sess"](day) or now.time() < m["watch_at"] or now.time() > hm(m["watch_at"].hour + 1):
        return
    if st["watch"].get(m["key"]) == day.isoformat():
        return
    groups = {k: [] for k, _ in SETUP_FR}
    n = 0
    ymap = {yahoo_symbol(x, m["key"]): x for x in CFG[m["list"]]}
    spark = yahoo_spark(list(ymap), "3mo", "1d")
    for ysym, sym in ymap.items():
        cl = spark.get(ysym)
        if not cl:
            continue
        bars = [(ts, c, c, c, c, 0.0) for ts, c in cl]
        tags, _ = find_setups(daily_closes(bars, tz, day, now_ts))
        n += 1
        for t in tags:
            groups[t].append(esc(label(sym)))
    if n == 0:
        return                                    # Yahoo indisponible : nouvel essai au tour suivant
    ico = {"breakout_20d": "🚀", "bull_flag": "🚩", "reclaim_ma20": "📈", "oversold": "📉",
            "oversold_bounce": "↩️"}
    lines = ["📋 <b>WATCHLIST DU JOUR · %s %s</b>" % (icon(m["key"]), m["name"].split(" ouverture")[0]),
             "%s · %d/%d graphiques lus" % (day.strftime("%d/%m"), n, len(CFG[m["list"]])), SEP]
    for key, lbl in SETUP_FR:
        if groups[key]:
            lines.append("%s <b>%s</b> : %s" % (ico[key], lbl, ", ".join(groups[key])))
    if len(lines) == 3:
        lines.append("aucune configuration aujourd'hui")
    o = m["sess"](day)[0]
    start = datetime.datetime.combine(day, o, tzinfo=tz) + datetime.timedelta(minutes=CFG["OR_MIN"])
    lines += [SEP, "⏰ Cassures de la plage d'ouverture surveillées dès %s (heure de Paris)" %
              start.astimezone(PARIS).strftime("%H:%M"),
              "<i>Graphiques qui correspondent à une règle, pas une liste d'achats</i>"]
    telegram("\n".join(lines))
    st["watch"][m["key"]] = day.isoformat()
    save_state(st)


# --------------------------------------------------------------- boucle
STOP = [False]


def _arret(*_):
    STOP[0] = True


def in_hours(now):
    g = time.gmtime(now)
    return 6 <= g.tm_hour < 21 or (g.tm_hour == 21 and g.tm_min < 15)


def main():
    signal.signal(signal.SIGTERM, _arret)
    signal.signal(signal.SIGINT, _arret)
    if not in_hours(time.time()):
        log.info("hors des fenetres de ce scanner (6 h - 21 h 15 UTC) : rien a faire")
        return
    st = load_state()
    end = time.time() + CFG["LOOP_MINUTES"] * 60
    log.info("scanner actions + forex + crypto : %d actions, %d forex, %d crypto, ORB %d min, %gR, ventes %s",
             len(CFG["STOCKS"]), len(CFG["FX"]), len(CFG["CRYPTO"]), CFG["OR_MIN"], CFG["RR"],
             "oui" if CFG["SHORTS"] else "non")
    done_slot = 0
    CFG["CRYPTO"] = crypto_universe()
    try:                                          # symboles que Yahoo ne connait pas (a corriger)
        bad = {}
        for mk, lst in (("stocks", "STOCKS"), ("fx_ldn", "FX"), ("cr_ldn", "CRYPTO")):
            ymap = {yahoo_symbol(x, mk): x for x in CFG[lst]}
            failed = set()
            got = yahoo_spark(list(ymap), "5d", "1d", failed)
            if got:                               # inconnu = absent d'un lot qui a REPONDU
                bad[lst] = [ymap[y] for y in ymap if y not in got and y not in failed]
                CFG[lst] = [ymap[y] for y in ymap if y in got or y in failed] or CFG[lst]
        st["univers"] = {"actions": len(CFG["STOCKS"]), "forex": len(CFG["FX"]), "crypto": len(CFG["CRYPTO"]),
                         "inconnus": bad}
        log.info("univers : %s", st["univers"])
    except Exception as ex:
        log.warning("univers : %s", ex)
    try:                                          # diagnostic : Yahoo repond-il depuis GitHub ?
        st["yahoo"] = "ok : %d bougies SPY lues a %s UTC" % (len(yahoo_chart("SPY", "5d", "5m")),
                                                            time.strftime("%H:%M", time.gmtime()))
    except Exception as ex:
        st["yahoo"] = "ERREUR : %s" % str(ex)[:150]
    log.info("Yahoo : %s", st["yahoo"])
    save_state(st)
    while not STOP[0] and time.time() < end:
        now = time.time()
        if not in_hours(now):
            log.info("hors des fenetres de ce scanner (6 h - 21 h 15 UTC) : fin du job")
            break
        for m in MARKETS:
            try:
                watchlist(m, st, now)
            except Exception as ex:
                log.warning("watchlist %s : %s", m["key"], ex)
        slot = int(now // BAR)
        if slot != done_slot and now - slot * BAR >= 25:   # 25 s apres la cloture : Yahoo a rattrape
            done_slot = slot
            for m in MARKETS:
                if STOP[0]:
                    break
                try:
                    scan_market(m, st, time.time())
                except Exception as ex:
                    log.warning("scan %s : %s", m["key"], ex)
            save_state(st)
        time.sleep(5)
    save_state(st)
    log.info("fin du job (%d requetes, %d erreurs)", STATS["requetes"], STATS["erreurs"])
    # fin normale (duree max atteinte) en pleine journee : le workflow relance un job
    if not STOP[0] and time.time() >= end and in_hours(time.time() + 600):
        try:
            with open(os.path.join(CFG["STATE_DIR"], "relance"), "w") as f:
                f.write("1")
        except OSError:
            pass


if __name__ == "__main__":
    main()
