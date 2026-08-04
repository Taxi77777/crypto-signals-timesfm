# 🌐 INSTITUTIONAL HUNTER PRO v3.0 — Multi-Exchange OBI & Trend Consensus

> **Stratégie d'Analyse Institutionnelle Multi-Échange & Tendance Long Terme**
> Interroge le carnet d'ordres (Order Book Imbalance) en temps réel sur **6 grands échanges mondiaux (MEXC, Bitget, Bybit, OKX, Binance, Kraken)** et exécute les trades sur **MEXC Futures** uniquement lorsque tous les échanges confirment la même direction !

---

## 🎯 Concept & Stratégie Institutionnelle

### 1. Le Déséquilibre du Carnet d'Ordres (Order Book Imbalance - OBI)
L'**OBI** mesure la pression nette réelle entre acheteurs et vendeurs dans la profondeur du carnet d'ordres (depth 20) :

$$\text{OBI} = \frac{\sum \text{Volume Bids (Acheteurs)}}{\sum \text{Volume Bids} + \sum \text{Volume Asks (Vendeurs)}}$$

- **$\text{OBI} \ge 0.58$ (58%)** $\rightarrow$ Domination nette des **Acheteurs** (Buy Pressure).
- **$\text{OBI} \le 0.42$ (42%)** $\rightarrow$ Domination nette des **Vendeurs** (Sell Pressure).

### 2. Consensus Multi-Échange (6 APIs 100% Gratuites)
Au lieu de se fier au carnet d'un seul échange, le bot analyse simultanément :
1. **MEXC** (`api.mexc.com`)
2. **Bitget** (`api.bitget.com`)
3. **Bybit** (`api.bybit.com`)
4. **OKX** (`www.okx.com`)
5. **Binance** (`data-api.binance.vision`)
6. **Kraken** (`api.kraken.com`)

Un trade est déclenché sur **MEXC Futures** **UNIQUEMENT SI au moins 70% des échanges** (ex: 4/5 ou 5/6) affichent un déséquilibre dans le même sens !

### 3. Filtre de Tendance Long Terme (Trend Filter)
Pour du trading long terme/tendance, le bot vérifie que la tendance globale (1H/4H) est alignée :
- **Condition BUY** : Consensus Multi-Échange ACHAT + Prix $\ge$ VWAP + EMA 50 $\ge$ EMA 200.
- **Condition SELL** : Consensus Multi-Échange VENTE + Prix $\le$ VWAP + EMA 50 $\le$ EMA 200.

---

## 📁 Architecture du Projet

```
├── bot.py           # Orchestrateur principal & boucle de trading
├── strategy.py      # Moteur de décision Consensus OBI + Tendance
├── exchanges.py     # Connecteur multi-échange (MEXC, Bitget, Bybit, OKX, Binance, Kraken)
├── indicators.py    # Tendance Long Terme (EMA 50/200, VWAP, ATR, RSI)
├── mexc_api.py      # Client API MEXC Futures pour l'exécution des ordres
├── scanner.py       # Scanner automatique des plus gros volumes USDT
├── doh_patch.py     # Patch DNS Over HTTPS (1.1.1.1) pour 100% de fiabilité API
├── config.py        # Configuration centralisée (Levier, Risque, Seuil %)
├── requirements.txt # Dépendances Python
└── LANCER_BOT.bat   # Script de démarrage rapide pour Windows
```

---

## ⚙️ Configuration (`config.py`)

```python
# Levier et Gestion du Risque
LEVERAGE            = 10      # Levier conseillé (5x à 20x)
RISK_PER_TRADE_PCT  = 1.0     # 1% du capital risqué par trade
MIN_RR              = 1.5     # Ratio Risque/Rendement minimum (1:1.5)
MIN_CONSENSUS_PCT   = 70.0    # 70% de consensus minimum entre les échanges

# Clés API MEXC (pour passer les ordres)
MEXC_API_KEY        = "votre_clé_api"
MEXC_SECRET_KEY     = "votre_clé_secrète"
```

---

## 🚀 Lancement du Bot

### Étape 1 : Dépendances
```bash
pip install -r requirements.txt
```

### Étape 2 : Lancer
```bash
# Sur Windows : Double-cliquer sur LANCER_BOT.bat
# Ou en ligne de commande :
python bot.py
```

---

## 📱 Alertes Telegram

Pour chaque opportunité validée par le consensus institutionnel, une alerte est émise :

```
🟢 ACHAT ⭐⭐⭐ — BTC_USDT (Multi-Exchange OBI & Trend)
───────────────────────────────────────────────────────
  Consensus Multi-Échange : 83% (5/6 échanges)
  OBI Moyen Global       : 0.684 (Domination Acheteurs)
  Tendance Long Terme    : BULLISH (Prix:65420.00 | VWAP:65100.00)
───────────────────────────────────────────────────────
   • MEXC     | OBI: 0.710 | BUY
   • Bitget   | OBI: 0.650 | BUY
   • Bybit    | OBI: 0.690 | BUY
   • OKX      | OBI: 0.640 | BUY
   • Binance  | OBI: 0.730 | BUY
   • Kraken   | OBI: 0.490 | NEUTRE
───────────────────────────────────────────────────────
  Prix Entrée : 65420.00
  Take Profit : 66800.00 (TP)
  Stop Loss   : 64500.00 (SL)
  Ratio R/R   : 1:1.52
```

---

## ⚖️ Licence
MIT License — Institutional Hunter Pro v3.0
