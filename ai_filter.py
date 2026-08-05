"""
╔══════════════════════════════════════════════════════════════════╗
║     INSTITUTIONAL HUNTER PRO v5.0 — AI CONFIRMATION LAYER       ║
║     ai_filter.py — Kimi AI + Google Gemini Double Validation     ║
║                                                                  ║
║  Les IAs reçoivent toutes les données du marché :                ║
║  - Carnet d'ordres niveau par niveau (50 niveaux)                ║
║  - CVD (flux de trades agressifs)                                ║
║  - Consensus multi-exchange                                       ║
║  - Tendance 4H (EMA/VWAP/RSI/ATR)                               ║
║                                                                  ║
║  Si Kimi ET Gemini sont d'accord avec le carnet → TRADE          ║
║  Si une IA contredit → PAS DE TRADE (sécurité maximale)          ║
╚══════════════════════════════════════════════════════════════════╝
"""
import os
import json
import requests
import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("IHP-AI")

# ─── Clés API IA : lues depuis config.py EN PRIORITÉ, puis variables d'env ───
try:
    from config import KIMI_API_KEY as _KIMI_CFG, GEMINI_API_KEY as _GEMINI_CFG
except ImportError:
    _KIMI_CFG, _GEMINI_CFG = "", ""

KIMI_API_KEY   = _KIMI_CFG   or os.getenv("KIMI_API_KEY",   "")
GEMINI_API_KEY = _GEMINI_CFG or os.getenv("GEMINI_API_KEY", "")



@dataclass
class AIVerdict:
    """Résultat de l'analyse par une IA."""
    ai_name:    str
    direction:  str          # 'BUY', 'SELL', 'NEUTRAL'
    confidence: float        # 0.0 à 1.0
    reasoning:  str
    available:  bool = True  # False si l'API n'est pas configurée


def _build_market_prompt(symbol: str, orderflow: dict, trend: dict) -> str:
    """
    Construit le prompt complet envoyé aux IAs avec toutes les données de marché.
    Le prompt est structuré pour que les IAs comprennent exactement les données.
    """
    n = orderflow.get('exchanges_ok', 0)
    details_lines = []
    for ex, d in orderflow.get('details', {}).items():
        if d.get('ok'):
            details_lines.append(
                f"  - {ex}: Dominant={d.get('dominant_side','N')} | "
                f"StackedBuy={d.get('stacked_buy',0)} niveaux consec | "
                f"StackedSell={d.get('stacked_sell',0)} niveaux consec | "
                f"CVD={d.get('cvd',{}).get('cvd_direction','N')} ({d.get('cvd',{}).get('delta_pct',0):+.1f}%) | "
                f"Score={d.get('direction_score',0):+.0f}/100"
            )

    prompt = f"""Tu es un trader institutionnel expert. Analyse ces données de marché en temps réel.

═══ DONNÉES MARCHÉ : {symbol} ═══

1. CARNET D'ORDRES MULTI-EXCHANGE (50 niveaux de profondeur, données RÉELLES)
{chr(10).join(details_lines)}

RÉSUMÉ MULTI-EXCHANGE ({n} exchanges analysés) :
- Exchanges où les ACHETEURS dominent le carnet : {orderflow.get('book_buy', 0)}/{n}
- Exchanges où les VENDEURS dominent le carnet  : {orderflow.get('book_sell', 0)}/{n}
- CVD positif (achats agressifs) : {orderflow.get('cvd_buy', 0)}/{n} exchanges
- CVD négatif (ventes agressives): {orderflow.get('cvd_sell', 0)}/{n} exchanges
- Score directionnel moyen       : {orderflow.get('avg_direction_score', 0):+.1f}/100
- Stacked Imbalances BUY moyen  : {orderflow.get('avg_stacked_buy', 0):.1f} niveaux consécutifs
- Stacked Imbalances SELL moyen : {orderflow.get('avg_stacked_sell', 0):.1f} niveaux consécutifs
- Consensus global               : {orderflow.get('consensus_direction', 'NEUTRAL')} à {orderflow.get('consensus_pct', 0):.0f}%

2. TENDANCE LONG TERME (4H)
- Prix actuel  : {trend.get('price', 0):.4f} USDT
- VWAP 4H      : {trend.get('vwap', 0):.4f} USDT
- EMA 21       : {trend.get('ema_fast', 0):.4f}
- EMA 55       : {trend.get('ema_slow', 0):.4f}
- ATR (14)     : {trend.get('atr', 0):.4f}
- RSI (14)     : {trend.get('rsi', 50):.1f}
- Biais 4H     : {trend.get('bias', 'NEUTRAL')}

3. EXPLICATION DES CONCEPTS
- Stacked Imbalances : niveaux CONSÉCUTIFS du carnet où un seul côté domine (bid/ask ratio > 3.0)
  → C'est la signature des institutions qui absorbent le côté opposé
- CVD positif : les acheteurs INITIENT les trades (frappent l'ask = urgence haussière)
- CVD négatif : les vendeurs INITIENT les trades (frappent le bid = urgence baissière)

═══ TA MISSION ═══
En te basant sur CES DONNÉES UNIQUEMENT (pas d'opinions extérieures), réponds avec ce format JSON exact :
{{
  "direction": "BUY" ou "SELL" ou "NEUTRAL",
  "confidence": 0.0 à 1.0,
  "reasoning": "explication courte en 2-3 phrases maximum"
}}

Sois STRICT : si les données ne sont pas claires (consensus < 70%, signaux mixtes), réponds NEUTRAL.
Réponds UNIQUEMENT avec le JSON, rien d'autre."""

    return prompt


