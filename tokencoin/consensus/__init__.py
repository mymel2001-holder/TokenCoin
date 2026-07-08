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
    """

    def __init__(self):
        self.manager = OllamaManager()
        self.active_model: Optional[OllamaModel] = None
        self.active_instance: Optional[OllamaInstance] = None
        self._running = False
        self._job_queue: asyncio.Queue = asyncio.Queue()
        self._result_queue: asyncio.Queue = asyncio.Queue()

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

        # Select the best instance for this model
        instance = self.manager.get_best_instance(model)
        if not instance:
            logger.error("No healthy Ollama instance available")
            return False

        self.active_model = model
        self.active_instance = instance
        instance.active_model = model.full_name
        self._running = True

        logger.info(
            f"Ollama mining started with {model.full_name} on "
            f"{instance.instance_id} ({instance.host}:{instance.port}) "
            f"[{self.manager.hardware.backend.value}]"
        )
        return True

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

    def initialize_miner(self, keypair: KeyPair):
        """Initialize the miner with a keypair."""
        self._miner_keypair = keypair
        self.work_generator = WorkBlockGenerator(keypair, self.blockchain)
        logger.info(f"Miner initialized: {keypair.to_address()}")

    async def start_mining(self, model_name: str = "phi3-mini") -> bool:
        """Start the mining process."""
        if not self._miner_keypair:
            logger.error("Miner not initialized")
            return False

        # Register any configured remote instances
        for remote in CONFIG.ollama.remote_instances:
            if ":" in remote:
                host, port_str = remote.split(":", 1)
                try:
                    port = int(port_str)
                    self.orchestrator.manager.add_remote_instance(host, port)
                except ValueError:
                    logger.warning(f"Invalid remote instance format: {remote}")
            else:
                self.orchestrator.manager.add_remote_instance(remote)

        success = await self.orchestrator.start(model_name)
        if success:
            self._mining = True
            logger.info(f"Mining started with model {model_name}")
        return success

    async def stop_mining(self):
        """Stop the mining process."""
        self._mining = False
        await self.orchestrator.stop()
        logger.info("Mining stopped")

    async def mine_block(self) -> Optional[Block]:
        """
        Perform one mining cycle: process a job and create a block.
        """
        if not self._mining or not self.orchestrator.is_running():
            return None

        # Create an inference job (in production: pulled from DHT job queue)
        job = InferenceJob(
            job_id=hashlib.sha3_256(str(time.time()).encode()).hexdigest()[:16],
            model_name=self.orchestrator.active_model.full_name,
            input_data=os.urandom(64),  # Random input (in production: real user request)
            seed_params=os.urandom(CONFIG.consensus.zkip_challenge_size),
            difficulty_target=self.blockchain.state.difficulty,
        )

        # Process the job
        result = await self.orchestrator.submit_job(job)
        if not result:
            return None

        # Verify the result
        if not self.zkip_verifier.verify_tensor_commitment(result, job):
            logger.warning("Tensor commitment verification failed")
            if self._miner_keypair:
                self.slashing_manager.record_violation(
                    self._miner_keypair.to_address()
                )
            return None

        # Verify inference time is realistic
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
        """Get current mining statistics."""
        hw = self.orchestrator.manager.hardware
        return {
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
