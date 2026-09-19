"""
Breaker FVG Sniper v3.60 -- portage Python fidele de Breaker_FVG_Sniper.mq4
===========================================================================
ORDER BLOCK casse par une bougie de deplacement = BREAKER
 -> attente du retest breaker / FVG -> signal RETEST sur Telegram.

Scanner CRYPTO uniquement (Kraken Pro, API publique, sans cle).
Telegram : secrets TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID du depot.

Seul le signal RETEST est envoye (prix revenu dans l'OB apres le depart,
setup toujours valide, score >= MIN_SCORE).
"""
import os
import sys
import json
import math
import time
import logging
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bfs")

# =================================================================== INPUTS (identiques au .mq4)
ATRPeriod = 14
DispATR = 1.2
OBSearchBars = 5
OBUseWicks = True
OBMaxAge = 150
OBAtSwing = True
SwingLB = 20
BreakBodyATR = 1.0
BreakBeyondATR = 0.25
BreakCloseRatio = 0.60
RequireSweep = True
SweepLookback = 20
SwingLeft = 10
SwingRight = 3
RallyLB = 100
MinLegATR = 1.5
WickKillATR = 1.0
RequireFVG = True
MinFVG_ATR = 0.10
FVGConfirmBars = 2
TRIG_CONFIRM, TRIG_LIMIT = 0, 1
EntryTrigger = TRIG_CONFIRM
ConfirmMaxBars = 6
ConfirmMaxR = 0.5
ConfirmMaxATR = 1.0
UsePremiumDiscount = True
PDLookback = 500
PDMaxPct = 55.0
UseFlip = True
ENTRY_FVG50, ENTRY_FVGEDGE, ENTRY_BREAKER, ENTRY_MEAN = 0, 1, 2, 3
EntryType = ENTRY_BREAKER
SLBufferATR = 0.20
StopMinATR = 1.0
SL_BREAKER, SL_SWEEP, SL_AUTO = 0, 1, 2
StopType = SL_SWEEP
StopMaxATR = 3.0
TP1_R = 2.0
TP2_R = 3.0
TP2OnLiquidity = True
LiqLookback = 100
TPMaxR = 6.0
MinRR = 2.0
MoveBEAtTP1 = True
MaxBarsWait = 40
FilterHTFTrend = False
RejectOppWall = True
OppWallR = 1.0
KillzoneBonus = True
BiasEMA = 50
ScanBars = 300
GoMaxR = 0.33

# =================================================================== CONFIG CRYPTO (env)
SCAN_DIRECTION = int(os.environ.get("BFS_DIRECTION", "0"))          # 0 = achats+ventes, 1 = achats, -1 = ventes
SCAN_TFS = os.environ.get("BFS_TFS", "M15,M30,H1,H4")
MIN_SCORE = float(os.environ.get("BFS_MIN_SCORE", "40"))
TOP_N = int(os.environ.get("BFS_TOP_N", "30"))                      # top N paires Kraken par volume
MIN_QUOTE_VOL = float(os.environ.get("BFS_MIN_QUOTE_VOL", "1000000"))
EXTRA_SYMBOLS = [s.strip().upper() for s in os.environ.get("BFS_SYMBOLS", "").split(",") if s.strip()]
STATE_FILE = os.environ.get("BFS_STATE_FILE", "bfs_state.json")
DRY_RUN = os.environ.get("BFS_DRY_RUN", "").lower() in ("1", "true", "yes")

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

KRAKEN = "https://api.kraken.com"
QUOTE = os.environ.get("BFS_QUOTE", "USD").upper()          # paires Kraken Pro cotees en USD
FALLBACK_SYMBOLS = ["XXBTZUSD", "XETHZUSD", "SOLUSD", "XXRPZUSD", "ADAUSD", "XDGUSD", "AVAXUSD",
                    "LINKUSD", "DOTUSD", "SUIUSD", "XLTCZUSD", "NEARUSD", "PEPEUSD", "TRXUSD"]
EXCLUDE = ("USDT", "USDC", "DAI", "PYUSD", "USDG", "RLUSD", "USDQ", "USDR", "USDS", "USDD", "TUSD", "FDUSD",
           "EUR", "GBP", "EURT", "EURQ", "EURR", "ZEUR", "ZGBP", "AUD", "CAD", "CHF", "JPY", "PAXG", "XAUT", "TBTC")

# unites de temps en minutes (= parametre interval de l'API Kraken)
TF_MIN = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440, "W1": 10080}
TF_NAME = {v: k for k, v in TF_MIN.items()}

ST_WAIT, ST_LIVE, ST_TP2, ST_BE, ST_SL, ST_EXPIRED, ST_REJECT, ST_CANCEL, ST_MISSED, ST_TOOFAR = range(10)

_http = requests.Session()
_http.headers["User-Agent"] = "breaker-fvg-sniper/3.60"


