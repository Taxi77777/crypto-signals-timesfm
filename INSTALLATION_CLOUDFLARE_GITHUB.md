# Régler la fréquence des signaux — Cloudflare + GitHub

Objectif : passer d'un scan toutes les 1 à 3 heures à un scan toutes les 5 minutes, sans toucher aux filtres de qualité.

---

## Le diagnostic en une phrase

Ton bot ne manque pas de signaux parce que sa stratégie est trop sévère. Il en manque parce qu'**il ne regarde presque jamais le marché** : GitHub étrangle les crons `schedule`, et deux exchanges sur six refusent l'IP des runners.

| Cause | Effet mesuré | Correctif |
|---|---|---|
| `schedule: '*/15'` étranglé par GitHub | runs espacés de 1 à 3 h | Cron Cloudflare → `workflow_dispatch` |
| Binance et Kraken bloquent les runners | « sur 4 exchanges » au lieu de 6 | relais via Worker Cloudflare |
| `bot.log` (1,4 Mo) commité à chaque run | dépôt qui gonfle, `git pull` de plus en plus lent | retiré du commit, gardé en artifact |

Le troisième point est le plus insidieux : à raison d'un run toutes les 5 minutes, tu committerais environ **400 Mo par jour**. Sans ce correctif, augmenter la cadence rendrait le bot progressivement plus lent, puis cassé.

---

## Pourquoi `workflow_dispatch` échappe à l'étranglement

GitHub traite les événements `schedule` en « meilleur effort » : ils sont mis en file d'attente derrière les runs payants et **supprimés** aux heures de pointe. C'est documenté, ce n'est pas un bug.

`workflow_dispatch` est un appel API explicite. Il est traité immédiatement. Ton workflow l'active déjà — il suffisait de trouver qui appuie sur le bouton toutes les 5 minutes. C'est le rôle du Worker.

Bonus : ton dépôt est **public**, donc les minutes GitHub Actions sont illimitées. 288 runs par jour ne te coûtent rien.

---

## Installation — 15 minutes

### 1. Le jeton GitHub

GitHub → `Settings` → `Developer settings` → `Personal access tokens` → **Fine-grained tokens** → `Generate new token`

- Repository access : **Only select repositories** → `crypto-signals-timesfm`
- Permissions → Repository permissions → **Actions : Read and write**
- Expiration : 1 an

Copie le jeton, tu ne le reverras plus.

### 2. Le Worker Cloudflare

```bash
mkdir ihp-scheduler && cd ihp-scheduler
# poser worker.js et wrangler.toml dans ce dossier

npm install -g wrangler
npx wrangler login

npx wrangler secret put GH_TOKEN
# coller le jeton GitHub

npx wrangler secret put PROXY_TOKEN
# inventer une chaîne aléatoire, par exemple : openssl rand -hex 24
# la garder de côté, elle servira à l'étape 4

npx wrangler deploy
```

Tu obtiens une URL du type `https://ihp-scheduler.<ton-compte>.workers.dev`.

### 3. Vérifier tout de suite

```bash
curl -H "X-Proxy-Token: <ton PROXY_TOKEN>" https://ihp-scheduler.<toi>.workers.dev/run
```

Réponse attendue : `{"ok": true, "status": 204}`. Va voir l'onglet Actions de ton dépôt : un run doit être en train de démarrer.

### 4. Les secrets côté GitHub

Dépôt → `Settings` → `Secrets and variables` → `Actions` → `New repository secret`

| Nom | Valeur |
|---|---|
| `EXCHANGE_PROXY_URL` | `https://ihp-scheduler.<toi>.workers.dev` |
| `EXCHANGE_PROXY_TOKEN` | le `PROXY_TOKEN` de l'étape 2 |

### 5. Les fichiers dans le dépôt

- `proxy_patch.py` → à la racine
- `telegram_messages.py` → à la racine
- `crypto_signals.yml` → remplace `.github/workflows/crypto_signals.yml`

Puis une seule ligne à ajouter **tout en haut** de `bot_once.py`, avant les autres imports :

```python
import proxy_patch  # noqa: F401  — relais Cloudflare pour les carnets d'ordres
```

Et pour les messages Telegram, remplace tes appels d'envoi par :

```python
from telegram_messages import msg_signal, msg_cloture, msg_bilan
```

### 6. Nettoyer le log du dépôt

```bash
git rm --cached bot.log
echo "bot.log" >> .gitignore
git commit -m "chore: bot.log en artifact uniquement"
git push
```

---

## Ce que tu dois observer après

Dans l'heure :

- Onglet Actions : un run toutes les 5 minutes, déclenché par `workflow_dispatch`
- Dans `bot.log` : `[PROXY] Relais actif via https://...`
- Dans tes alertes Telegram : **« sur 6 exchanges »** au lieu de 4 ou 5

Ce dernier point est le plus important pour le nombre de signaux. Avec 6 exchanges au lieu de 4, le consensus est calculé sur une base complète, et le seuil de 80 % devient franchissable sans rien assouplir.

Sur la journée : tu passes d'environ 8 à 24 scans à **288**. Si le taux de signaux par scan reste identique, tu obtiens de l'ordre de **12 à 30 fois plus d'opportunités** — avec exactement les mêmes critères de qualité.

---

## Sécurité du relais

Le Worker n'est pas un proxy ouvert :

- Jeton obligatoire dans l'en-tête `X-Proxy-Token`
- Liste blanche de six domaines, HTTPS uniquement
- **Aucun domaine MEXC** : les requêtes signées partent en direct depuis le runner et ne transitent jamais par Cloudflare
- Si le Worker tombe, `proxy_patch.py` bascule automatiquement en appel direct — le bot ne s'arrête pas

---

## Si tu veux aller plus loin

Une fois la cadence en place, l'étape suivante n'est plus l'infrastructure mais la mesure : `PAPER_MAX_CONCURRENT = 8` et 288 scans par jour vont te donner les 30 à 100 trades nécessaires pour que `msg_bilan` dise quelque chose de solide. C'est à ce moment-là, et pas avant, qu'il sera pertinent de discuter des seuils.
