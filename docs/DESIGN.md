# **System Design Document: TokenCoin (TKC)**

**Author:** Sammy Lord  
**Status:** Implementation Document  
**Date:** July 2026

## **1\. Executive Summary**

**TokenCoin (TKC)** is a next-generation, privacy-first cryptocurrency that fuses decentralized AI inference with private financial transactions. Instead of wasting energy on arbitrary Proof-of-Work (PoW) hashes, TokenCoin utilizes **Proof-of-Useful-Work (PoUW)**. Miners contribute computational power to a global, decentralized cluster of **Ollama** instances — supporting CPU, NVIDIA GPU (CUDA), AMD GPU (ROCm), and Apple Silicon (Metal).
Financially, TokenCoin enforces strict, untraceable anonymity inspired by Monero, utilizing a heavily modified ring signature and stealth address protocol natively mapped to isolated onion-routing network layers.

## **2\. Architecture Overview**

TokenCoin's architecture consists of three core layers interacting in parallel:

1. **The Network & Routing Layer:** Governs node discovery, communication, and native anonymous addressing.  
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
+-------------------------------------------------------------+
                            |
        +-------------------+-------------------+
        |                                       |
        v                                       v
+------------------------------+ +------------------------------+
| Ledger Layer (Privacy)       | | Consensus Layer (AI/Ollama)  |
| - RingCT & Stealth Addresses | | - Proof-of-Useful-Work       |
| - Single-hop Visibility      | | - Distributed Ollama Cluster |
+------------------------------+ +------------------------------+
```
4. Should dynamically digitally "print" money while mining - in a fair manner without bias.
       * We should start out with a "base amount" of 10T we can ever mine, starting printing at around 6.4B
       * Print dynamically yet fairly distributed so it never runs out
       * Start out with a block reward of 12TKC, go down from there.

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

* The node registers its hardware capabilities (CPU cores, RAM, GPU type/VRAM) to the decentralized public cluster via a DHT (Distributed Hash Table).
* The cluster serves public-facing AI requests (LLMs, vision models, embedding models) via a unified OpenAI-compatible API.
* Supports CPU-only mining, NVIDIA GPU (CUDA), AMD GPU (ROCm), and Apple Silicon (Metal).

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
    |                              +--> Broadcast job via P2P gossip
    |                                       |
    |                                       v
    |                              Mining Network (DHT)
    |                              +--> Miner A claims job
    |                              +--> Miner B claims job
    |                              +--> Miner C claims job
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

TokenCoin supports a fully distributed mining topology:

- **Local Mining:** Run Ollama directly on your machine (CPU, GPU, or Apple Silicon)
- **Remote Instances:** Connect to remote Ollama servers for distributed mining
- **P2P Job Distribution:** Inference jobs are broadcast via the Kademlia DHT gossip protocol. Miners claim jobs and submit results back through the network.
- **Docker Deployment:** Deploy Ollama via Docker for isolated, scalable mining
- **Auto-Discovery:** The DHT automatically discovers and registers mining nodes
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
