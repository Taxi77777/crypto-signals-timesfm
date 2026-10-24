# Stratégies et sources

## 1. Stocks in Play — cassure d'ouverture 5 min (actions US)

**Source** : C. Zarattini, A. Barbon, A. Aziz (2024), *A Profitable Day Trading Strategy For The U.S. Equity
Market*, SSRN 4729284.

| Règle de l'étude | Dans le bot |
|---|---|
| Univers : prix > 5 $, volume moyen 14 j ≥ 1 M, ATR 14 j > 0,50 $ | identique (`sip_analyse`) |
| « Stocks in Play » : volume relatif de la 1re bougie 5 min ≥ 100 % de la moyenne 14 j, top 20 | identique (`SIP_RVOL`, `SIP_TOP`) |
| Sens = couleur de la 1re bougie 5 min ; doji = rien | identique |
| Ordre stop au plus haut (achat) / plus bas (vente) de la 1re bougie | identique |
| Stop à 10 % de l'ATR 14 j | identique (`SIP_ATR_STOP`) |
| Sortie en fin de séance | identique (15 h 55 NY ; TP 3R affiché à titre indicatif) |

Résultats publiés (2016-2023) : Sharpe 2,81 et 1 637 % au total avec le filtre de volume, contre
Sharpe 0,48 sans filtre. Réserves des auteurs et des relecteurs : pas de période hors échantillon
clairement séparée, glissement non modélisé, portefeuille concentré.

**Backtest du bot** : bougies 5 min, ordre des prix dans la bougie d'entrée inconnu → le stop n'y est
compté que si la bougie clôture au-delà (choix neutre, ni optimiste ni pessimiste).

## 2. Momentum intraday — zone de bruit + VWAP (ETF, actions, forex, crypto)

**Sources** : C. Zarattini, A. Aziz, A. Barbon (2024), *Beat the Market: An Effective Intraday Momentum
Strategy for S&P500 ETF (SPY)* ; L. Gao, Y. Han, S. Z. Li, G. Zhou (2018), *Market intraday momentum*,
Journal of Financial Economics.

- Zone de bruit : ouverture du jour × (1 ± moyenne sur 14 jours de |cours / ouverture − 1| à la même heure),
  bornes corrigées des gaps (max / min de l'ouverture et de la clôture précédente).
- Décision à HH:00 et HH:30 : achat si le prix est au-dessus de la borne haute ET de la VWAP du jour,
  vente si en dessous de la borne basse ET de la VWAP.
- Stop suiveur : max(borne, VWAP) pour un achat, min(borne, VWAP) pour une vente, mis à jour toutes les
  30 min ; sortie en fin de séance.
- Adaptation : forex, or et crypto avec un jour qui commence à 0 h UTC ; décisions 7 h – 19 h 30 UTC
  (forex) et 6 h 30 – 20 h UTC (crypto, 7 j/7).

## 2 bis. Dernière demi-heure (ETF indiciels US)

- **Règle** : à 15 h 30 New York (12 h 30 les jours de clôture anticipée), rendement depuis la clôture de la veille
  (rROD). Si |rROD| ≥ `LH_MIN` (0,4 %), position dans ce sens jusqu'à la clôture.
- **Stop** : 2 × le déplacement moyen de la dernière demi-heure sur l'historique (60 jours).
- **Sources** : Gao, Han, Li & Zhou (2018, *Journal of Financial Economics*) ; Baltussen, Da, Lammers & Martens (2021,
  *JFE*) : 1974-2020, plus de 60 contrats à terme, Sharpe 0,87 à 1,73 ; effet plus fort quand les dealers d'options
  sont « short gamma » (marché volatil), et qui s'inverse dans les jours suivants.
- **Limites** : l'étude ne publie pas de stop ; celui du bot est un choix de gestion du risque. Le filtre de 0,4 % est
  un choix du bot pour éviter les journées sans direction.

## 3. Couche desks institutionnels

| Élément | Source | Utilisation dans le bot |
|---|---|---|
| Ciblage de volatilité | Harvey, Hoyle, Korgaonkar, Rattray, Sargaison & Van Hemert (2018), *JPM* (Man Group) : Sharpe des actions US 0,40 → 0,48-0,51 | risque par trade divisé par 2 si VIX ≥ `VOL_CUT` (25) |
| Dérive pré-FOMC | Lucca & Moench (2015), *Journal of Finance* / Fed de New York : ~49 pb sur le S&P dans les 24 h avant l'annonce (1994-2011) | alerte la veille et le jour de la Fed (briefing, signaux actions) |
| Courbe des taux 10 ans − 3 mois | indicateur de récession suivi par la Fed de New York | briefing du matin |
| Fear & Greed | CNN (actions), alternative.me (crypto) | briefing du matin |
| Funding / open interest | API publique Kraken Futures | briefing + chaque signal crypto (alerte si positions déjà très chargées) |
| Pré-ouverture | Yahoo (pré-marché) | message 15 h 15 Paris : plus gros gaps avec volume |

## 3 bis. Couche « Bloomberg »

- **Régime** : S&P 500 au-dessus / en dessous de sa moyenne 50 jours, VIX < 20 / ≥ 30.
- **Briefing** : indices (S&P 500, Nasdaq, Dow, DAX, CAC 40, Nikkei), VIX, DXY, taux 10 ans US, or, WTI, BTC,
  rotation des 11 secteurs SPDR sur 5 jours, agenda économique du jour.
- **Contexte** : actualités (Yahoo), résultats d'entreprise et calendrier économique (OpenBB, fournisseur Nasdaq).

## 4. Score et validation

| Composant | Points |
|---|---|
| Sortie de la zone de bruit (distance en écarts) | 20 |
| Tendance 1 h (EMA 20 / 50) | 20 |
| Force relative vs S&P (actions) ou BTC (crypto) | 15 |
| Volume relatif des 30 dernières minutes | 15 |
| Régime de marché | 10 |
| Backtest de la règle sur l'actif (profit factor) | 20 |

Envoi si score ≥ `MIN_SCORE` (60) et backtest de l'actif gagnant en moyenne (≥ 12 trades).

## Gestion du risque et suivi

- **Garde-fou de corrélation** : à chaque passage de 30 min, au plus `MAX_SAME` (3) signaux dans le même sens
  par classe d'actifs ; on garde les meilleurs scores. Dix cryptos qui cassent ensemble, c'est un seul pari sur le marché.
- **Taille de position** : `quantité = capital × RISK_PCT % ÷ (entrée − stop)`, affichée dans chaque signal si `CAPITAL` est renseigné.
- **Suivi automatique (track record)** : chaque signal envoyé est suivi bougie par bougie (stop suiveur, TP, fin de séance) ;
  le résultat en R est publié sur Telegram, cumulé dans `bot_pro_state.json` (`track`), repris dans le résumé du soir
  et dans un bilan hebdomadaire le dimanche.
Monte Carlo : 10 000 séquences de 20 trades tirées dans les résultats du backtest.
