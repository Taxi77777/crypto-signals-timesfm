# Stratégies, sources et écarts assumés

Ce document dit, règle par règle, ce que fait l'étude et ce que fait le bot. Quand le bot s'écarte de
l'étude, c'est écrit.

## 1. Stocks in Play — cassure d'ouverture 5 min (actions US)

**Source** : C. Zarattini, A. Barbon, A. Aziz (2024), *A Profitable Day Trading Strategy For The U.S. Equity
Market*, SSRN 4729284. Résultat publié : Sharpe 2,81, plus de 1 600 % au total, 2016-2023.

| Règle de l'étude | Dans le bot |
|---|---|
| Univers : tout le marché US (plus de 7 000 actions), prix > 5 $, volume moyen 14 j ≥ 1 M, ATR 14 j > 0,50 $ | **écart** : liste fixe de 177 actions liquides (`SIP_STOCKS`), mêmes filtres |
| Volume relatif de la 1re bougie 5 min ≥ 100 % de la moyenne 14 j, top 20 | identique (`SIP_RVOL`, `SIP_TOP`) |
| Portefeuille des 20, à poids égal | **écart** : les 10 meilleurs scores (`SIP_SEND`), après le score et le backtest |
| Sens = couleur de la 1re bougie ; doji = rien | identique |
| Ordre stop au plus haut (achat) / plus bas (vente) de la 1re bougie | identique ; ordres suivis jusqu'à 15 h 30 NY |
| Stop à 10 % de l'ATR 14 j | identique (`SIP_ATR_STOP`) |
| Pas d'objectif, sortie en fin de séance | identique (15 h 55 NY, 12 h 55 les jours de clôture à 13 h) ; l'objectif 3R affiché est indicatif et n'est **pas** utilisé par le backtest ni par le bilan |
| Levier max 4× | taille plafonnée à `MAX_LEV` (4) × le capital |

**Score Stocks in Play (sur 100)** : volume relatif (30 si ≥ 3×, 22 si ≥ 2×, sinon 12) · tendance 1 h (20) ·
régime de marché (10) · backtest (20) · rang du volume (10 dans le top 5, sinon 5) · pas de résultats
d'entreprise aujourd'hui / demain (10). Envoi si score ≥ 60 et backtest gagnant (≥ 12 trades).