# =================================================================== STRUCTURES
class OBZone:
    __slots__ = ("dir", "hi", "lo", "mHi", "mLo", "sh", "t", "sweepRef", "ext", "shExt", "alive")

    def __init__(self):
        for a in self.__slots__:
            setattr(self, a, 0)
        self.alive = False


class SwingPt:
    __slots__ = ("dir", "sh", "price", "ref", "ext", "shExt", "alive")

    def __init__(self):
        for a in self.__slots__:
            setattr(self, a, 0)
        self.alive = False


class Setup:
    def __init__(self):
        self.dir = 0
        self.bHi = self.bLo = self.mHi = self.mLo = 0.0
        self.kz = False
        self.tOB = 0
        self.shBreak = 0
        self.tBreak = 0
        self.ext = 0.0
        self.shExt = 0
        self.atr = 0.0
        self.disp = 0.0
        self.hasFVG = False
        self.fHi = self.fLo = 0.0
        self.tF = 0
        self.conf = False
        self.ready = False
        self.shReady = -1
        self.entry = self.sl = self.slCur = self.tp1 = self.tp2 = self.rr = 0.0
        self.status = ST_WAIT
        self.tp1Hit = False
        self.departed = False
        self.touched = False
        self.flip = False
        self.flipped = False
        self.pd = 0.0
        self.shTouch = 0
        self.shFill = -1
        self.tEnd = 0


