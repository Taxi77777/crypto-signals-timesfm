"""
test_granite_inference.py — Test d'inférence IBM Granite TTM (TinyTimeMixer r2)
Approche directe : TinyTimeMixerForPrediction + tenseur (1, 512, 1), sans préprocesseur.
"""

import sys
import numpy as np
import torch

try:
    from tsfm_public import TinyTimeMixerForPrediction
    print("Imports IBM Granite TTM: OK")
except Exception as e:
    print("Erreur import IBM Granite TTM:", e)
    sys.exit(1)


def test_inference():
    # 512 points de prix factices (contexte natif TTM-r2)
    np.random.seed(42)
    prices = (np.sin(np.linspace(0, 20, 512)) + 10.0).astype(np.float32)

    print("Chargement du modèle ibm-granite/granite-timeseries-ttm-r2...")
    try:
        model = TinyTimeMixerForPrediction.from_pretrained(
            "ibm-granite/granite-timeseries-ttm-r2"
        )
        model.eval()
        print("Modèle IBM TTM chargé !")

        tensor = torch.tensor(prices).reshape(1, 512, 1)
        with torch.no_grad():
            outputs = model(past_values=tensor)

        preds = outputs.prediction_outputs[0, :, 0].numpy()
        print("Prediction shape:", outputs.prediction_outputs.shape)
        print("Prédictions (4 premières valeurs, soit 1h en 15m) :", preds[:4])
        print("Inférence IBM TTM réussie !")
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    test_inference()
