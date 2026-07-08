"""
TokenCoin Mining P2P Subnet
============================
Fully decentralized mining subnet over the existing P2P/DHT network.
No central server, no static list of nodes. Every miner discovers
peers through the Kademlia DHT and gossips capabilities.

Architecture:
  - Miners broadcast their hardware capabilities via MINER_REGISTER gossip
  - Job requesters broadcast JOB_ANNOUNCE via gossip
  - Miners claim jobs via JOB_CLAIM (first-come, first-served)
  - Results are submitted via JOB_RESULT gossip
  - Peer scoring prevents sybil/griefing attacks
  - Periodic re-announcement keeps the miner registry fresh

This replaces the static CONFIG.ollama.remote_instances list with
a fully dynamic, P2P-discovered mining pool.
"""

import asyncio
import hashlib
import json
import logging
import struct
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, Callable, Any
from collections import defaultdict

from tokencoin.config import CONFIG

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mining Subnet Peer Info
# ---------------------------------------------------------------------------

@dataclass
class MiningPeerInfo:
    """
    Information about a mining peer discovered via the P2P subnet.
    Populated from MINER_REGISTER gossip messages.
    """
    node_id: str                        # 56-char TKC address
    host: str = ""                      # Reachable host (IP or Tor onion)
    port: int = 0                       # Ollama API port
    backend: str = "unknown"            # cpu, cuda, rocm, metal, vulkan
    backend_version: str = ""
    gpu_name: str = ""
    vram_total_gb: int = 0
    ram_total_gb: float = 0.0
    cpu_threads: int = 0
    models_available: List[str] = field(default_factory=list)
    jobs_completed: int = 0
    jobs_failed: int = 0
    avg_processing_time_ms: float = 0.0
    is_local: bool = False
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    score: float = 0.5                  # 0.0 - 1.0 reputation score

    @property
    def is_alive(self) -> bool:
        """Peer is considered alive if seen within the last 10 minutes."""
        return (time.time() - self.last_seen) < 600

    @property
    def effective_memory_gb(self) -> float:
        """Return usable memory: VRAM if GPU, RAM if CPU."""
        if self.vram_total_gb > 0:
            return float(self.vram_total_gb)
        return self.ram_total_gb

    def can_run_model(self, min_memory_gb: float) -> bool:
        """Check if this peer has enough memory for a model."""
        return self.effective_memory_gb >= min_memory_gb

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "host": self.host,
            "port": self.port,
            "backend": self.backend,
            "gpu_name": self.gpu_name,
            "vram_total_gb": self.vram_total_gb,
            "ram_total_gb": round(self.ram_total_gb, 1),
            "cpu_threads": self.cpu_threads,
            "models_available": self.models_available,
            "jobs_completed": self.jobs_completed,
            "jobs_failed": self.jobs_failed,
            "avg_processing_time_ms": round(self.avg_processing_time_ms, 1),
            "is_local": self.is_local,
            "score": round(self.score, 3),
            "alive": self.is_alive,
            "last_seen": self.last_seen,
        }


# ---------------------------------------------------------------------------
# Mining Job (P2P version - travels over the wire)
# ---------------------------------------------------------------------------

