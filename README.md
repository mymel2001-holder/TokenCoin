# TokenCoin (TKC) — Privacy-First AI Cryptocurrency

**TokenCoin** is a next-generation, privacy-first cryptocurrency that fuses decentralized AI inference with private financial transactions. Instead of wasting energy on arbitrary Proof-of-Work (PoW) hashes, TokenCoin utilizes **Proof-of-Useful-Work (PoUW)** — miners contribute computational power to a global, decentralized cluster of **Ollama** instances, supporting CPU, NVIDIA GPU (CUDA), AMD GPU (ROCm), and Apple Silicon (Metal).

TokenCoin exposes a **public, OpenAI-compatible API** (`/v1/chat/completions`, `/v1/embeddings`) that routes inference requests to the distributed mining network. External users call it like they would OpenAI, while miners earn TKC for processing the requests.

> **Status:** Alpha — Rough Draft
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
| **Dynamic Difficulty** | Targets 5-minute block times ([`DifficultyAdjuster`](tokencoin/consensus/__init__.py:555)) |
| **Slashing** | Penalizes dishonest miners ([`SlashingManager`](tokencoin/consensus/__init__.py:611)) |
| **One-Click Toggle** | [`Miner.toggle()`](tokencoin/mining/__init__.py:229) with real-time hardware stats and TKC rate |
| **P2P Job Distribution** | Inference jobs broadcast via DHT gossip protocol ([`MiningP2PSubnet`](tokencoin/network/mining_p2p.py:183)) |
| **P2P Miner Discovery** | Fully decentralized — no central server, no static node list ([`MiningP2PSubnet.get_available_miners()`](tokencoin/network/mining_p2p.py:548)) |
| **Public OpenAI API** | Unified `/v1/chat/completions` and `/v1/embeddings` endpoint ([`OpenAIServer`](tokencoin/api/__init__.py:300)) |
| **Distributed Mining** | Remote miners discovered dynamically via P2P subnet ([`MiningP2PSubnet`](tokencoin/network/mining_p2p.py:183)) |
| **Docker Deployment** | Run Ollama in Docker for isolated mining ([`DockerManager`](tokencoin/consensus/docker_nim.py:112)) |

### Monetary Policy
| Parameter | Value |
|---|---|
| **Max Supply** | 10 Trillion TKC |
| **Base Supply** | 6.4B TKC |
| **Initial Block Reward** | 12 TKC |
| **Block Time** | 5 minutes (300 seconds) |
| **Tail Emission** | 1 TKC (minimum reward forever) |
| **Emission Curve** | Smooth exponential decay — no halving events ([`EmissionCurve`](tokencoin/core/emission.py:100)) |

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
| **Fully P2P** | No central nodes — every wallet is a full node ([`P2PNode`](tokencoin/network/p2p.py:579)) |
| **Kademlia DHT** | 160 k-buckets, random-walk discovery, peer exchange ([`DHT`](tokencoin/network/p2p.py:206)) |
| **Gossip Protocol** | Epidemic broadcast with TTL, dedup, and fanout ([`GossipEngine`](tokencoin/network/p2p.py:401)) |
| **Mining P2P Subnet** | Fully decentralized miner discovery over DHT + gossip ([`MiningP2PSubnet`](tokencoin/network/mining_p2p.py:183)) |
| **Tor Integration** | Hidden service creation, SOCKS5 proxy ([`TorManager`](tokencoin/network/tor_integration.py:80)) |
| **Peer Scoring** | Reputation system with automatic bans ([`PeerScore`](tokencoin/network/p2p.py:134)) |
| **NAT Traversal** | STUN, TCP hole-punching ([`NATTraversal`](tokencoin/network/p2p.py:540)) |

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

