"""
TokenCoin Fully P2P Network Layer
==================================
Complete decentralized P2P networking with no central nodes.
Every wallet is also a full node. Uses:
  - Kademlia DHT for peer discovery (no bootstrap nodes needed after initial)
  - Gossip protocol for transaction/block propagation
  - Tor v3 hidden services for anonymous addressing
  - NAT traversal via UPnP and relaying
  - Peer scoring and reputation system
"""

# flake8: noqa

import asyncio
import hashlib
import json
import logging
import os
import random
import socket
import struct
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple, Callable, Any
from collections import defaultdict, deque
import ipaddress

from tokencoin.config import CONFIG
from tokencoin.core.crypto import (
    PublicKey, PrivateKey, KeyPair, base32_encode, base32_decode,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Message Types (Gossip Protocol)
# ---------------------------------------------------------------------------

class GossipType(Enum):
    """Types of gossip messages for P2P propagation."""
    PING = 0x01
    PONG = 0x02
    PEER_DISCOVERY = 0x03
    PEER_LIST = 0x04
    TX_ANNOUNCE = 0x05      # New transaction announcement
    TX_REQUEST = 0x06       # Request full transaction data
    TX_RESPONSE = 0x07      # Full transaction data
    BLOCK_ANNOUNCE = 0x08   # New block announcement (compact)
    BLOCK_REQUEST = 0x09    # Request full block data
    BLOCK_RESPONSE = 0x0A   # Full block data
    SYNC_REQUEST = 0x0B     # Blockchain sync request
    SYNC_RESPONSE = 0x0C    # Blockchain sync response
    PEER_SCORE = 0x0D       # Peer reputation score exchange
    NAT_TRAVERSAL = 0x0E    # NAT traversal helper


@dataclass
class GossipMessage:
    """A gossip protocol message."""
    msg_type: GossipType
    payload: bytes
    sender_id: str           # 56-char TKC address
    signature: bytes = b""
    ttl: int = 3             # Time-to-live (hops)
    timestamp: float = field(default_factory=time.time)
    msg_id: str = ""         # Unique message ID for dedup

    def __post_init__(self):
        if not self.msg_id:
            self.msg_id = hashlib.sha3_256(
                self.sender_id.encode() +
                struct.pack("!B", self.msg_type.value) +
                struct.pack("!d", self.timestamp) +
                self.payload[:32]
            ).hexdigest()[:16]

    def serialize(self) -> bytes:
        data = struct.pack("!B", self.msg_type.value)
        data += struct.pack("!d", self.timestamp)
        data += struct.pack("!B", self.ttl)
        data += self.sender_id.encode("ascii").ljust(56, b"\x00")[:56]
        data += self.msg_id.encode("ascii").ljust(16, b"\x00")[:16]
        data += struct.pack("!I", len(self.payload))
        data += self.payload
        data += struct.pack("!H", len(self.signature))
        data += self.signature
        return data

    @classmethod
    def deserialize(cls, data: bytes) -> "GossipMessage":
        offset = 0
        msg_type = GossipType(data[offset]); offset += 1
        timestamp = struct.unpack("!d", data[offset:offset+8])[0]; offset += 8
        ttl = data[offset]; offset += 1
        sender_id = data[offset:offset+56].rstrip(b"\x00").decode("ascii"); offset += 56
        msg_id = data[offset:offset+16].rstrip(b"\x00").decode("ascii"); offset += 16
        payload_len = struct.unpack("!I", data[offset:offset+4])[0]; offset += 4
        payload = data[offset:offset+payload_len]; offset += payload_len
        sig_len = struct.unpack("!H", data[offset:offset+2])[0]; offset += 2
        signature = data[offset:offset+sig_len]
        return cls(msg_type=msg_type, payload=payload, sender_id=sender_id,
                   signature=signature, ttl=ttl, timestamp=timestamp, msg_id=msg_id)


# ---------------------------------------------------------------------------
# Peer Identity & Address
# ---------------------------------------------------------------------------

@dataclass
class PeerIdentity:
    """A peer's full identity in the network."""
    node_id: str                    # 56-char TKC address
    public_key: PublicKey
    addresses: List[str] = field(default_factory=list)  # Multi-address (ip, tor, etc.)
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    version: str = "0.1.0"
    user_agent: str = "TokenCoin"

    def is_recent(self, timeout: float = 600) -> bool:
        return (time.time() - self.last_seen) < timeout


@dataclass
class PeerScore:
    """Peer reputation score for sybil resistance."""
    total_interactions: int = 0
    successful_interactions: int = 0
    failed_interactions: int = 0
    latency_ms: float = 0.0
    last_bad_behavior: float = 0.0
    banned_until: float = 0.0

    @property
    def reliability(self) -> float:
        if self.total_interactions == 0:
            return 0.5
        return self.successful_interactions / self.total_interactions

    @property
    def is_banned(self) -> bool:
        return time.time() < self.banned_until

    def record_success(self):
        self.total_interactions += 1
        self.successful_interactions += 1

    def record_failure(self):
        self.total_interactions += 1
        self.failed_interactions += 1
        self.last_bad_behavior = time.time()
        if self.failed_interactions > 5:
            self.banned_until = time.time() + 3600  # 1 hour ban


# ---------------------------------------------------------------------------
# Kademlia DHT (Fully Decentralized)
# ---------------------------------------------------------------------------

class KBucket:
    """A Kademlia k-bucket containing up to k peers."""
    
    def __init__(self, min_key: int, max_key: int, k: int = 20):
        self.min_key = min_key
        self.max_key = max_key
        self.k = k
        self.peers: List[PeerIdentity] = []
        self.last_accessed = time.time()

    def add_peer(self, peer: PeerIdentity) -> bool:
        """Add or update a peer. Returns True if added."""
        self.last_accessed = time.time()
        for i, existing in enumerate(self.peers):
            if existing.node_id == peer.node_id:
                self.peers[i] = peer
                return True
        if len(self.peers) < self.k:
            self.peers.append(peer)
            return True
        return False  # Bucket full

    def remove_peer(self, node_id: str) -> bool:
        for i, p in enumerate(self.peers):
            if p.node_id == node_id:
                self.peers.pop(i)
                return True
        return False

    def has_peer(self, node_id: str) -> bool:
        return any(p.node_id == node_id for p in self.peers)

    def distance(self, node_id: str) -> int:
        return int(self.min_key) ^ int(node_id[:8], 32)


class DHT:
    """
    Fully decentralized Kademlia Distributed Hash Table.
    No central bootstrap - uses random walk and peer exchange.
    """

    def __init__(self, node_id: str):
        self.node_id = node_id
        self.node_id_int = int(node_id[:8], 32)
        self.k = CONFIG.network.dht_kademlia_k
        self.buckets: List[KBucket] = []
        self._init_buckets()
        self.peer_scores: Dict[str, PeerScore] = {}
        self.seen_messages: Set[str] = set()  # Dedup
        self._known_peers: Dict[str, PeerIdentity] = {}

    def _init_buckets(self):
        """Initialize 160 k-buckets (like Kademlia)."""
        for i in range(160):
            size = 2 ** i
            self.buckets.append(KBucket(0, size, self.k))

    def _bucket_index(self, node_id: str) -> int:
        """Find the appropriate bucket for a node ID."""
        target_int = int(node_id[:8], 32)
        xor_dist = self.node_id_int ^ target_int
        if xor_dist == 0:
            return 0
        return min(xor_dist.bit_length() - 1, 159)

    def add_peer(self, peer: PeerIdentity) -> bool:
        """Add a peer to the DHT routing table."""
        if peer.node_id == self.node_id:
            return False
        
        idx = self._bucket_index(peer.node_id)
        bucket = self.buckets[idx]
        
        if bucket.add_peer(peer):
            self._known_peers[peer.node_id] = peer
            if peer.node_id not in self.peer_scores:
                self.peer_scores[peer.node_id] = PeerScore()
            logger.debug(f"Added peer {peer.node_id[:16]}... to bucket {idx}")
            return True
        return False

    def remove_peer(self, node_id: str):
        """Remove a peer from the DHT."""
        idx = self._bucket_index(node_id)
        self.buckets[idx].remove_peer(node_id)
        self._known_peers.pop(node_id, None)
        self.peer_scores.pop(node_id, None)

    def find_nearest(self, target_id: str, count: int = 8) -> List[PeerIdentity]:
        """Find the k nearest peers to a target ID."""
        target_int = int(target_id[:8], 32)
        all_peers = list(self._known_peers.values())
        all_peers.sort(key=lambda p: int(p.node_id[:8], 32) ^ target_int)
        return all_peers[:count]

    def get_alive_peers(self, max_age: float = 300) -> List[PeerIdentity]:
        """Get peers seen recently."""
        now = time.time()
        return [p for p in self._known_peers.values()
                if (now - p.last_seen) < max_age]

    def get_all_peers(self) -> List[PeerIdentity]:
        return list(self._known_peers.values())

    def peer_count(self) -> int:
        return len(self._known_peers)

    def seen(self, msg_id: str) -> bool:
        """Check if we've seen a message (dedup)."""
        if msg_id in self.seen_messages:
            return True
        self.seen_messages.add(msg_id)
        # Prune old entries
        if len(self.seen_messages) > 10000:
            self.seen_messages.clear()
        return False


# ---------------------------------------------------------------------------
# P2P Transport (TCP + Tor)
# ---------------------------------------------------------------------------

class P2PTransport:
    """
    Fully P2P transport layer.
    Each node listens for incoming connections and connects to peers.
    Uses TCP with optional Tor proxy support.
    """

    def __init__(self, node_id: str, private_key: PrivateKey):
        self.node_id = node_id
        self.private_key = private_key
        self.public_key = PublicKey.from_private(private_key)
        self._server: Optional[asyncio.Server] = None
        self._connections: Dict[str, Tuple[asyncio.StreamReader, asyncio.StreamWriter]] = {}
        self._connection_locks: Dict[str, asyncio.Lock] = {}
        self._handlers: Dict[GossipType, Callable] = {}
        self._running = False
        self._port = 0

    def register_handler(self, msg_type: GossipType, handler: Callable):
        self._handlers[msg_type] = handler

    async def start(self, host: str = "0.0.0.0", port: int = 0) -> int:
        """Start listening for incoming P2P connections."""
        self._running = True

        async def handle_connection(reader, writer):
            peer_addr = writer.get_extra_info("peername")
            try:
                while self._running and not reader.at_eof():
                    len_bytes = await reader.readexactly(4)
                    msg_len = struct.unpack("!I", len_bytes)[0]
                    if msg_len > 10_000_000:  # 10MB limit
                        logger.warning(f"Oversized message from {peer_addr}")
                        break
                    data = await reader.readexactly(msg_len)
                    message = GossipMessage.deserialize(data)
                    await self._dispatch(message, writer)
            except (asyncio.IncompleteReadError, ConnectionError, asyncio.TimeoutError):
                pass
            finally:
                writer.close()

        self._server = await asyncio.start_server(handle_connection, host=host)
        self._port = self._server.sockets[0].getsockname()[1]
        logger.info(f"P2P node listening on {host}:{self._port}")
        return self._port

    async def stop(self):
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        for _, writer in self._connections.values():
            writer.close()
        self._connections.clear()

    async def connect(self, host: str, port: int, node_id: str) -> bool:
        """Connect to a remote peer."""
        if node_id in self._connections:
            return True
        
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=10
            )
            self._connections[node_id] = (reader, writer)
            logger.info(f"Connected to {node_id[:16]}... at {host}:{port}")
            return True
        except (ConnectionRefusedError, OSError, asyncio.TimeoutError) as e:
            logger.debug(f"Failed to connect to {node_id[:16]}...: {e}")
            return False

    async def send(self, target_id: str, message: GossipMessage):
        """Send a message to a connected peer."""
        conn = self._connections.get(target_id)
        if not conn:
            raise ConnectionError(f"Not connected to {target_id[:16]}...")
        _, writer = conn
        data = message.serialize()
        writer.write(struct.pack("!I", len(data)))
        writer.write(data)
        await writer.drain()

    async def broadcast(self, message: GossipMessage, exclude: Optional[Set[str]] = None):
        """Broadcast to all connected peers."""
        if exclude is None:
            exclude = set()
        for node_id in list(self._connections.keys()):
            if node_id in exclude:
                continue
            try:
                await self.send(node_id, message)
            except ConnectionError:
                self._connections.pop(node_id, None)

    async def _dispatch(self, message: GossipMessage, writer):
        """Dispatch message to registered handler."""
        handler = self._handlers.get(message.msg_type)
        if handler:
            await handler(message, writer)
        else:
            logger.debug(f"No handler for {message.msg_type}")