def _parse_ai_response(raw: str, ai_name: str) -> dict:
    """Parse la réponse JSON d'une IA, même si elle contient du texte autour."""
    try:
        # Extraire le JSON de la réponse
        start = raw.find('{')
        end   = raw.rfind('}') + 1
        if start >= 0 and end > start:
            parsed = json.loads(raw[start:end])
            direction  = str(parsed.get('direction', 'NEUTRAL')).upper()
            confidence = float(parsed.get('confidence', 0.5))
            reasoning  = str(parsed.get('reasoning', ''))
            if direction not in ('BUY', 'SELL', 'NEUTRAL'):
                direction = 'NEUTRAL'
            return {'direction': direction, 'confidence': confidence, 'reasoning': reasoning}
    except Exception as e:
        log.warning(f"[{ai_name}] Impossible de parser la réponse : {e} | Raw: {raw[:200]}")
    return {'direction': 'NEUTRAL', 'confidence': 0.0, 'reasoning': 'Erreur de parsing'}


# ──────────────────────────────────────────────────────────────
#  KIMI AI (Moonshot AI)
# ──────────────────────────────────────────────────────────────

def ask_kimi(symbol: str, orderflow: dict, trend: dict) -> AIVerdict:
    """Interroge Kimi AI (Moonshot) pour une analyse du marché."""
    if not KIMI_API_KEY:
        log.info("[Kimi] API key non configurée — analyse ignorée")
        return AIVerdict(ai_name="Kimi", direction="NEUTRAL",
                         confidence=0.0, reasoning="API key manquante", available=False)

    prompt = _build_market_prompt(symbol, orderflow, trend)

    try:
        resp = requests.post(
            "https://api.moonshot.cn/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {KIMI_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "moonshot-v1-8k",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,   # Très bas pour des réponses consistantes
                "max_tokens": 300
            },
            timeout=15
        )
        resp.raise_for_status()
        raw = resp.json()['choices'][0]['message']['content']
        parsed = _parse_ai_response(raw, "Kimi")

        log.info(f"[Kimi] {symbol} → {parsed['direction']} (conf: {parsed['confidence']:.0%}) | {parsed['reasoning'][:80]}")
        return AIVerdict(
            ai_name="Kimi",
            direction=parsed['direction'],
            confidence=parsed['confidence'],
            reasoning=parsed['reasoning']
        )

    except requests.exceptions.Timeout:
        log.warning("[Kimi] Timeout — analyse ignorée")
        return AIVerdict(ai_name="Kimi", direction="NEUTRAL",
                         confidence=0.0, reasoning="Timeout API", available=False)
    except Exception as e:
        log.error(f"[Kimi] Erreur : {e}")
        return AIVerdict(ai_name="Kimi", direction="NEUTRAL",
                         confidence=0.0, reasoning=str(e), available=False)


# ──────────────────────────────────────────────────────────────
#  GOOGLE GEMINI
# ──────────────────────────────────────────────────────────────

def ask_gemini(symbol: str, orderflow: dict, trend: dict) -> AIVerdict:
    """Interroge Google Gemini pour une analyse du marché."""
    if not GEMINI_API_KEY:
        log.info("[Gemini] API key non configurée — analyse ignorée")
        return AIVerdict(ai_name="Gemini", direction="NEUTRAL",
                         confidence=0.0, reasoning="API key manquante", available=False)

    prompt = _build_market_prompt(symbol, orderflow, trend)

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(
            url,
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.1,
                    "maxOutputTokens": 300
                }
            },
            timeout=15
        )
        resp.raise_for_status()
        raw = resp.json()['candidates'][0]['content']['parts'][0]['text']
        parsed = _parse_ai_response(raw, "Gemini")

        log.info(f"[Gemini] {symbol} → {parsed['direction']} (conf: {parsed['confidence']:.0%}) | {parsed['reasoning'][:80]}")
        return AIVerdict(
            ai_name="Gemini",
            direction=parsed['direction'],
            confidence=parsed['confidence'],
            reasoning=parsed['reasoning']
        )

    except requests.exceptions.Timeout:
        log.warning("[Gemini] Timeout — analyse ignorée")
        return AIVerdict(ai_name="Gemini", direction="NEUTRAL",
                         confidence=0.0, reasoning="Timeout API", available=False)
    except Exception as e:
        log.error(f"[Gemini] Erreur : {e}")
        return AIVerdict(ai_name="Gemini", direction="NEUTRAL",
                         confidence=0.0, reasoning=str(e), available=False)