# =================================================================== MOTEUR
class Engine:
    """Tableaux en serie (index 0 = bougie en cours, comme MQL4)."""

    def __init__(self, O, H, L, C, T, tf_min, digits, direction=0):
        self.O, self.H, self.L, self.C, self.T = O, H, L, C, T
        self.N = len(C)
        self.tf = tf_min
        self.dig = digits
        self.pt = 10 ** (-digits)
        self.dirFilter = direction
        self.A = [0.0] * self.N
        for i in range(self.N - ATRPeriod - 2, -1, -1):
            s = 0.0
            for j in range(i, i + ATRPeriod):
                s += max(H[j], C[j + 1]) - min(L[j], C[j + 1])
            self.A[i] = s / ATRPeriod
        self.OB = []
        self.S = []
        self.SW = []
        self.histDir = []
        self.histSh = []

    # ---- utilitaires
    def nd(self, v):
        return round(v, self.dig)

    def lowest(self, start, count):
        e = min(self.N - 1, start + count - 1)
        b = start
        L = self.L
        for i in range(start, e + 1):
            if L[i] < L[b]:
                b = i
        return b

    def highest(self, start, count):
        e = min(self.N - 1, start + count - 1)
        b = start
        H = self.H
        for i in range(start, e + 1):
            if H[i] > H[b]:
                b = i
        return b

    def dir_allowed(self, d):
        return self.dirFilter == 0 or self.dirFilter == d

    @staticmethod
    def in_killzone(t):
        h = datetime.fromtimestamp(t, tz=timezone.utc).hour
        return (6 <= h < 10) or (11 <= h < 15)

    # ---- boucle
    def run(self, bars):
        self.OB, self.S, self.SW, self.histDir, self.histSh = [], [], [], [], []
        start = min(bars, self.N - max(SweepLookback, SwingLeft) - OBSearchBars - ATRPeriod - SwingRight - 10)
        for i in range(start, 0, -1):
            atr = self.A[i]
            if atr <= 0:
                continue
            self.update_setups(i)
            self.update_obs(i, atr)
            self.detect_breakers(i, atr)
            self.detect_ob(i, atr)
            if len(self.OB) > 300:
                self.OB = [o for o in self.OB if o.alive]

    # ---- Order Blocks
    def detect_ob(self, i, atr):
        O, C = self.O, self.C
        body = C[i] - O[i]
        if -body >= DispATR * atr:
            j = i + 1
            while j <= i + OBSearchBars and j < self.N - 1:
                if C[j] > O[j]:
                    self.add_ob(-1, j, i)
                    break
                j += 1
        if body >= DispATR * atr:
            j = i + 1
            while j <= i + OBSearchBars and j < self.N - 1:
                if C[j] < O[j]:
                    self.add_ob(1, j, i)
                    break
                j += 1

    def add_ob(self, d, j, i):
        O, H, L, C = self.O, self.H, self.L, self.C
        for o in self.OB[-20:]:
            if o.sh == j and o.dir == d:
                return
        o = OBZone()
        o.dir = d
        o.sh = j
        o.t = self.T[j]
        o.hi = H[j] if OBUseWicks else max(O[j], C[j])
        o.lo = L[j] if OBUseWicks else min(O[j], C[j])
        o.mHi = max(O[j], C[j])
        o.mLo = min(O[j], C[j])
        zb = j + OBSearchBars
        if OBAtSwing and zb + 1 + SwingLB < self.N:
            if d < 0 and H[self.highest(i, zb - i + 1)] < H[self.highest(zb + 1, SwingLB)]:
                return
            if d > 0 and L[self.lowest(i, zb - i + 1)] > L[self.lowest(zb + 1, SwingLB)]:
                return
        elif OBAtSwing:
            return
        if d < 0:
            if C[i] >= o.lo:
                return
            o.sweepRef = L[self.lowest(j + 1, SweepLookback)]
            e = self.lowest(i, j - i)
            o.ext = L[e]
            o.shExt = e
        else:
            if C[i] <= o.hi:
                return
            o.sweepRef = H[self.highest(j + 1, SweepLookback)]
            e = self.highest(i, j - i)
            o.ext = H[e]
            o.shExt = e
        o.alive = True
        self.OB.append(o)

    def update_obs(self, cur, atr):
        C = self.C
        for o in self.OB:
            if not o.alive:
                continue
            if o.sh - cur > OBMaxAge:
                o.alive = False
            elif o.dir < 0 and C[cur] > o.hi:
                o.alive = False
            elif o.dir > 0 and C[cur] < o.lo:
                o.alive = False

    # ---- Breakers
    def detect_breakers(self, cur, atr):
        O, H, L, C = self.O, self.H, self.L, self.C
        bestB = bestS = None
        for w in self.SW:
            if not w.alive:
                continue
            if w.sh - cur > OBMaxAge:
                w.alive = False
                continue
            rg = max(H[cur] - L[cur], self.pt)
            if w.dir > 0:
                if C[cur] > w.price:
                    w.alive = False
                    swept = (not RequireSweep) or w.ext < w.ref
                    franche = ((C[cur] - O[cur]) >= BreakBodyATR * atr
                               and C[cur] - w.price >= BreakBeyondATR * atr
                               and (C[cur] - L[cur]) / rg >= BreakCloseRatio)
                    if swept and franche and (bestB is None or w.price > bestB.price):
                        bestB = w
                elif H[cur] > w.price + WickKillATR * atr:
                    w.alive = False
                elif L[cur] < w.ext:
                    w.ext = L[cur]
                    w.shExt = cur
            else:
                if C[cur] < w.price:
                    w.alive = False
                    swept = (not RequireSweep) or w.ext > w.ref
                    franche = ((O[cur] - C[cur]) >= BreakBodyATR * atr
                               and w.price - C[cur] >= BreakBeyondATR * atr
                               and (H[cur] - C[cur]) / rg >= BreakCloseRatio)
                    if swept and franche and (bestS is None or w.price < bestS.price):
                        bestS = w
                elif L[cur] < w.price - WickKillATR * atr:
                    w.alive = False
                elif H[cur] > w.ext:
                    w.ext = H[cur]
                    w.shExt = cur
        if bestB is not None and self.dir_allowed(1):
            self.breaker_from_swing(bestB, cur, atr)
        if bestS is not None and self.dir_allowed(-1):
            self.breaker_from_swing(bestS, cur, atr)

        sp = cur + SwingRight
        if sp + SwingLeft + 1 >= self.N:
            return
        if H[sp] > H[self.highest(sp + 1, SwingLeft)] and H[sp] >= H[self.highest(cur, SwingRight)]:
            opp = self.last_swing_before(-1, sp)
            span = opp - sp if (opp > sp and opp - sp <= RallyLB) else SwingLeft * 2
            span = max(1, min(span, self.N - sp - 2))
            ref = L[self.lowest(sp + 1, span)]
            if H[sp] - ref >= MinLegATR * atr:
                w = SwingPt()
                w.dir, w.sh, w.price, w.ref = 1, sp, H[sp], ref
                e = self.lowest(cur, SwingRight)
                w.ext, w.shExt, w.alive = L[e], e, True
                self.add_swing(w)
            self.histDir.append(1)
            self.histSh.append(sp)
        if L[sp] < L[self.lowest(sp + 1, SwingLeft)] and L[sp] <= L[self.lowest(cur, SwingRight)]:
            opp = self.last_swing_before(1, sp)
            span = opp - sp if (opp > sp and opp - sp <= RallyLB) else SwingLeft * 2
            span = max(1, min(span, self.N - sp - 2))
            ref = H[self.highest(sp + 1, span)]
            if ref - L[sp] >= MinLegATR * atr:
                w = SwingPt()
                w.dir, w.sh, w.price, w.ref = -1, sp, L[sp], ref
                e = self.highest(cur, SwingRight)
                w.ext, w.shExt, w.alive = H[e], e, True
                self.add_swing(w)
            self.histDir.append(-1)
            self.histSh.append(sp)

    def last_swing_before(self, d, sh):
        for i in range(len(self.histSh) - 1, -1, -1):
            if self.histDir[i] == d and self.histSh[i] > sh:
                return self.histSh[i]
        return -1

    def add_swing(self, w):
        if len(self.SW) > 200:
            self.SW = [x for x in self.SW if x.alive]
        self.SW.append(w)

    def breaker_from_swing(self, w, cur, atr):
        O, H, L, C = self.O, self.H, self.L, self.C
        d, sp, j = w.dir, w.sh, -1
        b = sp
        while b <= sp + OBSearchBars and b < self.N - 1:
            if (d > 0 and C[b] > O[b]) or (d < 0 and C[b] < O[b]):
                j = b
                break
            b += 1
        if j < 0 or (d > 0 and H[j] < w.price - 0.5 * atr) or (d < 0 and L[j] > w.price + 0.5 * atr):
            j = sp
        self.new_setup(d, j, w.ext, w.shExt, cur, atr)

    def new_setup(self, d, j, ext, shExt, cur, atr):
        O, H, L, C = self.O, self.H, self.L, self.C
        s = Setup()
        s.dir = d
        s.bHi = H[j] if OBUseWicks else max(O[j], C[j])
        s.bLo = L[j] if OBUseWicks else min(O[j], C[j])
        s.mHi = max(O[j], C[j])
        s.mLo = min(O[j], C[j])
        s.kz = self.in_killzone(self.T[cur])
        s.tOB = self.T[j]
        s.shBreak = cur
        s.tBreak = self.T[cur]
        s.ext = ext
        s.shExt = shExt
        s.atr = atr
        s.disp = abs(C[cur] - O[cur]) / atr
        self.S.append(s)

    # ---- FVG
    def find_fvg(self, s, cur):
        H, L = self.H, self.L
        newest = cur + 1
        oldest = min(s.shExt, self.N - 3)
        minGap = MinFVG_ATR * s.atr
        found = bestConf = False
        bestDist = fh = fl = 0.0
        ft = 0
        for k in range(newest, oldest + 1):
            if s.dir > 0:
                lo, hi = H[k + 1], L[k - 1]
            else:
                lo, hi = H[k - 1], L[k + 1]
            if hi <= lo or hi - lo < minGap:
                continue
            filled = False
            for m in range(k - 2, cur - 1, -1):
                if s.dir > 0 and L[m] <= lo:
                    filled = True
                    break
                if s.dir < 0 and H[m] >= hi:
                    filled = True
                    break
            if filled:
                continue
            conf = min(hi, s.bHi) > max(lo, s.bLo)
            dist = 0.0
            if not conf:
                dist = (lo - s.bHi) if lo > s.bHi else (s.bLo - hi)
            if (not found) or (conf and not bestConf) or (conf == bestConf and dist < bestDist):
                found, bestConf, bestDist, fh, fl, ft = True, conf, dist, hi, lo, self.T[k + 1]
        if found:
            s.hasFVG, s.fHi, s.fLo, s.tF = True, fh, fl, ft

    # ---- niveaux
    def compute_levels(self, s):
        conf = False
        if s.hasFVG:
            oLo, oHi = max(s.fLo, s.bLo), min(s.fHi, s.bHi)
            conf = oHi > oLo
            if conf:
                e = (oLo + oHi) / 2.0
            elif EntryType == ENTRY_FVG50:
                e = (s.fHi + s.fLo) / 2.0
            elif EntryType == ENTRY_FVGEDGE:
                e = s.fHi if s.dir > 0 else s.fLo
            elif EntryType == ENTRY_MEAN:
                e = (s.mHi + s.mLo) / 2.0
            else:
                e = s.bHi if s.dir > 0 else s.bLo
        else:
            e = (s.mHi + s.mLo) / 2.0 if EntryType == ENTRY_MEAN else (s.bHi if s.dir > 0 else s.bLo)

        buf = SLBufferATR * s.atr
        sweepSL = StopType == SL_SWEEP or (StopType == SL_AUTO and self.tf >= 60)
        if s.dir > 0:
            base = min(s.bLo, s.fLo) if s.hasFVG else s.bLo
            sl = base - buf
            if sweepSL and e - (s.ext - buf) <= StopMaxATR * s.atr:
                sl = min(sl, s.ext - buf)
        else:
            base = max(s.bHi, s.fHi) if s.hasFVG else s.bHi
            sl = base + buf
            if sweepSL and (s.ext + buf) - e <= StopMaxATR * s.atr:
                sl = max(sl, s.ext + buf)

        minRisk = StopMinATR * s.atr
        if abs(e - sl) < minRisk:
            sl = e - s.dir * minRisk
        risk = abs(e - sl)
        if risk < self.pt * 5:
            return False
        tp1 = e + s.dir * TP1_R * risk
        tp2 = e + s.dir * TP2_R * risk
        if TP2OnLiquidity:
            cnt = min(LiqLookback, self.N - s.shBreak - 2)
            if cnt > 0:
                if s.dir > 0:
                    liq = self.H[self.highest(s.shBreak, cnt)]
                    if tp1 < liq <= e + TPMaxR * risk:
                        tp2 = liq
                else:
                    liq = self.L[self.lowest(s.shBreak, cnt)]
                    if tp1 > liq >= e - TPMaxR * risk:
                        tp2 = liq
        if s.dir * (tp2 - tp1) < 0:
            tp2 = tp1
        rr = abs(tp2 - e) / risk
        if rr < MinRR:
            return False
        s.conf = conf
        s.entry = self.nd(e)
        s.sl = self.nd(sl)
        s.slCur = s.sl
        s.tp1 = self.nd(tp1)
        s.tp2 = self.nd(tp2)
        s.rr = rr
        return True

    # ---- gestion des setups
    def update_setups(self, cur):
        O, H, L, C = self.O, self.H, self.L, self.C
        k = 0
        while k < len(self.S):
            s = self.S[k]
            k += 1
            st = s.status
            if st != ST_WAIT and st != ST_LIVE:
                continue
            if s.shBreak <= cur:
                continue
            since = s.shBreak - cur
            d = s.dir

            if st == ST_WAIT:
                if since <= FVGConfirmBars and not s.ready:
                    hadF, oH, oL, oT = s.hasFVG, s.fHi, s.fLo, s.tF
                    self.find_fvg(s, cur)
                    if s.hasFVG or not RequireFVG:
                        if self.compute_levels(s):
                            if not s.ready:
                                self.make_ready(s, cur)
                        elif hadF:
                            s.fHi, s.fLo, s.tF = oH, oL, oT
                if s.status == ST_REJECT:
                    continue
                if not s.ready:
                    if since >= FVGConfirmBars:
                        s.status = ST_REJECT
                    continue
                if not s.departed:
                    rk = abs(s.entry - s.sl)
                    dep = (L[cur] > s.bHi or H[cur] >= s.entry + rk) if d > 0 else (H[cur] < s.bLo or L[cur] <= s.entry - rk)
                    if dep:
                        s.departed = True
                    if (H[cur] >= s.tp2) if d > 0 else (L[cur] <= s.tp2):
                        self.close_setup(s, ST_MISSED, cur)
                        continue
                    if (d > 0 and C[cur] < s.ext) or (d < 0 and C[cur] > s.ext):
                        self.close_setup(s, ST_CANCEL, cur)
                    elif since > MaxBarsWait:
                        self.close_setup(s, ST_EXPIRED, cur)
                    continue
                if s.shReady == cur:
                    continue

                if EntryTrigger == TRIG_CONFIRM:
                    inZone = L[cur] <= s.entry if d > 0 else H[cur] >= s.entry
                    if not s.touched and inZone:
                        s.touched = True
                        s.shTouch = cur
                    if s.touched:
                        far = self.zone_far(s)
                        if (C[cur] < far or L[cur] <= s.sl) if d > 0 else (C[cur] > far or H[cur] >= s.sl):
                            beyond = C[cur] < far if d > 0 else C[cur] > far
                            self.close_setup(s, ST_CANCEL, cur)
                            if beyond:
                                self.create_flip(s, cur)
                            continue
                        conf = (C[cur] > O[cur] and C[cur] > s.entry) if d > 0 else (C[cur] < O[cur] and C[cur] < s.entry)
                        maxDist = max(ConfirmMaxR * abs(s.entry - s.sl), ConfirmMaxATR * s.atr)
                        if conf and abs(C[cur] - s.entry) > maxDist:
                            self.close_setup(s, ST_TOOFAR, cur)
                            continue
                        if conf:
                            s.entry = self.nd(C[cur])
                            rk = abs(s.entry - s.sl)
                            s.tp1 = self.nd(s.entry + d * TP1_R * rk)
                            t2 = s.entry + d * TP2_R * rk
                            if d * (s.tp2 - t2) < 0:
                                s.tp2 = self.nd(t2)
                            s.rr = abs(s.tp2 - s.entry) / max(rk, self.pt)
                            s.status = ST_LIVE
                            s.shFill = cur
                            continue
                        if s.shTouch - cur >= ConfirmMaxBars:
                            self.close_setup(s, ST_EXPIRED, cur)
                        continue

                touch = EntryTrigger == TRIG_LIMIT and ((L[cur] <= s.entry) if d > 0 else (H[cur] >= s.entry))
                if touch:
                    s.status = ST_LIVE
                    s.shFill = cur
                    if (L[cur] <= s.sl) if d > 0 else (H[cur] >= s.sl):
                        self.close_setup(s, ST_SL, cur)
                    continue
                if (H[cur] >= s.tp2) if d > 0 else (L[cur] <= s.tp2):
                    self.close_setup(s, ST_MISSED, cur)
                    continue
                if (d > 0 and C[cur] < s.ext) or (d < 0 and C[cur] > s.ext):
                    self.close_setup(s, ST_CANCEL, cur)
                elif since > MaxBarsWait:
                    self.close_setup(s, ST_EXPIRED, cur)
                continue

            # ---- EN POSITION
            hitSL = L[cur] <= s.slCur if d > 0 else H[cur] >= s.slCur
            if hitSL:
                wasTP1 = s.tp1Hit
                self.close_setup(s, ST_BE if wasTP1 else ST_SL, cur)
                if not wasTP1 and ((C[cur] < self.zone_far(s)) if d > 0 else (C[cur] > self.zone_far(s))):
                    self.create_flip(s, cur)
                continue
            if (H[cur] >= s.tp2) if d > 0 else (L[cur] <= s.tp2):
                self.close_setup(s, ST_TP2, cur)
                continue
            if not s.tp1Hit:
                if (H[cur] >= s.tp1) if d > 0 else (L[cur] <= s.tp1):
                    s.tp1Hit = True
                    if MoveBEAtTP1:
                        s.slCur = s.entry

    def opp_wall(self, s):
        risk = abs(s.entry - s.sl)
        for o in self.OB:
            if not o.alive or o.dir != -s.dir:
                continue
            if s.dir > 0 and o.lo > s.entry and o.lo - s.entry < OppWallR * risk:
                return True
            if s.dir < 0 and o.hi < s.entry and s.entry - o.hi < OppWallR * risk:
                return True
        return False

    @staticmethod
    def zone_far(s):
        if s.dir > 0:
            return min(s.bLo, s.fLo) if s.hasFVG else s.bLo
        return max(s.bHi, s.fHi) if s.hasFVG else s.bHi

    def create_flip(self, src, cur):
        if not UseFlip or src.flip or src.flipped or not self.dir_allowed(-src.dir):
            return
        src.flipped = True
        atr = self.A[cur]
        if atr <= 0:
            return
        H, L = self.H, self.L
        s = Setup()
        s.dir = -src.dir
        s.flip = True
        s.bHi, s.bLo, s.mHi, s.mLo = src.bHi, src.bLo, src.mHi, src.mLo
        s.tOB = src.tOB
        s.shBreak = cur
        s.tBreak = self.T[cur]
        s.atr = atr
        s.disp = abs(self.C[cur] - self.O[cur]) / atr
        s.kz = self.in_killzone(self.T[cur])
        cnt = max(1, src.shBreak - cur + 1)
        if s.dir < 0:
            e = self.highest(cur, cnt)
            s.ext, s.shExt = H[e], e
            s.entry = s.bLo
            s.sl = s.bHi + SLBufferATR * atr
            if s.sl - s.entry < StopMinATR * atr:
                s.sl = s.entry + StopMinATR * atr
        else:
            e = self.lowest(cur, cnt)
            s.ext, s.shExt = L[e], e
            s.entry = s.bHi
            s.sl = s.bLo - SLBufferATR * atr
            if s.entry - s.sl < StopMinATR * atr:
                s.sl = s.entry - StopMinATR * atr
        rk = abs(s.entry - s.sl)
        s.tp1 = s.entry + s.dir * TP1_R * rk
        s.tp2 = s.entry + s.dir * TP2_R * rk
        liq = src.sl
        taken = L[cur] <= liq if s.dir < 0 else H[cur] >= liq
        if not taken and TP1_R * rk <= s.dir * (liq - s.entry) <= TPMaxR * rk:
            s.tp2 = liq
        if s.dir * (s.tp2 - s.tp1) < 0:
            s.tp1 = (s.entry + s.tp2) / 2.0
        s.entry, s.sl = self.nd(s.entry), self.nd(s.sl)
        s.slCur = s.sl
        s.tp1, s.tp2 = self.nd(s.tp1), self.nd(s.tp2)
        s.rr = abs(s.tp2 - s.entry) / max(rk, self.pt)
        s.departed = True
        s.pd = self.range_pos(s.entry, cur)
        if not self.pd_ok(s.dir, s.pd):
            return
        s.ready = True
        s.shReady = cur
        self.S.append(s)

    def range_pos(self, price, cur):
        cnt = max(10, min(PDLookback, self.N - cur - 2))
        hi = self.H[self.highest(cur, cnt)]
        lo = self.L[self.lowest(cur, cnt)]
        if hi - lo <= self.pt:
            return 50.0
        return 100.0 * (price - lo) / (hi - lo)

    @staticmethod
    def pd_ok(d, pos):
        if not UsePremiumDiscount:
            return True
        return pos <= PDMaxPct if d > 0 else pos >= 100.0 - PDMaxPct

    def make_ready(self, s, cur):
        s.pd = self.range_pos(s.entry, cur)
        if not self.pd_ok(s.dir, s.pd):
            s.status = ST_REJECT
            return
        if RejectOppWall and self.opp_wall(s):
            s.status = ST_REJECT
            return
        rk = abs(s.entry - s.sl)
        for b in range(s.shBreak - 1, cur, -1):
            if (self.L[b] > s.bHi or self.H[b] >= s.entry + rk) if s.dir > 0 else (self.H[b] < s.bLo or self.L[b] <= s.entry - rk):
                s.departed = True
            if (self.H[b] >= s.tp2) if s.dir > 0 else (self.L[b] <= s.tp2):
                s.status = ST_MISSED
                s.tEnd = self.T[b]
                return
        s.ready = True
        s.shReady = cur

    def close_setup(self, s, st, cur):
        s.status = st
        s.tEnd = self.T[cur]