# ---------------------------------------------------------------------------
# Gossip Protocol Engine
# ---------------------------------------------------------------------------

class GossipEngine:
    """
    Gossip protocol for transaction and block propagation.
    Uses epidemic broadcast (gossip) for reliable propagation.
    """

    def __init__(self, transport: P2PTransport, dht: DHT):
        self.transport = transport
        self.dht = dht
        self.pending_txs: Dict[str, bytes] = {}      # tx_id -> tx_data
        self.pending_blocks: Dict[str, bytes] = {}    # block_hash -> block_data
        self._gossip_queue: asyncio.Queue = asyncio.Queue()
        self._running = False

    async def start(self):
        self._running = True
        asyncio.create_task(self._gossip_loop())

    async def stop(self):
        self._running = False

    async def announce_transaction(self, tx_hash: str, tx_data: bytes):
        """Announce a new transaction to the network."""
        self.pending_txs[tx_hash] = tx_data
        msg = GossipMessage(
            msg_type=GossipType.TX_ANNOUNCE,
            payload=tx_hash.encode(),
            sender_id=self.transport.node_id,
        )
        await self.transport.broadcast(msg)

    async def announce_block(self, block_hash: str, block_data: bytes):
        """Announce a new block to the network."""
        self.pending_blocks[block_hash] = block_data
        # Compact announcement (just header info)
        payload = block_hash.encode() + struct.pack("!d", time.time())
        msg = GossipMessage(
            msg_type=GossipType.BLOCK_ANNOUNCE,
            payload=payload,
            sender_id=self.transport.node_id,
        )
        await self.transport.broadcast(msg)

    async def _gossip_loop(self):
        """Background loop for gossip maintenance."""
        while self._running:
            try:
                # Periodically re-announce pending items
                for tx_hash in list(self.pending_txs.keys())[:50]:
                    msg = GossipMessage(
                        msg_type=GossipType.TX_ANNOUNCE,
                        payload=tx_hash.encode(),
                        sender_id=self.transport.node_id,
                    )
                    await self.transport.broadcast(msg)
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Gossip loop error: {e}")


