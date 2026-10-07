"""
PDH/PDL PIEGE -- scanner perpetuels Kraken Pro -> signaux Telegram
===================================================================
Strategie (meme logique que l'indicateur MT4 PDH_PDL_Strategy, mode PIEGE) :
 - PDH / PDL = plus haut / plus bas de la veille (bougie journaliere UTC).
 - PIEGE VENTE : le prix depasse le PDH avec une meche puis reclôture SOUS le PDH,
   confirme par une bougie de deplacement baissiere -> VENTE.
 - PIEGE ACHAT : le prix passe sous le PDL puis reclôture AU-DESSUS,
   confirme par une bougie de deplacement haussiere -> ACHAT.
 - SL derriere la meche, TP1 = milieu de la veille (50 %), TP2 = niveau oppose.
 - Filtre de session Londres / New York (heures UTC).
Seuls les signaux PIEGE ACHAT / PIEGE VENTE sont envoyes sur Telegram.
Donnees : API publique Kraken Futures (sans cle). Telegram : secrets
TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID du depot.
"""
import os
import sys
import json
import glob
import zlib
import math
import time
import logging
import threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pdh")


def _f(name, default):
    return float(os.environ.get(name, default))


# =================================================================== REGLAGES (= PDH_PDL_Strategy.mq4)
USE_SESSION = os.environ.get("PDH_USE_SESSION", "1").lower() in ("1", "true", "yes")
S1 = (int(_f("PDH_S1_START", 7)), int(_f("PDH_S1_END", 11)))     # Londres (UTC)
S2 = (int(_f("PDH_S2_START", 13)), int(_f("PDH_S2_END", 17)))    # New York (UTC)
CONF_BARS = int(_f("PDH_CONF_BARS", 3))       # bougies max pour la confirmation
DISP_ATR = _f("PDH_DISP_ATR", 0.8)            # bougie de deplacement : corps min (x ATR)
SL_BUF_ATR = _f("PDH_SL_BUF_ATR", 0.10)       # marge SL derriere la meche (x ATR)
MIN_RR = _f("PDH_MIN_RR", 2.0)                # R:R min vers le niveau oppose (TP2)
ATR_PERIOD = int(_f("PDH_ATR_PERIOD", 14))
RECENT_BARS = int(_f("PDH_RECENT_BARS", 3))   # signal envoye s'il date de <= X bougies
MAX_RUN_R = _f("PDH_MAX_RUN_R", 0.3)          # deja parti de plus de X R vers le TP = trop tard
SCAN_BARS = int(_f("PDH_SCAN_BARS", 300))

# =================================================================== CONFIG SCANNER
SCAN_TFS = os.environ.get("PDH_TFS", "M5,M15,M30,H1")
TOP_N = int(_f("PDH_TOP_N", 120))
MIN_QUOTE_VOL = _f("PDH_MIN_QUOTE_VOL", 1000000)
SHARD = int(_f("PDH_SHARD", 0))
SHARDS = max(1, int(_f("PDH_SHARDS", 1)))
EXTRA_SYMBOLS = [s.strip().upper() for s in os.environ.get("PDH_SYMBOLS", "").split(",") if s.strip()]
STATE_FILE = os.environ.get("PDH_STATE_FILE", "pdh_state.json" if SHARDS == 1 else f"pdh_state_{SHARD}.json")
DRY_RUN = os.environ.get("PDH_DRY_RUN", "").lower() in ("1", "true", "yes")
LOOP_MINUTES = _f("PDH_LOOP_MINUTES", 0)
INTERVAL = _f("PDH_INTERVAL", 180)
RATE_GAP = _f("PDH_RATE_GAP", 1.0)

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

FUTURES = "https://futures.kraken.com"
PERP_EXCLUDE_CAT = ("Stablecoin", "Forex", "xStocks", "Commodities", "Equities", "Indices", "Pre-IPO", "DTF")
PERP_FALLBACK = ["PF_XBTUSD", "PF_ETHUSD", "PF_SOLUSD", "PF_XRPUSD", "PF_DOGEUSD", "PF_ADAUSD", "PF_AVAXUSD",
                 "PF_LINKUSD", "PF_SUIUSD", "PF_LTCUSD", "PF_NEARUSD", "PF_DOTUSD", "PF_UNIUSD", "PF_AAVEUSD"]
