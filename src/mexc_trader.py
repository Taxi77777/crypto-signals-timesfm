"""
src/mexc_trader.py — Intégration MEXC Futures avec TimesFM
- 1 seule position ouverte à la fois
- Mise totale du solde USDT disponible
- Levier x20, market order isolé
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

MEXC_BASE          = "https://api.mexc.com"
LEVERAGE           = 20
MARGIN_PCT         = 0.90
TRAILING_CALLBACK  = 2.0

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# Seuils protection software (en % de profit)
TRAIL_BREAKEVEN_PCT = 1.5   # À +1.5% → SL déplacé au prix d'entrée (0 perte)
TRAIL_50PCT         = 3.0   # À +3.0% → SL capture 50% des gains
TRAIL_75PCT         = 5.0   # À +5.0% → SL capture 75% des gains

# Mapping yfinance → MEXC Futures
SYMBOL_MAP = {
    # Majeures
    "BTC-USD":      "BTC_USDT",
    "ETH-USD":      "ETH_USDT",
    "BNB-USD":      "BNB_USDT",
    "SOL-USD":      "SOL_USDT",
    "XRP-USD":      "XRP_USDT",
    "ADA-USD":      "ADA_USDT",
    "AVAX-USD":     "AVAX_USDT",
    "LINK-USD":     "LINK_USDT",
    "DOT-USD":      "DOT_USDT",
    "LTC-USD":      "LTC_USDT",
    "BCH-USD":      "BCH_USDT",
    "NEAR-USD":     "NEAR_USDT",
    "ICP-USD":      "ICP_USDT",
    "TIA-USD":      "TIA_USDT",
    "INJ-USD":      "INJ_USDT",
    "AAVE-USD":     "AAVE_USDT",
    "OP-USD":       "OP_USDT",
    "ARB11841-USD": "ARB_USDT",
    "TON11419-USD": "TON_USDT",
    "SUI20947-USD": "SUI_USDT",
    # Altcoins
    "APT21794-USD": "APT_USDT",
    "SEI-USD":      "SEI_USDT",
    "FET-USD":      "FET_USDT",
    "RUNE-USD":     "RUNE_USDT",
    "IMX10603-USD": "IMX_USDT",
    "LDO-USD":      "LDO_USDT",
    "GRT6719-USD":  "GRT_USDT",
    "STX4847-USD":  "STX_USDT",
    "JUP29210-USD": "JUP_USDT",
    "EGLD-USD":     "EGLD_USDT",
    "FIL-USD":      "FIL_USDT",
    "PYTH-USD":     "PYTH_USDT",
    "THETA-USD":    "THETA_USDT",
    "FLOW-USD":     "FLOW_USDT",
    "AXS-USD":      "AXS_USDT",
    "SAND-USD":     "SAND_USDT",
    "MANA-USD":     "MANA_USDT",
    "ATOM-USD":     "ATOM_USDT",
    "ALGO-USD":     "ALGO_USDT",
    "VET-USD":      "VET_USDT",
    "HBAR-USD":     "HBAR_USDT",
    "KAVA-USD":     "KAVA_USDT",
    "GALA-USD":     "GALA_USDT",
    "DYDX-USD":     "DYDX_USDT",
    "MINA-USD":     "MINA_USDT",
    "WOO-USD":      "WOO_USDT",
    "CHZ-USD":      "CHZ_USDT",
    "CRV-USD":      "CRV_USDT",
    "ENS-USD":      "ENS_USDT",
    "PENDLE-USD":   "PENDLE_USDT",
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
        "User-Agent":    USER_AGENT,
    }


def get_usdt_balance(api_key: str, secret_key: str) -> float:
    """Retourne le solde USDT disponible sur le compte futures MEXC."""
    try:
        headers = _get_headers(api_key, secret_key)
        r   = requests.get(
            f"{MEXC_BASE}/api/v1/private/account/assets",
            headers=headers,
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
        headers = _get_headers(api_key, secret_key)
        r   = requests.get(
            f"{MEXC_BASE}/api/v1/private/position/open_positions",
            headers=headers,
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


def get_contract_info(symbol_mexc: str) -> tuple[float, float, int]:
    """Retourne (contractSize, priceUnit, priceScale) pour un symbole MEXC."""
    try:
        r = requests.get(f"{MEXC_BASE}/api/v1/contract/detail?symbol={symbol_mexc}", timeout=10)
        data = r.json()
        if data.get("success"):
            c_size = float(data["data"].get("contractSize", 0.001))
            p_unit = float(data["data"].get("priceUnit", 0.01))
            p_scale = int(data["data"].get("priceScale", 2))
            return c_size, p_unit, p_scale
    except Exception as e:
        logger.error(f"Erreur get_contract_info {symbol_mexc}: {e}")
    return 0.001, 0.01, 2


def calculate_contracts(balance_usdt: float, price: float, contract_size: float) -> int:
    """Calcule le nombre de contrats avec la mise totale + levier."""
    usable    = balance_usdt * MARGIN_PCT
    contracts = int((usable * LEVERAGE) / (price * contract_size))
    return max(1, contracts)


def _place_stop_order(
    api_key:      str,
    secret_key:   str,
    symbol_mexc:  str,
    position_id:  str,
    vol:          int,
    tp_price:     float,
    sl_price:     float,
) -> bool:
    """Envoie la requête stoporder/place avec les paramètres dynamiques TP/SL."""
    try:
        payload = {
            "symbol":      symbol_mexc,
            "positionId":  position_id,
            "quantity":    vol,
            "vol":         vol,
            "profitTrend": 1,
            "lossTrend":   1,
        }
        # N'inclure que les prix valides (> 0)
        if tp_price and tp_price > 0:
            payload["takeProfitPrice"] = tp_price
        if sl_price and sl_price > 0:
            payload["stopLossPrice"] = sl_price

        body = json.dumps(payload, separators=(",", ":"))
        headers = _get_headers(api_key, secret_key, body)
        r = requests.post(
            f"{MEXC_BASE}/api/v1/private/stoporder/place",
            headers=headers,
            data=body,
            timeout=15,
        )
        data = r.json()
        if data.get("success"):
            logger.info(f"✅ Stop order posé sur position {position_id} : TP={tp_price} | SL={sl_price}")
            return True
        logger.error(f"❌ Échec stop order position : {data.get('message', data)}")
        return False
    except Exception as e:
        logger.error(f"❌ Exception stop order : {e}")
        return False


def update_stop_loss(api_key: str, secret_key: str, symbol_mexc: str,
                     position_type: int, new_sl_price: float) -> bool:
    """
    Met à jour le Stop Loss d'une position ouverte en préservant le Take Profit existant.
    """
    try:
        positions = get_open_positions(api_key, secret_key)
        position_id = None
        vol = 0
        cur_tp = 0.0
        for pos in positions:
            if pos.get("symbol") == symbol_mexc:
                position_id = pos.get("positionId")
                vol = int(pos.get("holdVol", 0))
                cur_tp = float(pos.get("takeProfitPrice", 0.0))
                break

        if not position_id or vol == 0:
            logger.error(f"❌ Impossible de mettre à jour le SL : aucune position active pour {symbol_mexc}")
            return False

        _, price_unit, price_scale = get_contract_info(symbol_mexc)
        sl_rounded = round(round(new_sl_price / price_unit) * price_unit, price_scale)

        logger.info(f"Mise à jour SL position {symbol_mexc} → {sl_rounded} | Maintien TP → {cur_tp}")
        return _place_stop_order(api_key, secret_key, symbol_mexc, position_id, vol, cur_tp, sl_rounded)
    except Exception as e:
        logger.error(f"❌ Erreur mise à jour SL : {e}")
        return False


def get_current_price(symbol_mexc: str) -> float:
    """Retourne le dernier prix de marché pour un symbole MEXC Futures."""
    try:
        r = requests.get(f"{MEXC_BASE}/api/v1/contract/ticker?symbol={symbol_mexc}", timeout=10)
        data = r.json()
        if data.get("success"):
            res = data.get("data")
            if isinstance(res, list) and len(res) > 0:
                return float(res[0].get("lastPrice", 0))
            elif isinstance(res, dict):
                return float(res.get("lastPrice", 0))
    except Exception as e:
        logger.error(f"Erreur get_current_price {symbol_mexc}: {e}")
    return 0.0


def check_and_trail(api_key: str, secret_key: str) -> dict | None:
    """
    Vérifie les positions ouvertes et applique le trailing stop software.
    Ne pose de Stop Loss que si la position est déjà en profit (TRAIL_BREAKEVEN_PCT).
    Zéro Stop Loss initial à l'ouverture.
    """
    try:
        positions = get_open_positions(api_key, secret_key)
        if not positions:
            return None

        pos          = positions[0]
        symbol       = pos.get("symbol", "")
        pos_type     = pos.get("positionType", 1)   # 1=Long, 2=Short
        entry_price  = float(pos.get("openAvgPrice", 0)) or float(pos.get("holdAvgPrice", 0))
        cur_sl       = float(pos.get("stopLossPrice", 0))

        # Obtenir le prix actuel en temps réel
        current_price = get_current_price(symbol)

        if entry_price == 0 or current_price == 0:
            logger.warning(f"check_and_trail: prix invalide pour {symbol} (Entrée: {entry_price}, Actuel: {current_price})")
            return None

        # Calcul du profit en %
        if pos_type == 1:  # Long
            profit_pct = (current_price - entry_price) / entry_price * 100
        else:              # Short
            profit_pct = (entry_price - current_price) / entry_price * 100

        logger.info(f"Position {symbol} | Profit: {profit_pct:.2f}% | SL actuel: {cur_sl} | Prix actuel: {current_price}")

        new_sl       = None
        trail_label  = ""

        if pos_type == 1:  # LONG
            if profit_pct >= TRAIL_75PCT:
                new_sl      = entry_price + (current_price - entry_price) * 0.75
                trail_label = f"🔒 Trailing +{TRAIL_75PCT}% → capture 75% gains"
            elif profit_pct >= TRAIL_50PCT:
                new_sl      = entry_price + (current_price - entry_price) * 0.50
                trail_label = f"🔒 Trailing +{TRAIL_50PCT}% → capture 50% gains"
            elif profit_pct >= TRAIL_BREAKEVEN_PCT:
                new_sl      = entry_price * 1.001
                trail_label = f"🔒 Trailing +{TRAIL_BREAKEVEN_PCT}% → Breakeven sécurisé"

            if new_sl:
                _, price_unit, price_scale = get_contract_info(symbol)
                new_sl_rounded = round(round(new_sl / price_unit) * price_unit, price_scale)

                # N'update que si le nouveau SL est meilleur que l'actuel
                if cur_sl == 0 or new_sl_rounded > cur_sl:
                    success = update_stop_loss(api_key, secret_key, symbol, pos_type, new_sl_rounded)
                    if success:
                        return {
                            "symbol":      symbol,
                            "profit_pct":  round(profit_pct, 2),
                            "old_sl":      cur_sl,
                            "new_sl":      new_sl_rounded,
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

            if new_sl:
                _, price_unit, price_scale = get_contract_info(symbol)
                new_sl_rounded = round(round(new_sl / price_unit) * price_unit, price_scale)

                if cur_sl == 0 or new_sl_rounded < cur_sl:
                    success = update_stop_loss(api_key, secret_key, symbol, pos_type, new_sl_rounded)
                    if success:
                        return {
                            "symbol":      symbol,
                            "profit_pct":  round(profit_pct, 2),
                            "old_sl":      cur_sl,
                            "new_sl":      new_sl_rounded,
                            "label":       trail_label,
                        }
    except Exception as e:
        logger.error(f"Erreur check_and_trail : {e}")
    return None

def place_position_tp_sl(
    api_key:     str,
    secret_key:  str,
    symbol_mexc: str,
    vol:         int,
    tp_price:    float,
    sl_price:    float,
) -> bool:
    """Pose un TP/SL sur une position active via /api/v1/private/stoporder/place."""
    try:
        # 1. Récupérer les positions ouvertes pour trouver le positionId
        positions = get_open_positions(api_key, secret_key)
        position_id = None
        for pos in positions:
            if pos.get("symbol") == symbol_mexc:
                position_id = pos.get("positionId")
                break

        if not position_id:
            logger.error(f"❌ Impossible de poser le TP/SL : aucune position active trouvée pour {symbol_mexc}")
            return False

        logger.info(f"Position active trouvée pour {symbol_mexc} | ID: {position_id}")

        body = json.dumps({
            "symbol":      symbol_mexc,
            "positionId":  position_id,
            "quantity":    vol,
            "vol":         vol,
            "profitTrend": 1,
            "lossTrend":   1,
            "takeProfitPrice": tp_price,
            "stopLossPrice":   sl_price,
        }, separators=(",", ":"))
        headers = _get_headers(api_key, secret_key, body)
        r = requests.post(
            f"{MEXC_BASE}/api/v1/private/stoporder/place",
            headers=headers,
            data=body,
            timeout=15,
        )
        data = r.json()
        if data.get("success"):
            logger.info(f"✅ TP/SL posés : TP={tp_price} | SL={sl_price}")
            return True
        logger.error(f"❌ Échec TP/SL position : {data.get('message', data)}")
        return False
    except Exception as e:
        logger.error(f"❌ Exception pose TP/SL : {e}")
        return False


def get_order_book_imbalance(symbol_mexc: str) -> float | None:
    """
    Récupère le carnet d'ordres (top 20) et calcule l'imbalance (OBI) PONDÉRÉ :
    les niveaux proches du prix comptent plus que les niveaux éloignés
    (poids 1/rang : 1er niveau = 1.0, 2e = 0.5, 3e = 0.33...).
    Retourne une valeur entre -1.0 (mur de vente) et +1.0 (mur d'achat), ou None si erreur.
    """
    try:
        r = requests.get(f"{MEXC_BASE}/api/v1/contract/depth/{symbol_mexc}?limit=20", timeout=10)
        data = r.json()
        if data.get("success"):
            depth = data.get("data", {})
            bids = depth.get("bids", [])[:20]
            asks = depth.get("asks", [])[:20]

            if not bids or not asks:
                return None

            sum_bids = sum(float(b[1]) / (i + 1) for i, b in enumerate(bids))
            sum_asks = sum(float(a[1]) / (i + 1) for i, a in enumerate(asks))

            if sum_bids + sum_asks == 0:
                return 0.0

            imbalance = (sum_bids - sum_asks) / (sum_bids + sum_asks)
            return round(imbalance, 3)
    except Exception as e:
        logger.error(f"Erreur calcul OBI pour {symbol_mexc}: {e}")
    return None


def get_funding_rate(symbol_mexc: str) -> float | None:
    """
    Récupère le funding rate actuel du contrat (en %).
    Funding très positif = longs surchargés (danger pour un BUY).
    Funding très négatif = shorts surchargés (danger pour un SELL).
    """
    try:
        r = requests.get(f"{MEXC_BASE}/api/v1/contract/funding_rate/{symbol_mexc}", timeout=10)
        data = r.json()
        if data.get("success"):
            rate = float(data.get("data", {}).get("fundingRate", 0))
            return round(rate * 100, 4)   # en pourcentage
    except Exception as e:
        logger.error(f"Erreur funding rate pour {symbol_mexc}: {e}")
    return None


def get_cumulative_depth_ratio(symbol_mexc: str, mark_price: float, depth_pct: float = 0.015) -> float | None:
    """
    Récupère la profondeur de carnet (jusqu'à 100 niveaux) et calcule le ratio des volumes
    Bids / Asks dans une zone de depth_pct (ex: 1.5%) autour du prix.
    Retourne le ratio Bids/Asks, ou None si erreur.
    """
    try:
        r = requests.get(f"{MEXC_BASE}/api/v1/contract/depth/{symbol_mexc}?limit=100", timeout=10)
        data = r.json()
        if data.get("success"):
            depth = data.get("data", {})
            bids = depth.get("bids", [])
            asks = depth.get("asks", [])
            
            if not bids or not asks:
                return None
            
            min_bid_price = mark_price * (1 - depth_pct)
            max_ask_price = mark_price * (1 + depth_pct)
            
            sum_bids = sum(float(b[1]) for b in bids if float(b[0]) >= min_bid_price)
            sum_asks = sum(float(a[1]) for a in asks if float(a[0]) <= max_ask_price)
            
            if sum_asks == 0.0:
                return 999.0 if sum_bids > 0 else 1.0
            
            ratio = sum_bids / sum_asks
            return round(ratio, 2)
    except Exception as e:
        logger.error(f"Erreur calcul cumulative depth pour {symbol_mexc}: {e}")
    return None


def get_recent_cvd_ratio(symbol_mexc: str) -> float | None:
    """
    Récupère les 100 dernières transactions (deals) et calcule le ratio Volume Achat / Volume Vente.
    Retourne le ratio (ex: 1.5 = +50% volume acheteur agressif), ou None si erreur.
    """
    try:
        r = requests.get(f"{MEXC_BASE}/api/v1/contract/deals/{symbol_mexc}?limit=100", timeout=10)
        data = r.json()
        if data.get("success"):
            deals = data.get("data", [])
            if not deals:
                return None
            
            buy_vol = 0.0
            sell_vol = 0.0
            for trade in deals:
                vol = float(trade.get("v", 0.0))
                side = int(trade.get("T", 0))
                if side == 1:     # Buy / Long taker
                    buy_vol += vol
                elif side == 2:   # Sell / Short taker
                    sell_vol += vol
            
            if sell_vol == 0.0:
                return 999.0 if buy_vol > 0 else 1.0
            
            ratio = buy_vol / sell_vol
            return round(ratio, 2)
    except Exception as e:
        logger.error(f"Erreur calcul CVD pour {symbol_mexc}: {e}")
    return None


def place_order(
    api_key:    str,
    secret_key: str,
    symbol_yf:  str,
    signal:     str,
    price:      float,
    tp_price:   float,
    sl_price:   float,
    margin_pct: float = None,
) -> dict | None:
    """Place un ordre futures MEXC (Market) puis pose le TP/SL séparément."""

    symbol_mexc = SYMBOL_MAP.get(symbol_yf)
    if not symbol_mexc:
        logger.warning(f"Symbole {symbol_yf} non mappé MEXC")
        return None

    balance = get_usdt_balance(api_key, secret_key)
    if balance < 1:
        logger.error(f"Solde insuffisant ({balance:.2f} USDT)")
        return None

    # Prix temps réel MEXC (le prix yfinance peut avoir 15 min de retard)
    live_price = get_current_price(symbol_mexc)
    if live_price > 0:
        logger.info(f"Prix live MEXC {symbol_mexc}: {live_price} (yfinance: {price})")
        price = live_price
    else:
        logger.warning(f"Prix live indisponible — utilisation du prix yfinance: {price}")

    pct = margin_pct if margin_pct is not None else MARGIN_PCT
    contract_size, price_unit, price_scale = get_contract_info(symbol_mexc)
    vol = calculate_contracts(balance * pct, price, contract_size)
    side = 1 if signal == "BUY" else 3   # 1=Open Long, 3=Open Short

    tp_rounded = round(round(tp_price / price_unit) * price_unit, price_scale)
    sl_rounded = round(round(sl_price / price_unit) * price_unit, price_scale)

    # Ordre Market avec TP/SL atomiques (supportés par /order/create)
    order = {
        "symbol":          symbol_mexc,
        "price":           price,
        "vol":             vol,
        "leverage":        LEVERAGE,
        "side":            side,
        "type":            5,       # Market order
        "openType":        1,       # Isolated margin
        "profitTrend":     1,       # déclenchement sur dernier prix
        "lossTrend":       1,
    }
    if tp_rounded > 0:
        order["takeProfitPrice"] = tp_rounded
    if sl_rounded > 0:
        order["stopLossPrice"] = sl_rounded

    body_str = json.dumps(order)
    headers  = _get_headers(api_key, secret_key, body_str)

    logger.info(f"Ordre Market MEXC : {symbol_mexc} {'LONG' if side==1 else 'SHORT'} x{LEVERAGE} — {vol} contrats")

    try:
        r = requests.post(
            f"{MEXC_BASE}/api/v1/private/order/create",
            headers=headers,
            data=body_str,
            timeout=15,
        )
        logger.info(f"Status HTTP : {r.status_code}")
        logger.info(f"Réponse brute : {r.text[:500]}")

        if not r.text.strip():
            logger.error("❌ Réponse vide de MEXC")
            return {"success": False, "error": "Réponse vide MEXC"}

        data = r.json()
        if data.get("success"):
            order_data = data.get("data")
            order_id = order_data.get("orderId") if isinstance(order_data, dict) else order_data
            logger.info(f"✅ Ordre Market placé ! ID : {order_id}")
            tp_sl_ok = True  # TP/SL inclus dans l'ordre lui-même

            return {
                "success":      True,
                "order_id":     order_id,
                "symbol":       symbol_mexc,
                "side":         "LONG" if side == 1 else "SHORT",
                "vol":          vol,
                "balance_used": round(balance * pct, 2),
                "leverage":     LEVERAGE,
                "trailing":     f"{TRAILING_CALLBACK}%",
                "tp_sl_set":    tp_sl_ok,
                "tp":           tp_rounded,
                "sl":           sl_rounded if sl_rounded > 0 else "Aucun",
            }
        else:
            err = data.get("message") or data.get("msg") or str(data)
            logger.error(f"❌ Erreur MEXC : {err}")
            return {"success": False, "error": err}
    except Exception as e:
        logger.error(f"❌ Exception : {e}")
        return {"success": False, "error": str(e)}

