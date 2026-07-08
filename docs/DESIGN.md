# **System Design Document: TokenCoin (TKC)**

**Author:** Sammy Lord
**Status:** Implementation Document
**Date:** July 2026

## **1\. Executive Summary**

**TokenCoin (TKC)** is a next-generation, privacy-first cryptocurrency that fuses decentralized AI inference with private financial transactions. Instead of wasting energy on arbitrary Proof-of-Work (PoW) hashes, TokenCoin utilizes **Proof-of-Useful-Work (PoUW)**. Miners contribute computational power to a global, decentralized cluster of **Ollama** instances — supporting CPU, NVIDIA GPU (CUDA), AMD GPU (ROCm), and Apple Silicon (Metal).

Financially, TokenCoin enforces strict, untraceable anonymity inspired by Monero, utilizing a heavily modified ring signature and stealth address protocol natively mapped to isolated onion-routing network layers.

**Key Design Decision:** The mining network is **fully P2P** with no central server, no bootstrap nodes, and no static node list. Miner discovery and job distribution happen entirely through the Kademlia DHT and gossip protocol. Every node discovers miners dynamically, builds a reputation-based registry, and distributes jobs via epidemic broadcast.

## **2\. Architecture Overview**

TokenCoin's architecture consists of three core layers interacting in parallel:

1. **The Network & Routing Layer:** Governs node discovery, communication, and native anonymous addressing. Includes the **MiningP2PSubnet** — a fully decentralized overlay for miner discovery and job distribution.
2. **The Consensus & AI Inference Layer:** Manages the distributed Ollama orchestration, job distribution, and Proof-of-Useful-Work verification.
3. **The Ledger & Privacy Layer:** Executes private financial transactions using advanced cryptographic primitives.

```
+-------------------------------------------------------------+
|                     User Interface / Wallet                 |
+-------------------------------------------------------------+
                            |
                            v
+-------------------------------------------------------------+
| Network Layer: Tor-based Addresses (Base32, 56 chars)       |
| Sub-layer: MiningP2PSubnet (DHT + gossip, no central node)  |
+-------------------------------------------------------------+
                            |
        +-------------------+-------------------+
        |                                       |
        v                                       v
+------------------------------+ +------------------------------+
| Ledger Layer (Privacy)       | | Consensus Layer (AI/Ollama)  |
| - RingCT & Stealth Addresses | | - Proof-of-Useful-Work       |
| - Single-hop Visibility      | | - Distributed Ollama Cluster |
| - Horizon Privacy            | | - P2P Miner Discovery        |
+------------------------------+ +------------------------------+
```

The mining subnet is a **logical overlay** on top of the P2P network. It reuses the existing gossip message types (`MINER_REGISTER`, `JOB_ANNOUNCE`, `JOB_CLAIM`, `JOB_RESULT`) and Kademlia DHT routing table for fully decentralized operation.

### Monetary Policy Constraints
- Should dynamically digitally "print" money while mining — in a fair manner without bias.
- Start with a "base amount" of 10T we can ever mine, starting printing at around 6.4B
- Print dynamically yet fairly distributed so it never runs out
- Start with a block reward of 12 TKC, go down from there with smooth exponential decay (no halving events)

## **3\. Network Layer: Tor-Based Addressing**

TokenCoin completely decouples human identity from network location by embedding Tor's v3 hidden service architecture directly into the wallet routing layer.

### **3.1 Custom Address Format**

* TokenCoin addresses are derived from the public key of a standard Tor v3 onion address.  
* The standard .onion suffix is stripped.  
* **Format:** A 56-character Base32 string (e.g., b32jalx77dfmknasdf8901234567890zxcvbnmasdfghjklertyuio).  
* This setup allows nodes to open direct, end-to-end encrypted Tor circuits to destination wallets for instantaneous, metadata-free P2P sync and tx propagation.

## **4\. Ledger & Privacy Layer**

TokenCoin implements a modified version of CryptoNote (Monero-like) privacy, but restricts visibility to the **immediate transaction horizon** to maintain lightweight state verification for AI nodes.

### **4.1 Transaction Confidentiality**

* **Stealth Addresses:** Every transaction is sent to a one-time public key automatically derived by the sender, ensuring the recipient's public address never appears on the public ledger.  
* **Ring Confidential Transactions (RingCT):** Sums of inputs and outputs are obscured using Pedersen commitments, proving that no coins were created out of thin air without revealing the actual values:

$$C \= aG \+ xH$$  
*(where $a$ is the transaction amount, $x$ is a blinding factor, and $G$ and $H$ are fixed generator points).*

### **4.2 Horizon Privacy (From \-\> To Routing)**

