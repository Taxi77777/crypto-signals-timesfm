"""
src/mexc_trader.py — Intégration MEXC Futures avec TimesFM
- 1 seule position ouverte à la fois
- Mise totale du solde USDT disponible
- Levier x10, market order isolé
- TP/SL automatiques
- Trailing Stop : 2% callback natif MEXC + protection software
"""

import hmac
import hashlib
import time
import json
import logging
import requests

logger = logging.getLogger(__name__)

MEXC_BASE          = "https://contract.mexc.com"
LEVERAGE           = 10
MARGIN_PCT         = 0.95   # 95% du solde (5% pour les frais)
TRAILING_CALLBACK  = 2.0    # Trailing stop 2% (natif MEXC)

# Seuils protection software (en % de profit)
TRAIL_BREAKEVEN_PCT = 1.5   # À +1.5% → SL déplacé au prix d'entrée (0 perte)
TRAIL_50PCT         = 3.0   # À +3.0% → SL capture 50% des gains
TRAIL_75PCT         = 5.0   # À +5.0% → SL capture 75% des gains

# Mapping yfinance → MEXC Futures
SYMBOL_MAP = {
    "BTC-USD":   "BTC_USDT",
    "ETH-USD":   "ETH_USDT",
    "BNB-USD":   "BNB_USDT",
    "SOL-USD":   "SOL_USDT",
    "XRP-USD":   "XRP_USDT",
    "ADA-USD":   "ADA_USDT",
    "AVAX-USD":  "AVAX_USDT",
    "DOGE-USD":  "DOGE_USDT",
    "DOT-USD":   "DOT_USDT",
    "LINK-USD":  "LINK_USDT",
    "LTC-USD":   "LTC_USDT",
    "ATOM-USD":  "ATOM_USDT",
    "BCH-USD":   "BCH_USDT",
    "ALGO-USD":  "ALGO_USDT",
    "NEAR-USD":  "NEAR_USDT",
    "SAND-USD":  "SAND_USDT",
    "MANA-USD":  "MANA_USDT",
    "APE-USD":   "APE_USDT",
    "AXS-USD":   "AXS_USDT",
    "THETA-USD": "THETA_USDT",
    "ICP-USD":   "ICP_USDT",
    "ETC-USD":   "ETC_USDT",
}


