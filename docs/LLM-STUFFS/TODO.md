# To do

Put stuff to be implemented here.

## The list

### Completed
- [x] Project structure (tokencoin/ package with subpackages)
- [x] Configuration system (config.py with all tunable parameters)
- [x] Cryptographic primitives (Ed25519 keys, Pedersen commitments, stealth addresses, ring signatures, key images, range proofs, Base32)
- [x] Network layer (Tor-based 56-char addresses, Kademlia DHT, P2P transport, peer management)
- [x] Ledger layer (blockchain, blocks, transactions, RingCT builder, mempool, UTXO set, horizon privacy)
- [x] Consensus layer (Ollama orchestrator, hardware detection, ZKIP verifier, work block generator, difficulty adjustment, slashing manager)
- [x] Wallet module (key management, wallet file I/O, balance scanning, transaction building, import/export)
- [x] Mining module (one-click toggle, real-time stats, dashboard visualization data)
- [x] CLI interface (wallet create/load/balance/send/export/import, mine start/stop/status, blockchain info/height)
- [x] Test suite (crypto primitives, ledger operations)
- [x] .gitignore (private keys, env, dependencies, build artifacts)
- [x] setup.py for pip installation
- [x] Fully P2P architecture — no central nodes, every wallet is a full node (Kademlia DHT, gossip protocol, peer scoring)
- [x] Docker-based NIM container management (pull, run --gpus all, health checks, auto-restart)
- [x] BIP39 mnemonic support (12-word phrases, PBKDF2 seed derivation, TKC seed derivation)
- [x] MLSAG ring signatures (multi-layer, linkable, anonymous group signatures)
- [x] Bulletproofs range proofs (zero-knowledge range proofs for RingCT)
- [x] C++ extension for elliptic curve operations (libsodium-based, scalar mult, Pedersen commitments, key images)
- [x] Tor daemon integration (stem library, hidden service creation, SOCKS5 proxy)
- [x] Flutter/Electron desktop UI architecture (dashboard, mining, send/receive, export/import tabs)
- [x] Mobile wallet (Flutter cross-platform)
- [x] Network stress testing (N-node simulation, DHT convergence, propagation metrics, churn)
- [x] Mainnet deployment configuration (production settings, testnet config)
- [x] Switched from NVIDIA NIM to distributed Ollama instances
- [x] CPU mining support (no GPU required)
- [x] AMD GPU (ROCm) mining support
- [x] Apple Silicon (Metal) mining support
- [x] Distributed mining across multiple Ollama instances (local + remote)
- [x] Automatic hardware detection (CPU, NVIDIA, AMD, Apple Silicon, Vulkan)
- [x] Ollama Docker container management
- [x] Comprehensive Ollama model registry (11 models from 1GB to 40GB)
- [x] Updated CLI, config, and documentation for Ollama
