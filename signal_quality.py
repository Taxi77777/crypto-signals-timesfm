"""
╔══════════════════════════════════════════════════════════════════╗
║  signal_quality.py — TRIER LES SIGNAUX PAR PROBABILITE REELLE    ║
║                                                                   ║
║  Deux outils independants :                                       ║
║                                                                   ║
║  PARTIE 1 — CALIBRATION (le plus important)                       ║
║    Lit paper_trades.csv et mesure, POUR CHAQUE CONDITION, le      ║
║    taux de reussite reellement obtenu. Tu arretes de regler tes   ║
║    seuils a l'intuition : tu lis ce qui a paye.                   ║
║                                                                   ║
║  PARTIE 2 — QUATRE GARDE-FOUS AVANT L'ENTREE                      ║
║    a) Regime BTC     — ne pas acheter un alt quand BTC s'effondre ║
║    b) Funding rate   — ne pas payer pour garder la position       ║
║    c) Cout / objectif— refuser si les frais mangent le gain       ║
║    d) Concentration  — 8 positions correlees = 1 seul pari        ║
║                                                                   ║
║  Utilisation dans bot_once.py, juste avant d'ouvrir la position : ║
║                                                                   ║
║      from signal_quality import evaluer_signal                    ║
║      verdict = evaluer_signal(signal, positions_ouvertes)         ║
║      if not verdict.accepte:                                      ║
║          log.info(f"Signal refuse : {verdict.raisons}")           ║
║          continue                                                  ║
║      # verdict.score = 0 a 100, a afficher dans l'alerte          ║
║                                                                   ║
║  Et une fois par jour, en ligne de commande :                     ║
║      python signal_quality.py                                     ║
╚══════════════════════════════════════════════════════════════════╝
"""
import os
import csv
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

import requests

log = logging.getLogger("IHP-QUALITY")

try:
    from config import (
        LEVERAGE, TAKER_FEE_PCT, MAX_HOLD_HOURS,
        PAPER_TRADES_FILE, MEXC_BASE_URL,
    )
except ImportError:  # utilisable seul, hors du bot
    LEVERAGE, TAKER_FEE_PCT, MAX_HOLD_HOURS = 20, 0.06, 24.0
    PAPER_TRADES_FILE = "paper_trades.csv"
    MEXC_BASE_URL = "https://contract.mexc.com"


# ══════════════════════════════════════════════════════════════════
#  PARAMETRES DES GARDE-FOUS
# ══════════════════════════════════════════════════════════════════
# a) Regime BTC — un alt qui monte pendant que BTC chute de 2 % en 1 h,
#    c'est un pari contre le marche entier. Le carnet d'ordres ne voit
#    pas ca : il ne regarde qu'une seule paire.
BTC_CHUTE_MAX_1H_PCT = 1.2      # au-dela, on refuse les ACHATS d'alts
BTC_HAUSSE_MAX_1H_PCT = 1.2     # au-dela, on refuse les VENTES d'alts

# b) Funding rate — sur les perpetuels, tu paies (ou recois) toutes les
#    8 h. Garder 24 h = 3 paiements. A levier x20, un funding de 0,05 %
#    coute 1 % de la marge par paiement, soit 3 % sur la duree.
#    Ton moteur de simulation ignore completement ce cout.
FUNDING_MAX_ADVERSE_PCT = 0.05  # refuse si le funding joue contre toi

# c) Cout / objectif — les frais taker sont de TAKER_FEE_PCT a l'aller
#    ET au retour. Si l'objectif de gain n'est pas nettement plus grand,
#    le trade est perdant par construction.
RATIO_COUT_MAX = 0.20           # les frais ne doivent pas depasser 20 % du gain visé

# d) Concentration — plafond de positions dans le meme sens.
MAX_POSITIONS_MEME_SENS = 4
GROUPES_CORRELES = {
    "majors": {"BTC", "ETH"},
    "l1":     {"SOL", "AVAX", "NEAR", "SUI", "APT", "SEI", "TON", "DOT", "ADA"},
    "defi":   {"UNI", "LINK", "AAVE", "MKR", "CRV", "LDO", "INJ"},
    "meme":   {"DOGE", "SHIB", "PEPE", "WIF", "BONK", "FLOKI"},
}
MAX_POSITIONS_PAR_GROUPE = 2


@dataclass
class Verdict:
    accepte: bool = True
    score: float = 50.0
    raisons: list = field(default_factory=list)   # pourquoi refuse
    atouts: list = field(default_factory=list)    # ce qui joue en faveur

    def refuser(self, motif: str):
        self.accepte = False
        self.raisons.append(motif)

    def __str__(self):
        etat = "ACCEPTE" if self.accepte else "REFUSE"
        return f"[{etat} {self.score:.0f}/100] " + " | ".join(self.raisons or self.atouts)