# =================================================================== DONNEES KRAKEN PRO
import threading
_rl_lock = threading.Lock()
_rl_last = [0.0]
RATE_GAP = float(os.environ.get("BFS_RATE_GAP", "1.0"))   # Kraken public : ~1 requete / seconde


def _throttle():
    with _rl_lock:
        w = _rl_last[0] + RATE_GAP - time.time()
        if w > 0:
            time.sleep(w)
        _rl_last[0] = time.time()


def get(path, params, tries=4):
    for a in range(tries):
        _throttle()
        try:
            r = _http.get(KRAKEN + path, params=params, timeout=15)
            if r.status_code == 200:
                j = r.json()
                if j.get("error"):
                    log.warning("Kraken %s %s -> %s", path, params.get("pair", ""), j["error"])
                    if any("Unknown asset pair" in e or "Invalid arguments" in e for e in j["error"]):
                        return None
                else:
                    return j.get("result")
            else:
                log.warning("Kraken %s %s -> HTTP %s", path, params.get("pair", ""), r.status_code)
        except Exception as e:
            log.warning("Kraken %s %s : %s", path, params.get("pair", ""), e)
        time.sleep(1.5 + a)
    return None


_PAIRS = {}   # altname -> {"wsname":..., "dig":...}


def load_pairs():
    res = get("/0/public/AssetPairs", {})
    if not res:
        return
    for key, p in res.items():
        ws = p.get("wsname", "")
        if not ws.endswith("/" + QUOTE) or p.get("status", "online") != "online" or ".d" in key:
            continue
        base = ws.split("/")[0]
        if base in EXCLUDE:
            continue
        _PAIRS[key] = {"wsname": ws, "base": base, "dig": int(p.get("pair_decimals", 5))}


