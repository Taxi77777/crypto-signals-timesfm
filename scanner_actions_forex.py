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
     (achat) ou, en option, sous le plus bas (vente). Une seule fois par jour et par sens.
  2. PLAN DE SORTIE                                                      trader-exit
     stop = milieu de la plage (defaut du pack), objectif = 2R, sortie a l'heure limite.
     Refus si le risque est < 0.05 % du prix (trop serre face au spread).
  3. TIME MACHINE                                                         trader-timemachine
     10 000 fins de seance reconstruites a partir des bougies 5 min des 60 derniers jours
     (meme heure +/- 2 bougies), stop teste avant l'objectif ; puis la MEME regle rejouee
     sur chaque seance passee. Verdict compare a l'equilibre 1/(1+R).
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
import json
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
    return type(default)(float(v))


DEFAULT_STOCKS = ("AAPL,MSFT,NVDA,AMZN,META,GOOGL,TSLA,AMD,NFLX,AVGO,PLTR,COIN,MSTR,SMCI,INTC,MU,"
                  "ORCL,CRM,ADBE,UBER,SHOP,PYPL,BABA,NIO,RIVN,SOFI,HOOD,ARM,QCOM,BA,DIS,JPM,BAC,"
                  "GS,XOM,CVX,WMT,COST,KO,MCD,NKE,F,GM,PFE,LLY,UNH,ABNB,SNOW,CRWD,PANW,NET,"
                  "DDOG,RBLX,MARA,RIOT,SPY,QQQ,IWM")
DEFAULT_FX = "EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD,USDCHF,NZDUSD,EURJPY,GBPJPY,EURGBP,XAUUSD"
DEFAULT_CRYPTO = "BTC,ETH,SOL,XRP,BNB,DOGE,ADA,AVAX,LINK,DOT,LTC,TRX,BCH,NEAR"

