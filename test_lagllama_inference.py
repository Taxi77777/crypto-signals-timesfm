import sys
import numpy as np
import pandas as pd
import torch
from huggingface_hub import hf_hub_download

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
    prices = np.sin(np.linspace(0, 20, 100)) + 10.0
    
    df = pd.DataFrame({
        "target": prices
    }, index=pd.date_range(start="2026-01-01", periods=100, freq="15min"))
    
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
            lags_seq=["min"],
            trainer_kwargs={"accelerator": "cpu", "max_epochs": 0}
        )
        predictor = estimator.create_predictor(ckpt_path=ckpt_path)
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

if __name__ == "__main__":
    test_inference()
