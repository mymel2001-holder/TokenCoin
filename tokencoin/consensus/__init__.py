"""
TokenCoin Consensus Layer: Proof-of-Useful-Work (PoUW) via Distributed Ollama
================================================================================
Implements the decentralized AI inference mining system using Ollama.

Key components:
  - OllamaOrchestrator: Manages local/remote Ollama instances for distributed mining
  - WorkBlockGenerator: Creates work blocks from AI inference jobs
  - ZKIPVerifier: Zero-Knowledge Inference Proof verification
  - DifficultyAdjuster: Dynamic difficulty based on network hashrate
  - SlashingManager: Penalizes dishonest miners
  - MiningP2PSubnet: Fully P2P miner discovery (replaces static remote_instances)
"""

import asyncio
import hashlib
import json
import logging
import os
import random
import struct
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple, Callable, Any
from collections import deque, defaultdict

from tokencoin.config import CONFIG
from tokencoin.core.crypto import (
    PrivateKey, PublicKey, KeyPair, _hash_to_scalar, _random_scalar,
    base32_encode, base32_decode,
)
from tokencoin.ledger import (
    Block, BlockHeader, Transaction, TxType, TxOutput,
    PedersenCommitment, RangeProof, StealthAddress,
)
from tokencoin.mining.ollama_miner import (
    OllamaManager, OllamaModel, OllamaInstance, HardwareInfo,
    HardwareBackend, OLLAMA_MODELS, MODEL_REGISTRY, detect_hardware,
)
from tokencoin.network.mining_p2p import (
    MiningP2PSubnet, MiningSubnetJob, MiningPeerInfo,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ollama Model Registry (re-exports from ollama_miner for convenience)
# ---------------------------------------------------------------------------

# Re-export the model registry so existing imports still work
# Maps model keys to OllamaModel instances
OLLAMA_MODELS = OLLAMA_MODELS


# ---------------------------------------------------------------------------
# Hardware Capability Detection (re-exports from ollama_miner)
# ---------------------------------------------------------------------------

@dataclass
class HardwareInfo:
    """Information about the local hardware capabilities."""
    name: str = "Unknown"
    memory_total_gb: float = 0.0
    memory_free_gb: float = 0.0
    backend: str = "cpu"
    backend_version: str = ""
    has_gpu: bool = False
    gpu_name: str = ""
    vram_total_gb: int = 0
    vram_free_gb: int = 0
    cpu_threads: int = 0

    def can_run_model(self, model: OllamaModel) -> bool:
        """Check if this hardware can run the given Ollama model."""
        if self.has_gpu and self.vram_total_gb > 0:
            return self.vram_total_gb >= model.min_memory_gb
        return self.memory_total_gb >= model.min_memory_gb


def detect_hardware() -> HardwareInfo:
    """
    Detect local hardware capabilities.
    Delegates to ollama_miner's comprehensive detection.
    """
    hw = detect_hardware()
    return HardwareInfo(
        name=hw.gpu_name if hw.has_gpu else hw.cpu_name,
        memory_total_gb=hw.ram_total_gb,
        memory_free_gb=hw.ram_free_gb,
        backend=hw.backend.value,
        backend_version=hw.backend_version,
        has_gpu=hw.has_gpu,
        gpu_name=hw.gpu_name,
        vram_total_gb=hw.vram_total_gb,
        vram_free_gb=hw.vram_free_gb,
        cpu_threads=hw.cpu_threads,
    )


# ---------------------------------------------------------------------------
# Inference Job
# ---------------------------------------------------------------------------

@dataclass
class InferenceJob:
    """
    An AI inference job that a miner processes.
    These are the "useful work" units in PoUW.
    """
    job_id: str
    model_name: str
    input_data: bytes
    seed_params: bytes  # Deterministic seed for verification
    timestamp: float = field(default_factory=time.time)
    difficulty_target: int = 1
    reward: int = CONFIG.monetary.initial_block_reward

    def hash(self) -> bytes:
        """Compute job hash."""
        h = hashlib.sha3_256()
        h.update(self.job_id.encode())
        h.update(self.model_name.encode())
        h.update(self.input_data)
        h.update(self.seed_params)
        h.update(struct.pack("!d", self.timestamp))
        return h.digest()


@dataclass
class InferenceResult:
    """
    Result of an AI inference job, including the proof.
    """
    job_id: str
    output_tokens: bytes       # The model output
    tensor_commitment: bytes   # Commitment to intermediate tensor weights
    processing_time_ms: float  # How long inference took
    model_name: str
    hardware_info: HardwareInfo
    instance_id: str = ""

    def hash(self) -> bytes:
        """Compute result hash for verification."""
        h = hashlib.sha3_256()
        h.update(self.job_id.encode())
        h.update(self.output_tokens)
        h.update(self.tensor_commitment)
        h.update(struct.pack("!d", self.processing_time_ms))
        return h.digest()


# ---------------------------------------------------------------------------
# ZKIP Verifier (Zero-Knowledge Inference Proof)
# ---------------------------------------------------------------------------

class ZKIPVerifier:
    """
    Verifies that a miner actually performed AI inference.
    Uses deterministic seed parameters to challenge nodes.
    """

    @staticmethod
    def create_challenge(job: InferenceJob) -> bytes:
        """
        Create a deterministic challenge for a job.
        The challenge includes the seed parameters and a random nonce.
        """
        challenge = hashlib.sha3_256(
            b"zkip_challenge:" +
            job.job_id.encode() +
            job.seed_params +
            struct.pack("!Q", int(time.time() * 1000))
        ).digest()
        return challenge

    @staticmethod
    def verify_tensor_commitment(
        result: InferenceResult,
        job: InferenceJob,
        tolerance: float = 0.01
    ) -> bool:
        """
        Verify that the tensor commitment matches expected values.
        In production: re-run inference with same seed and compare
        intermediate tensor hashes.
        """
        # Recompute expected commitment
        expected = hashlib.sha3_256(
            b"tensor_commit:" +
            job.seed_params +
            job.input_data +
            job.model_name.encode()
        ).digest()

        # Check if commitment matches within tolerance
        actual = result.tensor_commitment
        if len(actual) != len(expected):
            return False

        # Compare hash similarity (in production: actual tensor comparison)
        match_ratio = sum(1 for a, e in zip(actual, expected) if a == e) / len(actual)
        return match_ratio >= (1.0 - tolerance)

    @staticmethod
    def verify_inference_time(
        result: InferenceResult,
        model: OllamaModel,
        max_time_multiplier: float = 3.0
    ) -> bool:
        """
        Verify that inference time is realistic for the model.
        Prevents nodes from returning pre-computed results.
        Accounts for CPU vs GPU speed differences.
        """
        # Expected time based on model size
        # ~100ms per billion parameters on GPU
        # ~500ms per billion parameters on CPU
        expected_time_ms = model.parameters_billions * 100

        # Allow more variance for CPU mining
        min_time = expected_time_ms * 0.3
        max_time = expected_time_ms * max_time_multiplier

        return min_time <= result.processing_time_ms <= max_time


# ---------------------------------------------------------------------------
# Ollama Orchestrator
# ---------------------------------------------------------------------------

class OllamaOrchestrator:
    """
    Manages the lifecycle of local/remote Ollama instances.
    Handles model pulling, instance management, and inference requests.
    Supports CPU, NVIDIA GPU (CUDA), AMD GPU (ROCm), and Apple Silicon (Metal).

    Remote instances are discovered via the P2P mining subnet (DHT + gossip)
    instead of a static list of nodes. Every miner broadcasts its capabilities
    and discovers peers through the Kademlia routing table.
    """

    def __init__(self):
        self.manager = OllamaManager()
        self.active_model: Optional[OllamaModel] = None
        self.active_instance: Optional[OllamaInstance] = None
        self._running = False
        self._job_queue: asyncio.Queue = asyncio.Queue()
        self._result_queue: asyncio.Queue = asyncio.Queue()

        # P2P Mining Subnet (replaces static remote_instances)
        self.p2p_subnet: Optional[MiningP2PSubnet] = None

    def set_p2p_subnet(self, subnet: MiningP2PSubnet):
        """Attach the P2P mining subnet for dynamic miner discovery."""
        self.p2p_subnet = subnet
        logger.info("P2P mining subnet attached to orchestrator")

    async def start(self, model_name: str) -> bool:
        """Start mining with the specified Ollama model."""
        # Resolve model name — supports any Ollama model dynamically
        model = MODEL_REGISTRY.get(model_name)

        # Check hardware compatibility
        if not self.manager.hardware.can_run_model(model):
            logger.error(
                f"Insufficient memory for {model_name}: "
                f"need {model.min_memory_gb}GB, have "
                f"{self.manager.hardware.effective_memory_gb:.0f}GB"
            )
            return False

        # Start the Ollama manager
        await self.manager.start()

        # Auto-pull the model if needed
        if CONFIG.ollama.auto_pull_models:
            local_models = await self.manager.list_models()
            model_names = [m.get("name", "") for m in local_models]
            if model.full_name not in model_names:
                logger.info(f"Model {model.full_name} not found locally, pulling...")
                pulled = await self.manager.pull_model(model)
                if not pulled:
                    logger.error(f"Failed to pull model {model.full_name}")
                    return False

        # Register local miner with the P2P subnet so remote peers
        # can discover us and assign us jobs.
        if self.p2p_subnet and CONFIG.ollama.p2p_mining_enabled:
            hw = self.manager.hardware
            capabilities = {
                "host": "127.0.0.1",
                "port": CONFIG.ollama.default_port,
                "backend": hw.backend.value,
                "backend_version": hw.backend_version,
                "gpu_name": hw.gpu_name,
                "vram_total_gb": hw.vram_total_gb,
                "ram_total_gb": hw.ram_total_gb,
                "cpu_threads": hw.cpu_threads,
                "models_available": [model.full_name],
                "jobs_completed": 0,
                "jobs_failed": 0,
                "avg_processing_time_ms": 0.0,
            }
            self.p2p_subnet.register_local_miner(capabilities)
            logger.info("Registered local miner with P2P subnet")

        # Select the best instance for this model.
        # With P2P, this includes remote miners discovered via the subnet.
        instance = self._get_best_p2p_instance(model)
        if not instance:
            logger.error("No healthy Ollama instance available (local or P2P)")
            return False

        self.active_model = model
        self.active_instance = instance
        instance.active_model = model.full_name
        self._running = True

        backend_label = self.manager.hardware.backend.value
        if instance.is_local:
            logger.info(
                f"Ollama mining started with {model.full_name} on "
                f"local instance [{backend_label}]"
            )
        else:
            logger.info(
                f"Ollama mining started with {model.full_name} on "
                f"remote P2P miner {instance.instance_id} "
                f"({instance.host}:{instance.port}) [{backend_label}]"
            )
        return True

    def _get_best_p2p_instance(self, model: OllamaModel) -> Optional[OllamaInstance]:
        """
        Select the best instance for running a model.
        Steps:
          1. Check local instance first
          2. If P2P subnet is available, query discovered miners
          3. Fall back to any compatible instance

        This replaces the old static remote_instances approach.
        """
        # 1. Check local instance first
        local = self.manager.get_best_instance(model)
        if local and local.is_healthy:
            return local

        # 2. Check P2P subnet for remote miners
        if self.p2p_subnet and CONFIG.ollama.p2p_mining_enabled:
            p2p_miners = self.p2p_subnet.get_available_miners(
                model_memory_gb=model.min_memory_gb,
                min_score=CONFIG.ollama.p2p_min_peer_score,
            )

            # Collect the P2P node IDs that correspond to local instances
            # so we don't re-add ourselves as a remote miner.
            # Local instance uses a 12-char hex ID; P2P uses 56-char TKC address.
            local_p2p_node_id = self.p2p_subnet.node_id

            for miner in p2p_miners:
                # Skip our own node (P2P node_id = TKC address, not local instance hash)
                if miner.node_id == local_p2p_node_id:
                    continue

                # Check if we already have an instance for this miner
                if miner.node_id in self.manager.instances:
                    existing = self.manager.instances[miner.node_id]
                    if existing.is_healthy:
                        return existing

                # Also check by host:port to avoid duplicates
                already_connected = any(
                    inst.host == miner.host and inst.port == miner.port
                    for inst in self.manager.instances.values()
                )
                if already_connected:
                    continue

                # Add as a new remote instance discovered via P2P subnet
                if miner.host and miner.port:
                    inst_id = self.manager.add_remote_instance(miner.host, miner.port)
                    instance = self.manager.instances.get(inst_id)
                    if instance:
                        return instance

        # 3. Fallback: any compatible instance (local only)
        return self.manager.get_best_instance(model)

    async def stop(self):
        """Stop the Ollama orchestrator."""
        self._running = False
        await self.manager.stop()
        self.active_model = None
        self.active_instance = None
        logger.info("Ollama orchestrator stopped")

    async def submit_job(self, job: InferenceJob) -> Optional[InferenceResult]:
        """
        Submit an inference job and return the result.
        Uses the active Ollama instance to perform actual AI inference.
        """
        if not self._running or not self.active_model or not self.active_instance:
            logger.error("Ollama orchestrator not running")
            return None

        # Convert the job input data to a prompt string
        # The prompt includes the seed params to ensure deterministic output
        prompt = (
            f"TokenCoin PoUW Job {job.job_id}\n"
            f"Seed: {job.seed_params.hex()}\n"
            f"Input: {job.input_data.hex()}\n"
            f"Generate exactly {CONFIG.ollama.max_tokens_per_job} tokens "
            f"based on the above deterministic seed."
        )

        start_time = time.time()

        # Perform actual inference via Ollama
        result = await self.manager.generate(
            model=self.active_model,
            prompt=prompt,
            instance=self.active_instance,
        )

        if not result:
            return None

        processing_time = (time.time() - start_time) * 1000  # ms

        # Convert response to bytes for the proof
        response_text = result.get("response", "")
        output_bytes = response_text.encode("utf-8")[:256]  # Cap at 256 bytes

        # Create tensor commitment from the response hash
        tensor_comm = hashlib.sha3_256(
            b"tensor:" +
            output_bytes +
            job.seed_params +
            struct.pack("!Q", result.get("eval_count", 0))
        ).digest()

        return InferenceResult(
            job_id=job.job_id,
            output_tokens=output_bytes,
            tensor_commitment=tensor_comm,
            processing_time_ms=processing_time,
            model_name=self.active_model.full_name,
            hardware_info=HardwareInfo(
                name=self.manager.hardware.gpu_name if self.manager.hardware.has_gpu
                     else self.manager.hardware.cpu_name,
                memory_total_gb=self.manager.hardware.ram_total_gb,
                memory_free_gb=self.manager.hardware.ram_free_gb,
                backend=self.manager.hardware.backend.value,
                has_gpu=self.manager.hardware.has_gpu,
                gpu_name=self.manager.hardware.gpu_name,
                vram_total_gb=self.manager.hardware.vram_total_gb,
                vram_free_gb=self.manager.hardware.vram_free_gb,
                cpu_threads=self.manager.hardware.cpu_threads,
            ),
            instance_id=self.active_instance.instance_id,
        )

    def is_running(self) -> bool:
        return self._running and self.active_model is not None

    def get_hardware_info(self) -> Dict[str, Any]:
        """Get current hardware information."""
        hw = self.manager.hardware
        return {
            "backend": hw.backend.value,
            "cpu": {
                "name": hw.cpu_name,
                "cores": hw.cpu_cores,
                "threads": hw.cpu_threads,
            },
            "memory": {
                "total_gb": round(hw.ram_total_gb, 1),
                "free_gb": round(hw.ram_free_gb, 1),
            },
            "gpu": {
                "name": hw.gpu_name,
                "count": hw.gpu_count,
                "vram_total_gb": hw.vram_total_gb,
                "vram_free_gb": hw.vram_free_gb,
            } if hw.has_gpu else None,
        }


# ---------------------------------------------------------------------------
# Work Block Generator
# ---------------------------------------------------------------------------

class WorkBlockGenerator:
    """
    Generates work blocks from completed inference jobs.
    A work block is a standard TokenCoin block with PoUW metadata.
    """

    def __init__(self, miner_keypair: KeyPair, blockchain: "Blockchain"):
        self.miner_keypair = miner_keypair
        self.blockchain = blockchain
        self.miner_address = miner_keypair.to_address()

    def create_work_block(
        self,
        inference_result: InferenceResult,
        mempool_txs: List[Transaction],
        prev_block: Optional[Block] = None,
    ) -> Block:
        """
        Create a new block from completed work.
        """
        if prev_block is None:
            prev_block = self.blockchain.get_latest_block()

        prev_hash = prev_block.hash() if prev_block else b"\x00" * 32
        height = (prev_block.header.height + 1) if prev_block else 0

        # Calculate block reward (with halving)
        halvings = height // CONFIG.monetary.halving_interval
        block_reward = max(
            CONFIG.monetary.initial_block_reward >> halvings,
            CONFIG.monetary.min_block_reward
        )

        # Create coinbase transaction
        coinbase = Transaction(
            tx_type=TxType.COINBASE,
            outputs=[
                TxOutput(
                    stealth_address=StealthAddress(
                        public_spend=self.miner_keypair.public_key,
                        public_view=self.miner_keypair.public_key,
                        ephemeral=b"\x00" * 32,
                    ),
                    commitment=PedersenCommitment.create(block_reward),
                    range_proof=RangeProof.prove(block_reward, _random_scalar()),
                )
            ],
            fee=0,
        )

        # Assemble block
        block = Block(
            header=BlockHeader(
                version=1,
                height=height,
                timestamp=time.time(),
                prev_hash=prev_hash,
                difficulty=self.blockchain.state.difficulty,
                nonce=0,
                work_model=inference_result.model_name,
                work_commitment=inference_result.tensor_commitment,
                miner_address=self.miner_address,
            ),
            transactions=[coinbase] + mempool_txs,
        )

        # Compute Merkle root
        block.header.merkle_root = block.compute_merkle_root()

        return block


# ---------------------------------------------------------------------------
# Difficulty Adjustment
# ---------------------------------------------------------------------------

class DifficultyAdjuster:
    """
    Adjusts mining difficulty based on network hashrate.
    Targets 5-minute block times as specified in the design.
    """

    def __init__(self):
        self.target_block_time = CONFIG.monetary.block_time_seconds
        self.difficulty_window = 100  # Blocks to look back
        self.min_difficulty = 1
        self.max_difficulty = 1 << 64
        self.adjustment_factor = 0.25  # Max 25% change per adjustment

    def calculate_difficulty(self, blockchain: "Blockchain") -> int:
        """
        Calculate the next difficulty based on recent block times.
        """
        chain = blockchain.chain
        if len(chain) < 2:
            return self.min_difficulty

        # Look at the last `difficulty_window` blocks
        start_height = max(0, len(chain) - self.difficulty_window - 1)
        start_block = chain[start_height]
        end_block = chain[-1]

        actual_time = end_block.header.timestamp - start_block.header.timestamp
        expected_time = self.target_block_time * (end_block.header.height - start_block.header.height)

        if actual_time <= 0:
            return end_block.header.difficulty

        # Ratio of expected to actual time
        ratio = expected_time / actual_time

        # Clamp the adjustment
        ratio = max(1.0 - self.adjustment_factor,
                    min(1.0 + self.adjustment_factor, ratio))

        new_difficulty = max(
            self.min_difficulty,
            min(self.max_difficulty,
                int(end_block.header.difficulty * ratio))
        )

        logger.info(
            f"Difficulty adjusted: {end_block.header.difficulty} -> {new_difficulty} "
            f"(ratio: {ratio:.3f})"
        )
        return new_difficulty


# ---------------------------------------------------------------------------
# Slashing Manager
# ---------------------------------------------------------------------------

class SlashingManager:
    """
    Manages slashing of dishonest miners.
    Penalizes nodes that submit invalid work proofs.
    """

    def __init__(self):
        self.slash_penalty = CONFIG.consensus.slash_penalty
        self.slashed_miners: Dict[str, float] = {}  # address -> timestamp
        self.violation_count: Dict[str, int] = defaultdict(int)

    def record_violation(self, miner_address: str) -> int:
        """
        Record a violation for a miner.
        Returns the number of violations.
        """
        self.violation_count[miner_address] += 1
        count = self.violation_count[miner_address]
        self.slashed_miners[miner_address] = time.time()

        logger.warning(
            f"Miner {miner_address} violation #{count}: "
            f"slashing {self.slash_penalty * count} TKC"
        )
        return count

    def is_slashed(self, miner_address: str) -> bool:
        """Check if a miner is currently slashed."""
        if miner_address not in self.slashed_miners:
            return False
        # Slashing lasts for 24 hours
        return (time.time() - self.slashed_miners[miner_address]) < 86400

    def get_penalty(self, miner_address: str) -> int:
        """Get the current penalty for a miner."""
        count = self.violation_count.get(miner_address, 0)
        return self.slash_penalty * (count + 1)

    def clear_violations(self, miner_address: str):
        """Clear violations after good behavior."""
        if miner_address in self.violation_count:
            del self.violation_count[miner_address]
        if miner_address in self.slashed_miners:
            del self.slashed_miners[miner_address]
        logger.info(f"Violations cleared for miner {miner_address}")


# ---------------------------------------------------------------------------
# Consensus Engine
# ---------------------------------------------------------------------------

class ConsensusEngine:
    """
    The main consensus engine coordinating PoUW mining via Ollama.
    Manages the full lifecycle: job distribution -> inference -> verification -> block creation.
    Supports distributed mining across multiple Ollama instances (local and remote).

    Miner discovery is fully P2P-based via the MiningP2PSubnet (DHT + gossip),
    replacing the old static remote_instances list. No central server required.
    """

    def __init__(self, blockchain: "Blockchain"):
        self.blockchain = blockchain
        self.orchestrator = OllamaOrchestrator()
        self.zkip_verifier = ZKIPVerifier()
        self.difficulty_adjuster = DifficultyAdjuster()
        self.slashing_manager = SlashingManager()
        self.work_generator: Optional[WorkBlockGenerator] = None
        self._mining = False
        self._miner_keypair: Optional[KeyPair] = None

        # P2P Mining Subnet (fully decentralized, no central server)
        self.p2p_subnet: Optional[MiningP2PSubnet] = None

    def initialize_miner(self, keypair: KeyPair):
        """Initialize the miner with a keypair."""
        self._miner_keypair = keypair
        self.work_generator = WorkBlockGenerator(keypair, self.blockchain)
        logger.info(f"Miner initialized: {keypair.to_address()}")

    def set_p2p_subnet(self, subnet: MiningP2PSubnet):
        """
        Attach the P2P mining subnet for fully decentralized miner discovery.
        This replaces the old CONFIG.ollama.remote_instances static list.
        """
        self.p2p_subnet = subnet
        self.orchestrator.set_p2p_subnet(subnet)
        logger.info("P2P mining subnet attached to consensus engine")

    async def start_mining(self, model_name: str = "phi3-mini") -> bool:
        """Start the mining process.

        Miners are discovered via the P2P subnet (DHT + gossip) instead of
        a static list of nodes. If p2p_mining_enabled is False, falls back
        to local-only mining.
        """
        if not self._miner_keypair:
            logger.error("Miner not initialized")
            return False

        # Start the P2P mining subnet if enabled
        if CONFIG.ollama.p2p_mining_enabled and self.p2p_subnet:
            await self.p2p_subnet.start()
            logger.info("P2P mining subnet started for miner discovery")

        # NOTE: The old CONFIG.ollama.remote_instances loop has been removed.
        # Remote miners are now discovered dynamically via the P2P subnet's
        # DHT + gossip protocol. No central server or static node list needed.

        success = await self.orchestrator.start(model_name)
        if success:
            self._mining = True
            logger.info(f"Mining started with model {model_name} "
                        f"(P2P discovery: {CONFIG.ollama.p2p_mining_enabled})")
        return success

    async def stop_mining(self):
        """Stop the mining process."""
        self._mining = False
        await self.orchestrator.stop()

        # Stop the P2P mining subnet
        if self.p2p_subnet:
            await self.p2p_subnet.stop()

        logger.info("Mining stopped")

    async def mine_block(self) -> Optional[Block]:
        """
        Perform one mining cycle: process a job and create a block.
        Jobs can be obtained from the P2P subnet (distributed) or
        created locally for self-mining.
        """
        if not self._mining or not self.orchestrator.is_running():
            return None

        # Check if there are pending jobs from the P2P subnet to work on
        subnet_job: Optional[MiningSubnetJob] = None
        if self.p2p_subnet and CONFIG.ollama.p2p_mining_enabled:
            for jid, job in list(self.p2p_subnet._pending_jobs.items()):
                if job.model_name == self.orchestrator.active_model.full_name:
                    if jid not in self.p2p_subnet._claimed_jobs:
                        subnet_job = job
                        break

        if subnet_job:
            # Process a job from the P2P subnet (distributed mining)
            logger.info(f"Processing P2P subnet job {subnet_job.job_id[:16]}...")

            # Claim it
            await self.p2p_subnet.claim_job(subnet_job.job_id)

            # Convert to internal InferenceJob
            inference_job = InferenceJob(
                job_id=subnet_job.job_id,
                model_name=subnet_job.model_name,
                input_data=subnet_job.prompt.encode("utf-8"),
                seed_params=subnet_job.seed_params,
                difficulty_target=subnet_job.difficulty_target,
                reward=subnet_job.reward,
            )

            # Process the job
            result = await self.orchestrator.submit_job(inference_job)
            if not result:
                logger.warning(f"Failed to process P2P subnet job {subnet_job.job_id[:16]}...")
                return None

            # Submit result back to the subnet
            await self.p2p_subnet.submit_result(
                subnet_job.job_id,
                {
                    "output": result.output_tokens.decode("utf-8", errors="replace"),
                    "processing_time_ms": result.processing_time_ms,
                    "tensor_commitment": result.tensor_commitment.hex(),
                    "model": result.model_name,
                    "instance_id": result.instance_id,
                }
            )

            # Verify the result
            if not self.zkip_verifier.verify_tensor_commitment(result, inference_job):
                logger.warning("Tensor commitment verification failed for subnet job")
                if self._miner_keypair:
                    self.slashing_manager.record_violation(
                        self._miner_keypair.to_address()
                    )
                return None

            if not self.zkip_verifier.verify_inference_time(
                result, self.orchestrator.active_model
            ):
                logger.warning("Inference time verification failed for subnet job")
                return None

        else:
            # No pending subnet jobs — create a local job (self-mining)
            inference_job = InferenceJob(
                job_id=hashlib.sha3_256(str(time.time()).encode()).hexdigest()[:16],
                model_name=self.orchestrator.active_model.full_name,
                input_data=os.urandom(64),
                seed_params=os.urandom(CONFIG.consensus.zkip_challenge_size),
                difficulty_target=self.blockchain.state.difficulty,
            )

            # Announce the job to the P2P subnet so other miners can work on it too
            if self.p2p_subnet and CONFIG.ollama.p2p_mining_enabled:
                subnet_job = MiningSubnetJob(
                    job_id=inference_job.job_id,
                    model_name=inference_job.model_name,
                    prompt=inference_job.input_data.hex(),
                    seed_params=inference_job.seed_params,
                    difficulty_target=inference_job.difficulty_target,
                    reward=inference_job.reward,
                    requester_id=self._miner_keypair.to_address() if self._miner_keypair else "",
                )
                await self.p2p_subnet.announce_job(subnet_job)

            # Process locally
            result = await self.orchestrator.submit_job(inference_job)
            if not result:
                return None

            # Verify
            if not self.zkip_verifier.verify_tensor_commitment(result, inference_job):
                logger.warning("Tensor commitment verification failed")
                if self._miner_keypair:
                    self.slashing_manager.record_violation(
                        self._miner_keypair.to_address()
                    )
                return None

            if not self.zkip_verifier.verify_inference_time(
                result, self.orchestrator.active_model
            ):
                logger.warning("Inference time verification failed")
                return None

        # Get mempool transactions
        mempool_txs = self.blockchain.get_mempool_txs()

        # Create the work block
        prev_block = self.blockchain.get_latest_block()
        block = self.work_generator.create_work_block(
            result, mempool_txs, prev_block
        )

        # Update difficulty
        new_difficulty = self.difficulty_adjuster.calculate_difficulty(
            self.blockchain
        )
        self.blockchain.state.difficulty = new_difficulty

        logger.info(
            f"Block {block.header.height} mined with {len(mempool_txs)} transactions"
        )
        return block

    def is_mining(self) -> bool:
        return self._mining

    def get_mining_stats(self) -> Dict[str, Any]:
        """Get current mining statistics, including P2P subnet info."""
        hw = self.orchestrator.manager.hardware
        stats = {
            "mining": self._mining,
            "hardware": {
                "backend": hw.backend.value,
                "cpu_name": hw.cpu_name,
                "cpu_threads": hw.cpu_threads,
                "ram_total_gb": round(hw.ram_total_gb, 1),
                "gpu_name": hw.gpu_name,
                "vram_total_gb": hw.vram_total_gb,
                "vram_free_gb": hw.vram_free_gb,
            },
            "model": self.orchestrator.active_model.full_name
                     if self.orchestrator.active_model else None,
            "instances": {
                "total": len(self.orchestrator.manager.instances),
                "healthy": len(self.orchestrator.manager.get_healthy_instances()),
            },
            "difficulty": self.blockchain.state.difficulty,
            "height": self.blockchain.state.height,
        }

        # Add P2P subnet stats
        if self.p2p_subnet:
            p2p_status = self.p2p_subnet.get_subnet_status()
            stats["p2p_subnet"] = {
                "enabled": CONFIG.ollama.p2p_mining_enabled,
                "known_miners": p2p_status["miners"]["total_known"],
                "alive_miners": p2p_status["miners"]["alive"],
                "remote_miners": p2p_status["miners"]["remote"],
                "pending_jobs": p2p_status["jobs"]["pending"],
                "claimed_jobs": p2p_status["jobs"]["claimed"],
                "completed_jobs": p2p_status["jobs"]["completed"],
            }

        return stats
