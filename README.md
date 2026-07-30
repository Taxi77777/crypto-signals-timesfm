# 📊 MEXC LVN Trading Bot — 15m LVN Acceleration & Rejection

> **Stratégie séquentielle en 3 étapes** sur Volume Profile M15 pour MEXC Futures

---

## 🧠 Stratégie : 15m LVN ACCELERATION & REJECTION

### Principe
Exploiter les **creux de volume (LVN — Low Volume Node)** sur le graphique M15.
Les zones LVN sont des zones vides où le prix accélère rapidement.
Les zones HVN sont des zones denses où le prix s'arrête (cibles TP).

### Logique Séquentielle en 3 Étapes

```
Étape 1 (Bougie T-2) ── Identification du LVN
   └── Prix arrive près d'un creux de volume

Étape 2 (Bougie T-1) ── Test du niveau
   └── Mèche de rejet / volume faible / hésitation

Étape 3 (Bougie T)   ── CONFIRMATION → Entrée
   └── BUY  : bougie haussière clôture AU-DESSUS du LVN
   └── SELL : bougie baissière clôture EN-DESSOUS du LVN
```

### Filtres de Confirmation
| Indicateur | Rôle |
|---|---|
| **Fisher Transform 9** | Déclencheur principal — croisement depuis zone extrême (>±1.5) |
| **VPVR / Volume Profile** | Détection LVN (creux) et HVN (pics) |
| **VWAP Session** | Biais directionnel (au-dessus = bull, en-dessous = bear) |
| **MA 30 / MA 60** | Confirmation de tendance |
| **Order Book Imbalance** | Pression acheteur/vendeur temps réel |

### Gestion du Risque
- **Risque par trade** : 1% du capital
- **Levier** : x5 (conservateur)
- **RR minimum** : 1:1.5
- **Max positions** : 3 simultanées
- **Stop journalier** : -5%
- **Trailing Stop** : actif après +0.5% de profit

---

## 📁 Structure du Projet

```
├── bot.py           # Bot principal — orchestrateur multi-paires
├── strategy.py      # Moteur stratégie 3 étapes LVN
├── indicators.py    # Fisher, VWAP, MA, Volume Profile
├── mexc_api.py      # Wrapper API MEXC Futures v1
├── config.py        # Configuration (paires, risque, clés)
├── requirements.txt # Dépendances Python
└── LANCER_BOT.bat   # Démarrage Windows (double-clic)
```

---

## 🚀 Installation

### 1. Prérequis
```bash
pip install -r requirements.txt
```

### 2. Configuration
Ouvrir `config.py` et renseigner :
```python
MEXC_API_KEY    = "votre_clé_api"
MEXC_SECRET_KEY = "votre_clé_secrète"
```
> 🔑 Créer vos clés sur : https://www.mexc.com/user/openapi

### 3. Lancement
```bash
# Windows
double-clic sur LANCER_BOT.bat

# Linux / Mac
python bot.py
```

---

## 📊 Paires Tradées (par volume décroissant)

| Tier | Paires |
|---|---|
| 🔥 Tier 1 (>500M/24h) | BTC_USDT, ETH_USDT |
| ⭐ Tier 2 (>100M/24h) | OIL_USDT, SOL_USDT, BNB_USDT, XRP_USDT |
| ✅ Tier 3 (>50M/24h) | DOGE_USDT, ADA_USDT, AVAX_USDT, LINK_USDT, MATIC_USDT |
| 🏅 Matières premières | GOLD_USDT, SILVER_USDT |

---

## 📱 Alertes Telegram

Le bot envoie pour chaque signal la **rétrospective complète des 3 étapes** :

```
🔴 VENTE OIL_USDT
─────────────────────────────────────────────
1️⃣ Étape 1 : Prix proche LVN 85.20 | Fisher 2.04
2️⃣ Étape 2 : Mèche haute dominante → rejet | Volume faible
3️⃣ Étape 3 : ✅ Bougie BAISSIÈRE | clôture=85.04 sous LVN
─────────────────────────────────────────────
  Entrée : 85.04
  TP     : 84.40  (HVN cible)
  SL     : 85.45
  R/R    : 1:1.6
  LVN ↯  : 85.20
  Fisher : 1.08
  ✗ BUY invalidé → clôture E3 sous LVN + rejet E2
```

---

## ⚠️ Avertissement

Ce bot est fourni à des fins éducatives.
Le trading de cryptomonnaies comporte des risques importants.
Ne jamais investir plus que ce que vous pouvez vous permettre de perdre.

---

## 📜 Licence
MIT License