Unlike fully public ledgers or fully decoupled historical ledgers, TokenCoin implements **Single-Hop Graph Visibility**.

* Only the explicit Sender and explicit Receiver hold the view keys required to decrypt the immediate cryptographic linking of a transaction block.  
* Outside observers can only see that a cryptographically valid state transition occurred, but cannot trace the chain upwards or downwards past the immediate parent blocks of that transaction.

## **5\. Consensus Layer: Proof-of-Useful-Work (PoUW) via Distributed Ollama**

TokenCoin replaces traditional cryptographic hashing with verifiable AI inference hosting.

### **5.1 The Miner as an Ollama Node**

When a user clicks "Mine" in the TokenCoin client, the software connects to a local or remote **Ollama** instance.

* The node registers its hardware capabilities (CPU cores, RAM, GPU type/VRAM, supported models) to the decentralized public cluster via the **MiningP2PSubnet** — a fully P2P overlay on the Kademlia DHT.
* Miner discovery is **fully decentralized**: there is no central server, no bootstrap node, and no static list of remote instances. Every miner broadcasts its capabilities via `MINER_REGISTER` gossip messages, and the DHT propagates them across the network.
* The cluster serves public-facing AI requests (LLMs, vision models, embedding models) via a unified OpenAI-compatible API.
* Supports CPU-only mining, NVIDIA GPU (CUDA), AMD GPU (ROCm), and Apple Silicon (Metal).
* **Peer Scoring:** Each node maintains a reputation score for every known miner, providing sybil resistance and incentivizing honest behavior.

### **5.2 Public OpenAI-Compatible API**

TokenCoin exposes a unified API endpoint that routes inference requests to the distributed mining network:

```
External User
    |
    v
POST /v1/chat/completions  -->  OpenAIServer (tokencoin api start)
    |                              |
    |                              +--> Try local Ollama (fast path)
    |                              |
    |                              +--> Create MiningSubnetJob
    |                                       |
    |                                       v
    |                              MiningP2PSubnet (DHT + Gossip)
    |                              - JOB_ANNOUNCE broadcast
    |                              - Miners claim via JOB_CLAIM
    |                              - First claimant wins
    |                              - Result via JOB_RESULT
    |                              +--> Miner A (GPU)
    |                              +--> Miner B (CPU)
    |                              +--> Miner C (Apple Silicon)
    |
    v
Returns OpenAI-compatible JSON response
```

**Endpoints:**
- `POST /v1/chat/completions` — Chat completions (streaming supported)
- `POST /v1/embeddings` — Text embeddings
- `GET /v1/models` — List available models
- `GET /v1/health` — Server health and mining network status

### **5.3 Proof-of-Useful-Work Verification**

To prevent spoofing or lazy nodes, TokenCoin uses a deterministic verification method:

