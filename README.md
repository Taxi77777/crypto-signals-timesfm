# 📈 Bot Kraken « VWAP ORDER FLOW » (footprint 1 min) — `kraken_vwap_flow.py`

Stratégie de la vidéo *Comment confirmer une entrée sur la VWAP*
(Dualis Flow, partie 2). Tourne 24 h/24 sur GitHub Actions (`.github/workflows/kraken_vwap_flow.yml`),
sur les perpétuels Kraken Pro crypto les plus liquides (≥ 5 M$/24 h, 15 max) : l'order flow n'a de sens
que sur un carnet très actif.

Le bot reçoit **chaque trade Kraken en direct** (WebSocket) et construit lui-même des bougies **footprint
1 minute** : volume, acheteurs et vendeurs agressifs, **delta**, et les **gros ordres** (un ordre = les
exécutions d'une même milliseconde ; « gros » = ≥ 10× la taille médiane des 3000 derniers ordres).

| Checklist (il faut les 4) | Règle du bot (exemple à l'ACHAT) |
|---|---|
| **1 · Prix sur la VWAP** | VWAP du jour (depuis 0 h UTC). Sur les 15 bougies précédentes, ≥ 70 % au-dessus de la VWAP, puis une bougie touche la VWAP (± 0,06 %) et clôture sur ou au-dessus. |
| **2 · Vendeurs absorbés** | Sur cette bougie : delta ≤ −20 % du volume (les vendeurs frappent)… mais le prix ne descend plus. |
| **3 · Big trade respecté** | Un gros **acheteur** dans la zone de la bougie d'absorption, et la bougie **clôture au-dessus** de son prix. |
| **4 · Acheteurs de retour** | Bougie suivante : delta ≥ +20 % et clôture plus haute → **entrée confirmée**. |

Annulation : clôture **sous** la VWAP, ou séquence de plus de 5 bougies. **Vente** : exactement l'inverse.

**Sortie** : stop **sous la bougie d'absorption** (`STOP_MODE=safe`, défaut) ou **sous le big trade**
(`STOP_MODE=aggressive`), au moins 0,25 % et au minimum imposé par les frais Kraken (frais ≤ ⅓ du risque) ;
TP = 2 × le risque (`RR`) ; à +1 R le stop remonte à l'entrée + frais, puis **trailing stop** : le stop suit le
meilleur prix à 1 R de distance (`TRAIL_R`, par pas de 0,25 R). Stop et TP posés chez Kraken (reposés si refusés).
Une seule position. Si Kraken refuse pour marge insuffisante : nouvel essai à 84 / 68 / 53 % de la taille.

**Suivi** : Telegram (entrée avec la checklist, sorties, résumé toutes les 6 h). Le fichier
`kraken_vwap_state.json` est mis à jour dans le dépôt toutes les 15 min, avec le journal des checklists
(`events`) sans capital ni P&L.

⚠️ Stratégie non prouvée en automatique : dans la vidéo elle est appliquée à la main sur le Nasdaq (NQ), un
marché centralisé. Sur la crypto, Kraken ne voit qu'une partie des trades mondiaux : le delta et les gros
ordres y sont plus bruités. Juger sur plusieurs semaines de trades réels.

---

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
