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
| **Verdict** | 🟢 favorable / 🟡 neutre / 🔴 défavorable : le TP est-il touché avant le SL plus souvent que le seuil de rentabilité (33 % à 2R) ? |
| **Watchlist du jour** | Avant l'ouverture : cassure du plus haut 20 jours, drapeau haussier, reprise de la moyenne 20 jours, survente (RSI 14 < 30), rebond de survente. |

Le moteur reproduit l'exemple publié par le pack d'origine (TSLA, 19/10/2026 : 29,7 % / 25,3 % / 45,0 %
contre 29,4 % / 25,5 % / 45,1 % ; historique identique : 33 signaux, 13 TP, 15 SL, 5 sorties à l'heure).

## Marchés (heures de Paris)

| Marché | Séance | Signaux possibles | Watchlist |
|---|---|---|---|
| 🇺🇸 Actions US (58 titres, `SCAN_STOCKS`) | 15 h 30 – 22 h, calendrier NYSE | 15 h 45 – 21 h 30 | 15 h 00 |
| 💱 Forex + or (`SCAN_FX`), ouverture de Londres | 9 h – 17 h, en semaine | 9 h 15 – 12 h 00 | 8 h 30 |
| 💱 Forex + or, ouverture de New York | 14 h – 22 h, en semaine | 14 h 15 – 17 h 00 | — |
| 🪙 Crypto (`SCAN_CRYPTO`), ouverture de Londres | 7 j/7 | 9 h 15 – 12 h 00 | 8 h 30 |
| 🪙 Crypto, ouverture de Wall Street | 7 j/7 | 15 h 45 – 18 h 00 | — |

Un signal n'est envoyé que s'il date des **2 dernières bougies** et que le prix n'a touché ni le SL ni le TP depuis.

## Réglages (variables GitHub facultatives)

`SCAN_STOCKS`, `SCAN_FX`, `SCAN_CRYPTO` (listes séparées par des virgules), `SCAN_SHORTS` (1 = ventes aussi),
`SCAN_RR` (2 par défaut). Secrets : `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

Données : API graphiques Yahoo Finance (gratuite, non officielle, retard possible). Simulation statistique,
pas une prévision ni un conseil financier.