TokenCoin uses **Ollama** for AI inference mining. Install it from [ollama.com](https://ollama.com):

```bash
# macOS/Linux
curl -fsSL https://ollama.com/install.sh | sh
```

```powershell
# Windows
irm https://ollama.com/install.ps1 | iex
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

# Start mining with any Ollama model (CPU, GPU, or Apple Silicon — auto-detected)
tokencoin mine start --model phi3:mini
tokencoin mine start --model llama3.2:3b
tokencoin mine start --model mistral:7b
tokencoin mine start --model deepseek-r1:70b

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

**Chat completion** (routed to distributed miners — any Ollama model works):
```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "phi3:mini",
    "messages": [{"role": "user", "content": "What is TokenCoin?"}],
    "max_tokens": 128
  }'
```

**Embeddings** (any Ollama embedding model works):
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
├── core/                          # Cryptographic primitives & monetary policy
│   ├── crypto.py                  # Ed25519 keys, Pedersen commitments, stealth addresses, ring signatures, key images, range proofs, Base32
│   ├── emission.py                # Smooth emission curve — fair, unbiased printing (Monero-style)
│   ├── bip39.py                   # BIP39 mnemonic (2048-word list, PBKDF2 seed derivation)
│   ├── mlsag.py                   # MLSAG ring signatures (multi-layer, linkable)
│   ├── bulletproofs.py            # Bulletproofs range proofs (inner product argument)
│   └── tkc_crypto.c               # C++ extension (libsodium-based EC operations)
│
├── network/                       # P2P networking
│   ├── __init__.py                # Network layer exports (including MiningP2PSubnet)
│   ├── p2p.py                     # Fully P2P: Kademlia DHT, gossip protocol, peer scoring, NAT traversal
│   ├── mining_p2p.py              # Fully decentralized mining subnet (DHT + gossip, no central server)
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

### Core Cryptography & Monetary Policy ([`tokencoin/core/`](tokencoin/core/))

| File | Description |
|---|---|
| [`crypto.py`](tokencoin/core/crypto.py) | Ed25519 keys, Pedersen commitments, stealth addresses, ring signatures, key images, range proofs, Base32 encoding |
| [`emission.py`](tokencoin/core/emission.py) | Smooth emission curve — fair, unbiased printing with tail emission (Monero-style) |
| [`bip39.py`](tokencoin/core/bip39.py) | BIP39 mnemonic generation/validation, PBKDF2 seed derivation, TKC seed derivation |
| [`mlsag.py`](tokencoin/core/mlsag.py) | Multi-layered linkable ring signatures (MLSAG) for multi-input transactions |
| [`bulletproofs.py`](tokencoin/core/bulletproofs.py) | Zero-knowledge range proofs for confidential transaction amounts |
| [`tkc_crypto.c`](tokencoin/core/tkc_crypto.c) | C++ extension using libsodium for high-performance EC operations |

### P2P Network ([`tokencoin/network/`](tokencoin/network/))

| File | Description |
|---|---|
| [`p2p.py`](tokencoin/network/p2p.py) | Fully decentralized P2P: Kademlia DHT (160 buckets), gossip protocol (TTL flooding), peer scoring (sybil resistance), NAT traversal |
| [`mining_p2p.py`](tokencoin/network/mining_p2p.py) | **Fully decentralized mining subnet**: DHT + gossip based miner discovery, job distribution, first-come-first-served claiming, peer reputation — no central server, no static node list |
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
| [`OllamaOrchestrator`](tokencoin/consensus/__init__.py:234) | Hardware detection, model selection, inference job processing via Ollama; integrates with P2P mining subnet for remote miner discovery |
| [`DockerManager`](tokencoin/consensus/docker_nim.py:112) | Docker pull/run/stop for Ollama, GPU passthrough, health monitoring, auto-restart |
| [`ZKIPVerifier`](tokencoin/consensus/__init__.py:155) | Zero-knowledge inference proof verification |
| [`DifficultyAdjuster`](tokencoin/consensus/__init__.py:555) | Dynamic difficulty targeting 5-minute blocks |
| [`SlashingManager`](tokencoin/consensus/__init__.py:611) | Penalizes dishonest miners |
| [`MiningP2PSubnet`](tokencoin/network/mining_p2p.py:183) | Fully decentralized P2P miner discovery and job distribution (replaces static remote_instances) |

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
# macOS/Linux
curl -fsSL https://ollama.com/install.sh | sh
```

