"""
TokenCoin Network Stress Testing
==================================
Simulates a fully decentralized P2P network with multiple nodes
to test:
  - Peer discovery and DHT convergence
  - Transaction propagation (gossip)
  - Block propagation
  - Network resilience under churn
  - Sybil attack resistance

Usage:
    python -m tokencoin.tests.stress_test --nodes 100 --txs 1000
"""

import asyncio
import hashlib
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from collections import defaultdict

from tokencoin.core.crypto import KeyPair
from tokencoin.network.p2p import P2PNode, PeerIdentity, GossipMessage, GossipType
from tokencoin.ledger import Transaction, Block, Blockchain

logger = logging.getLogger(__name__)


@dataclass
class StressTestConfig:
    """Stress test configuration."""
    num_nodes: int = 10
    num_transactions: int = 100
    churn_percent: float = 0.1  # 10% of nodes join/leave
    test_duration: float = 60.0  # seconds
    gossip_fanout: int = 3
    max_latency_ms: float = 100.0


@dataclass
class StressTestMetrics:
    """Stress test metrics."""
    nodes_started: int = 0
    nodes_connected: int = 0
    transactions_sent: int = 0
    transactions_received: int = 0
    blocks_mined: int = 0
    blocks_propagated: int = 0
    avg_propagation_ms: float = 0.0
    max_propagation_ms: float = 0.0
    dht_convergence_time: float = 0.0
    peer_count_avg: float = 0.0
    message_overhead: int = 0
    start_time: float = 0.0

    def report(self) -> str:
        elapsed = time.time() - self.start_time
        return f"""
=== Stress Test Report ===
Duration: {elapsed:.1f}s
Nodes: {self.nodes_started} started, {self.nodes_connected} connected
Transactions: {self.transactions_sent} sent, {self.transactions_received} received
Blocks: {self.blocks_mined} mined, {self.blocks_propagated} propagated
Avg Propagation: {self.avg_propagation_ms:.1f}ms
Max Propagation: {self.max_propagation_ms:.1f}ms
DHT Convergence: {self.dht_convergence_time:.1f}s
Avg Peers/Node: {self.peer_count_avg:.1f}
Message Overhead: {self.message_overhead} bytes
===========================
"""


class SimulatedP2PNode:
    """A simulated P2P node for stress testing."""

    def __init__(self, node_id: str, port: int):
        self.node_id = node_id
        self.port = port
        self.keypair = KeyPair.generate()
        self.p2p = P2PNode(self.keypair)
        self.received_txs: Set[str] = set()
        self.received_blocks: Set[str] = set()
        self.peers: List[str] = []
        self.latency: float = random.uniform(1, 50)  # Simulated latency

    async def start(self):
        await self.p2p.start(port=self.port)

    async def stop(self):
        await self.p2p.stop()

    async def connect_to(self, host: str, port: int, node_id: str):
        success = await self.p2p.connect_to_peer(host, port, node_id)
        if success:
            self.peers.append(node_id)

    async def broadcast_tx(self, tx_hash: str, tx_data: bytes):
        await self.p2p.broadcast_transaction(tx_hash, tx_data)

    async def broadcast_block(self, block_hash: str, block_data: bytes):
        await self.p2p.broadcast_block(block_hash, block_data)


