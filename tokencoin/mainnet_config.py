"""
TokenCoin Mainnet Deployment Configuration
===========================================
Production configuration for mainnet deployment.
Overrides devnet defaults with secure mainnet values.
"""

from tokencoin.config import (
    TokenCoinConfig, MonetaryPolicy, NetworkConfig,
    LedgerConfig, ConsensusConfig, WalletConfig,
)


def get_mainnet_config() -> TokenCoinConfig:
    """Get the mainnet configuration."""
    return TokenCoinConfig(
        network_name="mainnet",
        log_level="WARNING",
        monetary=MonetaryPolicy(
            max_supply=10_000_000_000_000,  # 10 Trillion TKC
            base_supply=6_400_000_000,       # 6.4B TKC
            initial_block_reward=12,         # 12 TKC per block
            tail_emission=1,                 # 1 TKC floor (tail emission)
            block_time_seconds=300,          # 5 minutes
        ),
        network=NetworkConfig(
            address_length=56,
            p2p_port=18720,
            dht_kademlia_k=20,
            tor_control_port=9051,
            tor_socks_port=9050,
            max_peers=128,  # More peers for mainnet
        ),
        ledger=LedgerConfig(
            ring_size=11,           # 1 real + 10 decoys
            horizon_blocks=10_000,
            max_block_size_bytes=2_000_000,  # 2 MB
            max_tx_per_block=10_000,
        ),
        consensus=ConsensusConfig(
            min_vram_gb=8,
            challenge_interval=10,
            slash_penalty=100,
        ),
        wallet=WalletConfig(
            kdf_iterations=1_000_000,  # Higher for mainnet
            kdf_algorithm="argon2id",
            address_prefix="tkc1",
        ),
    )


# Testnet configuration (for testing before mainnet)
def get_testnet_config() -> TokenCoinConfig:
    """Get the testnet configuration."""
    config = get_mainnet_config()
    config.network_name = "testnet"
    config.monetary.initial_block_reward = 120  # Higher rewards on testnet
    config.monetary.min_block_reward = 10
    config.wallet.kdf_iterations = 100_000  # Lower for faster testing
    config.log_level = "DEBUG"
    return config