**Backtest du bot** (60 jours de bougies 5 min, frais inclus) : si la bougie d'entrée touche aussi le stop,
on compte −1 R (l'ordre des prix dans la bougie est inconnu : choix prudent) ; un gap au-delà du stop
s'exécute à l'ouverture de la bougie (perte bornée à −1,5 R).

**En direct** : un ordre déclenché avant l'envoi de la liste est signalé « entrée manquée » et n'est pas compté ;
un ordre déclenché puis stoppé est compté −1 R au bilan, même si le bot l'a vu après coup.

## 2. Momentum intraday — zone de bruit + VWAP (ETF, actions, forex, or, crypto)

**Sources** : C. Zarattini, A. Aziz, A. Barbon (2024), *Beat the Market: An Effective Intraday Momentum
Strategy for S&P500 ETF (SPY)* ; L. Gao, Y. Han, S. Z. Li, G. Zhou (2018), *Market intraday momentum*,
Journal of Financial Economics (SPY et 10 autres ETF très échangés).

| Règle de l'étude | Dans le bot |
|---|---|
| Zone de bruit : ouverture × (1 ± moyenne sur 14 jours de \|cours / ouverture − 1\| à la même minute), bornes corrigées des gaps | identique (≥ 10 jours valides exigés) |
| Décisions à HH:00 et HH:30 | identique |
| Achat au-dessus de la borne haute, vente sous la borne basse | identique, **plus** un filtre VWAP du même côté (sans effet pratique : sinon le stop serait déjà franchi) |
| Stop suiveur = max(borne haute actuelle, VWAP) (achat) / min(borne basse, VWAP) (vente) | identique, recalculé à chaque HH:00 / HH:30 ; **écart** : il ne recule jamais, et le stop est surveillé sur chaque bougie 5 min (plus protecteur) |
| Sortie en fin de séance | identique (15 h 55 NY ; 12 h 55 en demi-séance ; 20 h 55 UTC forex / crypto) |
| Plusieurs entrées possibles par jour | **écart** : une seule par sens et par jour |
| Taille ciblée sur la volatilité (levier ≤ 4) | taille par le risque (`RISK_PCT`), plafond `MAX_LEV`, risque ÷ 2 si VIX ≥ 25 (actions) |
| Pas d'objectif | l'objectif 2R affiché est indicatif ; backtest et bilan n'en tiennent pas compte |

Si, à un point de contrôle, le nouveau stop suiveur est déjà au-delà du prix, la sortie se fait à la clôture
de la bougie (jamais à un prix que le marché n'a pas traité). Un stop trop serré pour que les frais restent
sous 0,25 R est ignoré.

**Crypto = perpétuels Kraken** : les bougies 5 min viennent de l'API publique Kraken Futures (contrats PF_…USD), pour que l'entrée, le stop et le TP correspondent au prix affiché chez Kraken ; seules les cryptos disponibles en perpétuel sont analysées (Yahoo en secours).

**Adaptation forex / or / crypto** : jour à partir de 0 h UTC ; décisions 7 h – 19 h 30 UTC (forex, lundi-vendredi)
et 6 h 30 – 20 h UTC (crypto, 7 j/7). Le forex n'a pas de volume sur Yahoo : la « VWAP » y est une moyenne
simple des prix. L'or et l'argent utilisent les contrats à terme COMEX (GC=F, SI=F), dont le prix peut
différer de quelques dollars de ton CFD.

**Score momentum (sur 100)** : sortie de la zone (20) · tendance 1 h EMA 20 / 50 (20) · force relative vs
S&P ou BTC (15) · volume relatif 30 min (15, 8 fixes en forex) · régime (10) · backtest (20).

## 3. Dernière demi-heure (ETF indiciels US)

**Sources** : G. Baltussen, Z. Da, S. Lammers, M. Martens (2021), *Hedging demand and market intraday
momentum*, Journal of Financial Economics : plus de 60 contrats à terme, 1974-2020, Sharpe 0,87 à 1,73 par
classe d'actifs ; effet plus fort quand les dealers d'options sont « short gamma » ; il s'inverse dans les jours
suivants. Gao et al. (2018) utilisent, eux, la première demi-heure pour prédire la dernière.

- **Règle** : à 15 h 30 New York (12 h 30 en demi-séance), rendement depuis la clôture de la veille. Si
  |rendement| ≥ `LH_MIN` (0,4 %), position dans ce sens, sortie à la clôture, pas d'objectif.
- **Stop** (choix du bot, l'étude n'en publie pas) : 2 × le déplacement moyen de la dernière demi-heure sur
  les 20 séances précédentes ; le backtest n'utilise que les séances d'avant chaque trade.
- **Un seul signal par sens** (`LH_MAX`) : SPY, QQQ, IWM et DIA sont corrélés à environ 0,9.
- **Score (sur 100)** : ampleur du mouvement (30 / 18) · VIX ≥ 20 (20 / 8, approximation grossière du
  « short gamma ») · sens du régime (15) · backtest (35).

## 4. Couche desks institutionnels

| Élément | Source | Utilisation |
|---|---|---|
| Ciblage de volatilité | Harvey, Hoyle, Korgaonkar, Rattray, Sargaison & Van Hemert (2018), *Journal of Portfolio Management* (Man Group) : Sharpe actions US 0,40 → 0,48-0,51 | approximation simple : risque ÷ 2 si VIX ≥ `VOL_CUT` (actions) |
| Dérive pré-FOMC | Lucca & Moench (2015), *Journal of Finance* / Fed de New York : ~49 pb sur le S&P dans les 24 h avant l'annonce (1994-2011) | alerte la veille et le jour J (briefing, signaux actions, dernière demi-heure) ; calendrier 2027 provisoire |
| Courbe 10 ans − 3 mois | suivie par la Fed de New York comme indicateur de récession | briefing |
| Fear & Greed | CNN (actions, point d'accès non officiel), alternative.me (crypto) | briefing |
| Funding / open interest | API publique Kraken Futures | briefing + chaque signal crypto |
| Pré-ouverture | Yahoo (pré-marché) | 9 h 15 NY : plus gros gaps (≥ 2 %) avec volume |
| Positions des grands fonds | Rapport COT de la CFTC (API publique, hebdomadaire) : position nette des spéculateurs non commerciaux en % de l'open interest | briefing + chaque signal forex / or : +5 points dans le sens des fonds, −10 contre (écart > 5 points) |
| Achats des dirigeants | Formulaires 4 de la SEC via OpenInsider (gratuit) | liste Stocks in Play : achats des 30 derniers jours, +5 points pour un achat |
| Calendrier économique | ForexFactory (flux public de la semaine) | annonces du jour dans le briefing ; **aucun signal 30 min avant / après une annonce à fort impact** sur une devise concernée (USD pour les actions, l'or et la crypto) ; alerte dans le signal si une annonce forte arrive dans les 3 h |

## 5. Couche « terminal Bloomberg »

- **Régime** : S&P 500 au-dessus / en dessous de sa moyenne 50 jours ; VIX < 20 calme, ≥ 30 panique.
- **Briefing** : S&P 500, Nasdaq, Dow, DAX, CAC 40, Nikkei, VIX, DXY, taux 10 ans, or, WTI, BTC, rotation
  des 11 secteurs SPDR sur 5 jours, agenda économique.
- **Contexte** : actualités (Yahoo), résultats d'entreprise et agenda économique (OpenBB, fournisseur Nasdaq).

## 6. Validation, frais et bilan

- **Frais** : aller-retour estimé de 3 pb (actions), 1,5 pb (forex), 10 pb (crypto), retirés de chaque trade
  du backtest et du bilan (`COST_BPS_*`).
- **Intervalle à 90 % du R moyen** (bootstrap) : s'il contient 0, le message écrit « avantage NON démontré ».
  Avec 12 à 30 trades d'un seul actif, c'est le cas le plus fréquent : c'est honnête.
- **Rejeu (« 10 000 rejeux »)** : 20 trades tirés au hasard dans le backtest, 10 000 fois. Comme on n'envoie
  que des actifs au backtest positif, ce chiffre est mécaniquement flatteur : c'est une information, pas une
  probabilité.
- **Garde-fou de corrélation** : au plus `MAX_SAME` (3) positions ouvertes dans le même sens par classe
  d'actifs (positions déjà ouvertes comprises, hors liste Stocks in Play), meilleurs scores d'abord.
- **Bilan (track record)** : chaque signal est suivi bougie par bougie avec exactement les règles du backtest
  (stop, stop suiveur, fin de séance, pas d'objectif). Le résultat en R, frais inclus, est publié sur Telegram et
  gardé dans `bot_pro_state.json` (`track`), repris dans le résumé du soir et le bilan du dimanche.
- **Confidentialité** : le fichier d'état public ne contient ni capital, ni montants, ni tailles ; la ligne de
  taille est masquée dans les journaux GitHub publics, et `CAPITAL` est un secret.

## 7. Messages

- Par défaut (`SIMPLE=1`), messages en français simple pour débutant : catégorie en 1re ligne (🪙 CRYPTO, 💱 FOREX,
  🥇 FOREX · MÉTAUX, 📈 ACTION US / ETF US), « ACHETER / VENDRE », prix d'entrée et heure limite, sortie de sécurité,
  prendre le gain, heure de fermeture, fiabilité en étoiles, résultat de la règle sur 60 jours.
- Pendant le trade, un message « remonte ta sortie de sécurité » à chaque déplacement du stop suiveur (≥ 0,25 × le risque).
- Résultat de chaque signal en « fois le risque » et en € pour 10 € risqués ; bilan chaque soir, et sur 7 jours le dimanche.
- `SIMPLE=0` : version détaillée (score par critère, R, intervalle de confiance, rejeux).

## 8. Filtres d'envoi par stratégie

- **Stocks in Play** et **dernière demi-heure** : envoyés comme dans les études (`SIP_GATE=0`, `LH_GATE=0`) ; le
  backtest de l'actif sur 60 jours est affiché pour information, il ne bloque plus (60 jours d'une seule action est un
  échantillon trop court : l'avantage publié est celui du portefeuille sur des années).
- **Momentum sur les ETF indiciels** (SPY, QQQ, IWM, DIA, SMH) : envoyé comme dans l'étude « Beat the Market ».
- **Momentum sur actions individuelles, forex, or, crypto** (adaptations non testées par les études) : score ≥ 60 et
  backtest de l'actif gagnant exigés.

## 9. Réglages crypto (perpétuels Kraken)

- **Liquidité** : seulement les perpétuels Kraken avec au moins `CR_MIN_OI` (5 M$) de positions ouvertes, BTC et ETH
  toujours inclus, au plus `CR_MAX_COINS` (100).
- **Stop plus large** : `CR_STOP_MULT` (2) × la distance de l'étude ; le stop suiveur garde le même écart. Le backtest, le
  suivi et la taille de position utilisent ce même stop (risque constant en €, quantité plus petite).
- **Plus de signaux** : note mini `CR_MIN_SCORE` (50), backtest affiché pour information (`CR_NEED_BT=0`), jusqu'à
  `CR_MAX_SAME` (5) positions dans le même sens, signaux de 7 h 30 à 23 h (Paris), fermeture au plus tard 0 h 50.
