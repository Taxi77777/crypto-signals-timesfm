import sys
import numpy as np
import pandas as pd
import torch
from huggingface_hub import hf_hub_download

# Apply Monkeypatch BEFORE importing LagLlamaEstimator
try:
    import gluonts.time_feature.lag as lag_module
    original_norm = lag_module.norm_freq_str
    def patched_norm(freq_str: str) -> str:
        res = original_norm(freq_str)
        mapping = {
            "min": "T",
            "h": "H",
            "s": "S",
            "d": "D",
            "w": "W",
            "QE": "Q",
            "YE": "A",
            "ME": "M"
        }
        return mapping.get(res, res)
    lag_module.norm_freq_str = patched_norm
    print("Monkeypatch norm_freq_str: Applied")
except Exception as e:
    print("Failed to apply monkeypatch:", e)

try:
    from lag_llama.gluon.estimator import LagLlamaEstimator
    from gluonts.dataset.pandas import PandasDataset
    from gluonts.evaluation import make_evaluation_predictions
    print("Imports Lag-Llama/GluonTS: OK")
except Exception as e:
    print("Erreur import Lag-Llama:", e)
    sys.exit(1)

def test_inference():
    # 1. Télécharger le checkpoint via huggingface_hub
    print("Téléchargement du checkpoint Lag-Llama...")
    try:
        ckpt_path = hf_hub_download(
            repo_id="time-series-foundation-models/Lag-Llama",
            filename="lag-llama.ckpt"
        )
        print(f"Checkpoint téléchargé à : {ckpt_path}")
    except Exception as e:
        print("Erreur de téléchargement du checkpoint:", e)
        sys.exit(1)

    # 2. Créer de fausses données (100 points de prix)
    np.random.seed(42)
    # IMPORTANT : float32 obligatoire (le modèle est en Float, pandas crée du Double par défaut)
    prices = (np.sin(np.linspace(0, 20, 100)) + 10.0).astype(np.float32)

    df = pd.DataFrame({
        "target": prices
    }, index=pd.date_range(start="2026-01-01", periods=100, freq="15min"))
    df["target"] = df["target"].astype("float32")
    
    dataset = PandasDataset(df, target="target")
    
    # 3. Initialiser le modèle
    print("Chargement du modèle Lag-Llama...")
    try:
        # Lire les hyperparamètres depuis le checkpoint
        ckpt = torch.load(ckpt_path, map_location=torch.device('cpu'))
        estimator_args = ckpt["hyper_parameters"]["model_kwargs"]
        estimator = LagLlamaEstimator(
            ckpt_path=ckpt_path,
            prediction_length=4,
            context_length=32,
            input_size=estimator_args["input_size"],
            n_layer=estimator_args["n_layer"],
            n_embd_per_head=estimator_args["n_embd_per_head"],
            n_head=estimator_args["n_head"],
            scaling=estimator_args.get("scaling", "robust"),
            time_feat=estimator_args.get("time_feat", True),
            trainer_kwargs={"accelerator": "cpu", "max_epochs": 0}
        )
        transformation = estimator.create_transformation()
        lightning_module = estimator.create_lightning_module()
        predictor = estimator.create_predictor(transformation, lightning_module)
        print("Modèle chargé !")
        
        # 4. Inférence
        forecast_it, ts_it = make_evaluation_predictions(
            dataset=dataset,
            predictor=predictor,
            num_samples=100
        )
        forecasts = list(forecast_it)
        print("Inférence réussie !")
        
        forecast = forecasts[0]
        print("Forecast type:", type(forecast))
        print("Samples shape:", forecast.samples.shape)
        median_prediction = np.median(forecast.samples, axis=0)
        print("Median prediction:", median_prediction)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    test_inference()
