# 📈 Scanner ACTIONS · FOREX · CRYPTO — stratégies de la vidéo

Signaux **Telegram uniquement** : ce scanner n'envoie **aucun ordre**. Il tourne sur GitHub Actions
(`.github/workflows/scanner_actions_forex.yml`), tous les jours de 6 h à 21 h 15 UTC.

Règles reprises du pack open-source [dan-ai-lab/day-trader-fr](https://github.com/dan-ai-lab/day-trader-fr)
(adaptation française de [day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill), licence MIT).

## La stratégie

| Étape | Règle |
|---|---|
| **Cassure de la plage d'ouverture** | Plage = plus haut / plus bas des 15 premières minutes de la séance. Signal = 1re bougie 5 min **terminée** qui clôture au-dessus du plus haut (**achat**) ou sous le plus bas (**vente**). Une seule fois par jour et par sens. |
| **Plan de sortie** | SL au milieu de la plage, TP = 2 × le risque, sortie au plus tard à l'heure limite. Signal refusé si le risque est < 0,05 % du prix (trop serré face au spread). |
| **Time machine** | 10 000 fins de séance reconstruites à partir des bougies 5 min des 60 derniers jours (même heure ± 2 bougies, SL testé avant le TP), puis la même règle rejouée sur chaque séance passée. |
| **Filtre de tendance** (ajout) | Achat seulement si le prix est au-dessus de la VWAP du jour (actions : depuis 9 h 30 ; forex et crypto : depuis minuit UTC, comme MT4) **et** de la moyenne des 20 dernières clôtures quotidiennes ; vente seulement en dessous des deux. Un signal contre la tendance n'est jamais envoyé, et l'historique applique le même filtre. |
| **Verdict** | 🟢 favorable / 🟡 neutre / 🔴 défavorable : le TP est-il touché avant le SL plus souvent que le seuil de rentabilité (33 % à 2R) ? |
| **Watchlist du jour** | Avant l'ouverture : cassure du plus haut 20 jours, drapeau haussier, reprise de la moyenne 20 jours, survente (RSI 14 < 30), rebond de survente. |

Le moteur reproduit l'exemple publié par le pack d'origine (TSLA, 19/10/2026 : 29,7 % / 25,3 % / 45,0 %
contre 29,4 % / 25,5 % / 45,1 % ; historique identique : 33 signaux, 13 TP, 15 SL, 5 sorties à l'heure).

## Marchés (heures de Paris)

| Marché | Séance | Premiers signaux possibles → dernière entrée | Watchlist |
|---|---|---|---|
| 🇺🇸 Actions US (~190 titres et ETF les plus liquides, `SCAN_STOCKS`) | 15 h 30 – 22 h, calendrier NYSE | 15 h 50 → 21 h 30 | 15 h 00 |
| 💱 Forex : 28 majeures et croisées, 21 exotiques, or et argent (`SCAN_FX`), ouverture de Londres | 9 h – 17 h, en semaine | 9 h 20 → 12 h 00 | 8 h 30 |
| 💱 Forex, ouverture de New York | 14 h – 22 h, en semaine | 14 h 20 → 17 h 00 | — |
| 🪙 Crypto : liste de base + les plus grosses du classement Yahoo, sans stablecoins (~95, `SCAN_CRYPTO`), ouverture de Londres | **7 j/7** | 9 h 20 → 12 h 00 | 8 h 30 |
| 🪙 Crypto, ouverture de Wall Street | **7 j/7** | 15 h 50 → 18 h 00 | — |

Pendant les semaines où l'Europe et les États-Unis ne changent pas d'heure en même temps (fin octobre / début
novembre, mars), les heures « New York » arrivent une heure plus tôt à Paris (actions 14 h 30 – 21 h).

## Ce qui est envoyé

Un signal part seulement si :
- la cassure vient de la **dernière bougie clôturée** (5 min), le prix n'a touché ni le SL ni le TP et n'est pas déjà parti (plus de 0,25 R dans le sens du trade, ou 0,5 R vers le SL = trop tard) ; le message indique jusqu'à quelle heure il reste valable ;
- le verdict est **🟢 FAVORABLE** : simulation au moins 5 points au-dessus du seuil de rentabilité, résultat
  moyen simulé positif, moins de 40 % de trajectoires sans issue, **et** historique réel de la même règle sur
  ce symbole d'au moins 15 signaux, gagnant en moyenne, avec une part de TP nettement au-dessus du seuil
  (borne basse de Wilson à 90 %). Sur des prix purement aléatoires, ce filtre laisse passer environ 3 % des
  cassures, contre 11 à 14 % pour la simulation seule.

Chaque message donne l'entrée, le SL et le TP (copiables), l'heure limite de sortie, le retard éventuel et le
prix actuel, les probabilités, l'historique, le contexte quotidien et les avertissements du pack.

## Réglages (variables GitHub facultatives)

| Variable | Rôle | Défaut |
|---|---|---|
| `SCAN_STOCKS`, `SCAN_FX`, `SCAN_CRYPTO` | listes de symboles séparés par des virgules | listes intégrées |
| `SCAN_ONLY_FAVORABLE` | `0` = recevoir aussi les signaux neutres / défavorables | `1` |
| `SCAN_SHORTS` | `0` = achats seulement (comme le pack d'origine) | `1` |
| `SCAN_RR` | TP en multiple du risque | `2` |
| `SCAN_TREND` | `0` = désactiver le filtre de tendance | `1` |

Secrets : `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Le job se relance lui-même à la fin de sa durée maximale,
avec un filet de sécurité toutes les 15 minutes.

Données : API graphiques Yahoo Finance (gratuite, non officielle, retard possible). Simulation statistique,
pas une prévision ni un conseil financier.