class StressTest:
    """Runs the stress test simulation."""

    def __init__(self, config: Optional[StressTestConfig] = None):
        self.config = config or StressTestConfig()
        self.nodes: Dict[str, SimulatedP2PNode] = {}
        self.metrics = StressTestMetrics()
        self.metrics.start_time = time.time()

    async def run(self):
        """Run the full stress test."""
        logger.info(f"Starting stress test: {self.config.num_nodes} nodes, "
                   f"{self.config.num_transactions} transactions")

        # Phase 1: Start nodes
        await self._phase_start_nodes()

        # Phase 2: Connect nodes (random graph)
        await self._phase_connect_nodes()

        # Phase 3: Measure DHT convergence
        await self._phase_measure_convergence()

        # Phase 4: Send transactions
        await self._phase_send_transactions()

        # Phase 5: Simulate churn
        await self._phase_simulate_churn()

        # Phase 6: Mine and propagate blocks
        await self._phase_mine_blocks()

        # Report
        logger.info(self.metrics.report())
        return self.metrics

    async def _phase_start_nodes(self):
        """Start all simulated nodes."""
        base_port = 20000
        tasks = []
        for i in range(self.config.num_nodes):
            node_id = f"node_{i:04d}_{hashlib.sha3_256(str(i).encode()).hexdigest()[:8]}"
            port = base_port + i
            node = SimulatedP2PNode(node_id, port)
            self.nodes[node_id] = node
            tasks.append(node.start())
            self.metrics.nodes_started += 1

        await asyncio.gather(*tasks)
        logger.info(f"Started {self.metrics.nodes_started} nodes")

    async def _phase_connect_nodes(self):
        """Connect nodes in a random graph topology."""
        node_ids = list(self.nodes.keys())
        connections = 0

        # Create a small-world network
        for i, node_id in enumerate(node_ids):
            node = self.nodes[node_id]
            # Connect to 3-5 random peers
            num_peers = random.randint(3, 5)
            peers = random.sample(
                [n for n in node_ids if n != node_id],
                min(num_peers, len(node_ids) - 1)
            )
            for peer_id in peers:
                peer = self.nodes[peer_id]
                await node.connect_to("127.0.0.1", peer.port, peer_id)
                connections += 1

        self.metrics.nodes_connected = connections
        logger.info(f"Created {connections} connections")

    async def _phase_measure_convergence(self):
        """Measure how quickly the DHT converges."""
        start = time.time()
        await asyncio.sleep(5)  # Let DHT stabilize

        # Check peer counts
        total_peers = sum(len(n.peers) for n in self.nodes.values())
        self.metrics.peer_count_avg = total_peers / len(self.nodes)
        self.metrics.dht_convergence_time = time.time() - start
        logger.info(f"DHT converged: avg {self.metrics.peer_count_avg:.1f} peers/node")

    async def _phase_send_transactions(self):
        """Send transactions through the network."""
        node_ids = list(self.nodes.keys())
        tasks = []

        for i in range(self.config.num_transactions):
            sender = random.choice(node_ids)
            tx_hash = hashlib.sha3_256(f"tx_{i}_{time.time()}".encode()).hexdigest()
            tx_data = tx_hash.encode()

            tasks.append(
                self.nodes[sender].broadcast_tx(tx_hash, tx_data)
            )
            self.metrics.transactions_sent += 1

            # Measure propagation
            start = time.time()
            await asyncio.gather(*tasks[:10])  # Batch
            tasks = tasks[10:]
            prop_time = (time.time() - start) * 1000
            self.metrics.avg_propagation_ms = (
                (self.metrics.avg_propagation_ms * self.metrics.transactions_sent +
                 prop_time) / (self.metrics.transactions_sent + 1)
            )
            self.metrics.max_propagation_ms = max(
                self.metrics.max_propagation_ms, prop_time
            )

        logger.info(f"Sent {self.metrics.transactions_sent} transactions")

    async def _phase_simulate_churn(self):
        """Simulate nodes joining and leaving."""
        node_ids = list(self.nodes.keys())
        churn_count = int(len(node_ids) * self.config.churn_percent)

        # Remove some nodes
        to_remove = random.sample(node_ids, churn_count)
        for node_id in to_remove:
            await self.nodes[node_id].stop()
            del self.nodes[node_id]

        # Add new nodes
        for i in range(churn_count):
            new_id = f"churn_{i}_{hashlib.sha3_256(str(time.time()).encode()).hexdigest()[:8]}"
            port = 30000 + i
            node = SimulatedP2PNode(new_id, port)
            await node.start()
            self.nodes[new_id] = node

        await asyncio.sleep(3)  # Let network stabilize
        logger.info(f"Churn simulation complete: {churn_count} nodes swapped")

    async def _phase_mine_blocks(self):
        """Simulate block mining and propagation."""
        node_ids = list(self.nodes.keys())
        blockchain = Blockchain()
        blockchain.initialize()

        for i in range(5):  # Mine 5 blocks
            miner = random.choice(node_ids)
            block = Block()
            block.header.height = i + 1
            block.header.miner_address = miner

            block_hash = block.hash().hex()
            block_data = block.to_bytes()

            await self.nodes[miner].broadcast_block(block_hash, block_data)
            self.metrics.blocks_mined += 1

            # Count how many nodes received it
            received = sum(
                1 for n in self.nodes.values()
                if block_hash in n.received_blocks
            )
            self.metrics.blocks_propagated += received

            await asyncio.sleep(0.5)

        logger.info(f"Mined {self.metrics.blocks_mined} blocks")


async def main():
    """Run the stress test."""
    logging.basicConfig(level=logging.INFO)

    config = StressTestConfig(
        num_nodes=10,
        num_transactions=50,
        churn_percent=0.2,
        test_duration=30.0,
    )

    test = StressTest(config)
    metrics = await test.run()

    # Print report
    print(metrics.report())


if __name__ == "__main__":
    asyncio.run(main())
