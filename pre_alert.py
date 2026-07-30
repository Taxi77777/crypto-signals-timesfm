"""
╔══════════════════════════════════════════════════════════════════╗
║     INSTITUTIONAL HUNTER PRO — PRE-ALERT ENGINE                 ║
║     pre_alert.py — Détection anticipée Steps 1+2                ║
║                                                                  ║
║  Dès que Step1 + Step2 sont confirmés → alerte immédiate +      ║
║  ordre limite au niveau LVN prêt à déclencher.                  ║
║                                                                  ║
║  FLUX :                                                          ║
║  Étape 1 OK → surveillance active                               ║
║  Étape 2 OK → ordre LIMITE placé au LVN                        ║
║  Prix touche LVN → ENTRÉ  (avant Step 3 !)                     ║
║  Si Step 3 ne confirme pas → annuler l'ordre                   ║
╚══════════════════════════════════════════════════════════════════╝
"""
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

import mexc_api as api
from indicators import calc_fisher, calc_vwap, calc_moving_averages, VolumeProfile
from timing import seconds_to_next_close, m15_slot

log = logging.getLogger('IHP-PREALERT')


@dataclass
class PreAlert:
    """Setup en attente d'entrée (Steps 1+2 validés)."""
    symbol:      str
    direction:   str     # 'BUY' ou 'SELL'
    lvn_level:   float   # Niveau LVN = prix d'entrée limite
    hvn_target:  float   # TP cible
    sl_price:    float   # SL calculé
    rr_est:      float   # RR estimé
    created_at:  float = field(default_factory=time.time)
    candle_slot: str   = ""
    order_id:    str   = ""    # ID ordre limite placé
    is_active:   bool  = True
    step1_desc:  str   = ""
    step2_desc:  str   = ""

    def age_seconds(self) -> float:
        return time.time() - self.created_at

    def is_expired(self) -> bool:
        # Expire après 2 bougies M15 (30 min)
        return self.age_seconds() > 1800