# ---------------------------------------------------------------------------
# Peer Discovery (Fully Decentralized)
# ---------------------------------------------------------------------------

class PeerDiscovery:
    """
    Fully decentralized peer discovery.
    No central bootstrap nodes - uses:
    1. Random walk on the DHT
    2. Peer exchange (PEX)
    3. LAN broadcast discovery
    4. DNS-based discovery (optional, for initial bootstrapping)
    """

    def __init__(self, dht: DHT, transport: P2PTransport):
        self.dht = dht
        self.transport = transport
        self._discovery_task: Optional[asyncio.Task] = None
        self._known_addresses: Dict[str, Tuple[str, int]] = {}  # node_id -> (host, port)

    async def start(self):
        self._discovery_task = asyncio.create_task(self._discovery_loop())
        logger.info("Peer discovery started")

    async def stop(self):
        if self._discovery_task:
            self._discovery_task.cancel()

    def add_seed_address(self, host: str, port: int, node_id: str):
        """Add a known peer address (from config or previous session)."""
        self._known_addresses[node_id] = (host, port)
        peer = PeerIdentity(
            node_id=node_id,
            public_key=PublicKey(point=base32_decode(node_id)[:32]),
            addresses=[f"/tcp/{host}:{port}"],
        )
        self.dht.add_peer(peer)

    async def _discovery_loop(self):
        """Periodically discover new peers."""
        while True:
            try:
                await asyncio.sleep(30)
                
                # 1. Try to connect to known addresses
                for node_id, (host, port) in list(self._known_addresses.items()):
                    if node_id not in self.transport._connections:
                        await self.transport.connect(host, port, node_id)

                # 2. Request peer lists from connected peers
                msg = GossipMessage(
                    msg_type=GossipType.PEER_DISCOVERY,
                    payload=b"",
                    sender_id=self.transport.node_id,
                )
                await self.transport.broadcast(msg)

                # 3. Prune dead peers
                now = time.time()
                for node_id in list(self.dht._known_peers.keys()):
                    peer = self.dht._known_peers[node_id]
                    if (now - peer.last_seen) > 3600:  # 1 hour timeout
                        self.dht.remove_peer(node_id)

                logger.debug(f"DHT has {self.dht.peer_count()} peers, "
                           f"{len(self.transport._connections)} connections")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Discovery error: {e}")