PERP_RES = {5: "5m", 15: "15m", 30: "30m", 60: "1h", 240: "4h", 1440: "1d"}
TF_MIN = {"M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}
TF_NAME = {v: k for k, v in TF_MIN.items()}

_http = requests.Session()
_http.headers["User-Agent"] = "pdh-piege/1.0"
STATS = {"signals": 0, "fallback": False}


# =================================================================== STRATEGIE
def calc_atr(h, l, c, period=ATR_PERIOD):
    n = len(c)
    atr, tr, s = [0.0] * n, [0.0] * n, 0.0
    for i in range(n):
        a = h[i] - l[i]
        if i > 0:
            a = max(a, abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        tr[i] = a
        s += a
        if i >= period:
            s -= tr[i - period]
        atr[i] = s / period if i >= period - 1 else 0.0
    return atr


def in_session(ts):
    if not USE_SESSION:
        return True
    hr = datetime.fromtimestamp(ts, timezone.utc).hour
    return S1[0] <= hr < S1[1] or S2[0] <= hr < S2[1]


def find_traps(t, o, h, l, c, PH, PL, day_start):
    """Signaux PIEGE du jour (bougies a partir de day_start). Index chronologiques."""
    n = len(c)
    atr = calc_atr(h, l, c)
    last_closed = n - 2
    mid = (PH + PL) / 2.0
    out, used = [], {1: False, -1: False}
    for i in range(ATR_PERIOD + 1, last_closed + 1):
        if t[i] < day_start or not in_session(t[i]):
            continue
        a = atr[i]
        if a <= 0:
            continue
        for d in (-1, 1):                       # -1 = piege au PDH (vente), +1 = piege au PDL (achat)
            if used[d]:
                continue
            sweep = (h[i] > PH and c[i] < PH) if d < 0 else (l[i] < PL and c[i] > PL)
            if not sweep:
                continue
            j = -1
            for k in range(i, min(i + CONF_BARS, last_closed) + 1):
                body = (c[k] - o[k]) * d
                brk = True if k == i else ((c[k] < l[i]) if d < 0 else (c[k] > h[i]))
                if body >= DISP_ATR * a and brk:
                    j = k
                    break
            if j < 0:
                continue
            ext = max(h[i:j + 1]) if d < 0 else min(l[i:j + 1])
            entry = c[j]
            sl = ext - d * SL_BUF_ATR * a
            tp1, tp2 = mid, (PL if d < 0 else PH)
            risk = (entry - sl) * d
            if risk <= 0 or (tp1 - entry) * d <= 0 or (tp2 - entry) * d / risk < MIN_RR:
                continue
            # suivi apres l'entree : stop ou TP1 deja touches ?
            done = False
            for m in range(j + 1, n):
                if (l[m] <= sl) if d > 0 else (h[m] >= sl):
                    done = True
                    break
                if (h[m] >= tp1) if d > 0 else (l[m] <= tp1):
                    done = True
                    break
            out.append({"dir": d, "i": j, "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2,
                        "level": PH if d < 0 else PL, "done": done, "risk": risk})
            used[d] = True
    return out


# =================================================================== KRAKEN FUTURES
_rl_lock = threading.Lock()
_rl_last = [0.0]
HARD_END = [0.0]
_PAIRS = {}


def _throttle():
    with _rl_lock:
        w = _rl_last[0] + RATE_GAP - time.time()
        if w > 0:
            time.sleep(w)
        _rl_last[0] = time.time()


def fget(path, params=None, tries=4):
    for a in range(tries):
        if HARD_END[0] and time.time() > HARD_END[0]:
            return None
        _throttle()
        try:
            r = _http.get(FUTURES + path, params=params or {}, timeout=15)
            if r.status_code == 200:
                j = r.json()
                if isinstance(j, dict) and j.get("result", "success") == "success":
                    return j
                log.warning("Kraken Futures %s -> %s", path, str(j)[:160])
            else:
                log.warning("Kraken Futures %s -> HTTP %s", path, r.status_code)
        except Exception as e:
            log.warning("Kraken Futures %s : %s", path, e)
        time.sleep(1.5 + a)
    return None


def _tick_digits(tick):
    try:
        from decimal import Decimal
        return max(0, -Decimal(str(tick)).normalize().as_tuple().exponent)
    except Exception:
        return 5


def load_perps():
    j = fget("/derivatives/api/v3/instruments")
    if not j or not isinstance(j.get("instruments"), list):
        return
    for i in j["instruments"]:
        try:
            sym = i.get("symbol") or ""
            if not sym.startswith("PF_") or i.get("quote") != "USD" or not i.get("tradeable", True):
                continue
            if i.get("isExpired") or i.get("tradfi") or i.get("category") in PERP_EXCLUDE_CAT:
                continue
            base = i.get("base") or sym[3:-3]
            _PAIRS[sym] = {"name": f"{base} PERP", "dig": _tick_digits(i.get("tickSize", 0.00001))}
        except Exception:
            continue


def top_perps():
    load_perps()
    syms = []
    j = fget("/derivatives/api/v3/tickers") if _PAIRS else None
    if j and isinstance(j.get("tickers"), list):
        rows = []
        for t in j["tickers"]:
            try:
                sym = t.get("symbol")
                if sym in _PAIRS and not t.get("suspended"):
                    qv = float(t.get("volumeQuote") or 0)
                    if qv >= MIN_QUOTE_VOL:
                        rows.append((qv, sym))
            except Exception:
                continue
        rows.sort(reverse=True)
        syms = [k for _, k in rows[:TOP_N]]
    if not syms:
        log.warning("Tickers Kraken Futures indisponibles -> liste de secours")
        syms = list(PERP_FALLBACK)
        STATS["fallback"] = True
    for s in EXTRA_SYMBOLS:
        if s not in syms:
            syms.append(s)
    return syms


def klines(sym, tf_min, limit):
    res = PERP_RES.get(tf_min)
    if not res:
        return None
    now = int(time.time())
    j = fget(f"/api/charts/v1/trade/{sym}/{res}", {"from": now - (limit + 3) * tf_min * 60, "to": now})
    rows = j.get("candles") if j else None
    if not rows:
        return None
    try:
        rows = rows[-limit:]
        o = [float(r["open"]) for r in rows]
        h = [float(r["high"]) for r in rows]
        l = [float(r["low"]) for r in rows]
        c = [float(r["close"]) for r in rows]
        t = [int(r["time"]) // 1000 for r in rows]
    except Exception:
        return None
    return t, o, h, l, c


def digits(sym, price):
    d = _PAIRS.get(sym, {}).get("dig")
    if d is None:
        d = max(5, 4 - int(math.floor(math.log10(price)))) if price > 0 else 8
    return d


def display_name(sym):
    p = _PAIRS.get(sym)
    if p:
        return p["name"].replace("XBT", "BTC").replace("XDG", "DOGE")
    if sym.startswith("PF_") and sym.endswith("USD"):
        return sym[3:-3].replace("XBT", "BTC") + " PERP"
    return sym


# =================================================================== SCAN
def scan_symbol(sym, tfs):
    try:
        return _scan_symbol(sym, tfs)
    except Exception:
        log.exception("Erreur scan %s", sym)
        return []


def _scan_symbol(sym, tfs):
    day = klines(sym, 1440, 4)                      # bougies journalieres UTC
    if not day or len(day[0]) < 2:
        return []
    dt, do, dh, dl, dc = day
    day_start = dt[-1]                              # debut de la journee en cours
    PH, PL = dh[-2], dl[-2]                         # plus haut / plus bas de la veille
    if PH <= PL:
        return []
    opps = []
    for tf in tfs:
        kl = klines(sym, tf, SCAN_BARS)
        if not kl or len(kl[0]) < ATR_PERIOD + 10:
            continue
        t, o, h, l, c = kl
        n = len(c)
        for s in find_traps(t, o, h, l, c, PH, PL, day_start):
            STATS["signals"] += 1
            ago = (n - 1) - s["i"]
            run = (c[-1] - s["entry"]) * s["dir"] / s["risk"]
            if s["done"] or ago > RECENT_BARS or run > MAX_RUN_R or run <= -1:
                continue                               # plus jouable
            opps.append({
                "key": f"{sym}|{tf}|{s['dir']}|{day_start}",
                "sym": sym, "tf": tf, "dir": s["dir"], "dig": digits(sym, c[-1]),
                "entry": s["entry"], "sl": s["sl"], "tp1": s["tp1"], "tp2": s["tp2"],
                "level": s["level"], "rr": abs(s["tp2"] - s["entry"]) / s["risk"],
                "price": c[-1], "ago": ago,
            })
    return opps


# =================================================================== TELEGRAM
def tg_send(text):
    if DRY_RUN or not TG_TOKEN or not TG_CHAT:
        print("---- [TELEGRAM " + ("DRY-RUN" if DRY_RUN else "NON CONFIGURE") + "]\n" + text)
        return True
    for _ in range(3):
        try:
            r = _http.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                           json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML",
                                 "disable_web_page_preview": True}, timeout=10)
            if r.status_code == 200:
                return True
            log.warning("Telegram %s : %s", r.status_code, r.text[:200])
            if r.status_code == 429:
                time.sleep(int(r.json().get("parameters", {}).get("retry_after", 3)))
                continue
            return False
        except Exception as e:
            log.warning("Telegram : %s", e)
            time.sleep(2)
    return False


def fmt(v, dig):
    return f"{v:.{dig}f}"


def message(o):
    d, dig = o["dir"], o["dig"]
    if d > 0:
        titre = "\U0001F7E2 <b>PIEGE ACHAT</b>"
        expl = f"Passage sous le PDL ({fmt(o['level'], dig)}) puis retour au-dessus"
        tp2 = "TP2 (PDH)"
    else:
        titre = "\U0001F534 <b>PIEGE VENTE</b>"
        expl = f"Passage au-dessus du PDH ({fmt(o['level'], dig)}) puis retour en dessous"
        tp2 = "TP2 (PDL)"
    return (
        f"{titre} — <b>{display_name(o['sym'])}</b> {TF_NAME[o['tf']]}\n"
        f"{expl}\n"
        f"Entree : <code>{fmt(o['entry'], dig)}</code>\n"
        f"Stop : <code>{fmt(o['sl'], dig)}</code>\n"
        f"TP1 (50 %) : <code>{fmt(o['tp1'], dig)}</code>\n"
        f"{tp2} : <code>{fmt(o['tp2'], dig)}</code>\n"
        f"R:R {o['rr']:.1f} · prix actuel {fmt(o['price'], dig)} · signal il y a {o['ago']} bougie(s)"
    )


# =================================================================== ETAT (anti-doublon)
def load_state():
    try:
        with open(STATE_FILE) as f:
            st = json.load(f)
    except Exception:
        st = {"sent": {}}
    if not isinstance(st, dict) or not isinstance(st.get("sent"), dict):
        st = {"sent": {}}
    sent = st["sent"] = {k: v for k, v in st["sent"].items() if isinstance(v, (int, float))}
    for fn in glob.glob("pdh_state*.json"):
        if fn == STATE_FILE:
            continue
        try:
            with open(fn) as f:
                for k, v in json.load(f).get("sent", {}).items():
                    if isinstance(v, (int, float)):
                        sent.setdefault(k, v)
        except Exception:
            pass
    return st


def save_state(st):
    now = time.time()
    st["sent"] = {k: v for k, v in st.get("sent", {}).items() if now - v < 7 * 86400}
    st["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(STATE_FILE, "w") as f:
        json.dump(st, f, indent=1, sort_keys=True)


def run_once(st, tfs, syms):
    STATS["signals"] = 0
    opps = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        for res in ex.map(lambda s: scan_symbol(s, tfs), syms):
            opps.extend(res)
    sent = st.setdefault("sent", {})
    n = 0
    for o in sorted(opps, key=lambda x: (x["ago"], x["tf"])):
        if o["key"] in sent:
            continue
        if tg_send(message(o)):
            sent[o["key"]] = time.time()
            n += 1
            time.sleep(1)
    log.info("%d piege(s) aujourd'hui | %d jouable(s), %d envoye(s)", STATS["signals"], len(opps), n)
    save_state(st)


def main():
    tfs = [TF_MIN[x.strip().upper()] for x in SCAN_TFS.split(",") if x.strip().upper() in TF_MIN]
    if SHARD == 0 and os.environ.get("PDH_TEST", "").lower() in ("1", "true"):
        tg_send("✅ <b>TEST</b> — Strategie PDH/PDL connectee (perpetuels Kraken Pro, "
                + ", ".join(TF_NAME[t] for t in tfs) + ").\nSeuls les signaux PIEGE ACHAT / PIEGE VENTE seront envoyes.")
    st = load_state()
    end = time.time() + LOOP_MINUTES * 60
    if LOOP_MINUTES:
        HARD_END[0] = end + 12 * 60
    syms, syms_t = [], 0.0
    while True:
        t0 = time.time()
        if not syms or t0 - syms_t > 3600:
            STATS["fallback"] = False
            try:
                syms = [x for x in top_perps() if zlib.crc32(x.encode()) % SHARDS == SHARD]
            except Exception:
                log.exception("Liste des cryptos indisponible")
                syms = [x for x in PERP_FALLBACK if zlib.crc32(x.encode()) % SHARDS == SHARD]
                STATS["fallback"] = True
            syms_t = t0 - 3000 if STATS["fallback"] else t0
            log.info("Lot %d/%d : %d cryptos x %s", SHARD + 1, SHARDS, len(syms), ",".join(TF_NAME[t] for t in tfs))
        run_once(st, tfs, syms)
        if not LOOP_MINUTES:
            break
        wait = INTERVAL - (time.time() - t0)
        if time.time() + max(wait, 0) + 150 > end:
            break
        if wait > 0:
            time.sleep(wait)
    return 0


if __name__ == "__main__":
    sys.exit(main())