class PreAlertManager:
    """
    Gère les pré-alertes (setups Steps 1+2 confirmés).
    Place des ordres limites au niveau LVN pour ne pas rater l'entrée.
    """

    def __init__(self):
        self._alerts: Dict[str, PreAlert] = {}

    def scan_pre_setup(self, symbol: str, df) -> Optional[PreAlert]:
        """
        Détecte si Steps 1+2 sont validés sans attendre Step 3.
        Retourne un PreAlert si setup en formation, None sinon.
        """
        if df is None or len(df) < 70:
            return None

        # Calculer indicateurs
        df = calc_fisher(df)
        df = calc_vwap(df)
        df = calc_moving_averages(df)
        vp = VolumeProfile(df).compute()

        # Step 1 = T-2 (avant-avant-dernière fermée)
        s1 = df.iloc[-3]   # Step 1
        s2 = df.iloc[-2]   # Step 2 (dernière fermée)
        # Step 3 = bougie en cours (pas encore fermée)

        cur_price = float(df.iloc[-1]['close'])   # Prix actuel (bougie ouverte)
        levels    = vp.nearest(cur_price)
        lvn_above = levels['lvn_above']
        lvn_below = levels['lvn_below']
        hvn_above = levels['hvn_above']
        hvn_below = levels['hvn_below']

        avg_vol = float(df['volume'].iloc[-22:-1].mean())
        fisher2 = float(s2['fisher']) if not float('nan') == s2['fisher'] else 0

        # ── Détection Setup SELL anticipé ──────────────────────
        # Step 1 : prix proche HVN résistance
        s1_density = vp.density_at(float(s1['close']))
        s1_near_hvn = s1_density >= 0.65

        # Step 2 : mèche haute + volume faible + bougie baissière
        bar_range2 = float(s2['high']) - float(s2['low'])
        wick_top2  = float(s2['high']) - max(float(s2['open']), float(s2['close']))
        s2_rejection = bar_range2 > 0 and (wick_top2 / bar_range2) >= 0.35
        s2_weak_vol  = float(s2['volume']) < avg_vol * 0.9
        s2_bearish   = float(s2['close']) < float(s2['open'])
        fisher_high  = fisher2 > 1.2    # Fisher encore élevé → potentiel croisement

        sell_setup = (s1_near_hvn and
                      (s2_rejection or s2_weak_vol or s2_bearish) and
                      fisher_high and lvn_below)

        if sell_setup and lvn_below and hvn_below:
            # Ordre limite SOUS le LVN (entrée dès cassure)
            entry_price = lvn_below * 0.9995   # Juste sous le LVN
            sl_price    = float(s2['high']) + (bar_range2 * 0.15)
            tp_price    = hvn_below * 1.001
            sl_dist     = sl_price - entry_price
            tp_dist     = entry_price - tp_price
            rr          = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0

            if rr >= 1.5:
                return PreAlert(
                    symbol      = symbol,
                    direction   = 'SELL',
                    lvn_level   = entry_price,
                    hvn_target  = tp_price,
                    sl_price    = sl_price,
                    rr_est      = rr,
                    candle_slot = m15_slot(),
                    step1_desc  = f"HVN résistance density={s1_density:.0%}",
                    step2_desc  = (f"Rejet={s2_rejection} | "
                                   f"Vol faible={s2_weak_vol} | "
                                   f"Fisher={fisher2:.2f}"),
                )

        # ── Détection Setup BUY anticipé ───────────────────────
        wick_bot2    = min(float(s2['open']), float(s2['close'])) - float(s2['low'])
        s2_bounce    = bar_range2 > 0 and (wick_bot2 / bar_range2) >= 0.35
        s2_bullish   = float(s2['close']) > float(s2['open'])
        fisher_low   = fisher2 < -1.2

        buy_setup = (s1_near_hvn and
                     (s2_bounce or s2_weak_vol or s2_bullish) and
                     fisher_low and lvn_above)

        if buy_setup and lvn_above and hvn_above:
            entry_price = lvn_above * 1.0005
            sl_price    = float(s2['low']) - (bar_range2 * 0.15)
            tp_price    = hvn_above * 0.999
            sl_dist     = entry_price - sl_price
            tp_dist     = tp_price - entry_price
            rr          = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0

            if rr >= 1.5:
                return PreAlert(
                    symbol      = symbol,
                    direction   = 'BUY',
                    lvn_level   = entry_price,
                    hvn_target  = tp_price,
                    sl_price    = sl_price,
                    rr_est      = rr,
                    candle_slot = m15_slot(),
                    step1_desc  = f"HVN support density={s1_density:.0%}",
                    step2_desc  = (f"Rebond={s2_bounce} | "
                                   f"Vol faible={s2_weak_vol} | "
                                   f"Fisher={fisher2:.2f}"),
                )

        return None

    def add_alert(self, alert: PreAlert):
        """Enregistre un pré-alerte et place l'ordre limite."""
        if alert.symbol in self._alerts:
            return  # Déjà un setup sur ce symbole

        self._alerts[alert.symbol] = alert

        arrow = "🔴" if alert.direction == 'SELL' else "🟢"
        log.info(
            f"\n{arrow} PRÉ-ALERTE {alert.direction} — {alert.symbol}\n"
            f"  Étape 1 : {alert.step1_desc}\n"
            f"  Étape 2 : {alert.step2_desc}\n"
            f"  → Ordre limite à {alert.lvn_level:.4f} (LVN)\n"
            f"  → TP : {alert.hvn_target:.4f} | SL : {alert.sl_price:.4f}\n"
            f"  → R/R estimé : 1:{alert.rr_est:.2f}\n"
            f"  → Clôture M15 dans {seconds_to_next_close():.0f}s"
        )

        # Placer l'ordre limite MAINTENANT (avant la clôture Step 3)
        self._place_limit_order(alert)

    def _place_limit_order(self, alert: PreAlert):
        """Place l'ordre limite au niveau LVN."""
        try:
            # side: 1=BUY Long, 3=SELL Short
            side = 1 if alert.direction == 'BUY' else 3
            result = api.place_order(
                symbol     = alert.symbol,
                side       = side,
                vol        = 0,        # Volume calculé dans bot.py
                order_type = 1,        # 1 = LIMIT
                price      = alert.lvn_level,
                sl_price   = alert.sl_price,
                tp_price   = alert.hvn_target,
            )
            if result and result.get('code') == 200:
                alert.order_id = str(result.get('data', ''))
                log.info(f"  ✅ Ordre limite #{alert.order_id} placé à {alert.lvn_level:.4f}")
            else:
                log.warning(f"  ⚠️  Ordre limite refusé : {result}")
        except Exception as e:
            log.error(f"  Erreur ordre limite {alert.symbol} : {e}")

    def cancel_expired(self):
        """Annule les pré-alertes expirées ou dont la bougie a changé."""
        current_slot = m15_slot()
        to_remove = []

        for sym, alert in self._alerts.items():
            # Expirée (>2 bougies) ou setup appartient à une ancienne bougie
            if alert.is_expired():
                log.info(f"[PRE-ALERT] {sym} expirée — annulation de l'ordre limite")
                if alert.order_id:
                    try:
                        api.cancel_order(alert.order_id)
                    except Exception:
                        pass
                to_remove.append(sym)

        for sym in to_remove:
            del self._alerts[sym]

    def get_active(self) -> Dict[str, PreAlert]:
        return {k: v for k, v in self._alerts.items() if v.is_active}

    def remove(self, symbol: str):
        """Supprime un pré-alerte (ex: position déjà ouverte)."""
        self._alerts.pop(symbol, None)

    def summary(self) -> str:
        if not self._alerts:
            return "Aucun setup en attente"
        lines = [f"⏳ {len(self._alerts)} setup(s) en attente :"]
        for sym, a in self._alerts.items():
            lines.append(f"  {'🔴' if a.direction=='SELL' else '🟢'} "
                         f"{a.direction} {sym} @ {a.lvn_level:.4f} "
                         f"(âge: {a.age_seconds():.0f}s)")
        return "\n".join(lines)
