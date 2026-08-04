"""
╔══════════════════════════════════════════════════════════════════╗
║     INSTITUTIONAL HUNTER PRO — MULTI-EXCHANGE OBI STRATEGY       ║
║     strategy.py — Déséquilibre carnet d'ordres & Tendance      ║
║                                                                  ║
║  Règles d'Exécution :                                            ║
║   1. Consensus Carnet d'Ordres (OBI) sur 6 Echanges >= 70%      ║
║   2. Alignement Tendance Long Terme (Prix > VWAP / EMA 50 > 200)║
║   3. Entrée sur MEXC Futures avec SL/TP calculés par ATR        ║
╚══════════════════════════════════════════════════════════════════╝
"""
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional
from exchanges import get_multi_exchange_obi
from indicators import calc_trend_indicators, get_trend_bias
from config import (EXCHANGES_TO_CHECK, OBI_BUY_THRESHOLD, OBI_SELL_THRESHOLD,
                    MIN_CONSENSUS_PCT, USE_TREND_FILTER, MIN_RR, ORDERBOOK_DEPTH)


@dataclass
class Signal:
    """Signal de trading complet basé sur le consensus multi-échange."""
    symbol:         str
    direction:      str          # 'BUY', 'SELL', 'NEUTRAL'
    strength:       str = 'WEAK' # 'STRONG', 'NORMAL', 'WEAK'
    entry:          float = 0.0
    sl:             float = 0.0
    tp:             float = 0.0
    rr:             float = 0.0

    # Métriques Consensus Multi-Échange
    consensus_pct:  float = 0.0  # % d'échanges en accord (ex: 83.3%)
    avg_obi:        float = 0.5  # OBI moyen (0 à 1)
    exchanges_buy:  int = 0      # Nombre d'échanges avec OBI > 0.55
    exchanges_sell: int = 0      # Nombre d'échanges avec OBI < 0.45
    exchanges_total:int = 0

    # Tendance Long Terme
    trend_bias:     str = 'NEUTRAL'
    vwap:           float = 0.0
    ema_fast:       float = 0.0
    ema_slow:       float = 0.0
    atr:            float = 0.0

    reason:         str = ""
    details:        dict = field(default_factory=dict)
    warnings:       list = field(default_factory=list)

    def is_valid(self) -> bool:
        return (self.direction in ('BUY', 'SELL')
                and self.consensus_pct >= MIN_CONSENSUS_PCT
                and self.rr >= MIN_RR
                and self.entry > 0
                and self.sl > 0
                and self.tp > 0)

    def summary(self) -> str:
        arrow  = "[BUY]" if self.direction == 'BUY' else "[SELL]" if self.direction == 'SELL' else "[NEUTRAL]"
        stars  = "***" if self.strength == 'STRONG' else "**" if self.strength == 'NORMAL' else "*"
        
        lines = [
            f"{arrow} {stars} -- {self.symbol} (Multi-Exchange OBI & Trend)",
            "-" * 55,
            f"  Consensus Multi-Echange : {self.consensus_pct:.0f}% ({self.exchanges_buy if self.direction=='BUY' else self.exchanges_sell}/{self.exchanges_total} echanges)",
            f"  OBI Moyen Global       : {self.avg_obi:.3f} ({'Domination Acheteurs' if self.avg_obi>=0.55 else 'Domination Vendeurs' if self.avg_obi<=0.45 else 'Equilibre'})",
            f"  Tendance Long Terme    : {self.trend_bias} (Prix:{self.entry:.4f} | VWAP:{self.vwap:.4f})",
            "-" * 55,
        ]

        # Détails par échange
        for ex, d in self.details.items():
            if d.get('ok'):
                obi_val = d.get('obi', 0.5)
                tag = "BUY" if obi_val >= 0.55 else ("SELL" if obi_val <= 0.45 else "NEUTRE")
                lines.append(f"   * {ex:<8} | OBI: {obi_val:.3f} | {tag}")

        lines += [
            "-" * 55,
            f"  Prix Entree : {self.entry:.4f}",
            f"  Take Profit : {self.tp:.4f} (TP)",
            f"  Stop Loss   : {self.sl:.4f} (SL)",
            f"  Ratio R/R   : 1:{self.rr:.2f}",
            f"  Raison      : {self.reason}"
        ]
        if self.warnings:
            lines.append(f"  [!] Avertissements : {' | '.join(self.warnings)}")

        return "\n".join(lines)