@dataclass
class MiningSubnetJob:
    """
    A mining job distributed over the P2P subnet.
    Serialized into gossip payloads for JOB_ANNOUNCE / JOB_RESPONSE.
    """
    job_id: str
    model_name: str
    prompt: str
    seed_params: bytes
    difficulty_target: int = 1
    reward: int = 0
    requester_id: str = ""              # Node that created the job
    timestamp: float = field(default_factory=time.time)
    assigned_miner: str = ""            # Set when claimed

    def serialize(self) -> bytes:
        """Serialize job to bytes for gossip payload."""
        data = self.job_id.encode("utf-8")
        data += b"\x00"
        data += self.model_name.encode("utf-8")
        data += b"\x00"
        data += self.prompt.encode("utf-8")
        data += b"\x00"
        data += struct.pack("!I", len(self.seed_params))
        data += self.seed_params
        data += struct.pack("!Q", self.difficulty_target)
        data += struct.pack("!Q", self.reward)
        data += self.requester_id.encode("ascii").ljust(56, b"\x00")[:56]
        data += struct.pack("!d", self.timestamp)
        data += self.assigned_miner.encode("ascii").ljust(56, b"\x00")[:56]
        return data

    @classmethod
    def deserialize(cls, data: bytes) -> "MiningSubnetJob":
        """Deserialize job from bytes."""
        parts = data.split(b"\x00", 3)
        if len(parts) < 4:
            raise ValueError("Invalid job data")
        job_id = parts[0].decode("utf-8")
        model_name = parts[1].decode("utf-8")
        prompt = parts[2].decode("utf-8")
        remainder = parts[3]

        offset = 0
        seed_len = struct.unpack("!I", remainder[offset:offset+4])[0]
        offset += 4
        seed_params = remainder[offset:offset+seed_len]
        offset += seed_len
        difficulty = struct.unpack("!Q", remainder[offset:offset+8])[0]
        offset += 8
        reward = struct.unpack("!Q", remainder[offset:offset+8])[0]
        offset += 8
        requester_id = remainder[offset:offset+56].rstrip(b"\x00").decode("ascii")
        offset += 56
        timestamp = struct.unpack("!d", remainder[offset:offset+8])[0]
        offset += 8
        assigned_miner = remainder[offset:offset+56].rstrip(b"\x00").decode("ascii")
        
        return cls(
            job_id=job_id,
            model_name=model_name,
            prompt=prompt,
            seed_params=seed_params,
            difficulty_target=difficulty,
            reward=reward,
            requester_id=requester_id,
            timestamp=timestamp,
            assigned_miner=assigned_miner,
        )


# ---------------------------------------------------------------------------
# Mining P2P Subnet
# ---------------------------------------------------------------------------

