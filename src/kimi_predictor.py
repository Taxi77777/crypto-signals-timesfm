"""
src/kimi_predictor.py — Connecteur IA Kimi (Moonshot AI / Kimi-K3)
"""

import os
import logging
import requests
import config

logger = logging.getLogger(__name__)

MOONSHOT_BASE_URL = "https://api.moonshot.cn/v1"

class KimiPredictor:
    def __init__(self):
        self.api_key = os.getenv("KIMI_API_KEY", os.getenv("MOONSHOT_API_KEY", ""))
        self.model_name = os.getenv("KIMI_MODEL", "moonshot-v1-8k")
        self.is_available = bool(self.api_key)
        if self.is_available:
            logger.info(f"🤖 Agent IA Kimi (Moonshot / Kimi-K3) prêt avec le modèle {self.model_name} !")
        else:
            logger.info("ℹ️ Clé KIMI_API_KEY non fournie — Agent Kimi en mode simulation fallback.")

    def predict(self, df, symbol: str, horizon: int = 4) -> list | None:
        """
        Analyse les séries temporelles et fournit la prédiction de prix IA Kimi.
        """
        if df.empty or len(df) < 20:
            return None

        cur_price = float(df["close"].iloc[-1])
        
        if not self.is_available:
            # Simulation mathématique d'analyse de tendance basée sur la volatilité
            mean_ret = float(df["close"].pct_change().tail(10).mean())
            trend_factor = 1.0 + (mean_ret * 0.5)
            preds = [cur_price * (trend_factor ** i) for i in range(1, horizon + 1)]
            return preds

        try:
            # Appel API Moonshot Kimi AI
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            sub_df = df.tail(15)[["open", "high", "low", "close", "volume"]]
            history_str = sub_df.to_string()
            
            prompt = (
                f"Act as a quantitative financial trading AI model (Kimi-K3).\n"
                f"Symbol: {symbol}\n"
                f"Current Price: {cur_price}\n"
                f"Recent 15 candles data:\n{history_str}\n\n"
                f"Predict the next {horizon} candle close prices as a comma-separated list of numbers ONLY. Example: 64500.5, 64520.1, 64550.0, 64600.2"
            )

            payload = {
                "model": self.model_name,
                "messages": [
                    {"role": "system", "content": "You are a professional financial quantitative forecasting model."},
                    {"role": "role", "content": prompt}
                ],
                "temperature": 0.2
            }

            r = requests.post(f"{MOONSHOT_BASE_URL}/chat/completions", headers=headers, json=payload, timeout=10)
            if r.status_code == 200:
                content = r.json()["choices"][0]["message"]["content"].strip()
                parts = [float(p.strip()) for p in content.split(",") if p.strip()]
                if len(parts) >= horizon:
                    return parts[:horizon]
        except Exception as e:
            logger.warning(f"Kimi Predictor Exception pour {symbol}: {e}")

        # Fallback si erreur API
        mean_ret = float(df["close"].pct_change().tail(5).mean())
        return [cur_price * (1 + mean_ret * i) for i in range(1, horizon + 1)]