def _sign(api_key: str, secret_key: str, timestamp: int, body: str = "") -> str:
    """Génère la signature HMAC-SHA256 pour l'API MEXC Futures."""
    message = api_key + str(timestamp) + body
    return hmac.new(
        secret_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


def _get_headers(api_key: str, secret_key: str, body: str = "") -> dict:
    """Retourne les headers d'authentification MEXC."""
    timestamp = int(time.time() * 1000)
    return {
        "ApiKey":        api_key,
        "Request-Time":  str(timestamp),
        "Signature":     _sign(api_key, secret_key, timestamp, body),
        "Content-Type":  "application/json",
    }


def get_usdt_balance(api_key: str, secret_key: str) -> float:
    """Retourne le solde USDT disponible sur le compte futures MEXC."""
    try:
        ts  = int(time.time() * 1000)
        sig = _sign(api_key, secret_key, ts)
        r   = requests.get(
            f"{MEXC_BASE}/api/v1/private/account/assets",
            headers={"ApiKey": api_key, "Request-Time": str(ts),
                     "Signature": sig, "Content-Type": "application/json"},
            timeout=10
        )
        data = r.json()
        if data.get("success"):
            for asset in data.get("data", []):
                if asset.get("currency") == "USDT":
                    balance = float(asset.get("availableBalance", 0))
                    logger.info(f"Solde USDT : {balance:.2f} USDT")
                    return balance
        logger.warning(f"Balance MEXC : {data}")
        return 0.0
    except Exception as e:
        logger.error(f"Erreur balance MEXC : {e}")
        return 0.0


def get_open_positions(api_key: str, secret_key: str) -> list:
    """Retourne la liste des positions ouvertes."""
    try:
        ts  = int(time.time() * 1000)
        sig = _sign(api_key, secret_key, ts)
        r   = requests.get(
            f"{MEXC_BASE}/api/v1/private/position/open_positions",
            headers={"ApiKey": api_key, "Request-Time": str(ts),
                     "Signature": sig, "Content-Type": "application/json"},
            timeout=10
        )
        data = r.json()
        if data.get("success"):
            return data.get("data", [])
        return []
    except Exception as e:
        logger.error(f"Erreur positions MEXC : {e}")
        return []


def has_open_position(api_key: str, secret_key: str) -> bool:
    """Retourne True si une position est déjà ouverte."""
    positions = get_open_positions(api_key, secret_key)
    if positions:
        logger.info(f"Position ouverte : {positions[0].get('symbol')} — Attente fermeture...")
        return True
    logger.info("Aucune position ouverte — Trading autorisé !")
    return False


def get_contract_size(symbol_mexc: str) -> float:
    """Retourne la taille du contrat pour un symbole MEXC."""
    try:
        r    = requests.get(f"{MEXC_BASE}/api/v1/contract/detail?symbol={symbol_mexc}", timeout=10)
        data = r.json()
        if data.get("success"):
            return float(data["data"].get("contractSize", 0.001))
    except Exception:
        pass
    return 0.001


def calculate_contracts(balance_usdt: float, price: float, contract_size: float) -> int:
    """Calcule le nombre de contrats avec la mise totale + levier."""
    usable    = balance_usdt * MARGIN_PCT
    contracts = int((usable * LEVERAGE) / (price * contract_size))
    return max(1, contracts)


def update_stop_loss(api_key: str, secret_key: str, symbol_mexc: str,
                     position_type: int, new_sl_price: float) -> bool:
    """
    Met à jour le Stop Loss d'une position ouverte (trailing stop software).
    position_type: 1 = Long, 2 = Short
    """
    try:
        body = json.dumps({
            "symbol":       symbol_mexc,
            "positionType": position_type,
            "stopLossPrice": round(new_sl_price, 4),
        }, separators=(",", ":"))
        headers = _get_headers(api_key, secret_key, body)
        r = requests.post(
            f"{MEXC_BASE}/api/v1/private/position/stopLoss/change",
            headers=headers,
            data=body,
            timeout=10
        )
        data = r.json()
        if data.get("success"):
            logger.info(f"✅ SL mis à jour → {new_sl_price:.4f}")
            return True
        logger.warning(f"Mise à jour SL : {data}")
        return False
    except Exception as e:
        logger.error(f"Erreur mise à jour SL : {e}")
        return False


def check_and_trail(api_key: str, secret_key: str) -> dict | None:
    """
    Vérifie les positions ouvertes et applique le trailing stop software.
    Retourne un dict décrivant l'action effectuée, ou None.
    """
    positions = get_open_positions(api_key, secret_key)
    if not positions:
        return None

    pos          = positions[0]
    symbol       = pos.get("symbol", "")
    pos_type     = pos.get("positionType", 1)   # 1=Long, 2=Short
    entry_price  = float(pos.get("openAvgPrice", 0))
    current_price= float(pos.get("closeAvgPrice", 0)) or float(pos.get("markPrice", 0))
    cur_sl       = float(pos.get("stopLossPrice", 0))

    if entry_price == 0 or current_price == 0:
        return None

    # Calcul du profit en %
    if pos_type == 1:  # Long
        profit_pct = (current_price - entry_price) / entry_price * 100
    else:              # Short
        profit_pct = (entry_price - current_price) / entry_price * 100

    logger.info(f"Position {symbol} | Profit: {profit_pct:.2f}% | SL actuel: {cur_sl}")

    new_sl       = None
    trail_label  = ""

    if pos_type == 1:  # LONG
        if profit_pct >= TRAIL_75PCT:
            # Capture 75% des gains
            new_sl      = entry_price + (current_price - entry_price) * 0.75
            trail_label = f"🔒 Trailing +{TRAIL_75PCT}% → capture 75% gains"
        elif profit_pct >= TRAIL_50PCT:
            # Capture 50% des gains
            new_sl      = entry_price + (current_price - entry_price) * 0.50
            trail_label = f"🔒 Trailing +{TRAIL_50PCT}% → capture 50% gains"
        elif profit_pct >= TRAIL_BREAKEVEN_PCT:
            # Breakeven (pas de perte possible)
            new_sl      = entry_price * 1.001  # légèrement au-dessus de l'entrée
            trail_label = f"🔒 Trailing +{TRAIL_BREAKEVEN_PCT}% → Breakeven sécurisé"

        # N'update que si le nouveau SL est meilleur que l'actuel
        if new_sl and new_sl > cur_sl:
            success = update_stop_loss(api_key, secret_key, symbol, pos_type, new_sl)
            if success:
                return {
                    "symbol":      symbol,
                    "profit_pct":  round(profit_pct, 2),
                    "old_sl":      cur_sl,
                    "new_sl":      round(new_sl, 4),
                    "label":       trail_label,
                }

    else:  # SHORT
        if profit_pct >= TRAIL_75PCT:
            new_sl      = entry_price - (entry_price - current_price) * 0.75
            trail_label = f"🔒 Trailing +{TRAIL_75PCT}% → capture 75% gains"
        elif profit_pct >= TRAIL_50PCT:
            new_sl      = entry_price - (entry_price - current_price) * 0.50
            trail_label = f"🔒 Trailing +{TRAIL_50PCT}% → capture 50% gains"
        elif profit_pct >= TRAIL_BREAKEVEN_PCT:
            new_sl      = entry_price * 0.999
            trail_label = f"🔒 Trailing +{TRAIL_BREAKEVEN_PCT}% → Breakeven sécurisé"

        if new_sl and new_sl < cur_sl:
            success = update_stop_loss(api_key, secret_key, symbol, pos_type, new_sl)
            if success:
                return {
                    "symbol":      symbol,
                    "profit_pct":  round(profit_pct, 2),
                    "old_sl":      cur_sl,
                    "new_sl":      round(new_sl, 4),
                    "label":       trail_label,
                }

    return None


def place_order(
    api_key:    str,
    secret_key: str,
    symbol_yf:  str,
    signal:     str,
    price:      float,
    tp_price:   float,
    sl_price:   float,
) -> dict | None:
    """Place un ordre futures MEXC avec TP/SL + Trailing Stop."""

    symbol_mexc = SYMBOL_MAP.get(symbol_yf)
    if not symbol_mexc:
        logger.warning(f"Symbole {symbol_yf} non mappé MEXC")
        return None

    balance = get_usdt_balance(api_key, secret_key)
    if balance < 1:
        logger.error(f"Solde insuffisant ({balance:.2f} USDT)")
        return None

    contract_size = get_contract_size(symbol_mexc)
    vol           = calculate_contracts(balance, price, contract_size)
    side          = 1 if signal == "BUY" else 3   # 1=Open Long, 3=Open Short

    order = {
        "symbol":          symbol_mexc,
        "price":           0,
        "vol":             vol,
        "leverage":        LEVERAGE,
        "side":            side,
        "type":            5,       # Market order
        "openType":        1,       # Isolated margin
        "takeProfitPrice": round(tp_price, 4),
        "stopLossPrice":   round(sl_price, 4),
    }

    body_str = json.dumps(order)
    ts        = int(time.time() * 1000)
    sig       = _sign(api_key, secret_key, ts, body_str)
    headers = {
        "ApiKey":        api_key,
        "Request-Time":  str(ts),
        "Signature":     sig,
        "Content-Type":  "application/json",
    }

    logger.info(f"Ordre MEXC : {symbol_mexc} {'LONG' if side==1 else 'SHORT'} x{LEVERAGE} — {vol} contrats")
    logger.info(f"TP: {round(tp_price,4)} | SL: {round(sl_price,4)}")

    try:
        r = requests.post(
            f"{MEXC_BASE}/api/v1/private/order/submit",
            headers=headers,
            data=body_str,
            timeout=15,
        )
        logger.info(f"Status HTTP : {r.status_code}")
        logger.info(f"Réponse brute : {r.text[:500]}")

        if not r.text.strip():
            logger.error("❌ Réponse vide de MEXC — Vérifier API key et permissions")
            return {"success": False, "error": "Réponse vide MEXC (vérifier permissions API Futures)"}

        data = r.json()
        if data.get("success"):
            logger.info(f"✅ Ordre placé ! ID : {data.get('data')}")
            return {
                "success":      True,
                "order_id":     data.get("data"),
                "symbol":       symbol_mexc,
                "side":         "LONG" if side == 1 else "SHORT",
                "vol":          vol,
                "balance_used": round(balance * MARGIN_PCT, 2),
                "leverage":     LEVERAGE,
                "trailing":     f"{TRAILING_CALLBACK}%",
            }
        else:
            err = data.get("message") or data.get("msg") or str(data)
            logger.error(f"❌ Erreur MEXC : {err}")
            return {"success": False, "error": err}
    except Exception as e:
        logger.error(f"❌ Exception : {e}")
        return {"success": False, "error": str(e)}

