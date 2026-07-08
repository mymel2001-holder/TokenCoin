"""
TokenCoin (TKC) - Global Configuration
========================================
Central configuration for the TokenCoin cryptocurrency system.
All tunable parameters are defined here.
"""

from dataclasses import dataclass, field
from typing import List, Optional
import os


# ---------------------------------------------------------------------------
# Monetary Policy
# ---------------------------------------------------------------------------
@dataclass
class MonetaryPolicy:
    """TokenCoin monetary policy parameters.

    Implements a smooth emission curve (Monero-style) rather than
    Bitcoin's discrete halving events. This ensures fair, unbiased
    printing of new coins — rewards decrease smoothly and asymptotically,
    never reaching zero, so mining is always rewarded.

    Key design:
      - max_supply: Hard cap (10 Trillion TKC)
      - base_supply: Pre-mined supply before mining begins (6.4B TKC)
      - initial_block_reward: Starting reward per block (12 TKC)
      - tail_emission: Minimum reward floor (0.1 TKC) — ensures mining
        is always viable, even as supply approaches the cap
      - block_time_seconds: Target block time (5 minutes)
    """
    # Maximum total supply: 10 Trillion TKC (10_000_000_000_000)
    max_supply: int = 10_000_000_000_000

    # Initial pre-mined / base amount before mining begins
    base_supply: int = 6_400_000_000  # 6.4B TKC

    # Starting block reward (TKC)
    initial_block_reward: int = 12  # 12 TKC per block

    # Tail emission floor (TKC) — minimum reward per block forever
    # This ensures "fair, unbiased printing" never stops
    tail_emission: int = 1  # 1 TKC floor (atomic: 1_000_000_000)

    # Block target time (seconds) - 5 minutes
    block_time_seconds: int = 300


# ---------------------------------------------------------------------------
# Network Layer
# ---------------------------------------------------------------------------
@dataclass
class NetworkConfig:
    """Tor-based addressing and P2P network configuration."""
    # Address format: 56-character Base32 string (Tor v3 style)
    address_length: int = 56
    address_alphabet: str = "abcdefghijklmnopqrstuvwxyz234567"

    # Default P2P port
    p2p_port: int = 18720

    # DHT (Distributed Hash Table) configuration
    dht_bootstrap_nodes: List[str] = field(default_factory=lambda: [
        "b32example1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "b32example2xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    ])
    dht_kademlia_k: int = 20  # Kademlia replication parameter

    # Tor control port (for local Tor daemon interaction)
    tor_control_port: int = 9051
    tor_socks_port: int = 9050

    # Maximum peers
    max_peers: int = 64


# ---------------------------------------------------------------------------
# Ledger & Privacy Layer
# ---------------------------------------------------------------------------
@dataclass
class LedgerConfig:
    """Blockchain and privacy configuration."""
    # Ring signature size (number of decoys per ring)
    ring_size: int = 11  # 1 real + 10 decoys

    # Horizon privacy: number of blocks considered "recent" for decoy selection
    horizon_blocks: int = 10_000

    # Pedersen commitment curve (Ed25519)
    curve_name: str = "Ed25519"

    # Block size limits
    max_block_size_bytes: int = 2_000_000  # 2 MB
    max_tx_per_block: int = 10_000

    # Transaction version
    tx_version: int = 1


# ---------------------------------------------------------------------------
# Consensus Layer (PoUW / Ollama)
# ---------------------------------------------------------------------------
@dataclass
class ConsensusConfig:
    """Proof-of-Useful-Work via distributed Ollama instances configuration."""
    # Minimum memory required (GB) - RAM for CPU, VRAM for GPU
    min_memory_gb: int = 4

    # Supported Ollama models
    supported_models: List[str] = field(default_factory=lambda: [
        "phi3-mini",
        "tinyllama",
        "llama3.2-3b",
        "mistral-7b",
        "nomic-embed-text",
        "all-minilm",
    ])

    # Verification challenge interval (blocks)
    challenge_interval: int = 10

    # Slashing penalty (TKC)
    slash_penalty: int = 100

    # ZKIP (Zero-Knowledge Inference Proof) parameters
    zkip_challenge_size: int = 32  # bytes
    zkip_tensor_commitment_bits: int = 256


# ---------------------------------------------------------------------------
# Ollama Distributed Mining
# ---------------------------------------------------------------------------
@dataclass
class OllamaConfig:
    """Distributed Ollama mining configuration.

    Mining is now fully P2P-based. The static remote_instances list has been
    replaced by the MiningP2PSubnet, which discovers miners via the Kademlia
    DHT and gossip protocol — no central server or static node list required.
    """
    # Default Ollama API port
    default_port: int = 11434

    # Maximum tokens to generate per inference job
    max_tokens_per_job: int = 128

    # Inference temperature (deterministic for verifiable mining)
    inference_temperature: float = 0.0

    # Job timeout in seconds
    job_timeout_seconds: int = 120

    # Health check interval (seconds)
    health_check_interval: int = 30

    # Auto-pull models if not available locally
    auto_pull_models: bool = True

    # P2P Mining Subnet Configuration
    # ================================
    # These replace the old static remote_instances list.

    # Enable P2P-based miner discovery (DHT + gossip, no central server)
    # When disabled, falls back to local-only mining
    p2p_mining_enabled: bool = True

    # How often (seconds) to re-announce our capabilities to the subnet
    p2p_announce_interval: int = 120

    # How often (seconds) to clean up dead peers from the registry
    p2p_cleanup_interval: int = 300

    # Maximum age (seconds) for a peer to be considered alive
    p2p_peer_timeout: int = 600

    # Maximum age (seconds) before removing a peer from the registry entirely
    p2p_peer_eviction_timeout: int = 1800

    # Minimum reputation score (0.0-1.0) to accept jobs from a miner
    p2p_min_peer_score: float = 0.0

    # Maximum concurrent jobs per instance
    max_concurrent_jobs: int = 1

    # Preferred hardware backend (auto, cpu, cuda, rocm, metal, vulkan)
    preferred_backend: str = "auto"

    # Number of CPU threads to use (0 = all available)
    cpu_threads: int = 0

    # Model to use for mining (auto-selects best compatible if empty)
    mining_model: str = "phi3-mini"


# ---------------------------------------------------------------------------
# Wallet
# ---------------------------------------------------------------------------
@dataclass
class WalletConfig:
    """Wallet configuration."""
    # Key derivation
    kdf_iterations: int = 100_000
    kdf_algorithm: str = "argon2id"

    # Address prefix for human-readable format
    address_prefix: str = "tkc1"

    # Default wallet file name
    default_wallet_file: str = "wallet.tkc"


# ---------------------------------------------------------------------------
# Master Configuration
# ---------------------------------------------------------------------------
@dataclass
class TokenCoinConfig:
    """Master configuration aggregating all sub-configurations."""
    monetary: MonetaryPolicy = field(default_factory=MonetaryPolicy)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    ledger: LedgerConfig = field(default_factory=LedgerConfig)
    consensus: ConsensusConfig = field(default_factory=ConsensusConfig)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    wallet: WalletConfig = field(default_factory=WalletConfig)

    # Network name (mainnet, testnet, devnet)
    network_name: str = "devnet"

    # Data directory
    data_dir: str = field(default_factory=lambda: os.path.expanduser("~/.tokencoin"))

    # Logging level
    log_level: str = "INFO"


# Global singleton configuration
CONFIG = TokenCoinConfig()
