## Ideas

Put ideas and concepts that are still a bit "rough sketch" style here.

## Ideas list

### C++ Extension for EC Operations
The current Python reference implementation uses hash-based stand-ins for elliptic curve operations. For production, we need a C++ extension (or use libsodium bindings) for:
- Ed25519 scalar multiplication
- Pedersen commitment point operations
- Ring signature MLSAG implementation
- Bulletproofs range proofs

### Ollama Distributed Mining
The orchestrator now uses Ollama for distributed mining. Production considerations:
- Multiple remote Ollama instance coordination
- Load balancing across instances
- Graceful degradation when instances go offline
- Resource limits per instance (CPU threads, GPU selection)
- Auto-scaling with Docker Compose/Kubernetes

### Tor Integration
Addresses are derived from Tor v3 onion addresses. Production needs:
- `stem` library integration for Tor control port
- Automatic hidden service creation
- Tor circuit management for P2P connections
- Bandwidth monitoring

### Flutter/Electron UI
The design doc specifies a unified desktop/mobile app. Consider:
- Flutter for cross-platform (desktop + mobile)
- Local Rust backend for crypto (via FFI)
- WebSocket connection to local Python node
- Real-time mining visualization with charts

### Network Stress Testing
Before mainnet:
- Simulate 1000+ nodes on testnet
- Measure block propagation times
- Test DHT resilience under churn
- Benchmark Tor circuit establishment

### Monetary Policy Refinements
The current policy is basic. Consider:
- Tail emission (like Monero) for long-term security
- Dynamic block reward based on inference demand
- Treasury/development fund allocation
- Burn mechanism for fee market