# ══════════════════════════════════════════════════════════════════
#  DONNEES DE MARCHE (tolerantes a la panne : jamais de blocage)
# ══════════════════════════════════════════════════════════════════
_cache = {}

def _cache_get(cle, ttl_sec):
    entree = _cache.get(cle)
    if entree and (datetime.now().timestamp() - entree[0]) < ttl_sec:
        return entree[1]
    return None

def _cache_set(cle, valeur):
    _cache[cle] = (datetime.now().timestamp(), valeur)


def variation_btc_1h() -> float:
    """Variation de BTC sur la derniere heure, en %. 0.0 si indisponible."""
    cache = _cache_get("btc_1h", 120)
    if cache is not None:
        return cache
    try:
        r = requests.get(
            f"{MEXC_BASE_URL}/api/v1/contract/kline/BTC_USDT",
            params={"interval": "Min60", "limit": 2}, timeout=8,
        )
        d = r.json().get("data", {})
        closes = d.get("close") or []
        if len(closes) >= 2 and float(closes[-2]):
            var = (float(closes[-1]) - float(closes[-2])) / float(closes[-2]) * 100.0
            _cache_set("btc_1h", var)
            return var
    except Exception as e:
        log.debug(f"[QUALITY] Variation BTC indisponible : {e}")
    return 0.0


def funding_rate(symbol: str) -> float:
    """Funding rate courant en %, signe. 0.0 si indisponible."""
    cache = _cache_get(f"fund_{symbol}", 300)
    if cache is not None:
        return cache
    try:
        r = requests.get(
            f"{MEXC_BASE_URL}/api/v1/contract/funding_rate/{symbol}", timeout=8
        )
        taux = float(r.json().get("data", {}).get("fundingRate", 0)) * 100.0
        _cache_set(f"fund_{symbol}", taux)
        return taux
    except Exception as e:
        log.debug(f"[QUALITY] Funding {symbol} indisponible : {e}")
    return 0.0


def _base(symbol: str) -> str:
    return str(symbol).split("_")[0].upper()


def _groupe(symbol: str) -> str:
    b = _base(symbol)
    for nom, membres in GROUPES_CORRELES.items():
        if b in membres:
            return nom
    return "autres"