def klines(pair, tf_min, limit):
    res = get("/0/public/OHLC", {"pair": pair, "interval": tf_min})
    if not res:
        return None
    rows = next((v for k, v in res.items() if k != "last"), None)
    if not rows:
        return None
    rows = rows[-limit:][::-1]   # serie : index 0 = bougie en cours
    O = [float(r[1]) for r in rows]
    H = [float(r[2]) for r in rows]
    L = [float(r[3]) for r in rows]
    C = [float(r[4]) for r in rows]
    T = [int(r[0]) for r in rows]
    dig = _PAIRS.get(pair, {}).get("dig", 5)
    return O, H, L, C, T, dig


def top_symbols():
    load_pairs()
    syms = []
    if _PAIRS:
        keys = list(_PAIRS)
        rows = []
        for i in range(0, len(keys), 40):
            chunk = keys[i:i + 40]
            res = get("/0/public/Ticker", {"pair": ",".join(chunk)})
            if not res:
                continue
            for k, t in res.items():
                try:
                    qv = float(t["v"][1]) * float(t["p"][1])
                except Exception:
                    continue
                if k in _PAIRS and qv >= MIN_QUOTE_VOL:
                    rows.append((qv, k))
        rows.sort(reverse=True)
        syms = [k for _, k in rows[:TOP_N]]
    if not syms:
        log.warning("Ticker Kraken indisponible -> liste de secours")
        syms = list(FALLBACK_SYMBOLS)
    for s in EXTRA_SYMBOLS:
        if s not in syms:
            syms.append(s)
    return syms


