# Déploiement du bot sur VPS

## Pourquoi quitter GitHub Actions

Mesuré sur les 39 derniers runs planifiés de ton dépôt, via l'API GitHub :

| Écart entre deux exécutions | Valeur |
|---|---|
| Demandé dans le cron | 15 min |
| Minimum observé | **50 min** |
| Médian observé | **94 min** |
| Maximum observé | **201 min** (3h20) |

Pas une seule exécution en dessous de 50 minutes. GitHub étrangle les crons
fréquents sur les dépôts publics en tier gratuit. Pour une stratégie de scalp en
15m, ça signifie que le bot rate la majorité des configurations, et surtout que
`check_and_trail()` — le seul trailing stop existant — n'est appelé qu'une fois
toutes les 94 minutes en médiane, sur des trades qui se résolvent en 5 à 30
minutes. Le trailing n'a donc quasiment jamais l'occasion d'agir.

Sur VPS : **15 minutes, systématiquement**, alignées sur la clôture des bougies.
Soit environ 6 fois plus de cycles d'analyse.

## Ce que change ce déploiement

**Les dépendances sont installées une fois au build.** Sur GitHub Actions, 7 à 9
minutes de chaque run partaient dans `pip install` de torch et des 5 modèles.
Ici, chaque exécution ne fait plus que l'analyse.

**Les réveils sont alignés sur les clôtures de bougies** : `hh:00:20`, `hh:15:20`,
`hh:30:20`, `hh:45:20`. C'est important depuis le correctif du Volume Climax, qui
lit désormais la dernière bougie **clôturée** : se réveiller juste après une
clôture garantit des données complètes.

**Un verrou empêche deux exécutions simultanées.** Le bot ouvre des positions
réelles ; deux instances concurrentes pourraient doubler un trade.

## Matériel nécessaire

Les 5 modèles sont chargés **séquentiellement**, avec `unload_*()` et
`gc.collect()` entre chaque passe (`run_once.py`, passes 1/5 à 5/5). Le pic
mémoire ne dépend donc que du plus gros modèle, pas de leur somme.

| Ressource | Minimum | Pourquoi |
|---|---|---|
| RAM | **4 Go** | runtime torch + TimesFM 200M en pic ≈ 3 Go. 2 Go déclencherait un OOM kill. |
| Disque | **20 Go** | torch ≈ 2,5 Go, poids des modèles ≈ 1,5 Go, image de base ≈ 1 Go. |
| CPU | 2 vCPU | inférence CPU sur 55 paires. |

Une machine à environ 4-5 €/mois avec 4 Go de RAM convient (par exemple Hetzner
CX22 : 2 vCPU, 4 Go, 40 Go). **Éviter les offres à 2 Go** : l'OOM killer
interromprait le bot en pleine exécution, potentiellement après l'ouverture
d'une position mais avant la pose du TP.

## Installation

```bash
# 1. Docker (Debian/Ubuntu)
curl -fsSL https://get.docker.com | sh

# 2. Récupérer le code
git clone https://github.com/Taxi77777/crypto-signals-timesfm.git
cd crypto-signals-timesfm

# 3. Copier les fichiers de déploiement à la racine du dépôt
#    (Dockerfile, docker-compose.yml, run_scheduler.sh, .env.example, .dockerignore)

# 4. Renseigner les secrets
cp .env.example .env
nano .env
chmod 600 .env          # lisible par toi seul

# 5. Build — compter 10 à 20 min la première fois (torch + poids des modèles)
docker compose build

# 6. Démarrage
docker compose up -d
```

## Vérifier que ça tourne

```bash
# Suivre l'ordonnanceur en direct
docker compose logs -f

# Les lignes attendues :
#   [15:30:00] Ordonnanceur démarré — exécution à chaque clôture 15m (+20s)
#   [15:30:00] Prochaine exécution dans 20s (à 15:30:20)
#   [15:30:20] ▶ Lancement de run_once.py
#   [15:31:05] ✅ Terminé en 45s

# Logs applicatifs du bot
tail -f logs/signals.log

# Les 3 lignes à surveiller en priorité :
grep "BILAN FILTRES"   logs/signals.log    # quel filtre bloque, et combien de candidats
grep "Vol .*clôturée"  logs/signals.log    # ratio de volume réel — valide le correctif Volume Climax
grep "Solde MEXC"      logs/signals.log    # confirme que tes USDC sont bien vus
```

## Commandes utiles

```bash
docker compose restart              # redémarrer
docker compose down                 # arrêter (aucune position n'est fermée !)
docker compose up -d --build        # après un git pull
docker stats crypto-signals         # surveiller la RAM réelle
```

## Points d'attention

**`docker compose down` n'arrête que le bot, pas tes positions.** Une position
ouverte reste ouverte sur MEXC, avec son TP posé côté exchange, mais sans plus
aucun trailing. Vérifie MEXC avant d'arrêter le conteneur.

**Sécurise la clé API MEXC.** Permissions Futures uniquement, aucun droit de
retrait, et si MEXC le permet, restreinte à l'IP du VPS.

**Le `.env` ne doit jamais être committé.** Il est déjà dans `.dockerignore`,
ajoute-le aussi à ton `.gitignore`.

**Surveille la RAM au premier run** avec `docker stats`. Si tu vois le conteneur
redémarrer tout seul, c'est l'OOM killer : il faut plus de mémoire.

## Ce que ce déploiement ne corrige pas

Il rend l'exécution fiable et fréquente. Il ne crée pas d'avantage statistique.

D'après `track_record.json`, les 5 modèles totalisent **46,6 % de réussite sur
47 835 prédictions**, alors que le seuil de rentabilité de la configuration
actuelle (x80, TP +1,2 %, pas de stop loss, frais taker aller-retour) se situe à
**47,3 %**. Tourner 6 fois plus souvent avec un edge négatif accélère le
résultat, il ne l'inverse pas.

La ligne `BILAN FILTRES` des logs est l'outil qui permet de mesurer plutôt que
de supposer. C'est par là qu'il faut commencer.
