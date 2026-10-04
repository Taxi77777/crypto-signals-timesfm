# test
"""
BSCI -- scanner perpetuels Kraken Pro -> signaux Telegram
=========================================================
Strategie (video "Lecon de trading live") :
 1) Gros DEPLACEMENT qui laisse un BISI (FVG haussier) / SIBI (FVG baissier)
 2) On n'achete PAS le premier retest (erreur du debutant)
 3) Le prix reintegre le desequilibre, prend la liquidite (casse d'un creux / equal lows)
 4) Retracement en zone Fibo 0.5 - 0.786 de la jambe (sans casser l'origine)
 5) NOUVEAU deplacement ("3/DISPLACEMENT") qui laisse un nouveau BISI/SIBI
 6) Entree : retest du nouveau BISI (mode 0, original) ou cloture (mode 2, video)
    SL sous le creux du retracement, TP au sommet de la jambe (mode 0) ou R:R fixe (mode 1)

Portage Python fidele de l'indicateur MT4 BSCI.mq4 (meme logique, memes reglages).
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
log = logging.getLogger("bsci")

# =================================================================== REGLAGES STRATEGIE (= BSCI.mq4)
def _f(name, default):
    return float(os.environ.get(name, default))


DISP_ATR = _f("BSCI_DISP_ATR", 1.5)          # 1er deplacement : corps min (x ATR)
CONF_ATR = _f("BSCI_CONF_ATR", 0.8)          # 2e deplacement : corps min (x ATR)
MIN_GAP_ATR = _f("BSCI_MIN_GAP_ATR", 0.05)   # taille min du BISI/SIBI (x ATR)
ATR_PERIOD = int(_f("BSCI_ATR_PERIOD", 14))
LEG_BACK = int(_f("BSCI_LEG_BACK", 5))       # bougies avant le deplacement (origine de la jambe)
FIB_MIN = _f("BSCI_FIB_MIN", 0.5)            # retracement minimum (zone discount / premium)
FIB_MAX = _f("BSCI_FIB_MAX", 0.786)          # retracement maximum
FIB_INVALID = _f("BSCI_FIB_INVALID", 1.0)    # origine cassee = invalide
NEED_SWEEP = os.environ.get("BSCI_NEED_SWEEP", "1").lower() in ("1", "true", "yes")
MAX_RETRACE = int(_f("BSCI_MAX_RETRACE", 60))
PENDING_BARS = int(_f("BSCI_PENDING_BARS", 20))
ENTRY_MODE = int(_f("BSCI_ENTRY_MODE", 0))   # 0 = retest bord du BISI (original), 1 = milieu, 2 = cloture (video)
SL_BUFFER_ATR = _f("BSCI_SL_BUFFER_ATR", 0.1)
TP_MODE = int(_f("BSCI_TP_MODE", 0))         # 0 = sommet de la jambe (original), 1 = R:R fixe (video)
RR = _f("BSCI_RR", 2.0)
MIN_RR = _f("BSCI_MIN_RR", 1.5)
SCAN_BARS = int(_f("BSCI_SCAN_BARS", 300))
RECENT_BARS = int(_f("BSCI_RECENT_BARS", 2))  # signal envoye seulement s'il date de <= X bougies

ST_WAIT, ST_LIVE, ST_TP, ST_SL, ST_CANCEL = 1, 2, 3, 4, 5

# =================================================================== CONFIG SCANNER (env)
SCAN_TFS = os.environ.get("BSCI_TFS", "M5,M15,M30,H1")
TOP_N = int(_f("BSCI_TOP_N", 120))
MIN_QUOTE_VOL = _f("BSCI_MIN_QUOTE_VOL", 1000000)
SHARD = int(_f("BSCI_SHARD", 0))
SHARDS = max(1, int(_f("BSCI_SHARDS", 1)))
EXTRA_SYMBOLS = [s.strip().upper() for s in os.environ.get("BSCI_SYMBOLS", "").split(",") if s.strip()]
STATE_FILE = os.environ.get("BSCI_STATE_FILE", "bsci_state.json" if SHARDS == 1 else f"bsci_state_{SHARD}.json")
DRY_RUN = os.environ.get("BSCI_DRY_RUN", "").lower() in ("1", "true", "yes")
LOOP_MINUTES = _f("BSCI_LOOP_MINUTES", 0)    # >0 : scan en boucle pendant N minutes
INTERVAL = _f("BSCI_INTERVAL", 180)          # secondes entre deux debuts de scan
RATE_GAP = _f("BSCI_RATE_GAP", 1.0)          # Kraken public : ~1 requete / seconde

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

FUTURES = "https://futures.kraken.com"
PERP_EXCLUDE_CAT = ("Stablecoin", "Forex", "xStocks", "Commodities", "Equities", "Indices", "Pre-IPO", "DTF")
PERP_FALLBACK = ["PF_XBTUSD", "PF_ETHUSD", "PF_SOLUSD", "PF_XRPUSD", "PF_DOGEUSD", "PF_ADAUSD", "PF_AVAXUSD",
                 "PF_LINKUSD", "PF_SUIUSD", "PF_LTCUSD", "PF_NEARUSD", "PF_DOTUSD", "PF_UNIUSD", "PF_AAVEUSD"]
PERP_RES = {1: "1m", 5: "5m", 15: "15m", 30: "30m", 60: "1h", 240: "4h", 1440: "1d"}
TF_MIN = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
TF_NAME = {v: k for k, v in TF_MIN.items()}

_http = requests.Session()
_http.headers["User-Agent"] = "bsci/1.0"
STATS = {"setups": 0, "live": 0, "fallback": False}


# =================================================================== STRATEGIE
def calc_atr(h, l, c, period=ATR_PERIOD):
    n = len(c)
    atr = [0.0] * n
    tr = [0.0] * n
    s = 0.0
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


def analyze_bull(o, h, l, c, atr, direction):
    """Setups haussiers sur series chronologiques (index 0 = plus ancienne bougie,
    n-1 = bougie en cours). Pour la vente on passe les prix inverses (x -1)."""
    n = len(c)
    out = []
    last_closed = n - 2
    k = ATR_PERIOD + LEG_BACK + 2
    while k + 1 <= last_closed:
        a = atr[k]
        disp = (a > 0 and c[k] > o[k] and (c[k] - o[k]) >= DISP_ATR * a
                and l[k + 1] > h[k - 1] and (l[k + 1] - h[k - 1]) >= MIN_GAP_ATR * a)
        if not disp:
            k += 1
            continue
        f_lo, f_hi = h[k - 1], l[k + 1]
        leg_lo = min(l[max(0, k - LEG_BACK):k + 1])
        leg_hi = max(h[k], h[k + 1])
        phase, ret_low, i_ret = 0, float("inf"), -1
        swept, has_piv, piv, disc = not NEED_SWEEP, False, 0.0, False
        found = -1
        for j in range(k + 2, last_closed + 1):
            if j - k > MAX_RETRACE:
                break
            # prise de liquidite : casse d'un creux (pivot / equal lows) deja forme
            if not swept and has_piv and l[j] < piv:
                swept = True
            if j - 2 > k and l[j - 1] < l[j - 2] and l[j - 1] < l[j]:
                has_piv, piv = True, l[j - 1]
            if phase == 0:
                if l[j] > f_hi:
                    leg_hi = max(leg_hi, h[j])           # la jambe continue
                    continue
                phase = 1                                 # le prix reintegre le BISI
                leg_hi = max(leg_hi, h[j])
            elif h[j] > leg_hi and not disc:
                leg_hi = h[j]                             # nouveau sommet avant la zone discount
                ret_low = float("inf")
            rng = leg_hi - leg_lo
            if rng <= 0:
                break
            if l[j] < leg_hi - FIB_INVALID * rng:
                break                                     # origine cassee
            if l[j] < ret_low:
                ret_low, i_ret = l[j], j
            if ret_low < leg_hi - FIB_MAX * rng:
                break                                     # trop profond
            if ret_low <= leg_hi - FIB_MIN * rng:
                disc = True
            if not (disc and swept) or j + 1 > last_closed or j < 2:
                continue
            aj = atr[j]
            if aj <= 0:
                continue
            conf = (c[j] > o[j] and (c[j] - o[j]) >= CONF_ATR * aj and c[j] > max(h[j - 1], h[j - 2])
                    and l[j + 1] > h[j - 1] and (l[j + 1] - h[j - 1]) >= MIN_GAP_ATR * aj)
            if not conf:
                continue
            n_lo, n_hi = h[j - 1], l[j + 1]
            entry = n_hi if ENTRY_MODE == 0 else (n_lo + n_hi) / 2.0 if ENTRY_MODE == 1 else c[j + 1]
            sl = ret_low - SL_BUFFER_ATR * aj
            risk = entry - sl
            if risk <= 0:
                break
            tp = leg_hi if TP_MODE == 0 else entry + RR * risk
            if (tp - entry) / risk < MIN_RR:
                break
            s = {"dir": direction, "i_disp": k, "i_conf": j, "i_entry": -1, "i_end": -1,
                 "f_lo": f_lo, "f_hi": f_hi, "origin": leg_lo, "extreme": leg_hi,
                 "n_lo": n_lo, "n_hi": n_hi, "entry": entry, "sl": sl, "tp": tp, "state": ST_WAIT}
            if ENTRY_MODE == 2:
                s["state"], s["i_entry"] = ST_LIVE, j + 1
            for m in range(j + 2, n):
                if s["state"] == ST_WAIT:
                    if m - (j + 1) > PENDING_BARS:
                        s["state"], s["i_end"] = ST_CANCEL, m
                        break
                    if l[m] <= entry:
                        s["state"], s["i_entry"] = ST_LIVE, m
                        if l[m] <= sl:
                            s["state"], s["i_end"] = ST_SL, m
                            break
                        continue
                    if h[m] >= tp:
                        s["state"], s["i_end"] = ST_CANCEL, m   # parti sans retest
                        break
                elif s["state"] == ST_LIVE:
                    if l[m] <= sl:
                        s["state"], s["i_end"] = ST_SL, m
                        break
                    if h[m] >= tp:
                        s["state"], s["i_end"] = ST_TP, m
                        break
            out.append(s)
            found = j
            break
        k = found + 1 if found >= 0 else k + 1
    return out


def _flip(s):
    s["f_lo"], s["f_hi"] = -s["f_hi"], -s["f_lo"]
    s["n_lo"], s["n_hi"] = -s["n_hi"], -s["n_lo"]
    for key in ("origin", "extreme", "entry", "sl", "tp"):
        s[key] = -s[key]
    return s


def analyze(o, h, l, c):
    """Achats + ventes, tries par bougie de confirmation."""
    atr = calc_atr(h, l, c)
    bull = analyze_bull(o, h, l, c, atr, 1)
    bear = [_flip(s) for s in analyze_bull([-x for x in o], [-x for x in l], [-x for x in h],
                                           [-x for x in c], atr, -1)]
    return sorted(bull + bear, key=lambda s: s["i_conf"])


# =================================================================== KRAKEN FUTURES (API publique)
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
    """Series chronologiques (index 0 = plus ancienne, -1 = bougie en cours)."""
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
    dig = _PAIRS.get(sym, {}).get("dig")
    if dig is None:
        dig = max(5, 4 - int(math.floor(math.log10(c[-1])))) if c and c[-1] > 0 else 8
    return o, h, l, c, t, dig


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
    opps = []
    for tf in tfs:
        kl = klines(sym, tf, SCAN_BARS)
        if not kl or len(kl[3]) < ATR_PERIOD + LEG_BACK + 30:
            continue
        o, h, l, c, t, dig = kl
        n = len(c)
        setups = analyze(o, h, l, c)
        STATS["setups"] += len(setups)
        for s in reversed(setups):
            # signal PRESENT : ordre en attente juste confirme, ou entree declenchee a l'instant
            if s["state"] == ST_WAIT:
                ago = (n - 1) - (s["i_conf"] + 1)
            elif s["state"] == ST_LIVE:
                ago = (n - 1) - s["i_entry"]
                STATS["live"] += 1
            else:
                continue
            if ago > RECENT_BARS:
                continue
            risk = abs(s["entry"] - s["sl"])
            opps.append({
                "key": f"{sym}|{tf}|{s['dir']}|{t[s['i_conf']]}",
                "sym": sym, "tf": tf, "dir": s["dir"], "state": s["state"], "dig": dig,
                "entry": s["entry"], "sl": s["sl"], "tp": s["tp"],
                "rr": abs(s["tp"] - s["entry"]) / risk if risk > 0 else 0.0,
                "price": c[-1],
            })
            break
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
    sens = "ACHAT" if d > 0 else "VENTE"
    if o["state"] == ST_WAIT:
        titre = f"{'🟢' if d > 0 else '🔴'} <b>BSCI {sens} LIMITE</b>"
        action = f"⏳ Ordre limite au retest du {'BISI' if d > 0 else 'SIBI'}"
    else:
        titre = f"{'🟢' if d > 0 else '🔴'} <b>BSCI {sens} MAINTENANT</b>"
        action = "⚡ Entree declenchee"
    return (
        f"{titre} — <b>{display_name(o['sym'])}</b> {TF_NAME[o['tf']]}\n"
        f"{action}\n"
        f"Entree : <code>{fmt(o['entry'], dig)}</code>\n"
        f"Stop : <code>{fmt(o['sl'], dig)}</code>\n"
        f"TP : <code>{fmt(o['tp'], dig)}</code>\n"
        f"R:R {o['rr']:.1f} · prix actuel {fmt(o['price'], dig)}"
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
    for fn in glob.glob("bsci_state*.json"):             # signaux deja envoyes par les autres lots
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
    st["sent"] = {k: v for k, v in st.get("sent", {}).items() if now - v < 14 * 86400}
    st["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(STATE_FILE, "w") as f:
        json.dump(st, f, indent=1, sort_keys=True)


def run_once(st, tfs, syms):
    STATS["setups"] = STATS["live"] = 0
    opps = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        for res in ex.map(lambda s: scan_symbol(s, tfs), syms):
            opps.extend(res)
    sent = st.setdefault("sent", {})
    n = 0
    # une seule alerte par setup : la cle ne depend pas de l'etat (limite puis declenche = 1 message)
    for o in sorted(opps, key=lambda x: (x["state"] != ST_LIVE, x["tf"])):
        if o["key"] in sent:
            continue
        if tg_send(message(o)):
            sent[o["key"]] = time.time()
            n += 1
            time.sleep(1)
    log.info("%d setups sur l'historique | %d signal(aux) present(s), %d envoye(s)", STATS["setups"], len(opps), n)
    save_state(st)


def main():
    tfs = [TF_MIN[x.strip().upper()] for x in SCAN_TFS.split(",") if x.strip().upper() in TF_MIN]
    if SHARD == 0 and os.environ.get("BSCI_TEST", "").lower() in ("1", "true"):
        tg_send("✅ <b>TEST</b> — BSCI connecte (perpetuels Kraken Pro, " + ", ".join(TF_NAME[t] for t in tfs) + ").")
    st = load_state()
    end = time.time() + LOOP_MINUTES * 60
    if LOOP_MINUTES:
        HARD_END[0] = end + 12 * 60
    syms, syms_t = [], 0.0
    while True:
        t0 = time.time()
        if not syms or t0 - syms_t > 3600:                # liste rafraichie toutes les heures
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