def display_name(pair):
    p = _PAIRS.get(pair)
    if p:
        return p["wsname"].replace("XBT", "BTC").replace("XDG", "DOGE")
    return pair


def htf_of(tf):
    if tf <= 5:
        return 60
    if tf <= 30:
        return 240
    if tf <= 240:
        return 1440
    return 10080


def ema_last_closed(closes_series, period):
    """EMA facon iMA(MODE_EMA) evaluee sur la bougie cloturee (shift 1)."""
    c = closes_series[::-1]  # chronologique
    if len(c) < 3:
        return 0.0
    k = 2.0 / (period + 1)
    e = c[0]
    vals = [e]
    for x in c[1:]:
        e = x * k + e * (1 - k)
        vals.append(e)
    return vals[-2]


# =================================================================== SCAN
def fmt(v, dig):
    return f"{v:.{dig}f}"


def score_setup(eng, s, tf, bias):
    sc = 0.0
    if s.conf:
        sc += 25
    elif s.hasFVG:
        sc += 12
    sc += min(s.rr, 5.0) / 5.0 * 20.0
    sc += min(s.disp, 3.0) / 3.0 * 15.0
    sc += {1: 0, 5: 2, 15: 4, 30: 6, 60: 8}.get(tf, 10)
    if bias == s.dir:
        sc += 15
    sc += 5.0 * (1.0 - min(s.shBreak / max(MaxBarsWait, 1), 1.0))
    if KillzoneBonus and s.kz:
        sc += 10
    return min(sc, 90.0)


