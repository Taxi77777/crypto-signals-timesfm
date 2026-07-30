"""
╔══════════════════════════════════════════════════════════════════╗
║     INSTITUTIONAL HUNTER PRO — KIMI AI FILTER                   ║
║     kimi_filter.py — Validation IA par Moonshot / Kimi API      ║
║                                                                  ║
║  Rôle : Reçoit le setup 3 étapes LVN/HVN + données M15          ║
║  et donne un score de confiance IA (0 à 100).                   ║
║  Ne valide le trade que si Confiance IA >= 70%.                 ║
╚══════════════════════════════════════════════════════════════════╝
"""
import os
import json
import logging
import requests
from config import KIMI_API_KEY, KIMI_MIN_CONFIDENCE, USE_KIMI_FILTER

log = logging.getLogger('IHP-KIMI')

KIMI_BASE_URL = "https://api.moonshot.cn/v1/chat/completions"


def analyze_with_kimi(symbol: str, direction: str, entry: float, sl: float, tp: float,
                       rr: float, fisher_val: float, vwap: float,
                       step1_desc: str, step2_desc: str, step3_desc: str) -> dict:
    """
    Interroge l'IA Kimi (Moonshot API) pour valider un setup LVN 3 étapes.
    
    Returns:
        dict: {'approved': bool, 'confidence': int, 'reason': str}
    """
    if not USE_KIMI_FILTER or not KIMI_API_KEY:
        log.debug(f"[{symbol}] Filtre Kimi désactivé ou clé API absente — validation automatique par défaut.")
        return {'approved': True, 'confidence': 100, 'reason': 'Kimi filtrage désactivé (pass-through)'}

    prompt = f"""Tu es Kimi, un assistant IA expert en Day Trading et Volume Profile M15.
Analyse le setup de trading suivant sur {symbol} et détermine s'il est de haute qualité.

SETUP DE TRADING :
- Symbole: {symbol}
- Direction: {direction}
- Prix d'entrée: {entry}
- Stop Loss: {sl}
- Take Profit (TP sur HVN): {tp}
- Ratio Risque/Rendement (RR): 1:{rr}
- Valeur Fisher (9): {fisher_val}
- VWAP Session: {vwap}

RÉTROSPECTIVE DU SETUP EN 3 ÉTAPES (M15) :
1. Étape 1 (Identification): {step1_desc}
2. Étape 2 (Test du LVN): {step2_desc}
3. Étape 3 (Confirmation): {step3_desc}

CONSIGNES :
Évalue la cohérence de ce trade. Un bon trade doit respecter :
- Rejet/rebond net sur le LVN/HVN.
- Bon alignement du Fisher et du VWAP.
- Ratio RR >= 1.5.

Réponds STRICTEMENT au format JSON JSON suivant (sans autre texte) :
{{
  "approved": true ou false,
  "confidence": score de 0 à 100,
  "reason": "explication concise en une phrase en français"
}}
"""

    headers = {
        "Authorization": f"Bearer {KIMI_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "moonshot-v1-8k",
        "messages": [
            {"role": "system", "content": "Tu es un expert financier en trading algorithmique de précision sur le Volume Profile."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2
    }

    try:
        response = requests.post(KIMI_BASE_URL, headers=headers, json=payload, timeout=10)
        if response.status_code == 200:
            res_data = response.json()
            content = res_data['choices'][0]['message']['content'].strip()
            
            # Nettoyer le contenu JSON au cas où il contient des balises markdown ```json
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            content = content.strip()

            result = json.loads(content)
            confidence = int(result.get('confidence', 0))
            approved = result.get('approved', False) and (confidence >= KIMI_MIN_CONFIDENCE)

            log.info(f"🤖 [KIMI AI] {symbol} {direction} | Approuvé: {approved} | Confiance: {confidence}% | Raison: {result.get('reason')}")
            return {
                'approved': approved,
                'confidence': confidence,
                'reason': result.get('reason', 'Pas de raison fournie')
            }
        else:
            log.warning(f"[KIMI API] Erreur HTTP {response.status_code}: {response.text}")
            # En cas d'erreur de quota/API, on ne bloque pas les trades valides
            return {'approved': True, 'confidence': 75, 'reason': f'Fallback API HTTP {response.status_code}'}

    except Exception as e:
        log.error(f"[KIMI API] Exception : {e}")
        return {'approved': True, 'confidence': 75, 'reason': 'Fallback exception API Kimi'}
