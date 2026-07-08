"""
TokenCoin (TKC) - Privacy-First AI Cryptocurrency
==================================================
A next-generation cryptocurrency that fuses decentralized AI inference
with private financial transactions using Proof-of-Useful-Work (PoUW).

Key Features:
  - Tor-based 56-char Base32 addresses for anonymous routing
  - RingCT & Stealth Addresses for transaction privacy
  - Proof-of-Useful-Work via NVIDIA NIM inference
  - Single-hop graph visibility (horizon privacy)
  - Dynamic monetary policy with fair distribution
"""

__version__ = "0.1.0"
__author__ = "Sammy Lord"
__license__ = "MIT"

from tokencoin.config import CONFIG, TokenCoinConfig
from tokencoin.core.crypto import KeyPair, PublicKey, PrivateKey
from tokencoin.ledger import Blockchain, Transaction, Block
from tokencoin.wallet import Wallet
from tokencoin.mining import Miner
from tokencoin.consensus import ConsensusEngine