def scan_symbol(sym, tfs, need):
    out = []
    htf_cache = {}
    for tf in tfs:
        k = klines(sym, tf, min(720, need))
        if not k:
            continue
        O, H, L, C, T, dig = k
        if len(C) < SweepLookback + OBSearchBars + ATRPeriod + 60:
            continue
        eng = Engine(O, H, L, C, T, tf, dig, SCAN_DIRECTION)
        eng.run(ScanBars)

        htf = htf_of(tf)
        if htf not in htf_cache:
            hk = klines(sym, htf, 200)
            bias = 0
            if hk:
                hc = hk[3]
                e = ema_last_closed(hc, BiasEMA)
                if len(hc) > 1 and hc[1] > 0 and e > 0:
                    bias = 1 if hc[1] > e else -1
            htf_cache[htf] = bias
        bias = htf_cache[htf]

        bid = C[0]
        took = {1: False, -1: False}
        for s in reversed(eng.S):
            active = (s.status == ST_WAIT and s.ready) or (s.status == ST_LIVE and not s.tp1Hit)
            if not active or took[s.dir]:
                continue
            took[s.dir] = True
            if s.status != ST_WAIT:
                continue
            d = s.dir
            base = score_setup(eng, s, tf, bias)
            atr = eng.A[1]
            prox = 10.0 * (1.0 - min(abs(bid - s.entry) / (3.0 * atr), 1.0)) if atr > 0 else 0.0
            score = base + prox
            zfar = eng.zone_far(s)
            dead = (bid <= s.sl) if d > 0 else (bid >= s.sl)
            if EntryTrigger == TRIG_CONFIRM:
                if (bid < zfar) if d > 0 else (bid > zfar):
                    dead = True
                go = s.departed and not dead and ((bid <= s.entry) if d > 0 else (bid >= s.entry))
            else:
                r = abs(s.entry - s.sl)
                go = s.departed and ((s.entry - GoMaxR * r <= bid <= s.entry) if d > 0 else (s.entry <= bid <= s.entry + GoMaxR * r))
            if go and score >= MIN_SCORE:
                out.append({
                    "key": f"S{sym}_{tf}_{s.tBreak}_{d}{'_F' if s.flip else ''}G",
                    "sym": sym, "tf": tf, "dir": d, "flip": s.flip, "conf": s.conf, "fvg": s.hasFVG,
                    "entry": s.entry, "sl": s.sl, "tp1": s.tp1, "tp2": s.tp2, "rr": s.rr,
                    "score": score, "price": bid, "dig": dig, "tBreak": s.tBreak,
                })
    return out