1. **Zero-Knowledge Inference Proofs (ZKIP):** Random inference requests are sent with strict seed parameters. The node must return the output tokens along with a cryptographic commitment of the intermediate tensor weights.
2. **Slashing and Rewards:** If a node returns a malformed tensor calculation (indicating it didn't actually run the model or used underpowered hardware), its stake/reputation is slashed. If it successfully processes valid user requests, it generates a "Work Block," which rewards the miner with freshly minted TKC.

### **5.4 Distributed Mining Architecture**

TokenCoin supports a fully distributed mining topology with **no central server** and **no static node list**. The [`MiningP2PSubnet`](../tokencoin/network/mining_p2p.py) is a logical overlay on the existing P2P network that handles all miner discovery and job distribution.

#### 5.4.1 P2P Miner Discovery

Instead of maintaining a static list of remote instances, miners discover each other dynamically:

1. **Capability Announcement:** Every miner broadcasts its hardware capabilities (backend type, GPU model, VRAM, supported models, CPU threads) via `MINER_REGISTER` gossip messages.
2. **DHT Propagation:** The Kademlia DHT routes these announcements through the network, populating each node's routing table with mining peers.
3. **Live Registry:** Each node maintains a [`MiningPeerInfo`](../tokencoin/network/mining_p2p.py:30) registry with reputation scores, hardware specs, and last-seen timestamps.
4. **Periodic Re-announcement:** Miners re-announce their presence every 2 minutes to keep the registry fresh.
5. **Dead Peer Eviction:** Peers not seen for 30 minutes are automatically removed from the registry.

#### 5.4.2 Job Distribution Flow

```
┌──────────────────────────────────────────────────────────────────┐
│                    P2P Mining Subnet                             │
│                                                                  │
│  ┌──────────────┐    JOB_ANNOUNCE (gossip)    ┌──────────────┐  │
│  │              │ ──────────────────────────►  │              │  │
│  │  Requester   │                              │  Miner A     │  │
│  │  (creates    │ ◄──────────────────────────  │  (claims     │  │
│  │   job)       │    JOB_CLAIM (first wins)    │   job)       │  │
│  │              │                              │              │  │
│  │              │ ◄──────────────────────────  │              │  │
│  │              │    JOB_RESULT + verification │              │  │
│  └──────────────┘                              └──────────────┘  │
│                                                                  │
│  All communication via gossip protocol over Kademlia DHT         │
│  No central server, no bootstrap nodes, no static lists          │
└──────────────────────────────────────────────────────────────────┘
```

1. **Job Announcement:** Any node can create a [`MiningSubnetJob`](../tokencoin/network/mining_p2p.py:148) and broadcast it to the subnet via `JOB_ANNOUNCE` gossip.
2. **First-Come, First-Served:** Miners monitor the gossip stream for jobs matching their model. The first miner to broadcast a `JOB_CLAIM` wins the job.
3. **Result Submission:** The winning miner processes the inference and submits the result via `JOB_RESULT` gossip.
4. **Reputation Update:** Successful completions increase the miner's peer score. Failed or invalid results decrease it.

#### 5.4.3 Peer Scoring and Sybil Resistance

Each miner maintains a reputation score for every peer:

- **Score Range:** 0.0 (untrusted) to 1.0 (fully trusted). Default: 0.5.
- **Score Increases:** +0.05 per successful job completion, +0.01 per valid registration.
- **Score Decreases:** -0.1 per invalid registration or failed job.
- **Automatic Bans:** Peers with excessive failures are temporarily banned.
- **Minimum Score Filter:** The `p2p_min_peer_score` config option (default: 0.0) lets nodes reject low-reputation miners.

#### 5.4.4 Integration with Consensus Engine

The [`ConsensusEngine`](../tokencoin/consensus/__init__.py:684) integrates the P2P subnet via `set_p2p_subnet()`:

- **`start_mining()`:** Starts the P2P subnet and registers local miner capabilities.
- **`mine_block()`:** First checks for pending jobs from the subnet. If found, processes and returns results. If not, creates a local job and announces it to the subnet for other miners.
- **`get_mining_stats()`:** Includes P2P subnet status (known miners, alive miners, pending/claimed/completed jobs).

#### 5.4.5 Supported Mining Topologies

- **Local Mining:** Run Ollama directly on your machine (CPU, GPU, or Apple Silicon)
- **P2P Remote Mining:** Miners discovered automatically via the DHT — no manual configuration needed
- **Docker Deployment:** Deploy Ollama via Docker for isolated, scalable mining
- **Unified API:** All mining nodes contribute to a single, public OpenAI-compatible endpoint

## **6\. User Interface & Wallet Design**

The TokenCoin client is a unified, user-friendly desktop and mobile application written in Flutter/Electron with a local Rust back-end to handle heavy cryptographic lifting.

### **6.1 The Interface Experience**

* **Dashboard Tab:** A minimalist layout showing available balance, locked balance, and recent peer-to-peer activities.  
* **Mining Tab ("Earn"):** A simple one-click toggle: **\[ Start AI Mining \]**. Underneath, it displays a clean visualization of current hardware (CPU/GPU), model being served (e.g., phi3-mini, llama3.2-3b), and current TKC generation rate.  
* **Send/Receive Tab:** Features a clean input field natively validating the 56-character Tor-based addresses, omitting any need to understand onion routing behind the scenes.
* **Export/Import Tab** Exports and imports wallets via the private key and such.

## **7\. Security and Technical Risks**


| Risk | Description | Mitigation |
| :---- | :---- | :---- |
| **Sybil AI Spoofing** | Miners manipulating software to pretend they ran an Ollama model without using actual compute power. | **Deterministic Tensor Verification.** Periodically challenge nodes with identical seeds; mismatches result in instant block disqualification. |
| **Tor Latency** | Onion routing naturally introduces latency, potentially stalling block times. | Block times are targets for a generous **5 minutes**, with transactions held in localized mempools before final anchoring. |
| **Memory Bottlenecks** | Running local models requires substantial RAM/VRAM, which might alienate casual users. | The orchestrator dynamically selects models based on available memory. Small embedding models (all-minilm, nomic-embed-text) work on as little as 1GB RAM. CPU mining is fully supported. |


## **8\. Important things not to forget.**

* .gitignore (put any private files and junk here, such as installed 3rd party dependencies within project structure and .env - but not any equivalent to stuff such as (but not limited to): package.json, index.js, or main.py)
* Create it in a combination of Python3 and optionally C++ (if it helps)