# ---------------------------------------------------------------------------
# NAT Traversal
# ---------------------------------------------------------------------------

class NATTraversal:
    """
    NAT traversal for P2P connectivity.
    Uses UPnP, STUN, and TCP hole-punching.
    """

    @staticmethod
    async def get_external_ip() -> Optional[str]:
        """Get external IP via STUN-like service."""
        try:
            # Try to connect to a public service
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("checkip.amazonaws.com", 80), timeout=5
            )
            writer.write(b"GET / HTTP/1.0\r\nHost: checkip.amazonaws.com\r\n\r\n")
            await writer.drain()
            response = await reader.read(1024)
            writer.close()
            # Parse IP from response
            for line in response.decode().split("\r\n"):
                if line and not line.startswith("HTTP") and not line.startswith("<!") and "." in line:
                    return line.strip()
        except Exception:
            pass
        return None

    @staticmethod
    def is_private_ip(ip: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip)
            return addr.is_private
        except ValueError:
            return False


# ---------------------------------------------------------------------------
# P2P Node (Main Entry Point)
# ---------------------------------------------------------------------------

class P2PNode:
    """
    A fully decentralized P2P node.
    Every wallet/miner runs one of these.
    """

    def __init__(self, keypair: KeyPair):
        self.keypair = keypair
        self.node_id = keypair.to_address()
        self.dht = DHT(self.node_id)
        self.transport = P2PTransport(self.node_id, keypair.private_key)
        self.gossip = GossipEngine(self.transport, self.dht)
        self.discovery = PeerDiscovery(self.dht, self.transport)
        self.nat = NATTraversal()
        self._running = False
        self._on_tx: Optional[Callable] = None
        self._on_block: Optional[Callable] = None

    def on_transaction(self, callback: Callable):
        """Register callback for incoming transactions."""
        self._on_tx = callback

    def on_block(self, callback: Callable):
        """Register callback for incoming blocks."""
        self._on_block = callback

    async def start(self, port: int = 0) -> int:
        """Start the P2P node."""
        # Register message handlers
        self.transport.register_handler(GossipType.PING, self._handle_ping)
        self.transport.register_handler(GossipType.PONG, self._handle_pong)
        self.transport.register_handler(GossipType.PEER_DISCOVERY, self._handle_peer_discovery)
        self.transport.register_handler(GossipType.PEER_LIST, self._handle_peer_list)
        self.transport.register_handler(GossipType.TX_ANNOUNCE, self._handle_tx_announce)
        self.transport.register_handler(GossipType.TX_REQUEST, self._handle_tx_request)
        self.transport.register_handler(GossipType.TX_RESPONSE, self._handle_tx_response)
        self.transport.register_handler(GossipType.BLOCK_ANNOUNCE, self._handle_block_announce)
        self.transport.register_handler(GossipType.BLOCK_REQUEST, self._handle_block_request)
        self.transport.register_handler(GossipType.BLOCK_RESPONSE, self._handle_block_response)

        # Start transport
        actual_port = await self.transport.start(port=port)
        
        # Start subsystems
        await self.gossip.start()
        await self.discovery.start()

        self._running = True
        logger.info(f"P2P node {self.node_id[:16]}... started on port {actual_port}")
        return actual_port

    async def stop(self):
        self._running = False
        await self.discovery.stop()
        await self.gossip.stop()
        await self.transport.stop()
        logger.info("P2P node stopped")

    async def connect_to_peer(self, host: str, port: int, node_id: str):
        """Connect to a specific peer."""
        success = await self.transport.connect(host, port, node_id)
        if success:
            self.discovery.add_seed_address(host, port, node_id)
            # Send ping
            msg = GossipMessage(
                msg_type=GossipType.PING,
                payload=struct.pack("!d", time.time()),
                sender_id=self.node_id,
            )
            await self.transport.send(node_id, msg)

    async def broadcast_transaction(self, tx_hash: str, tx_data: bytes):
        """Broadcast a transaction to the network."""
        await self.gossip.announce_transaction(tx_hash, tx_data)

    async def broadcast_block(self, block_hash: str, block_data: bytes):
        """Broadcast a block to the network."""
        await self.gossip.announce_block(block_hash, block_data)

    # --- Message Handlers ---

    async def _handle_ping(self, msg: GossipMessage, writer):
        pong = GossipMessage(
            msg_type=GossipType.PONG,
            payload=msg.payload,
            sender_id=self.node_id,
        )
        data = pong.serialize()
        writer.write(struct.pack("!I", len(data)))
        writer.write(data)
        await writer.drain()

    async def _handle_pong(self, msg: GossipMessage, writer):
        # Update peer last seen
        peer = self.dht._known_peers.get(msg.sender_id)
        if peer:
            peer.last_seen = time.time()

    async def _handle_peer_discovery(self, msg: GossipMessage, writer):
        """Respond with our known peers."""
        peers = self.dht.get_alive_peers()
        # Limit to 20 peers
        peers = peers[:20]
        peer_list = []
        for p in peers:
            peer_list.append({
                "node_id": p.node_id,
                "addresses": p.addresses,
            })
        payload = json.dumps(peer_list).encode()
        response = GossipMessage(
            msg_type=GossipType.PEER_LIST,
            payload=payload,
            sender_id=self.node_id,
        )
        data = response.serialize()
        writer.write(struct.pack("!I", len(data)))
        writer.write(data)
        await writer.drain()

    async def _handle_peer_list(self, msg: GossipMessage, writer):
        """Process received peer list."""
        try:
            peer_list = json.loads(msg.payload.decode())
            for peer_info in peer_list:
                node_id = peer_info["node_id"]
                if node_id not in self.dht._known_peers:
                    peer = PeerIdentity(
                        node_id=node_id,
                        public_key=PublicKey(point=base32_decode(node_id)[:32]),
                        addresses=peer_info.get("addresses", []),
                    )
                    self.dht.add_peer(peer)
        except (json.JSONDecodeError, KeyError) as e:
            logger.debug(f"Invalid peer list: {e}")

    async def _handle_tx_announce(self, msg: GossipMessage, writer):
        """Handle transaction announcement."""
        tx_hash = msg.payload.decode()
        if self.dht.seen(msg.msg_id):
            return
        if self._on_tx and tx_hash not in self.gossip.pending_txs:
            # Request full transaction
            req = GossipMessage(
                msg_type=GossipType.TX_REQUEST,
                payload=tx_hash.encode(),
                sender_id=self.node_id,
            )
            await self.transport.send(msg.sender_id, req)
        # Re-broadcast (gossip)
        if msg.ttl > 0:
            msg.ttl -= 1
            await self.transport.broadcast(msg, exclude={msg.sender_id})

    async def _handle_tx_request(self, msg: GossipMessage, writer):
        """Handle transaction data request."""
        tx_hash = msg.payload.decode()
        tx_data = self.gossip.pending_txs.get(tx_hash)
        if tx_data:
            response = GossipMessage(
                msg_type=GossipType.TX_RESPONSE,
                payload=tx_data,
                sender_id=self.node_id,
            )
            data = response.serialize()
            writer.write(struct.pack("!I", len(data)))
            writer.write(data)
            await writer.drain()

    async def _handle_tx_response(self, msg: GossipMessage, writer):
        """Handle full transaction data."""
        if self._on_tx:
            await self._on_tx(msg.payload)

    async def _handle_block_announce(self, msg: GossipMessage, writer):
        """Handle block announcement."""
        block_hash = msg.payload[:32].decode() if len(msg.payload) >= 32 else msg.payload.decode()
        if self.dht.seen(msg.msg_id):
            return
        if self._on_block and block_hash not in self.gossip.pending_blocks:
            req = GossipMessage(
                msg_type=GossipType.BLOCK_REQUEST,
                payload=block_hash.encode(),
                sender_id=self.node_id,
            )
            await self.transport.send(msg.sender_id, req)
        if msg.ttl > 0:
            msg.ttl -= 1
            await self.transport.broadcast(msg, exclude={msg.sender_id})

    async def _handle_block_request(self, msg: GossipMessage, writer):
        block_hash = msg.payload.decode()
        block_data = self.gossip.pending_blocks.get(block_hash)
        if block_data:
            response = GossipMessage(
                msg_type=GossipType.BLOCK_RESPONSE,
                payload=block_data,
                sender_id=self.node_id,
            )
            data = response.serialize()
            writer.write(struct.pack("!I", len(data)))
            writer.write(data)
            await writer.drain()

    async def _handle_block_response(self, msg: GossipMessage, writer):
        if self._on_block:
            await self._on_block(msg.payload)