class MiningP2PSubnet:
    """
    Fully decentralized mining subnet over the existing P2P/DHT network.
    
    No central server. No static node list. Miners discover each other
    through the P2P gossip layer and the Kademlia DHT.
    
    Key design:
      - Each miner periodically broadcasts MINER_REGISTER with capabilities
      - The subnet maintains a live registry of all known miners (from gossip)
      - Jobs are announced via JOB_ANNOUNCE gossip to the entire subnet
      - Miners claim jobs via JOB_CLAIM (first-come, first-served)
      - Results are submitted via JOB_RESULT
      - Peer scoring prevents sybil attacks
      - Periodic cleanup removes dead peers
    
    This replaces the CONFIG.ollama.remote_instances static list.
    """

    def __init__(self, node_id: str, p2p_node=None):
        self.node_id = node_id
        self._p2p_node = p2p_node  # Reference to P2PNode for gossip broadcast
        self._running = False

        # Discovered miners (node_id -> MiningPeerInfo)
        self._known_miners: Dict[str, MiningPeerInfo] = {}

        # Pending jobs (job_id -> MiningSubnetJob)
        self._pending_jobs: Dict[str, MiningSubnetJob] = {}

        # Claimed jobs (job_id -> assigned_miner)
        self._claimed_jobs: Dict[str, str] = {}

        # Completed results (job_id -> result dict)
        self._completed_results: Dict[str, Dict] = {}

        # Peer scores for reputation
        self._peer_scores: Dict[str, float] = defaultdict(lambda: 0.5)

        # Callbacks
        self._on_miner_discovered: Optional[Callable] = None
        self._on_job_received: Optional[Callable] = None
        self._on_result_received: Optional[Callable] = None

        # Background tasks
        self._announce_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def on_miner_discovered(self, callback: Callable):
        """Called when a new miner is discovered via gossip."""
        self._on_miner_discovered = callback

    def on_job_received(self, callback: Callable):
        """Called when a new job announcement is received."""
        self._on_job_received = callback

    def on_result_received(self, callback: Callable):
        """Called when a job result is received."""
        self._on_result_received = callback

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        """Start the mining subnet."""
        self._running = True
        self._announce_task = asyncio.create_task(self._announce_loop())
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info(f"Mining P2P subnet started for node {self.node_id[:16]}...")
        return True

    async def stop(self):
        """Stop the mining subnet."""
        self._running = False
        if self._announce_task:
            self._announce_task.cancel()
            self._announce_task = None
        if self._cleanup_task:
            self._cleanup_task.cancel()
            self._cleanup_task = None
        logger.info("Mining P2P subnet stopped")

    def set_p2p_node(self, p2p_node):
        """Set the P2P node reference for gossip broadcast."""
        self._p2p_node = p2p_node

    # ------------------------------------------------------------------
    # Miner Registration / Discovery
    # ------------------------------------------------------------------

    def register_local_miner(self, capabilities: Dict[str, Any]):
        """
        Register this node's mining capabilities so it's discoverable
        by other nodes in the subnet. The capabilities dict is broadcast
        via MINER_REGISTER gossip.
        """
        peer_info = MiningPeerInfo(
            node_id=self.node_id,
            host=capabilities.get("host", "127.0.0.1"),
            port=capabilities.get("port", 11434),
            backend=capabilities.get("backend", "unknown"),
            backend_version=capabilities.get("backend_version", ""),
            gpu_name=capabilities.get("gpu_name", ""),
            vram_total_gb=capabilities.get("vram_total_gb", 0),
            ram_total_gb=capabilities.get("ram_total_gb", 0.0),
            cpu_threads=capabilities.get("cpu_threads", 0),
            models_available=capabilities.get("models_available", []),
            jobs_completed=capabilities.get("jobs_completed", 0),
            jobs_failed=capabilities.get("jobs_failed", 0),
            avg_processing_time_ms=capabilities.get("avg_processing_time_ms", 0.0),
            is_local=True,
            last_seen=time.time(),
        )
        self._known_miners[self.node_id] = peer_info
        logger.debug(f"Registered local miner with backend {capabilities.get('backend', 'unknown')}")

    def handle_miner_register(self, payload: bytes, sender_id: str):
        """
        Process a MINER_REGISTER gossip message from a remote peer.
        Adds or updates the peer in the known miners registry.
        """
        try:
            data = json.loads(payload.decode())
            peer = MiningPeerInfo(
                node_id=sender_id,
                host=data.get("host", ""),
                port=data.get("port", 11434),
                backend=data.get("backend", "unknown"),
                backend_version=data.get("backend_version", ""),
                gpu_name=data.get("gpu_name", ""),
                vram_total_gb=data.get("vram_total_gb", 0),
                ram_total_gb=data.get("ram_total_gb", 0.0),
                cpu_threads=data.get("cpu_threads", 0),
                models_available=data.get("models_available", []),
                jobs_completed=data.get("jobs_completed", 0),
                jobs_failed=data.get("jobs_failed", 0),
                avg_processing_time_ms=data.get("avg_processing_time_ms", 0.0),
                is_local=False,
                last_seen=time.time(),
                score=self._peer_scores[sender_id],
            )

            existing = self._known_miners.get(sender_id)
            if existing:
                # Preserve cumulative stats
                peer.jobs_completed = max(peer.jobs_completed, existing.jobs_completed)
                peer.jobs_failed = max(peer.jobs_failed, existing.jobs_failed)
                peer.first_seen = existing.first_seen

            self._known_miners[sender_id] = peer
            self._peer_scores[sender_id] = min(1.0, self._peer_scores[sender_id] + 0.01)

            logger.debug(
                f"Miner discovered: {sender_id[:16]}... "
                f"({data.get('backend', 'unknown')}, "
                f"vram={data.get('vram_total_gb', 0)}GB)"
            )

            if self._on_miner_discovered:
                self._on_miner_discovered(peer)

        except (json.JSONDecodeError, KeyError, UnicodeDecodeError) as e:
            logger.debug(f"Invalid miner register message: {e}")
            # Penalize sender for bad data
            self._peer_scores[sender_id] = max(0.0, self._peer_scores[sender_id] - 0.1)

    def handle_miner_list_response(self, payload: bytes):
        """
        Process a MINER_LIST response (batch of known miners).
        """
        try:
            miners = json.loads(payload.decode())
            for miner_data in miners:
                node_id = miner_data.get("node_id", "")
                if not node_id or node_id == self.node_id:
                    continue
                peer = MiningPeerInfo(
                    node_id=node_id,
                    host=miner_data.get("host", ""),
                    port=miner_data.get("port", 11434),
                    backend=miner_data.get("backend", "unknown"),
                    gpu_name=miner_data.get("gpu_name", ""),
                    vram_total_gb=miner_data.get("vram_total_gb", 0),
                    ram_total_gb=miner_data.get("ram_total_gb", 0.0),
                    cpu_threads=miner_data.get("cpu_threads", 0),
                    models_available=miner_data.get("models_available", []),
                    is_local=False,
                    last_seen=time.time(),
                )
                if node_id not in self._known_miners:
                    self._known_miners[node_id] = peer
                    logger.debug(f"Discovered miner from list: {node_id[:16]}...")
        except (json.JSONDecodeError, KeyError) as e:
            logger.debug(f"Invalid miner list response: {e}")

    # ------------------------------------------------------------------
    # Job Distribution (P2P)
    # ------------------------------------------------------------------

    async def announce_job(self, job: MiningSubnetJob) -> bool:
        """
        Announce a new mining job to the subnet via gossip.
        Returns True if the announcement was broadcast.
        """
        if not self._p2p_node:
            logger.warning("P2P node not set, cannot announce job")
            return False

        self._pending_jobs[job.job_id] = job
        job_data = job.serialize()

        # Use the P2P node's gossip engine to broadcast
        from tokencoin.network.p2p import GossipMessage, GossipType
        msg = GossipMessage(
            msg_type=GossipType.JOB_ANNOUNCE,
            payload=job_data,
            sender_id=self.node_id,
        )
        await self._p2p_node.transport.broadcast(msg)
        logger.info(f"Announced job {job.job_id[:16]}... to subnet "
                    f"(model={job.model_name}, reward={job.reward})")
        return True

    def handle_job_announce(self, payload: bytes, sender_id: str) -> Optional[MiningSubnetJob]:
        """
        Process a JOB_ANNOUNCE gossip message.
        Returns the deserialized job if it's new, None if already seen.
        """
        try:
            job = MiningSubnetJob.deserialize(payload)
            if job.job_id in self._pending_jobs or job.job_id in self._claimed_jobs:
                return None  # Already seen

            job.requester_id = sender_id
            self._pending_jobs[job.job_id] = job
            logger.debug(f"Received job announcement: {job.job_id[:16]}... "
                         f"(model={job.model_name})")

            if self._on_job_received:
                self._on_job_received(job)

            return job
        except (ValueError, UnicodeDecodeError) as e:
            logger.debug(f"Invalid job announce: {e}")
            return None

    async def claim_job(self, job_id: str) -> bool:
        """
        Claim a pending job. Broadcasts JOB_CLAIM to the subnet.
        Returns True if the claim was broadcast.
        First-come, first-served: the first claim the requester sees wins.
        """
        if not self._p2p_node:
            return False

        job = self._pending_jobs.get(job_id)
        if not job:
            logger.warning(f"Cannot claim unknown job {job_id[:16]}...")
            return False

        job.assigned_miner = self.node_id
        self._claimed_jobs[job_id] = self.node_id

        from tokencoin.network.p2p import GossipMessage, GossipType
        msg = GossipMessage(
            msg_type=GossipType.JOB_CLAIM,
            payload=job_id.encode(),
            sender_id=self.node_id,
        )
        await self._p2p_node.transport.broadcast(msg)
        logger.info(f"Claimed job {job_id[:16]}...")
        return True

    def handle_job_claim(self, job_id: str, claimant_id: str) -> bool:
        """
        Process a JOB_CLAIM message.
        Returns True if the claim is accepted (first claimant wins).
        """
        if job_id in self._claimed_jobs:
            logger.debug(f"Job {job_id[:16]}... already claimed by "
                         f"{self._claimed_jobs[job_id][:16]}...")
            return False

        if job_id not in self._pending_jobs:
            logger.debug(f"Claim for unknown job {job_id[:16]}...")
            return False

        self._claimed_jobs[job_id] = claimant_id
        self._pending_jobs[job_id].assigned_miner = claimant_id
        logger.info(f"Job {job_id[:16]}... claimed by miner {claimant_id[:16]}...")
        return True

    async def submit_result(self, job_id: str, result: Dict) -> bool:
        """
        Submit a completed job result back to the subnet.
        """
        if not self._p2p_node:
            return False

        payload = json.dumps({
            "job_id": job_id,
            "result": result,
            "miner_id": self.node_id,
            "timestamp": time.time(),
        }).encode()

        from tokencoin.network.p2p import GossipMessage, GossipType
        msg = GossipMessage(
            msg_type=GossipType.JOB_RESULT,
            payload=payload,
            sender_id=self.node_id,
        )
        await self._p2p_node.transport.broadcast(msg)
        self._completed_results[job_id] = result

        # Clean up pending/claimed
        self._pending_jobs.pop(job_id, None)
        self._claimed_jobs.pop(job_id, None)

        logger.info(f"Submitted result for job {job_id[:16]}...")
        return True

    def handle_job_result(self, payload: bytes) -> Optional[Dict]:
        """
        Process a JOB_RESULT gossip message.
        Returns the result dict if valid.
        """
        try:
            data = json.loads(payload.decode())
            job_id = data.get("job_id", "")
            result = data.get("result", {})
            miner_id = data.get("miner_id", "")

            if not job_id:
                return None

            self._completed_results[job_id] = result
            self._pending_jobs.pop(job_id, None)
            self._claimed_jobs.pop(job_id, None)

            # Reward the miner with a score bump
            if miner_id:
                self._peer_scores[miner_id] = min(1.0, self._peer_scores[miner_id] + 0.05)
                if miner_id in self._known_miners:
                    self._known_miners[miner_id].jobs_completed += 1
                    self._known_miners[miner_id].score = self._peer_scores[miner_id]

            logger.debug(f"Received result for job {job_id[:16]}... from {miner_id[:16] if miner_id else 'unknown'}...")

            if self._on_result_received:
                self._on_result_received(job_id, result, miner_id)

            return result
        except (json.JSONDecodeError, KeyError) as e:
            logger.debug(f"Invalid job result: {e}")
            return None

    # ------------------------------------------------------------------
    # Peer Queries
    # ------------------------------------------------------------------

    def get_available_miners(self, 
                              model_memory_gb: float = 0.0,
                              min_score: float = 0.0) -> List[MiningPeerInfo]:
        """
        Get all alive miners, optionally filtered by model compatibility
        and minimum reputation score.
        
        This is the replacement for reading from CONFIG.ollama.remote_instances.
        """
        miners = [
            p for p in self._known_miners.values()
            if p.is_alive and p.score >= min_score
        ]

        if model_memory_gb > 0:
            miners = [p for p in miners if p.can_run_model(model_memory_gb)]

        # Sort by score (reputation), then by jobs completed (load balancing)
        miners.sort(key=lambda p: (-p.score, p.jobs_completed))
        return miners

    def get_best_miner(self, model_memory_gb: float = 0.0) -> Optional[MiningPeerInfo]:
        """
        Get the best available miner for a job.
        Prefers: higher reputation > lower load > more capable hardware.
        """
        miners = self.get_available_miners(model_memory_gb)
        if not miners:
            # Fallback: try without memory filter
            miners = self.get_available_miners()
        if not miners:
            return None

        # Prefer local miner, then highest score, then least loaded
        miners.sort(key=lambda p: (
            not p.is_local,        # Local first
            -p.score,               # Then highest reputation
            p.jobs_completed,       # Then least loaded
        ))
        return miners[0]

    def get_miner_count(self) -> int:
        """Get number of alive miners in the subnet."""
        return sum(1 for p in self._known_miners.values() if p.is_alive)

    def get_peer_count(self) -> int:
        """Get total number of known peers (including dead)."""
        return len(self._known_miners)

    def get_job_stats(self) -> Dict[str, int]:
        """Get current job statistics."""
        return {
            "pending": len(self._pending_jobs),
            "claimed": len(self._claimed_jobs),
            "completed": len(self._completed_results),
        }

    # ------------------------------------------------------------------
    # Background Tasks
    # ------------------------------------------------------------------

    async def _announce_loop(self):
        """
        Periodically re-announce our presence to the subnet.
        Keeps the miner registry fresh across the network.
        """
        while self._running:
            await asyncio.sleep(120)  # Every 2 minutes
            try:
                # If we have local miner info registered, re-broadcast it
                local = self._known_miners.get(self.node_id)
                if local and self._p2p_node:
                    from tokencoin.network.p2p import GossipMessage, GossipType
                    capabilities = {
                        "host": local.host,
                        "port": local.port,
                        "backend": local.backend,
                        "backend_version": local.backend_version,
                        "gpu_name": local.gpu_name,
                        "vram_total_gb": local.vram_total_gb,
                        "ram_total_gb": local.ram_total_gb,
                        "cpu_threads": local.cpu_threads,
                        "models_available": local.models_available,
                        "jobs_completed": local.jobs_completed,
                        "jobs_failed": local.jobs_failed,
                        "avg_processing_time_ms": local.avg_processing_time_ms,
                    }
                    msg = GossipMessage(
                        msg_type=GossipType.MINER_REGISTER,
                        payload=json.dumps(capabilities).encode(),
                        sender_id=self.node_id,
                    )
                    await self._p2p_node.transport.broadcast(msg)
                    local.last_seen = time.time()
                    logger.debug(f"Re-announced miner capabilities to subnet "
                                f"({len(self._known_miners)} known miners)")
            except Exception as e:
                logger.error(f"Announce loop error: {e}")

    async def _cleanup_loop(self):
        """
        Periodically remove dead peers from the registry.
        """
        while self._running:
            await asyncio.sleep(300)  # Every 5 minutes
            try:
                now = time.time()
                dead = [
                    nid for nid, p in self._known_miners.items()
                    if (now - p.last_seen) > 1800  # 30 min timeout
                ]
                for nid in dead:
                    del self._known_miners[nid]
                    logger.debug(f"Removed dead miner {nid[:16]}...")

                # Clean up stale jobs (older than 1 hour)
                stale_cutoff = now - 3600
                stale_jobs = [
                    jid for jid, j in self._pending_jobs.items()
                    if j.timestamp < stale_cutoff
                ]
                for jid in stale_jobs:
                    self._pending_jobs.pop(jid, None)
                    self._claimed_jobs.pop(jid, None)

                if dead or stale_jobs:
                    logger.debug(f"Cleaned up {len(dead)} dead miners, "
                                f"{len(stale_jobs)} stale jobs")
            except Exception as e:
                logger.error(f"Cleanup loop error: {e}")

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def get_subnet_status(self) -> Dict[str, Any]:
        """Get full status of the mining subnet."""
        alive = self.get_available_miners()
        return {
            "running": self._running,
            "node_id": self.node_id[:16] + "...",
            "miners": {
                "total_known": len(self._known_miners),
                "alive": len(alive),
                "local": sum(1 for p in alive if p.is_local),
                "remote": sum(1 for p in alive if not p.is_local),
            },
            "jobs": self.get_job_stats(),
            "top_miners": [
                {
                    "node_id": p.node_id[:16] + "...",
                    "backend": p.backend,
                    "gpu": p.gpu_name or "N/A",
                    "vram_gb": p.vram_total_gb,
                    "score": round(p.score, 3),
                    "jobs": p.jobs_completed,
                }
                for p in sorted(alive, key=lambda x: -x.score)[:10]
            ],
        }