def tg_send(text):
    if DRY_RUN or not TG_TOKEN or not TG_CHAT:
        print("---- [TELEGRAM " + ("DRY-RUN" if DRY_RUN else "NON CONFIGURE") + "]\n" + text)
        return True
    for a in range(3):
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


def message(o):
    d = o["dir"]
    dig = o["dig"]
    return (
        f"{'🟢' if d > 0 else '🔴'} <b>RETEST {'ACHAT' if d > 0 else 'VENTE'}</b> — <b>{display_name(o['sym'])}</b> {TF_NAME[o['tf']]}\n"
        f"Entree : <code>{fmt(o['entry'], dig)}</code>\n"
        f"Stop : <code>{fmt(o['sl'], dig)}</code>\n"
        f"TP1 : <code>{fmt(o['tp1'], dig)}</code>\n"
        f"TP2 : <code>{fmt(o['tp2'], dig)}</code>\n"
        f"Score {o['score']:.0f}/100 · RR {o['rr']:.1f}"
    )


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"sent": {}}


def save_state(st):
    now = time.time()
    st["sent"] = {k: v for k, v in st.get("sent", {}).items() if now - v < 14 * 86400}
    st["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(STATE_FILE, "w") as f:
        json.dump(st, f, indent=1, sort_keys=True)


def main():
    tfs = [TF_MIN[t.strip().upper()] for t in SCAN_TFS.split(",") if t.strip().upper() in TF_MIN]
    need = ScanBars + max(SweepLookback, SwingLeft) + OBSearchBars + ATRPeriod + max(LiqLookback, RallyLB) + 30
    need = max(need, 500)
    syms = top_symbols()
    log.info("Scan %d cryptos x %s (%d bougies)", len(syms), ",".join(TF_NAME[t] for t in tfs), need)

    opps = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        for res in ex.map(lambda s: scan_symbol(s, tfs, need), syms):
            opps.extend(res)

    st = load_state()
    sent = st.setdefault("sent", {})
    opps.sort(key=lambda o: -o["score"])
    n = 0
    for o in opps:
        if o["key"] in sent:
            continue
        if tg_send(message(o)):
            sent[o["key"]] = time.time()
            n += 1
            time.sleep(1)
    log.info("%d retest(s) actifs, %d nouveau(x) signal(aux) envoye(s)", len(opps), n)
    save_state(st)


if __name__ == "__main__":
    sys.exit(main())