# ──────────────────────────────────────────────────────────────
#  CONSENSUS IA GLOBAL
# ──────────────────────────────────────────────────────────────

def get_ai_consensus(symbol: str, orderflow: dict, trend: dict) -> dict:
    """
    Interroge Kimi et Gemini en parallèle et calcule le consensus IA.

    Règle d'or :
    - Si les 2 IAs sont disponibles : elles doivent toutes les 2 dire la même direction
    - Si 1 seule IA disponible : elle doit avoir confidence >= 0.70
    - Si aucune IA disponible : signal passé sans filtre IA (warning)

    Returns:
        dict avec ai_direction, ai_confidence_avg, kimi, gemini, ai_agrees_with_book
    """
    import concurrent.futures

    kimi_verdict   = AIVerdict("Kimi",   "NEUTRAL", 0.0, "")
    gemini_verdict = AIVerdict("Gemini", "NEUTRAL", 0.0, "")

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        kimi_future   = ex.submit(ask_kimi,   symbol, orderflow, trend)
        gemini_future = ex.submit(ask_gemini, symbol, orderflow, trend)

        kimi_verdict   = kimi_future.result()
        gemini_verdict = gemini_future.result()

    book_direction = orderflow.get('consensus_direction', 'NEUTRAL')
    available_count = sum(1 for v in [kimi_verdict, gemini_verdict] if v.available)

    # ── Décision consensus IA ───────────────────────────────
    ai_direction = 'NEUTRAL'
    ai_confidence = 0.0
    ai_agrees = False
    reasoning_combined = ""

    if available_count == 0:
        # Aucune IA disponible → signal passe sans filtre IA
        ai_direction  = book_direction
        ai_confidence = 0.5
        ai_agrees     = True
        reasoning_combined = "Aucune IA disponible — signal carnet d'ordres non filtré"
        log.warning(f"[AI-CONSENSUS] {symbol} — Aucune IA disponible, signal non filtré par IA")

    elif available_count == 1:
        # 1 seule IA : doit avoir conf >= 0.70 ET être d'accord avec le carnet
        verdict = kimi_verdict if kimi_verdict.available else gemini_verdict
        if verdict.direction == book_direction and verdict.confidence >= 0.70:
            ai_direction  = verdict.direction
            ai_confidence = verdict.confidence
            ai_agrees     = True
        reasoning_combined = f"{verdict.ai_name}: {verdict.reasoning}"
        log.info(f"[AI-CONSENSUS] {symbol} — 1 IA ({verdict.ai_name}): {verdict.direction} conf={verdict.confidence:.0%}")

    else:
        # 2 IAs disponibles : elles DOIVENT être d'accord entre elles ET avec le carnet
        conf_avg = (kimi_verdict.confidence + gemini_verdict.confidence) / 2.0

        if (kimi_verdict.direction == gemini_verdict.direction == book_direction):
            # TRIPLE ACCORD : carnet + Kimi + Gemini
            ai_direction  = book_direction
            ai_confidence = conf_avg
            ai_agrees     = True
        elif kimi_verdict.direction == gemini_verdict.direction and kimi_verdict.direction != 'NEUTRAL':
            # Kimi et Gemini d'accord mais pas avec le carnet → méfiance
            ai_direction  = 'NEUTRAL'
            ai_confidence = conf_avg
            ai_agrees     = False
            log.warning(f"[AI-CONSENSUS] {symbol} — IAs d'accord ({kimi_verdict.direction}) mais divergent du carnet ({book_direction})")
        else:
            # Désaccord entre IAs → NEUTRAL
            ai_direction  = 'NEUTRAL'
            ai_confidence = 0.0
            ai_agrees     = False

        reasoning_combined = f"Kimi: {kimi_verdict.reasoning} | Gemini: {gemini_verdict.reasoning}"
        log.info(f"[AI-CONSENSUS] {symbol} — Kimi={kimi_verdict.direction} | Gemini={gemini_verdict.direction} | "
                 f"Carnet={book_direction} | ACCORD={ai_agrees}")

    return {
        'ai_direction':       ai_direction,
        'ai_confidence':      round(ai_confidence, 3),
        'ai_agrees_with_book': ai_agrees,
        'available_ais':      available_count,
        'kimi':               {'direction': kimi_verdict.direction, 'confidence': kimi_verdict.confidence,
                               'reasoning': kimi_verdict.reasoning, 'available': kimi_verdict.available},
        'gemini':             {'direction': gemini_verdict.direction, 'confidence': gemini_verdict.confidence,
                               'reasoning': gemini_verdict.reasoning, 'available': gemini_verdict.available},
        'reasoning':          reasoning_combined
    }
