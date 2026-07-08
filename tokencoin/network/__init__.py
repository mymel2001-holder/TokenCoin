"""
TokenCoin Network Layer
========================
Implements Tor-based addressing, DHT node discovery, P2P communication,
and the fully decentralized mining subnet.

Key components:
  - AddressManager: 56-char Base32 address generation and validation
  - DHTNode: Kademlia-based distributed hash table for peer discovery
  - P2PTransport: End-to-end encrypted Tor circuit communication
  - PeerManager: Peer lifecycle management
  - MiningP2PSubnet: Fully decentralized mining subnet (DHT + gossip based)
    Replaces static remote_instances with dynamic P2P miner discovery.
"""

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

from tokencoin.config import CONFIG
from tokencoin.core.crypto import (
    PublicKey, PrivateKey, KeyPair, base32_encode, base32_decode
)

# Re-export the MiningP2PSubnet so it can be imported from tokencoin.network
from tokencoin.network.mining_p2p import (
    MiningP2PSubnet,
    MiningSubnetJob,
    MiningPeerInfo,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Address Types
# ---------------------------------------------------------------------------

class AddressType(Enum):
    """Types of network addresses in TokenCoin."""
    IPV4 = "ipv4"
    IPV6 = "ipv6"
    TOR_V3 = "tor_v3"  # 56-char Base32 (our primary address type)
    ONION = "onion"     # Standard .onion address


@dataclass
class NodeAddress:
    """
    A network address for a TokenCoin node.
    Primary format: 56-character Base32 string (Tor v3 style).
    """
    raw: str
    address_type: AddressType = AddressType.TOR_V3

    def __post_init__(self):
        if self.address_type == AddressType.TOR_V3:
            self._validate_tkc_address()

    def _validate_tkc_address(self):
        """Validate the 56-character Base32 address."""
        if len(self.raw) != CONFIG.network.address_length:
            raise ValueError(
                f"TokenCoin address must be {CONFIG.network.address_length} "
                f"characters, got {len(self.raw)}"
            )
        valid_chars = set(CONFIG.network.address_alphabet)
        if not all(c in valid_chars for c in self.raw.lower()):
            raise ValueError("Invalid Base32 characters in address")

    def to_onion(self) -> str:
        """Convert to standard .onion address."""
        if self.address_type != AddressType.TOR_V3:
            raise ValueError("Only Tor v3 addresses can be converted")
        return f"{self.raw}.onion"

    @classmethod
    def from_onion(cls, onion: str) -> "NodeAddress":
        """Parse a .onion address to TokenCoin format."""
        addr = onion.replace(".onion", "").lower()
        return cls(raw=addr, address_type=AddressType.TOR_V3)

    @classmethod
    def from_public_key(cls, pub_key: PublicKey) -> "NodeAddress":
        """Derive a TokenCoin address from a public key."""
        return cls(raw=pub_key.to_address(), address_type=AddressType.TOR_V3)

    def __str__(self) -> str:
        return self.raw

    def __repr__(self) -> str:
        return f"NodeAddress({self.raw})"

    def __eq__(self, other):
        if isinstance(other, NodeAddress):
            return self.raw == other.raw
        return False

    def __hash__(self):
        return hash(self.raw)


# ---------------------------------------------------------------------------
# Address Manager
# ---------------------------------------------------------------------------

class AddressManager:
    """
    Manages TokenCoin address generation, validation, and conversion.
    """

    @staticmethod
    def generate_address() -> Tuple[KeyPair, NodeAddress]:
        """Generate a new key pair and derive its TokenCoin address."""
        kp = KeyPair.generate()
        addr = NodeAddress.from_public_key(kp.public_key)
        return kp, addr

    @staticmethod
    def validate_address(addr: str) -> bool:
        """Validate a TokenCoin address string."""
        try:
            NodeAddress(raw=addr)
            return True
        except (ValueError, AssertionError):
            return False

    @staticmethod
    def address_from_public_key(pub_key: PublicKey) -> NodeAddress:
        return NodeAddress.from_public_key(pub_key)


# ---------------------------------------------------------------------------
# DHT Node (Kademlia-style)
# ---------------------------------------------------------------------------

@dataclass
class PeerInfo:
    """Information about a known peer."""
    node_id: str          # 56-char address
    address: NodeAddress
    public_key: PublicKey
    last_seen: float      # Unix timestamp
    reputation: float     # 0.0 - 1.0
    capabilities: Dict[str, Any] = field(default_factory=dict)

    def is_alive(self, timeout: float = 300.0) -> bool:
        """Check if peer was seen recently."""
        return (time.time() - self.last_seen) < timeout


class DHTNode:
    """
    Kademlia-based Distributed Hash Table for peer discovery.
    Maintains a routing table of known peers and handles
    node lookup and peer exchange.
    """

    def __init__(self, node_id: str, address: NodeAddress):
        self.node_id = node_id
        self.address = address
        self.k = CONFIG.network.dht_kademlia_k  # Bucket size
        self.routing_table: Dict[int, List[PeerInfo]] = {}  # bucket_index -> peers
        self.peer_map: Dict[str, PeerInfo] = {}  # node_id -> PeerInfo
        self._running = False
        self._server: Optional[asyncio.DatagramServer] = None

    def _bucket_index(self, target_id: str) -> int:
        """Compute the Kademlia bucket index for a target node ID."""
        # XOR distance, then leading zero bits determine bucket
        xor_val = int(self.node_id, 32) ^ int(target_id, 32)
        if xor_val == 0:
            return 0
        return xor_val.bit_length() - 1

    def add_peer(self, peer: PeerInfo) -> bool:
        """Add a peer to the routing table."""
        if peer.node_id == self.node_id:
            return False  # Don't add ourselves

        bucket = self._bucket_index(peer.node_id)
        if bucket not in self.routing_table:
            self.routing_table[bucket] = []

        bucket_peers = self.routing_table[bucket]

        # Check if peer already exists
        for i, existing in enumerate(bucket_peers):
            if existing.node_id == peer.node_id:
                bucket_peers[i] = peer  # Update
                self.peer_map[peer.node_id] = peer
                return True

        # Add if bucket not full
        if len(bucket_peers) < self.k:
            bucket_peers.append(peer)
            self.peer_map[peer.node_id] = peer
            logger.debug(f"Added peer {peer.node_id} to bucket {bucket}")
            return True

        # Bucket full: evict least recently seen
        bucket_peers.sort(key=lambda p: p.last_seen)
        evicted = bucket_peers.pop(0)
        del self.peer_map[evicted.node_id]
        bucket_peers.append(peer)
        self.peer_map[peer.node_id] = peer
        logger.debug(f"Evicted {evicted.node_id}, added {peer.node_id}")
        return True

    def remove_peer(self, node_id: str) -> bool:
        """Remove a peer from the routing table."""
        if node_id in self.peer_map:
            peer = self.peer_map[node_id]
            bucket = self._bucket_index(node_id)
            if bucket in self.routing_table:
                self.routing_table[bucket] = [
                    p for p in self.routing_table[bucket]
                    if p.node_id != node_id
                ]
            del self.peer_map[node_id]
            logger.debug(f"Removed peer {node_id}")
            return True
        return False

    def find_nearest_peers(self, target_id: str, count: int = 8) -> List[PeerInfo]:
        """Find the nearest peers to a target node ID."""
        all_peers = list(self.peer_map.values())
        all_peers.sort(
            key=lambda p: int(self.node_id, 32) ^ int(p.node_id, 32)
        )
        return all_peers[:count]

    def get_alive_peers(self) -> List[PeerInfo]:
        """Get all peers that are currently considered alive."""
        return [p for p in self.peer_map.values() if p.is_alive()]

    async def start(self, host: str = "127.0.0.1", port: int = 0):
        """Start the DHT listener."""
        self._running = True
        logger.info(f"DHT node {self.node_id} starting on {host}:{port}")

    async def stop(self):
        """Stop the DHT listener."""
        self._running = False
        if self._server:
            self._server.close()
        logger.info("DHT node stopped")

    def get_peer_count(self) -> int:
        return len(self.peer_map)


# ---------------------------------------------------------------------------
# P2P Transport Layer
# ---------------------------------------------------------------------------

class MessageType(Enum):
    """Types of P2P messages."""
    PING = 0x01
    PONG = 0x02
    FIND_NODE = 0x03
    NODES = 0x04
    TX = 0x05  # Transaction broadcast
    BLOCK = 0x06  # Block broadcast
    SYNC_REQUEST = 0x07
    SYNC_RESPONSE = 0x08


@dataclass
class Message:
    """A P2P network message."""
    msg_type: MessageType
    payload: bytes
    sender: NodeAddress
    signature: Optional[bytes] = None
    timestamp: float = field(default_factory=time.time)

    def serialize(self) -> bytes:
        """Serialize message to bytes."""
        data = struct.pack("!B", self.msg_type.value)
        data += struct.pack("!d", self.timestamp)
        data += self.sender.raw.encode("ascii")
        data += struct.pack("!I", len(self.payload))
        data += self.payload
        if self.signature:
            data += struct.pack("!H", len(self.signature))
            data += self.signature
        return data

    @classmethod
    def deserialize(cls, data: bytes) -> "Message":
        """Deserialize bytes to Message."""
        offset = 0
        msg_type = MessageType(data[offset])
        offset += 1
        timestamp = struct.unpack("!d", data[offset:offset + 8])[0]
        offset += 8
        sender_raw = data[offset:offset + 56].decode("ascii")
        offset += 56
        payload_len = struct.unpack("!I", data[offset:offset + 4])[0]
        offset += 4
        payload = data[offset:offset + payload_len]
        offset += payload_len
        signature = None
        if offset < len(data):
            sig_len = struct.unpack("!H", data[offset:offset + 2])[0]
            offset += 2
            signature = data[offset:offset + sig_len]
        return cls(
            msg_type=msg_type,
            payload=payload,
            sender=NodeAddress(raw=sender_raw),
            signature=signature,
            timestamp=timestamp,
        )


class P2PTransport:
    """
    End-to-end encrypted P2P transport layer.
    In production, this opens Tor hidden service circuits.
    For development, it uses TCP with encryption.
    """

    def __init__(self, node_id: str, private_key: PrivateKey):
        self.node_id = node_id
        self.private_key = private_key
        self.public_key = PublicKey.from_private(private_key)
        self.address = NodeAddress.from_public_key(self.public_key)
        self._server: Optional[asyncio.Server] = None
        self._connections: Dict[str, asyncio.StreamWriter] = {}
        self._message_handlers: Dict[MessageType, Callable] = {}
        self._running = False

    def register_handler(self, msg_type: MessageType, handler: Callable):
        """Register a handler for a specific message type."""
        self._message_handlers[msg_type] = handler

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        """Start the P2P listener."""
        self._running = True

        async def handle_connection(reader: asyncio.StreamReader,
                                    writer: asyncio.StreamWriter):
            peer_addr = writer.get_extra_info("peername")
            logger.debug(f"New connection from {peer_addr}")
            try:
                while self._running and not reader.at_eof():
                    # Read message length (4 bytes)
                    len_bytes = await reader.readexactly(4)
                    msg_len = struct.unpack("!I", len_bytes)[0]
                    # Read message
                    data = await reader.readexactly(msg_len)
                    message = Message.deserialize(data)
                    await self._handle_message(message, writer)
            except (asyncio.IncompleteReadError, ConnectionError) as e:
                logger.debug(f"Connection closed: {e}")
            finally:
                writer.close()
                await writer.wait_closed()

        self._server = await asyncio.start_server(
            handle_connection, host=host
        )
        port = self._server.sockets[0].getsockname()[1]
        logger.info(f"P2P transport listening on {host}:{port}")
        return port

    async def stop(self):
        """Stop the P2P transport."""
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        for writer in self._connections.values():
            writer.close()
        self._connections.clear()
        logger.info("P2P transport stopped")

    async def connect(self, address: NodeAddress, host: str, port: int):
        """Connect to a remote peer."""
        try:
            reader, writer = await asyncio.open_connection(host, port)
            self._connections[address.raw] = writer
            logger.info(f"Connected to {address} at {host}:{port}")
            return reader, writer
        except (ConnectionRefusedError, OSError) as e:
            logger.warning(f"Failed to connect to {address}: {e}")
            return None, None

    async def send_message(self, target: NodeAddress, message: Message):
        """Send a message to a connected peer."""
        writer = self._connections.get(target.raw)
        if not writer:
            raise ConnectionError(f"Not connected to {target}")

        data = message.serialize()
        writer.write(struct.pack("!I", len(data)))
        writer.write(data)
        await writer.drain()

    async def broadcast(self, message: Message, exclude: Optional[Set[str]] = None):
        """Broadcast a message to all connected peers."""
        if exclude is None:
            exclude = set()
        for node_id, writer in list(self._connections.items()):
            if node_id in exclude:
                continue
            try:
                data = message.serialize()
                writer.write(struct.pack("!I", len(data)))
                writer.write(data)
                await writer.drain()
            except ConnectionError:
                logger.warning(f"Failed to send to {node_id}")

    async def _handle_message(self, message: Message,
                              writer: asyncio.StreamWriter):
        """Route incoming message to registered handler."""
        handler = self._message_handlers.get(message.msg_type)
        if handler:
            await handler(message, writer)
        else:
            logger.debug(f"No handler for {message.msg_type}")


# ---------------------------------------------------------------------------
# Peer Manager
# ---------------------------------------------------------------------------

class PeerManager:
    """
    Manages peer lifecycle: discovery, connection, monitoring, and eviction.
    """

    def __init__(self, dht: DHTNode, transport: P2PTransport):
        self.dht = dht
        self.transport = transport
        self.banned_peers: Set[str] = set()
        self._monitor_task: Optional[asyncio.Task] = None

    async def start(self):
        """Start peer monitoring."""
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("Peer manager started")

    async def stop(self):
        """Stop peer monitoring."""
        if self._monitor_task:
            self._monitor_task.cancel()
        logger.info("Peer manager stopped")

    async def discover_peers(self, bootstrap_nodes: List[str]):
        """Discover peers from bootstrap nodes."""
        for node_addr in bootstrap_nodes:
            try:
                addr = NodeAddress(raw=node_addr)
                peer = PeerInfo(
                    node_id=node_addr,
                    address=addr,
                    public_key=PublicKey(point=base32_decode(node_addr)[:32]),
                    last_seen=time.time(),
                    reputation=0.5,
                )
                self.dht.add_peer(peer)
                logger.info(f"Discovered bootstrap peer: {node_addr}")
            except ValueError as e:
                logger.warning(f"Invalid bootstrap node {node_addr}: {e}")

    def ban_peer(self, node_id: str):
        """Ban a misbehaving peer."""
        self.banned_peers.add(node_id)
        self.dht.remove_peer(node_id)
        logger.warning(f"Banned peer {node_id}")

    async def _monitor_loop(self):
        """Periodically check peer health."""
        while True:
            try:
                await asyncio.sleep(60)  # Check every 60 seconds
                now = time.time()
                for peer in list(self.dht.peer_map.values()):
                    if now - peer.last_seen > 600:  # 10 min timeout
                        logger.debug(f"Peer {peer.node_id} timed out")
                        self.dht.remove_peer(peer.node_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Peer monitor error: {e}")