CFG = {
    "STOCKS": [s.strip().upper() for s in envf("SCAN_STOCKS", DEFAULT_STOCKS).split(",") if s.strip()],
    "FX": [s.strip().upper() for s in envf("SCAN_FX", DEFAULT_FX).split(",") if s.strip()],
    "CRYPTO": [s.strip().upper() for s in envf("SCAN_CRYPTO", DEFAULT_CRYPTO).split(",") if s.strip()],
    "OR_MIN": envf("OR_MIN", 15),               # plage d'ouverture (minutes)
    "RR": envf("RR", 2.0),                      # objectif = RR x risque
    "SHORTS": envf("SHORTS", 1),                # 1 = aussi les cassures du plus bas (vente)
    "MAX_AGE_BARS": envf("MAX_AGE_BARS", 2),    # alerte seulement si la cassure date de <= X bougies
    "SIMS": envf("SIMS", 10000),
    "SEED": envf("SEED", 19),
    "WATCHLIST": envf("WATCHLIST", 1),          # message "watchlist du jour" avant l'ouverture
    "LOOP_MINUTES": min(envf("LOOP_MINUTES", 330), 340),
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
        return sym + "-USD"
    if sym in ("XAUUSD", "GOLD"):
        return "GC=F"                             # or : contrat a terme COMEX (proche du spot)
    if sym in ("XAGUSD", "SILVER"):
        return "SI=F"
    return sym + "=X"


def label(sym):
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
            break                                 # bougie absente (trou ou pas encore publiee)
        if b[0] + BAR > now_ts:
            break                                 # bougie pas terminee
        for d in ((1, -1) if shorts else (1,)):
            if d in seen or (d_want and d != d_want):
                continue
            if (d > 0 and b[4] > orh) or (d < 0 and b[4] < orl):
                seen.add(d)
                out.append((d, k, b, orh, orl))
    return out, orh, orl


def exit_plan(e, orh, orl, d, rr):
    """trader-exit : stop au milieu de la plage, objectif RR x risque."""
    sl = (orh + orl) / 2.0
    risk = (e - sl) * d
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
        if not s:
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
            ("reclaim_ma20", "Reprise de la moyenne 20 jours"), ("oversold", "Survente (RSI 14 < 30)"),
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


def daily_closes(bars, tz, today):
    """Clotures quotidiennes COMPLETES (la journee en cours est exclue)."""
    out = {}
    for b in bars:
        d = local_dt(b[0], tz).date()
        if d < today:
            out[d] = b[4]
    return [out[d] for d in sorted(out)]


# --------------------------------------------------------------- Telegram / etat
def strip_tags(t):
    return re.sub(r"<[^>]+>", "", t)


def telegram(msg, html=True):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    log.info("\n" + strip_tags(msg))
    if not tok or not chat:
        return
    for essai in range(3):
        try:
            body = {"chat_id": chat, "text": msg, "disable_web_page_preview": True}
            if html:
                body["parse_mode"] = "HTML"
            r = SESSION.post("https://api.telegram.org/bot%s/sendMessage" % tok, json=body, timeout=10)
            if r.ok:
                return
            log.warning("telegram HTTP %s", r.status_code)
            if html and r.status_code == 400:     # mise en forme refusee : texte brut
                html, msg = False, strip_tags(msg)
                continue
        except Exception as e:
            log.warning("telegram : %s", e)
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
    """actions et or : 2 decimales ; paires JPY : 3 ; autres paires : 5"""
    if FMT[0] == 2 or x >= 1000:
        return "%.2f" % x if x >= 1 else "%.4f" % x
    return "%.3f" % x if x >= 20 else "%.5f" % x


SEP = "━━━━━━━━━━━━━━━"
PARIS = ZoneInfo("Europe/Paris")


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


def signal_message(m, sym, d, k, b, orh, orl, sl, tp, day, sess, now_ts):
    tz = m["tz"]
    open_t, last_t, flat_t = sess
    ysym = yahoo_symbol(sym, m["key"])
    FMT[0] = 2 if m["key"] == "stocks" else 5
    e = b[4]
    rr = CFG["RR"]
    sig_paris = local_dt(b[0] + BAR, PARIS).strftime("%H:%M")
    flat_paris = datetime.datetime.combine(day, flat_t, tzinfo=tz).astimezone(PARIS).strftime("%H:%M")
    mc, hist, ctx, n_ses = None, None, "", 0
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
        log.warning("analyse %s : %s", sym, ex)
    try:
        daily = yahoo_chart(ysym, "3mo", "1d")
        tags, info = find_setups(daily_closes(daily, tz, day))
        lbls = [lbl for key, lbl in SETUP_FR if key in tags]
        if info.get("rsi") is not None:
            ctx = "RSI %.0f" % info["rsi"] + ("".join(" · " + x for x in lbls))
    except Exception as ex:
        log.warning("contexte %s : %s", sym, ex)
    sgn = -1 if d > 0 else 1
    em, vtxt, pt, be = badge(mc, rr)
    L = ["%s <b>%s %s</b>" % ("🟢" if d > 0 else "🔴", "ACHAT" if d > 0 else "VENTE", label(sym)),
         "%s %s · cassure de la plage d'ouverture %d min" % (icon(m["key"]), m["name"], CFG["OR_MIN"]),
         SEP,
         "▶️ Entrée    <code>%s</code>" % fmt(e),
         "🛑 SL        <code>%s</code>  (%+.2f %%)" % (fmt(sl), sgn * 100 * abs(e - sl) / e),
         "🎯 TP        <code>%s</code>  (%+.2f %% · %gR)" % (fmt(tp), -sgn * 100 * abs(tp - e) / e, rr),
         "⏰ Signal %s · sortie avant %s (heure de Paris)" % (sig_paris, flat_paris),
         SEP]
    if mc:
        L.append("%s <b>Verdict : %s</b>" % (em, vtxt))
        if pt is not None:
            L.append("TP touché avant le SL dans <b>%.0f %%</b> des cas (rentable dès %.0f %%)" % (pt, be))
        L.append("📊 %s simulations : 🎯 %.0f %% · 🛑 %.0f %% · ➖ %.0f %%" % (
            "{:,}".format(int(CFG["SIMS"])).replace(",", " "), 100 * mc["p_tp"], 100 * mc["p_sl"], 100 * mc["p_none"]))
    else:
        L.append("⚪ <b>Verdict : non calculé</b> (historique insuffisant : %d séances)" % n_ses)
    if hist:
        nt = hist["tp"] + hist["sl"] + hist["flat"]
        if nt:
            L.append("📜 %d dernières séances : %d signaux → %d 🎯 · %d 🛑 · %d ⏰ (moy. %+.2f R)" % (
                n_ses, nt, hist["tp"], hist["sl"], hist["flat"], sum(hist["rs"]) / nt))
    if ctx:
        L.append("📈 Contexte : " + ctx)
    L.append("<i>Signal seulement, aucun ordre envoyé · pas un conseil</i>")
    return "\n".join(L)


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
    for sym in CFG[m["list"]]:
        ysym = yahoo_symbol(sym, m["key"])
        try:
            bars = yahoo_chart(ysym, "1d" if m["key"] == "stocks" else "2d", "5m")
        except Exception as ex:
            log.warning("%s : %s", sym, ex)
            continue
        finally:
            time.sleep(CFG["PAUSE"])
        sb = session_bars(bars, day, tz, open_t, flat_t)
        r = orb_rule(sb, n_or, last_k, now_ts, t0, shorts=bool(CFG["SHORTS"]))
        if not r:
            continue
        scanned += 1
        trig, orh, orl = r
        for d, k, b, _, _ in trig:
            key = "%s|%s|%s|%d" % (m["key"], day.isoformat(), sym, d)
            if key in st["alerts"]:
                continue
            st["alerts"][key] = time.time()       # une seule fois par jour et par sens
            age = (now_ts - (b[0] + BAR)) // BAR + 1
            if age > CFG["MAX_AGE_BARS"]:
                log.info("%s %s : cassure ancienne (%d bougies), pas d'alerte", sym, d, age)
                continue
            pl = exit_plan(b[4], orh, orl, d, CFG["RR"])
            if not pl:
                continue
            sl, tp = pl
            later = [sb[j] for j in range(k + 1, last_k + 40) if j in sb and sb[j][0] + BAR <= now_ts]
            if any((d > 0 and (x[3] <= sl or x[2] >= tp)) or (d < 0 and (x[2] >= sl or x[3] <= tp)) for x in later):
                continue                          # deja au stop / a l'objectif : plus jouable
            msg = signal_message(m, sym, d, k, b, orh, orl, sl, tp, day, sess, now_ts)
            telegram(msg)
            found += 1
            st["signals"].append({"t": time.strftime("%d/%m %H:%M", time.gmtime()), "marche": m["key"],
                                  "sym": sym, "sens": "ACHAT" if d > 0 else "VENTE", "entree": b[4],
                                  "stop": sl, "objectif": tp})
            save_state(st)
    st.setdefault("scans", {})[m["key"]] = {"heure": now.strftime("%d/%m %H:%M"), "lus": scanned,
                                            "symboles": len(CFG[m["list"]]), "signaux": found}
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
    st["watch"][m["key"]] = day.isoformat()
    groups = {k: [] for k, _ in SETUP_FR}
    n = 0
    for sym in CFG[m["list"]]:
        try:
            bars = yahoo_chart(yahoo_symbol(sym, m["key"]), "3mo", "1d")
        except Exception as ex:
            log.warning("watchlist %s : %s", sym, ex)
            continue
        finally:
            time.sleep(CFG["PAUSE"])
        tags, _ = find_setups(daily_closes(bars, tz, day))
        n += 1
        for t in tags:
            groups[t].append(label(sym))
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
    save_state(st)


# --------------------------------------------------------------- boucle
STOP = [False]


def _arret(*_):
    STOP[0] = True


def main():
    signal.signal(signal.SIGTERM, _arret)
    signal.signal(signal.SIGINT, _arret)
    st = load_state()
    end = time.time() + CFG["LOOP_MINUTES"] * 60
    log.info("scanner actions + forex + crypto : %d actions, %d forex, %d crypto, ORB %d min, %gR, ventes %s",
             len(CFG["STOCKS"]), len(CFG["FX"]), len(CFG["CRYPTO"]), CFG["OR_MIN"], CFG["RR"],
             "oui" if CFG["SHORTS"] else "non")
    done_slot = 0
    try:                                          # diagnostic : Yahoo repond-il depuis GitHub ?
        st["yahoo"] = "ok : %d bougies SPY lues a %s UTC" % (len(yahoo_chart("SPY", "5d", "5m")),
                                                            time.strftime("%H:%M", time.gmtime()))
    except Exception as ex:
        st["yahoo"] = "ERREUR : %s" % str(ex)[:150]
    log.info("Yahoo : %s", st["yahoo"])
    save_state(st)
    while not STOP[0] and time.time() < end:
        now = time.time()
        g = time.gmtime(now)
        if not (6 <= g.tm_hour < 21 or (g.tm_hour == 21 and g.tm_min < 15)):
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


if __name__ == "__main__":
    main()
