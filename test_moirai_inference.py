import sys
import numpy as np
import pandas as pd
import torch

try:
    from uni2ts.model.moirai2 import Moirai2Forecast, Moirai2Module
    from gluonts.dataset.pandas import PandasDataset
    print("Imports uni2ts/gluonts: OK")
except Exception as e:
    print("Erreur import Moirai:", e)
    sys.exit(1)

def test_inference():
    np.random.seed(42)
    prices = np.sin(np.linspace(0, 20, 100)) + 10.0
    
    df = pd.DataFrame({
        "target": prices
    }, index=pd.date_range(start="2026-01-01", periods=100, freq="15min"))
    
    dataset = PandasDataset(df, target="target")
    
    print("Chargement du modèle Moirai-2.0-R-small...")
    try:
        module = Moirai2Module.from_pretrained("Salesforce/moirai-2.0-R-small")
        model = Moirai2Forecast(
            module=module,
            prediction_length=4,  # Forecast horizon (4 paires = 1h en 15m)
            context_length=100,   # Longueur de l'historique passé
            target_dim=1,
            feat_dynamic_real_dim=0,
            past_feat_dynamic_real_dim=0,
        )
        print("Modèle chargé !")
        
        predictor = model.create_predictor(batch_size=1)
        forecasts = list(predictor.predict(dataset))
        print("Inférence réussie !")
        
        forecast = forecasts[0]
        print("Forecast type:", type(forecast))
        print("Samples shape:", forecast.samples.shape)
        median_prediction = np.median(forecast.samples, axis=0)
        print("Median prediction:", median_prediction)
        
    except Exception as e:
        print("Erreur lors de l'exécution de Moirai:", e)

if __name__ == "__main__":
    test_inference()