# ══════════════════════════════════════════════════════════════════
#  PARTIE 2 — EVALUATION D'UN SIGNAL
# ══════════════════════════════════════════════════════════════════
def evaluer_signal(signal, positions_ouvertes=None) -> Verdict:
    v = Verdict()
    positions_ouvertes = positions_ouvertes or []
    sens = signal.direction.upper()
    sym = str(signal.symbol)
    entree = float(signal.entry)
    tp = float(getattr(signal, "tp", 0) or 0)

    # ── a) Regime BTC ────────────────────────────────────────────
    if _base(sym) != "BTC":
        btc = variation_btc_1h()
        if sens == "BUY" and btc <= -BTC_CHUTE_MAX_1H_PCT:
            v.refuser(f"BTC chute de {btc:.2f}% sur 1 h — achat d'alt a contre-courant")
        elif sens == "SELL" and btc >= BTC_HAUSSE_MAX_1H_PCT:
            v.refuser(f"BTC monte de {btc:.2f}% sur 1 h — vente d'alt a contre-courant")
        elif abs(btc) < 0.3:
            v.atouts.append(f"BTC calme ({btc:+.2f}% sur 1 h)")
            v.score += 6
        elif (sens == "BUY" and btc > 0.3) or (sens == "SELL" and btc < -0.3):
            v.atouts.append(f"BTC dans le sens du trade ({btc:+.2f}%)")
            v.score += 10

    # ── b) Funding rate ──────────────────────────────────────────
    f = funding_rate(sym)
    if f:
        # funding positif = les longs paient les shorts
        adverse = f if sens == "BUY" else -f
        nb_paiements = max(1, int(MAX_HOLD_HOURS // 8))
        cout_marge = abs(adverse) * LEVERAGE * nb_paiements
        if adverse > FUNDING_MAX_ADVERSE_PCT:
            v.refuser(
                f"funding {f:+.4f}% contre la position — "
                f"environ {cout_marge:.1f}% de la marge sur {MAX_HOLD_HOURS:.0f} h"
            )
        elif adverse < -FUNDING_MAX_ADVERSE_PCT:
            v.atouts.append(f"funding {f:+.4f}% en ta faveur (+{cout_marge:.1f}% de marge)")
            v.score += 8

    # ── c) Cout par rapport a l'objectif ─────────────────────────
    if tp > 0 and entree > 0:
        gain_pct = abs((tp - entree) / entree * 100.0)
        frais_pct = TAKER_FEE_PCT * 2.0
        if gain_pct <= 0:
            v.refuser("objectif de gain invalide")
        else:
            ratio = frais_pct / gain_pct
            if ratio > RATIO_COUT_MAX:
                v.refuser(
                    f"frais {frais_pct:.2f}% pour un objectif de {gain_pct:.2f}% "
                    f"({ratio*100:.0f}% du gain part en frais)"
                )
            elif ratio < 0.05:
                v.atouts.append(f"objectif {gain_pct:.2f}% tres au-dessus des frais")
                v.score += 6

    # ── d) Concentration du portefeuille ─────────────────────────
    meme_sens = [p for p in positions_ouvertes
                 if str(p.get("direction", "")).upper() == sens]
    if len(meme_sens) >= MAX_POSITIONS_MEME_SENS:
        v.refuser(
            f"{len(meme_sens)} positions deja ouvertes en {sens} — "
            "ce serait le meme pari une fois de plus"
        )

    g = _groupe(sym)
    if g != "autres":
        meme_groupe = [p for p in meme_sens if _groupe(p.get("symbol", "")) == g]
        if len(meme_groupe) >= MAX_POSITIONS_PAR_GROUPE:
            v.refuser(
                f"{len(meme_groupe)} positions deja ouvertes sur le groupe '{g}' "
                f"({', '.join(_base(p.get('symbol','')) for p in meme_groupe)})"
            )

    if any(_base(p.get("symbol", "")) == _base(sym) for p in positions_ouvertes):
        v.refuser(f"position deja ouverte sur {_base(sym)}")

    # ── Bonus de qualite intrinseque ─────────────────────────────
    cons = float(getattr(signal, "consensus_pct", 0) or 0)
    if cons >= 90:
        v.atouts.append(f"consensus {cons:.0f}% (tres eleve)")
        v.score += 12
    elif cons >= 85:
        v.score += 6

    tconf = getattr(signal, "timesfm_confidence", None)
    if tconf is not None:
        c = tconf * 100 if tconf <= 1 else tconf
        if c >= 75:
            v.atouts.append(f"IA confiante a {c:.0f}%")
            v.score += 10
        elif c >= 65:
            v.score += 5

    nex = getattr(signal, "n_exchanges", None)
    if nex:
        if nex >= 6:
            v.atouts.append("6 exchanges sur 6 ont repondu")
            v.score += 8
        elif nex <= 3:
            v.raisons.append(f"seulement {nex} exchanges — consensus fragile")
            v.score -= 10

    v.score = max(0.0, min(100.0, v.score))
    if not v.accepte:
        v.score = min(v.score, 35.0)
    return v


# ══════════════════════════════════════════════════════════════════
#  PARTIE 1 — CALIBRATION SUR TES PROPRES TRADES
# ══════════════════════════════════════════════════════════════════
def _lire_trades(chemin=None):
    chemin = chemin or PAPER_TRADES_FILE
    if not os.path.exists(chemin):
        return []
    lignes = []
    with open(chemin, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                r["pnl_usdt"] = float(r.get("pnl_usdt") or 0)
                r["move_pct"] = float(r.get("move_pct") or 0)
                r["consensus_pct"] = float(r.get("consensus_pct") or 0)
                r["duration_min"] = int(float(r.get("duration_min") or 0))
                lignes.append(r)
            except (TypeError, ValueError):
                continue
    return lignes


def _bloc(nom, groupes):
    """Formate un tableau : nom du bucket, n, reussite, PnL, profit factor."""
    out = [f"\n{nom}", "-" * 74,
           f"  {'bucket':<22}{'n':>5}{'reussite':>11}{'PnL total':>13}{'PF':>8}"]
    for cle in sorted(groupes):
        trades = groupes[cle]
        n = len(trades)
        if n == 0:
            continue
        gains = [t["pnl_usdt"] for t in trades if t["pnl_usdt"] > 0]
        pertes = [t["pnl_usdt"] for t in trades if t["pnl_usdt"] <= 0]
        pf = (sum(gains) / abs(sum(pertes))) if pertes and sum(pertes) else None
        out.append(
            f"  {str(cle):<22}{n:>5}{len(gains)/n*100:>10.0f}%"
            f"{sum(t['pnl_usdt'] for t in trades):>13.4f}"
            f"{(f'{pf:.2f}' if pf is not None else '  inf'):>8}"
            + ("   <- trop peu" if n < 10 else "")
        )
    return "\n".join(out)


def rapport_calibration(chemin=None) -> str:
    trades = _lire_trades(chemin)
    if not trades:
        return ("CALIBRATION — aucun trade dans paper_trades.csv.\n"
                "Laisse tourner le bot, puis reviens.")

    n = len(trades)
    entete = [
        "=" * 74,
        f"  CALIBRATION SUR TES PROPRES TRADES — {n} positions fermees",
        "=" * 74,
        "",
        "  Ce rapport ne devine rien. Il mesure ce qui a paye chez TOI.",
        "  Un bucket sous 10 trades ne veut encore rien dire.",
    ]

    # Par consensus
    par_cons = defaultdict(list)
    for t in trades:
        c = t["consensus_pct"]
        cle = "< 80 %" if c < 80 else "80-85 %" if c < 85 else "85-90 %" if c < 90 else ">= 90 %"
        par_cons[cle].append(t)

    # Par raison de sortie
    par_sortie = defaultdict(list)
    for t in trades:
        par_sortie[t.get("exit_reason", "?")].append(t)

    # Par paire
    par_paire = defaultdict(list)
    for t in trades:
        par_paire[_base(t.get("symbol", "?"))].append(t)

    # Par groupe correle
    par_groupe = defaultdict(list)
    for t in trades:
        par_groupe[_groupe(t.get("symbol", "?"))].append(t)

    # Par heure UTC d'ouverture (session de marche)
    par_session = defaultdict(list)
    for t in trades:
        try:
            h = datetime.fromisoformat(t["open_time"].replace("Z", "+00:00")).hour
        except Exception:
            continue
        if 0 <= h < 7:
            par_session["asie (00-07 UTC)"].append(t)
        elif 7 <= h < 13:
            par_session["europe (07-13 UTC)"].append(t)
        elif 13 <= h < 21:
            par_session["US (13-21 UTC)"].append(t)
        else:
            par_session["creux (21-24 UTC)"].append(t)

    # Par sens
    par_sens = defaultdict(list)
    for t in trades:
        par_sens[t.get("direction", "?")].append(t)

    corps = [
        _bloc("PAR NIVEAU DE CONSENSUS", par_cons),
        _bloc("PAR RAISON DE SORTIE", par_sortie),
        _bloc("PAR SESSION HORAIRE", par_session),
        _bloc("PAR SENS", par_sens),
        _bloc("PAR GROUPE CORRELE", par_groupe),
        _bloc("PAR PAIRE", par_paire),
    ]

    # Lecture guidee
    liqs = len(par_sortie.get("LIQUIDATION", []))
    tps = len(par_sortie.get("TAKE_PROFIT", []))
    trails = len(par_sortie.get("TRAILING_STOP", []))
    exps = len(par_sortie.get("EXPIRATION", []))
    duree_moy = sum(t["duration_min"] for t in trades) / n

    lecture = [
        "\n" + "=" * 74,
        "  COMMENT LIRE CE RAPPORT",
        "=" * 74,
        f"  Liquidations : {liqs}/{n} ({liqs/n*100:.0f}%).",
        "    Au-dela de 20 %, le levier est trop haut pour cette strategie.",
        f"  Objectifs atteints : {tps} | Gains securises : {trails} | Delai ecoule : {exps}",
        "    Si le trailing ferme presque tout, c'est LUI qui porte la performance,",
        "    pas l'objectif de gain. Baisser le TP le rendrait atteignable.",
        f"  Duree moyenne : {duree_moy:.0f} min sur un maximum de {MAX_HOLD_HOURS*60:.0f} min.",
        "    Proche du maximum = tes signaux mettent trop longtemps a se realiser.",
        "",
        "  LA DECISION A PRENDRE :",
        "  Repere le bucket de consensus dont le profit factor est le plus haut",
        "  ET qui compte au moins 10 trades. C'est la ton seuil MIN_CONSENSUS_PCT.",
        "  Meme logique pour les sessions horaires et les groupes de paires :",
        "  ce qui perd de facon repetee doit etre exclu, pas optimise.",
    ]

    if n < 30:
        lecture += [
            "",
            f"  ATTENTION : {n} trades, c'est trop peu pour conclure.",
            "  Il en faut 30 au minimum, 100 pour etre serieux.",
        ]

    return "\n".join(entete + corps + lecture)


if __name__ == "__main__":
    print(rapport_calibration())
    etat = "paper_state.json"
    if os.path.exists(etat):
        try:
            with open(etat, encoding="utf-8") as f:
                s = json.load(f)
            print(f"\n  Positions actuellement ouvertes : {len(s.get('open', []))}")
        except Exception:
            pass
