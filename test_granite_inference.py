import sys
import numpy as np
import pandas as pd
import torch

try:
    from tsfm_public import TinyTimeMixerForPrediction, TimeSeriesPreprocessor
    from tsfm_public.toolkit.dataset import ForecastDataset
    print("Imports IBM Granite TTM: OK")
except Exception as e:
    print("Erreur import IBM Granite TTM:", e)
    sys.exit(1)

def test_inference():
    # 1. Créer de fausses données (512 points de prix historique)
    np.random.seed(42)
    prices = np.sin(np.linspace(0, 20, 512)) + 10.0
    
    df = pd.DataFrame({
        "date": pd.date_range(start="2026-01-01", periods=512, freq="15min"),
        "target": prices
    })
    
    # TTM-R2 s'attend généralement à context_length=512 et prediction_length=96
    context_length = 512
    prediction_length = 96
    
    print("Initialisation du préprocesseur...")
    try:
        # Initialiser le preprocessor
        tsp = TimeSeriesPreprocessor(
            timestamp_column="date",
            target_columns=["target"],
            context_length=context_length,
            prediction_length=prediction_length,
            scaling=True,
            scaler_type="standard"
        )
        # Ajuster et transformer les données
        tsp.train(df)
        processed_df = tsp.preprocess(df)
        print("Préprocès terminé !")
        
        # Charger le modèle depuis HF
        print("Chargement du modèle ibm-granite/granite-timeseries-ttm-r2...")
        model = TinyTimeMixerForPrediction.from_pretrained(
            "ibm-granite/granite-timeseries-ttm-r2",
            num_input_channels=tsp.num_input_channels,
            prediction_channel_indices=tsp.prediction_channel_indices
        )
        print("Modèle IBM TTM chargé !")
        
        # Préparer le dataset PyTorch pour le modèle
        # TTM utilise des batchs PyTorch standard
        # On peut convertir le dataframe pré-traité en tensor d'entrée
        # La forme d'entrée attendue est (batch_size, context_length, num_input_channels)
        # Mais le préprocesseur peut être utilisé directement avec TimeSeriesForecastingPipeline si disponible,
        # ou on peut fabriquer le tensor manuellement.
        # Faisons le manuellement pour garder le contrôle :
        values = processed_df["target"].values[-context_length:]
        input_tensor = torch.tensor(values, dtype=torch.float32).unsqueeze(0).unsqueeze(-1) # shape: (1, 512, 1)
        
        model.eval()
        with torch.no_grad():
            outputs = model(past_values=input_tensor)
            # Les sorties contiennent 'prediction_outputs' ou similar, let's inspecter
            print("Outputs keys:", outputs.keys())
            prediction = outputs.prediction_outputs # shape: (batch_size, prediction_length, num_targets)
            print("Prediction shape:", prediction.shape)
            
            # Re-mettre à l'échelle inverse si besoin, ou juste inspecter la direction
            # La première prédiction (index 0) du premier lot
            pred_values = prediction[0, :, 0].numpy()
            
            # Inverse scaling
            scaler = tsp.scalers["target"]
            # Reshape pour inverse_transform : (prediction_length, 1)
            pred_values_orig = scaler.inverse_transform(pred_values.reshape(-1, 1)).flatten()
            
            print("Predictions (premières 4 valeurs, soit 1h en 15m) :", pred_values_orig[:4])
            print("Inférence IBM TTM réussie !")
            
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_inference()