class MultiExchangeOBIStrategy:
    """
    Stratégie Institutionnelle basée sur le Déséquilibre du Carnet d'Ordres (OBI)
    et le Consensus Multi-Échange avec Filtre de Tendance Long Terme.
    """

    def __init__(self, symbol: str):
        self.symbol = symbol

    def analyze(self, df_klines: pd.DataFrame) -> Signal:
        """
        Effectue l'analyse complète :
        1. Récupération des carnets d'ordres sur 6 échanges (MEXC, Bitget, Bybit, OKX, Binance, Kraken).
        2. Calcul du pourcentage de consensus OBI.
        3. Calcul des indicateurs de tendance Long Terme (VWAP, EMA 50/200, ATR).
        4. Décision finale BUY / SELL / NEUTRE.
        """
        signal = Signal(symbol=self.symbol, direction='NEUTRAL')

        # ── 1. Interrogation Multi-Échange ──────────────────────────
        multi_obi = get_multi_exchange_obi(self.symbol, EXCHANGES_TO_CHECK, ORDERBOOK_DEPTH)

        signal.consensus_pct  = multi_obi['consensus_pct']
        signal.avg_obi        = multi_obi['avg_obi']
        signal.exchanges_buy  = multi_obi['exchanges_buy']
        signal.exchanges_sell = multi_obi['exchanges_sell']
        signal.exchanges_total= multi_obi['exchanges_ok']
        signal.details        = multi_obi['details']

        if multi_obi['exchanges_ok'] < 3:
            signal.reason = f"Nombre d'échanges connectés insuffisant ({multi_obi['exchanges_ok']}/6)"
            return signal

        # ── 2. Calcul Tendance Long Terme ──────────────────────────
        df = calc_trend_indicators(df_klines)
        trend_info = get_trend_bias(df)

        signal.trend_bias = trend_info['bias']
        signal.entry      = trend_info['price']
        signal.vwap       = trend_info['vwap']
        signal.ema_fast   = trend_info['ema_fast']
        signal.ema_slow   = trend_info['ema_slow']
        signal.atr        = trend_info['atr']

        price = signal.entry
        atr   = signal.atr if signal.atr > 0 else (price * 0.01)

        # ══════════════════════════════════════════════════════════
        #  CONDITION BUY (ACHAT)
        #   - Majority des échanges en BUY OBI (>= 58%)
        #   - Consensus >= MIN_CONSENSUS_PCT
        #   - Tendance Long Terme Haussière (Prix >= VWAP / EMA50 >= EMA200)
        # ══════════════════════════════════════════════════════════
        if multi_obi['consensus_direction'] == 'BUY' and multi_obi['consensus_pct'] >= MIN_CONSENSUS_PCT:
            
            # Filtre Tendance
            if USE_TREND_FILTER and trend_info['bias'] == 'BEARISH':
                signal.reason = f"Consensus OBI Achat ({multi_obi['consensus_pct']:.0f}%) mais Tendance 1H Baissière (Prix < VWAP) → Filtré"
                signal.warnings.append("Contre-tendance 1H")
                return signal

            # Calcul SL et TP basés sur l'ATR pour du trend trading
            sl_price = price - (atr * 1.8)
            tp_price = price + (atr * 3.2)

            sl_dist = price - sl_price
            tp_dist = tp_price - price
            rr      = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0

            if rr >= MIN_RR:
                signal.direction = 'BUY'
                signal.strength  = 'STRONG' if multi_obi['consensus_pct'] >= 80.0 and trend_info['bias'] == 'BULLISH' else 'NORMAL'
                signal.sl        = round(sl_price, 6)
                signal.tp        = round(tp_price, 6)
                signal.rr        = rr
                signal.reason    = f"Consensus Institutionnel Achat {multi_obi['consensus_pct']:.0f}% sur {multi_obi['exchanges_ok']} échanges + Tendance Haussière"
                return signal

        # ══════════════════════════════════════════════════════════
        #  CONDITION SELL (VENTE)
        #   - Majority des échanges en SELL OBI (<= 42%)
        #   - Consensus >= MIN_CONSENSUS_PCT
        #   - Tendance Long Terme Baissière (Prix <= VWAP / EMA50 <= EMA200)
        # ══════════════════════════════════════════════════════════
        elif multi_obi['consensus_direction'] == 'SELL' and multi_obi['consensus_pct'] >= MIN_CONSENSUS_PCT:
            
            # Filtre Tendance
            if USE_TREND_FILTER and trend_info['bias'] == 'BULLISH':
                signal.reason = f"Consensus OBI Vente ({multi_obi['consensus_pct']:.0f}%) mais Tendance 1H Haussière (Prix > VWAP) → Filtré"
                signal.warnings.append("Contre-tendance 1H")
                return signal

            sl_price = price + (atr * 1.8)
            tp_price = price - (atr * 3.2)

            sl_dist = sl_price - price
            tp_dist = price - tp_price
            rr      = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0

            if rr >= MIN_RR:
                signal.direction = 'SELL'
                signal.strength  = 'STRONG' if multi_obi['consensus_pct'] >= 80.0 and trend_info['bias'] == 'BEARISH' else 'NORMAL'
                signal.sl        = round(sl_price, 6)
                signal.tp        = round(tp_price, 6)
                signal.rr        = rr
                signal.reason    = f"Consensus Institutionnel Vente {multi_obi['consensus_pct']:.0f}% sur {multi_obi['exchanges_ok']} échanges + Tendance Baissière"
                return signal

        # Neutre
        signal.reason = (
            f"[NEUTRAL] Consensus OBI insuffisant ({multi_obi['consensus_pct']:.0f}% | Achat:{multi_obi['exchanges_buy']} Vente:{multi_obi['exchanges_sell']}/{multi_obi['exchanges_ok']}) "
            f"ou pas d'alignement avec la tendance ({trend_info['bias']})"
        )
        return signal
