# TokenCoin (TKC) — Privacy-First AI Cryptocurrency

**TokenCoin** is a next-generation, privacy-first cryptocurrency that fuses decentralized AI inference with private financial transactions. Instead of wasting energy on arbitrary Proof-of-Work (PoW) hashes, TokenCoin utilizes **Proof-of-Useful-Work (PoUW)** — miners contribute computational power to a global, decentralized cluster of **Ollama** instances, supporting CPU, NVIDIA GPU (CUDA), AMD GPU (ROCm), and Apple Silicon (Metal).

TokenCoin exposes a **public, OpenAI-compatible API** (`/v1/chat/completions`, `/v1/embeddings`) that routes inference requests to the distributed mining network. External users call it like they would OpenAI, while miners earn TKC for processing the requests.

> **Status:** Alpha — Reference Implementation
> **Author:** Sammy Lord
> **License:** MIT

---

## Table of Contents

- [Architecture](#architecture)
- [Key Features](#key-features)
- [Quick Start](#quick-start)
- [CLI Usage](#cli-usage)
- [Public OpenAI-Compatible API](#public-openai-compatible-api)
- [Project Structure](#project-structure)
- [Modules](#modules)
- [Running Tests](#running-tests)
- [Building the C++ Extension](#building-the-c-extension)
- [Ollama Setup](#ollama-setup)
- [Distributed Mining](#distributed-mining)
- [Tor Integration](#tor-integration)
- [Flutter UI](#flutter-ui)
- [Network Stress Testing](#network-stress-testing)
- [Mainnet Deployment](#mainnet-deployment)
- [License](#license)

---

## Architecture

TokenCoin's architecture consists of three core layers interacting in parallel:

```
+-------------------------------------------------------------+
|                     User Interface / Wallet                 |
+-------------------------------------------------------------+
                            |
                            v
+-------------------------------------------------------------+
| Network Layer: Tor-based Addresses (Base32, 56 chars)       |
| Fully P2P: Kademlia DHT, Gossip Protocol, Peer Scoring     |
+-------------------------------------------------------------+
                            |
        +-------------------+-------------------+
        |                                       |
        v                                       v
+------------------------------+ +------------------------------+
| Ledger Layer (Privacy)       | | Consensus Layer (AI/Ollama)  |
| - RingCT & Stealth Addresses | | - Proof-of-Useful-Work       |
| - MLSAG Ring Signatures     | | - Distributed Ollama Cluster|
| - Bulletproofs Range Proofs | | - ZKIP Verification          |
| - Single-hop Visibility      | | - CPU/GPU/Metal Mining      |
+------------------------------+ +------------------------------+
```

### Fully Decentralized P2P

Every wallet is also a full node. There are **no central bootstrap nodes**, no central servers, and no single points of failure:

- **Kademlia DHT** — 160 k-buckets for peer discovery with random-walk bootstrapping
- **Gossip Protocol** — Epidemic broadcast for transaction/block propagation (TTL-based flooding)
- **Peer Scoring** — Reputation system with automatic bans for sybil resistance
- **NAT Traversal** — STUN and TCP hole-punching for connectivity

---

## Key Features

### Privacy
| Feature | Implementation |
|---|---|
| **Stealth Addresses** | One-time public keys per transaction ([`StealthAddress`](tokencoin/core/crypto.py:231)) |
| **RingCT** | Confidential amounts via Pedersen commitments `C = aG + xH` ([`PedersenCommitment`](tokencoin/core/crypto.py:178)) |
| **MLSAG Ring Signatures** | Multi-layered linkable anonymous group signatures ([`MLSAGSignature`](tokencoin/core/mlsag.py:50)) |
| **Bulletproofs** | Zero-knowledge range proofs for RingCT ([`Bulletproof`](tokencoin/core/bulletproofs.py:50)) |
| **Horizon Privacy** | Single-hop graph visibility — only sender/recipient see links ([`HorizonPrivacy`](tokencoin/ledger/__init__.py:310)) |
| **Tor-based Addressing** | 56-character Base32 addresses derived from Tor v3 ([`PublicKey.to_address()`](tokencoin/core/crypto.py:106)) |
| **Key Images** | Double-spend prevention via `I = x * H_p(P)` ([`KeyImage`](tokencoin/core/crypto.py:222)) |

### Mining (PoUW via Ollama)
| Feature | Implementation |
|---|---|
| **Ollama Integration** | Distributed AI inference via local/remote Ollama instances ([`OllamaManager`](tokencoin/mining/ollama_miner.py:200)) |
| **Hardware Detection** | Automatic detection of CPU, NVIDIA GPU, AMD GPU, Apple Silicon ([`detect_hardware()`](tokencoin/mining/ollama_miner.py:100)) |
| **CPU Mining** | Full CPU support — no GPU required ([`HardwareBackend.CPU`](tokencoin/mining/ollama_miner.py:30)) |
| **NVIDIA GPU Mining** | CUDA acceleration via nvidia-smi detection ([`HardwareBackend.CUDA`](tokencoin/mining/ollama_miner.py:30)) |
| **AMD GPU Mining** | ROCm support for AMD GPUs ([`HardwareBackend.ROCM`](tokencoin/mining/ollama_miner.py:30)) |
| **Apple Silicon Mining** | Metal acceleration on M-series Macs ([`HardwareBackend.METAL`](tokencoin/mining/ollama_miner.py:30)) |
| **ZKIP Verification** | Zero-Knowledge Inference Proofs prevent spoofing ([`ZKIPVerifier`](tokencoin/consensus/__init__.py:145)) |
| **Dynamic Difficulty** | Targets 5-minute block times ([`DifficultyAdjuster`](tokencoin/consensus/__init__.py:280)) |
| **Slashing** | Penalizes dishonest miners ([`SlashingManager`](tokencoin/consensus/__init__.py:310)) |
| **One-Click Toggle** | [`Miner.toggle()`](tokencoin/mining/__init__.py:130) with real-time hardware stats and TKC rate |
| **P2P Job Distribution** | Inference jobs broadcast via DHT gossip protocol ([`P2PNode.broadcast_job()`](tokencoin/network/p2p.py:780)) |
| **Public OpenAI API** | Unified `/v1/chat/completions` and `/v1/embeddings` endpoint ([`OpenAIServer`](tokencoin/api/__init__.py:300)) |
| **Distributed Mining** | Connect remote Ollama instances for cluster mining ([`OllamaManager.add_remote_instance()`](tokencoin/mining/ollama_miner.py:300)) |
| **Docker Deployment** | Run Ollama in Docker for isolated mining ([`DockerManager`](tokencoin/consensus/docker_nim.py:100)) |

### Monetary Policy
| Parameter | Value |
|---|---|
| **Max Supply** | 10 Trillion TKC |
| **Base Supply** | 6.4B TKC |
| **Initial Block Reward** | 12 TKC |
| **Block Time** | 5 minutes (300 seconds) |
| **Halving Interval** | ~4 years (210,000 blocks) |
| **Minimum Reward** | 1 TKC |

### Wallet
| Feature | Implementation |
|---|---|
| **BIP39 Mnemonic** | 12-word phrases with PBKDF2 seed derivation ([`BIP39Mnemonic`](tokencoin/core/bip39.py:200)) |
| **Encrypted Storage** | Wallet file I/O with password protection ([`WalletFile`](tokencoin/wallet/__init__.py:100)) |
| **Dual-Key System** | Separate spend and view keys for stealth address compatibility |
| **Balance Scanning** | Blockchain scan with view key to find owned outputs ([`BalanceScanner`](tokencoin/wallet/__init__.py:150)) |
| **Import/Export** | Private key hex, BIP39 mnemonic, encrypted wallet file |

### Network
| Feature | Implementation |
|---|---|
| **Fully P2P** | No central nodes — every wallet is a full node ([`P2PNode`](tokencoin/network/p2p.py:550)) |
| **Kademlia DHT** | 160 k-buckets, random-walk discovery, peer exchange ([`DHT`](tokencoin/network/p2p.py:100)) |
| **Gossip Protocol** | Epidemic broadcast with TTL, dedup, and fanout ([`GossipEngine`](tokencoin/network/p2p.py:420)) |
| **Tor Integration** | Hidden service creation, SOCKS5 proxy ([`TorManager`](tokencoin/network/tor_integration.py:80)) |
| **Peer Scoring** | Reputation system with automatic bans ([`PeerScore`](tokencoin/network/p2p.py:80)) |
| **NAT Traversal** | STUN, TCP hole-punching ([`NATTraversal`](tokencoin/network/p2p.py:520)) |

---

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/sammylord/tokencoin.git
cd tokencoin

# Install in development mode
pip install -e .

# With GPU support (for mining)
pip install -e ".[gpu]"

# With development tools
pip install -e ".[dev]"

# For Tor integration
pip install stem

# For C++ extension (optional, for performance)
# Requires: libsodium (brew install libsodium on macOS)
python setup.py build_ext --inplace
```

### Install Ollama

TokenCoin uses **Ollama** for AI inference mining. Install it from [ollama.ai](https://ollama.ai):

```bash
# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.ai/install.sh | sh

# Windows
# Download from https://ollama.ai/download
```

### CLI Usage

```bash
# Create a new wallet (shows BIP39 mnemonic)
tokencoin wallet create

# Load an existing wallet
tokencoin wallet load wallet.tkc

# Check balance
tokencoin wallet balance

# Send TKC
tokencoin wallet send <address> <amount>

# Export private key or BIP39 mnemonic
tokencoin wallet export

# Import from BIP39 mnemonic
tokencoin wallet import "abandon ability able about above absent ..."

# Start mining (CPU, GPU, or Apple Silicon — auto-detected)
tokencoin mine start --model phi3-mini

# Check mining status (backend, hardware, TKC rate)
tokencoin mine status

# Stop mining
tokencoin mine stop

# View blockchain info
tokencoin blockchain info

# View blockchain height
tokencoin blockchain height

# Start the public OpenAI-compatible API server
tokencoin api start --port 8080
```

### Public OpenAI-Compatible API

TokenCoin exposes a **unified, OpenAI-compatible API** that routes inference requests to the distributed mining network. External users call it like OpenAI, while miners earn TKC for processing the requests.

```bash
# Start the API server
tokencoin api start --port 8080
```

**Chat completion** (routed to distributed miners):
```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "phi3-mini",
    "messages": [{"role": "user", "content": "What is TokenCoin?"}],
    "max_tokens": 128
  }'
```

**Embeddings**:
```bash
curl http://localhost:8080/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nomic-embed-text",
    "input": "TokenCoin is a privacy-first AI cryptocurrency"
  }'
```

**List models**:
```bash
curl http://localhost:8080/v1/models
```

**Health check**:
```bash
curl http://localhost:8080/v1/health
```

**How it works:**
1. External users call the API like OpenAI (`/v1/chat/completions`, `/v1/embeddings`)
2. The API server creates a PoUW inference job and broadcasts it via the P2P gossip protocol
3. Available miners on the network claim and process the job using their local Ollama instance
4. The result is returned to the external user
5. The miner earns TKC block rewards for the useful work

---

## Project Structure

```
tokencoin/
├── __init__.py                    # Package init, version 0.1.0
├── .gitignore                     # Ignores keys, env, deps, build artifacts
├── config.py                      # All tunable parameters
├── cli.py                         # Command-line interface
├── mainnet_config.py              # Mainnet/testnet deployment config
│
├── core/                          # Cryptographic primitives
│   ├── crypto.py                  # Ed25519 keys, Pedersen commitments, stealth addresses, ring signatures, key images, range proofs, Base32
│   ├── bip39.py                   # BIP39 mnemonic (2048-word list, PBKDF2 seed derivation)
│   ├── mlsag.py                   # MLSAG ring signatures (multi-layer, linkable)
│   ├── bulletproofs.py            # Bulletproofs range proofs (inner product argument)
│   └── tkc_crypto.c               # C++ extension (libsodium-based EC operations)
│
├── network/                       # P2P networking
│   ├── __init__.py                # Original network layer
│   ├── p2p.py                     # Fully P2P: Kademlia DHT, gossip protocol, peer scoring, NAT traversal
│   └── tor_integration.py         # Tor daemon: hidden services, SOCKS5 proxy, circuit management
│
├── ledger/                        # Blockchain & privacy
│   └── __init__.py                # Blockchain, blocks, transactions, RingCT builder, mempool, UTXO set, horizon privacy
│
├── consensus/                     # PoUW consensus
│   ├── __init__.py                # Ollama orchestrator, hardware detection, ZKIP verifier, work block generator, difficulty adjustment, slashing manager
│   └── docker_nim.py              # Ollama Docker container management (pull, run, health checks)
│
├── wallet/                        # Wallet management
│   └── __init__.py                # Key management, wallet file I/O, balance scanning, transaction building, BIP39 import/export
│
├── mining/                        # Mining controller
│   ├── __init__.py                # One-click toggle, real-time stats, dashboard visualization data
│   └── ollama_miner.py            # Distributed Ollama manager: hardware detection, instance management, model registry, inference
│
├── ui/                            # Flutter/Electron UI
│   └── README.md                  # UI architecture, WebSocket API, build instructions
│
└── tests/                         # Test suite
    ├── test_crypto.py             # 18 tests for all crypto primitives
    ├── test_ledger.py             # 12 tests for blockchain, transactions, horizon privacy
    └── stress_test.py             # Network stress testing (N-node simulation, DHT convergence, propagation metrics)
```

---

## Modules

### Core Cryptography ([`tokencoin/core/`](tokencoin/core/))

| File | Description |
|---|---|
| [`crypto.py`](tokencoin/core/crypto.py) | Ed25519 keys, Pedersen commitments, stealth addresses, ring signatures, key images, range proofs, Base32 encoding |
| [`bip39.py`](tokencoin/core/bip39.py) | BIP39 mnemonic generation/validation, PBKDF2 seed derivation, TKC seed derivation |
| [`mlsag.py`](tokencoin/core/mlsag.py) | Multi-layered linkable ring signatures (MLSAG) for multi-input transactions |
| [`bulletproofs.py`](tokencoin/core/bulletproofs.py) | Zero-knowledge range proofs for confidential transaction amounts |
| [`tkc_crypto.c`](tokencoin/core/tkc_crypto.c) | C++ extension using libsodium for high-performance EC operations |

### P2P Network ([`tokencoin/network/`](tokencoin/network/))

| File | Description |
|---|---|
| [`p2p.py`](tokencoin/network/p2p.py) | Fully decentralized P2P: Kademlia DHT (160 buckets), gossip protocol (TTL flooding), peer scoring (sybil resistance), NAT traversal |
| [`tor_integration.py`](tokencoin/network/tor_integration.py) | Tor daemon management: hidden service creation (v3 .onion), SOCKS5 proxy client, circuit management |

### Ledger ([`tokencoin/ledger/`](tokencoin/ledger/))

| Component | Description |
|---|---|
| [`Transaction`](tokencoin/ledger/__init__.py:100) | RingCT transaction with stealth addresses, Pedersen commitments, range proofs |
| [`Block`](tokencoin/ledger/__init__.py:200) | Block with Merkle tree, PoUW metadata (work model, tensor commitment) |
| [`Blockchain`](tokencoin/ledger/__init__.py:250) | Chain management, UTXO set, mempool, orphan block handling |
| [`RingCTBuilder`](tokencoin/ledger/__init__.py:350) | Builds RingCT transactions with proper commitments and range proofs |
| [`HorizonPrivacy`](tokencoin/ledger/__init__.py:310) | Single-hop graph visibility enforcement |

### Consensus ([`tokencoin/consensus/`](tokencoin/consensus/))

| Component | Description |
|---|---|
| [`OllamaOrchestrator`](tokencoin/consensus/__init__.py:195) | Hardware detection, model selection, inference job processing via Ollama |
| [`DockerManager`](tokencoin/consensus/docker_nim.py:100) | Docker pull/run/stop for Ollama, GPU passthrough, health monitoring, auto-restart |
| [`ZKIPVerifier`](tokencoin/consensus/__init__.py:145) | Zero-knowledge inference proof verification |
| [`DifficultyAdjuster`](tokencoin/consensus/__init__.py:280) | Dynamic difficulty targeting 5-minute blocks |
| [`SlashingManager`](tokencoin/consensus/__init__.py:310) | Penalizes dishonest miners |

### Wallet ([`tokencoin/wallet/`](tokencoin/wallet/))

| Component | Description |
|---|---|
| [`WalletAccount`](tokencoin/wallet/__init__.py:45) | Dual-key system (spend + view), BIP39 mnemonic generation/recovery |
| [`WalletFile`](tokencoin/wallet/__init__.py:100) | Encrypted wallet file I/O |
| [`BalanceScanner`](tokencoin/wallet/__init__.py:150) | Blockchain scan with view key |
| [`Wallet`](tokencoin/wallet/__init__.py:360) | High-level wallet operations |

### Mining ([`tokencoin/mining/`](tokencoin/mining/))

| Component | Description |
|---|---|
| [`Miner`](tokencoin/mining/__init__.py:80) | One-click mining toggle, real-time stats |
| [`MiningStats`](tokencoin/mining/__init__.py:40) | Hardware info (CPU/GPU), model info, TKC generation rate |
| [`MiningVisualizer`](tokencoin/mining/__init__.py:180) | Dashboard visualization data |
| [`OllamaManager`](tokencoin/mining/ollama_miner.py:200) | Distributed Ollama instance management, hardware detection, model registry |

---

## Running Tests

```bash
# Run all tests
python -m unittest discover -s tokencoin/tests -v

# Run specific test file
python -m unittest tokencoin.tests.test_crypto -v
python -m unittest tokencoin.tests.test_ledger -v

# Run stress test (simulates N-node P2P network)
python -m tokencoin.tests.stress_test --nodes 10 --txs 50
```

**Current test results: 30/30 tests passing** — all crypto primitives (key generation, Base32, Pedersen commitments, stealth addresses, key images, ring signatures, range proofs) and ledger operations (transactions, blocks, blockchain, mempool, horizon privacy) verified.

---

## Building the C++ Extension

The C++ extension provides high-performance elliptic curve operations using libsodium:

```bash
# Install libsodium
# macOS:
brew install libsodium

# Ubuntu/Debian:
sudo apt-get install libsodium-dev

# Build the extension
python setup.py build_ext --inplace
```

The extension provides:
- [`ed25519_scalar_mult()`](tokencoin/core/tkc_crypto.c:60) — scalar × point multiplication
- [`pedersen_commit()`](tokencoin/core/tkc_crypto.c:100) — C = a*G + x*H commitment
- [`compute_key_image()`](tokencoin/core/tkc_crypto.c:130) — I = x * H_p(P)
- [`point_add()`](tokencoin/core/tkc_crypto.c:160) / [`point_subtract()`](tokencoin/core/tkc_crypto.c:180) — point arithmetic
- [`random_scalar()`](tokencoin/core/tkc_crypto.c:210) — secure random scalar generation

---

## Ollama Setup

TokenCoin uses **Ollama** for Proof-of-Useful-Work mining. Ollama runs on CPU, NVIDIA GPU, AMD GPU, and Apple Silicon.

### Install Ollama

```bash
# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.ai/install.sh | sh

# Windows
# Download from https://ollama.ai/download
```

### Start Ollama

Ollama typically runs as a background service. The TokenCoin miner will automatically start it if needed.

```bash
# Start Ollama manually (if not running)
ollama serve

# Pull a model manually (optional — TokenCoin auto-pulls)
ollama pull phi3:mini
ollama pull llama3.2:3b
```

### Supported Models

Models are auto-selected based on available memory (RAM for CPU, VRAM for GPU):

| Model | Min Memory | Type | Use Case |
|---|---|---|---|
| `all-minilm` | 1 GB | Embedding | Lightweight CPU mining |
| `nomic-embed-text` | 2 GB | Embedding | CPU-friendly embedding |
| `tinyllama` | 3 GB | LLM | Entry-level LLM mining |
| `phi3-mini` | 4 GB | LLM | Default — CPU & GPU |
| `llama3.2-3b` | 4 GB | LLM | Small LLM, fast inference |
| `phi3-small` | 6 GB | LLM | Medium LLM |
| `mistral-7b` | 8 GB | LLM | Popular 7B model |
| `llama3.1-8b` | 8 GB | LLM | Meta's 8B model |
| `gemma2-9b` | 10 GB | LLM | Google's 9B model |
| `mixtral-8x7b` | 32 GB | LLM | Mixture of experts |
| `llama3.1-70b` | 40 GB | LLM | Large model, high-end GPU |

### Docker Deployment

For isolated or server-based mining, run Ollama in Docker:

```bash
# CPU-only
docker run -d -p 11434:11434 --name tokencoin-ollama \
  -v ~/.tokencoin/ollama_models:/root/.ollama \
  ollama/ollama

# NVIDIA GPU
docker run -d --gpus all -p 11434:11434 --name tokencoin-ollama \
  -v ~/.tokencoin/ollama_models:/root/.ollama \
  ollama/ollama

# AMD GPU (ROCm)
docker run -d --device /dev/kfd --device /dev/dri \
  -p 11434:11434 --name tokencoin-ollama \
  -v ~/.tokencoin/ollama_models:/root/.ollama \
  ollama/ollama:rocm
```

---

## Distributed Mining

TokenCoin supports distributed mining across multiple Ollama instances:

### Adding Remote Instances

Configure remote Ollama instances in your config or via environment:

```bash
# Via config (tokencoin/config.py)
# CONFIG.ollama.remote_instances = ["192.168.1.100:11434", "mining-node.local:11434"]

# Or set environment variable
export TKC_REMOTE_INSTANCES="192.168.1.100:11434,mining-node.local:11434"
```

### How Distributed Mining Works

1. **Local Instance:** Your local Ollama daemon runs the primary mining model
2. **Remote Instances:** Additional Ollama servers contribute inference capacity
3. **Health Monitoring:** The orchestrator continuously checks all instances
4. **Load Distribution:** Jobs are distributed to the healthiest, least-loaded instance
5. **Verification:** All results are verified via ZKIP regardless of which instance processed them

### Hardware Backend Detection

The miner automatically detects and reports your hardware:

| Backend | Detection Method | Mining Support |
|---|---|---|
| CPU | `/proc/cpuinfo`, `sysctl` | Full support |
| NVIDIA GPU | `nvidia-smi` | CUDA acceleration |
| AMD GPU | `rocm-smi` | ROCm acceleration |
| Apple Silicon | `sysctl hw.optional.arm64` | Metal acceleration |
| Vulkan | `vulkaninfo` | Cross-platform GPU |

---

## Tor Integration

For anonymous P2P communication:

```bash
# Install stem library
pip install stem

# The node will automatically:
# 1. Start a Tor daemon
# 2. Create a v3 hidden service
# 3. Derive a 56-char TKC address from the .onion address
# 4. Route all P2P traffic through Tor SOCKS5 proxy
```

---

## Flutter UI

The UI architecture is documented in [`tokencoin/ui/README.md`](tokencoin/ui/README.md). It provides:

- **Dashboard Tab** — Balance, recent transactions, network status
- **Mining Tab ("Earn")** — One-click toggle, hardware visualization, TKC rate chart
- **Send/Receive Tab** — Address input with 56-char validation, QR scanner
- **Export/Import Tab** — Private key, BIP39 mnemonic, wallet file management

```bash
# Build the Flutter app
cd tokencoin_ui
flutter build macos   # Desktop
flutter build apk     # Android
flutter build ios     # iOS
```

---

## Network Stress Testing

Simulate a fully decentralized P2P network:

```bash
python -m tokencoin.tests.stress_test --nodes 100 --txs 1000
```

The stress test measures:
- DHT convergence time
- Transaction/block propagation latency
- Network resilience under churn (nodes joining/leaving)
- Peer count distribution
- Message overhead

---

## Mainnet Deployment

Production configuration is in [`tokencoin/mainnet_config.py`](tokencoin/mainnet_config.py):

```python
from tokencoin.mainnet_config import get_mainnet_config

config = get_mainnet_config()
# 10T max supply, 5-min blocks, 128 max peers, 1M KDF iterations
```

Testnet configuration is also available:

```python
from tokencoin.mainnet_config import get_testnet_config

config = get_testnet_config()
# Higher rewards, lower KDF for faster testing
```

---

## License

MIT License — see [LICENSE](LICENSE) file for details.

Copyright (c) 2026 Sammy Lord