```powershell
# Windows
irm https://ollama.com/install.ps1 | iex
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

TokenCoin accepts **any Ollama model** — there is no hardcoded allowlist. The
[`ModelRegistry`](tokencoin/mining/ollama_miner.py:818) dynamically resolves model names
and auto-estimates memory requirements, parameter counts, and inference type from the
model tag.

**Model name format:** `name:tag` (e.g. `llama3.2:3b`, `mistral:7b`, `deepseek-r1:70b`)

| Tag Pattern | Example | Estimated Params |
|---|---|---|
| `{N}b` | `7b`, `70b`, `1.5b` | N billion |
| `{N}x{M}b` (MoE) | `8x7b`, `8x22b` | ~N×M×0.7 billion |
| `mini` / `small` / `large` | `phi3:mini` | 3.8 / 7.0 / 70.0 billion |

Memory is estimated at ~4 GB base + ~0.5 GB per billion parameters (Q4 quantized).
The miner will warn if your hardware has insufficient memory for the selected model.

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

TokenCoin supports fully distributed mining across a global P2P network with **no central server** and **no static node list**.

### How It Works

The [`MiningP2PSubnet`](tokencoin/network/mining_p2p.py:183) is a fully decentralized mining subnet built on top of the existing Kademlia DHT and gossip protocol:

1. **Miner Discovery:** Every miner broadcasts its hardware capabilities (backend, GPU, VRAM, models) via `MINER_REGISTER` gossip messages. The Kademlia DHT routes these messages across the network.
2. **Dynamic Registry:** Each node maintains a live registry of all known miners. Peers score each other based on successful job completions (sybil resistance).
3. **Job Distribution:** Any node can announce an inference job via `JOB_ANNOUNCE` gossip. The job is propagated to the entire subnet.
4. **First-Come, First-Served:** Miners claim jobs via `JOB_CLAIM` messages. The first claimant wins.
5. **Result Submission:** Completed results are submitted via `JOB_RESULT` gossip. Peer scores are updated based on successful completions.
6. **Self-Healing:** Dead peers are automatically evicted after 30 minutes. Stale jobs are cleaned up after 1 hour. Miners re-announce their presence every 2 minutes.

### No Configuration Needed

```bash
# Just start mining — the P2P subnet auto-discovers other miners
tokencoin mine start --model phi3:mini

# The old remote_instances config has been removed.
# Miners are discovered dynamically through the DHT + gossip protocol.
```

### Architecture

```
┌─────────────────────────────────────────────────────┐
│                 P2P Network (DHT)                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
│  │ Miner A  │  │ Miner B  │  │ Miner C  │          │
│  │ (GPU)    │  │ (CPU)    │  │ (Apple)  │          │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘          │
│       │             │             │                 │
│       │  MINER_REGISTER (gossip)  │                 │
│       │◄────────────┼────────────►│                 │
│       │             │             │                 │
│       │  JOB_ANNOUNCE             │                 │
│       │──────────────────────────►│                 │
│       │◄── JOB_CLAIM ──────────── │                 │
│       │             │             │                 │
│       │  JOB_RESULT               │                 │
│       │◄──────────────────────────│                 │
└───────┴─────────────┴─────────────┴─────────────────┘
```

### How Distributed Mining Works

1. **Local Instance:** Your local Ollama daemon runs the primary mining model
2. **P2P Discovery:** Other miners on the network are discovered automatically via the DHT — no manual configuration needed
3. **Health Monitoring:** The orchestrator continuously checks local and P2P-discovered instances
4. **Load Distribution:** Jobs are distributed to the healthiest, highest-reputation instance
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

## One last, small note

I designed this specifically so I don't have to run any nodes myself and I'm not putting SPL-R5 on this, therefore I shall not make money off of my own creation - that way I'm not as biased towards my own creation. If you like this work, [PLEASE DONATE](https://coindrop.to/sam) as much as you feel and are financially able to so I can keep doing this.

## License

MIT License — see [LICENSE](LICENSE) file for details.

Copyright (c) 2026 Sammy Lord
