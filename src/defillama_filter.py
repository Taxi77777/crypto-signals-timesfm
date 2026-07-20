"""
src/defillama_filter.py — Filtre fondamental basé sur la TVL (Total Value Locked) via DefiLlama
Vérifie la croissance/déclin de la TVL pour filtrer les faux signaux.
"""

import urllib.request
import json
import logging

logger = logging.getLogger(__name__)

# Cartographie des Layer 1 vers les slugs de chaînes DefiLlama
L1_CHAIN_MAPPING = {
    "SOL": "solana",
    "ETH": "ethereum",
    "AVAX": "avalanche",
    "SUI": "sui",
    "APT": "aptos",
    "STRK": "starknet",
    "NEAR": "near",
    "ADA": "cardano",
    "DOT": "polkadot",
    "TRX": "tron",
    "ALGO": "algorand",
    "OP": "optimism",
    "ARB": "arbitrum",
    "MATIC": "polygon",
    "POL": "polygon",
}

class DefiLlamaFilter:
    def __init__(self):
        self.protocol_map = {}
        self.chain_cache = {}
        self.initialized = False

    def initialize(self):
        """Charge la liste globale des protocoles DefiLlama."""
        if self.initialized:
            return
        
        try:
            logger.info("📡 Chargement des protocoles DefiLlama pour le filtre fondamental...")
            req = urllib.request.Request(
                "https://api.llama.fi/protocols",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                protocols = json.loads(response.read().decode())
            
            for p in protocols:
                sym = p.get("symbol", "").upper()
                if sym:
                    # Si doublon, on garde celui avec la plus grosse TVL
                    existing = self.protocol_map.get(sym)
                    tvl = p.get("tvl") or 0
                    if not existing or tvl > existing.get("tvl", 0):
                        self.protocol_map[sym] = p
            
            logger.info(f"✅ {len(self.protocol_map)} symboles de protocoles mappés avec succès.")
            self.initialized = True
        except Exception as e:
            logger.error(f"❌ Erreur initialisation DefiLlama : {e}")
            self.initialized = False

    def _fetch_chain_change(self, chain_slug: str) -> tuple[float | None, float | None]:
        """Récupère l'historique TVL d'une chaîne et calcule les variations 1D et 7D."""
        if chain_slug in self.chain_cache:
            return self.chain_cache[chain_slug]
        
        url = f"https://api.llama.fi/charts/{chain_slug}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
            
            if len(data) >= 2:
                last = float(data[-1]["totalLiquidityUSD"])
                
                # 1D Change
                prev_1d = float(data[-2]["totalLiquidityUSD"])
                change_1d = (last - prev_1d) / prev_1d * 100 if prev_1d > 0 else 0.0
                
                # 7D Change
                change_7d = 0.0
                if len(data) >= 8:
                    prev_7d = float(data[-8]["totalLiquidityUSD"])
                    change_7d = (last - prev_7d) / prev_7d * 100 if prev_7d > 0 else 0.0
                
                res = (change_1d, change_7d)
                self.chain_cache[chain_slug] = res
                return res
        except Exception as e:
            logger.error(f"Erreur historique TVL chaine {chain_slug}: {e}")
        
        self.chain_cache[chain_slug] = (None, None)
        return (None, None)

    def check_tvl_guard(self, symbol: str, signal_dir: str) -> tuple[bool, str]:
        """
        Vérifie si la tendance TVL est compatible avec le signal.
        Retourne (is_allowed: bool, reason: str).
        """
        # Nettoyer le symbole (ex: JUP29210-USD -> JUP, BTC-USD -> BTC)
        clean_sym = symbol.split("-")[0]
        # Retirer les chiffres éventuels à la fin (ex: UNI7083 -> UNI, SUI20947 -> SUI)
        clean_sym = ''.join([c for c in clean_sym if not c.isdigit()]).upper()

        if not self.initialized:
            self.initialize()
            if not self.initialized:
                return True, "Filtre TVL non initialisé (problème réseau) - Autorisé par défaut"

        change_1d, change_7d = None, None
        source_type = None

        # 1. Vérifier si c'est un protocole DeFi référencé
        if clean_sym in self.protocol_map:
            p = self.protocol_map[clean_sym]
            change_1d = p.get("change_1d")
            change_7d = p.get("change_7d")
            source_type = "Protocole"

        # 2. Sinon, vérifier si c'est une chaîne de Layer 1 mappée
        elif clean_sym in L1_CHAIN_MAPPING:
            chain_slug = L1_CHAIN_MAPPING[clean_sym]
            change_1d, change_7d = self._fetch_chain_change(chain_slug)
            source_type = "Chaîne L1"

        # Si aucune donnée TVL n'existe pour ce symbole (ex: Meme coin, BTC, LTC...)
        if change_1d is None or change_7d is None:
            return True, "Pas de donnée TVL disponible (Meme/Store-of-value coin) - Autorisé"

        # Conversion en float
        try:
            c1d = float(change_1d)
            c7d = float(change_7d)
        except (TypeError, ValueError):
            return True, "Données TVL corrompues - Autorisé"

        # 3. Application des filtres de sécurité fondamentaux
        if signal_dir == "BUY":
            # Si la TVL s'effondre (fuite des capitaux), on n'achète pas !
            if c1d < -5.0 or c7d < -10.0:
                reason = f"🚫 Bloqué par TVL Guard ({source_type}) | Fuite de capitaux détectée : 1D={c1d:+.2f}%, 7D={c7d:+.2f}%"
                return False, reason
        elif signal_dir == "SELL":
            # Si la TVL explose (adoption massive), on ne short pas !
            if c1d > 7.0 or c7d > 15.0:
                reason = f"🚫 Bloqué par TVL Guard ({source_type}) | Forte croissance TVL détectée : 1D={c1d:+.2f}%, 7D={c7d:+.2f}%"
                return False, reason

        return True, f"TVL stable/favorable ({source_type}) : 1D={c1d:+.2f}%, 7D={c7d:+.2f}%"
