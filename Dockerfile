# ─────────────────────────────────────────────────────────────────────────────
# Bot Crypto Signals — image de déploiement VPS
#
# Pourquoi cette image existe : sur GitHub Actions, 7 à 9 minutes de chaque run
# étaient consommées par `pip install` (torch + 5 modèles de forecasting), et le
# cron était étranglé à ~94 min d'écart médian. Ici les dépendances ET les poids
# des modèles sont installés UNE FOIS au build. Chaque exécution ne fait plus que
# l'analyse : quelques dizaines de secondes.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Paris \
    HF_HOME=/models \
    TRANSFORMERS_CACHE=/models

# git : requis par pip pour installer Lag-Llama depuis GitHub
# cron : ordonnanceur système (fiable, contrairement au cron GitHub Actions)
# tzdata : pour que les timestamps des logs soient à ton heure locale
RUN apt-get update && apt-get install -y --no-install-recommends \
        git cron tzdata ca-certificates curl \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Couche de dépendances séparée du code : un changement de code ne réinstalle pas torch
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Modèles IA additionnels — même ordre que le workflow GitHub, qui est significatif :
# "transformers<4.50" DOIT venir en dernier, sinon uni2ts tire une version
# incompatible avec torch 2.4 et provoque l'erreur DTensor.
RUN pip install --no-cache-dir uni2ts \
 && pip install --no-cache-dir scikit-learn deprecated \
 && pip install --no-cache-dir granite-tsfm --no-deps \
 && pip install --no-cache-dir git+https://github.com/time-series-foundation-models/lag-llama.git \
 && pip install --no-cache-dir "transformers<4.50"

COPY . /app
RUN mkdir -p /app/logs /models

# Pré-téléchargement des poids au BUILD plutôt qu'au premier run : évite qu'un
# premier trade soit retardé de plusieurs minutes par le download des modèles.
# Échec toléré (|| true) : si HuggingFace est indisponible pendant le build,
# les poids seront simplement récupérés au premier run.
RUN python -c "\
import logging; logging.basicConfig(level=logging.INFO);\
from huggingface_hub import snapshot_download;\
[snapshot_download(r) for r in ['google/timesfm-2.5-200m-pytorch','amazon/chronos-t5-mini','Salesforce/moirai-2.0-R-small','ibm-granite/granite-timeseries-ttm-r2']]\
" || echo "Pre-download partiel — les poids manquants seront pris au premier run"

CMD ["python", "run_once.py"]
