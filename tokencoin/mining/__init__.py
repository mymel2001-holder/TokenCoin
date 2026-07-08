"""
TokenCoin Mining Module
=======================
Implements the mining interface for Proof-of-Useful-Work via distributed Ollama.
Provides the "Start AI Mining" one-click toggle and real-time
visualization of hardware stats, model info, and TKC generation rate.

Key components:
  - Miner: Main mining controller
  - MiningStats: Real-time mining statistics
  - MiningVisualizer: Data for dashboard visualization
"""

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable
from enum import Enum

from tokencoin.config import CONFIG
from tokencoin.core.crypto import KeyPair
from tokencoin.consensus import (
    ConsensusEngine, OllamaOrchestrator, HardwareInfo, OllamaModel,
    OLLAMA_MODELS, InferenceJob, InferenceResult,
)
from tokencoin.mining.ollama_miner import HardwareBackend
from tokencoin.ledger import Blockchain, Block

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mining State
# ---------------------------------------------------------------------------

class MiningStatus(Enum):
    """Current mining status."""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


@dataclass
class MiningStats:
    """Real-time mining statistics for dashboard display."""
    # Status
    status: MiningStatus = MiningStatus.STOPPED
    uptime_seconds: float = 0.0

    # Hardware information
    backend: str = "N/A"
    cpu_name: str = "N/A"
    cpu_threads: int = 0
    ram_total_gb: float = 0.0
    gpu_name: str = "N/A"
    gpu_vram_total_gb: int = 0
    gpu_vram_used_gb: int = 0
    gpu_temperature_c: float = 0.0

    # Model information
    active_model: str = "N/A"
    model_params_b: float = 0.0

    # Mining performance
    hash_rate: float = 0.0  # Jobs per hour
    tkc_generation_rate: float = 0.0  # TKC per hour
    blocks_mined: int = 0
    total_reward_earned: int = 0  # In atomic units

    # Job statistics
    jobs_completed: int = 0
    jobs_failed: int = 0
    avg_job_time_ms: float = 0.0

    # Network
    network_difficulty: int = 1
    blockchain_height: int = 0

    # Distributed instances
    instances_total: int = 0
    instances_healthy: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API/UI display."""
        return {
            "status": self.status.value,
            "uptime": f"{self.uptime_seconds / 3600:.1f}h",
            "hardware": {
                "backend": self.backend,
                "cpu": {
                    "name": self.cpu_name,
                    "threads": self.cpu_threads,
                },
                "ram_gb": round(self.ram_total_gb, 1),
                "gpu": {
                    "name": self.gpu_name,
                    "vram": f"{self.gpu_vram_used_gb}/{self.gpu_vram_total_gb} GB",
                    "temperature": f"{self.gpu_temperature_c:.0f}°C",
                } if self.gpu_name != "N/A" else None,
            },
            "model": {
                "name": self.active_model,
                "parameters": f"{self.model_params_b}B",
            },
            "performance": {
                "hash_rate": f"{self.hash_rate:.1f} jobs/h",
                "tkc_rate": f"{self.tkc_generation_rate:.4f} TKC/h",
                "blocks_mined": self.blocks_mined,
                "total_reward": f"{self.total_reward_earned / 1e9:.4f} TKC",
            },
            "jobs": {
                "completed": self.jobs_completed,
                "failed": self.jobs_failed,
                "avg_time_ms": f"{self.avg_job_time_ms:.0f}",
            },
            "instances": {
                "total": self.instances_total,
                "healthy": self.instances_healthy,
            },
            "network": {
                "difficulty": self.network_difficulty,
                "height": self.blockchain_height,
            },
        }


# ---------------------------------------------------------------------------
# Miner
# ---------------------------------------------------------------------------

class Miner:
    """
    The main mining controller.
    Provides the one-click "Start AI Mining" interface.
    """

    def __init__(self, blockchain: Blockchain):
        self.blockchain = blockchain
        self.consensus = ConsensusEngine(blockchain)
        self.stats = MiningStats()
        self._miner_keypair: Optional[KeyPair] = None
        self._mining_task: Optional[asyncio.Task] = None
        self._start_time: float = 0.0
        self._status_callbacks: List[Callable] = []
        self._stats_callbacks: List[Callable] = []

    def initialize(self, keypair: KeyPair):
        """Initialize the miner with a keypair."""
        self._miner_keypair = keypair
        self.consensus.initialize_miner(keypair)
        logger.info(f"Miner initialized for {keypair.to_address()[:16]}...")

    def on_status_change(self, callback: Callable):
        """Register a callback for status changes."""
        self._status_callbacks.append(callback)

    def on_stats_update(self, callback: Callable):
        """Register a callback for stats updates."""
        self._stats_callbacks.append(callback)

    async def start(self, model_name: str = "phi3-mini") -> bool:
        """
        Start mining with the specified Ollama model.
        One-click toggle: [ Start AI Mining ]
        """
        if not self._miner_keypair:
            logger.error("Miner not initialized. Create or load a wallet first.")
            return False

        if self.stats.status == MiningStatus.RUNNING:
            logger.warning("Miner is already running")
            return False

        self.stats.status = MiningStatus.STARTING
        self._notify_status_change()

        # Start the consensus engine
        success = await self.consensus.start_mining(model_name)
        if not success:
            self.stats.status = MiningStatus.ERROR
            self._notify_status_change()
            return False

        # Start mining loop
        self._start_time = time.time()
        self.stats.status = MiningStatus.RUNNING
        self.stats.active_model = model_name

        # Populate hardware info from orchestrator
        hw = self.consensus.orchestrator.manager.hardware
        self.stats.backend = hw.backend.value
        self.stats.cpu_name = hw.cpu_name
        self.stats.cpu_threads = hw.cpu_threads
        self.stats.ram_total_gb = hw.ram_total_gb
        self.stats.gpu_name = hw.gpu_name if hw.has_gpu else "N/A"
        self.stats.gpu_vram_total_gb = hw.vram_total_gb
        self.stats.gpu_vram_used_gb = hw.vram_total_gb - hw.vram_free_gb

        # Model info
        if self.consensus.orchestrator.active_model:
            self.stats.model_params_b = self.consensus.orchestrator.active_model.parameters_billions

        # Instance info
        self.stats.instances_total = len(self.consensus.orchestrator.manager.instances)
        self.stats.instances_healthy = len(self.consensus.orchestrator.manager.get_healthy_instances())

        self._mining_task = asyncio.create_task(self._mining_loop())
        self._notify_status_change()

        logger.info(f"Mining started with model {model_name} on {hw.backend.value}")
        return True

    async def stop(self):
        """Stop mining."""
        if self._mining_task:
            self._mining_task.cancel()
            self._mining_task = None

        await self.consensus.stop_mining()
        self.stats.status = MiningStatus.STOPPED
        self._notify_status_change()
        logger.info("Mining stopped")

    async def toggle(self, model_name: str = "phi3-mini") -> bool:
        """
        Toggle mining on/off.
        This is the one-click interface.
        """
        if self.stats.status == MiningStatus.RUNNING:
            await self.stop()
            return False
        else:
            return await self.start(model_name)

    def get_stats(self) -> MiningStats:
        """Get current mining statistics."""
        if self.stats.status == MiningStatus.RUNNING:
            self.stats.uptime_seconds = time.time() - self._start_time
            self.stats.network_difficulty = self.blockchain.state.difficulty
            self.stats.blockchain_height = self.blockchain.state.height

            # Estimate TKC generation rate
            if self.stats.uptime_seconds > 0:
                blocks_per_hour = (self.stats.blocks_mined /
                                   (self.stats.uptime_seconds / 3600))
                if blocks_per_hour > 0:
                    reward_per_block = max(
                        CONFIG.monetary.initial_block_reward >>
                        (self.blockchain.state.height // CONFIG.monetary.halving_interval),
                        CONFIG.monetary.min_block_reward
                    )
                    self.stats.tkc_generation_rate = blocks_per_hour * reward_per_block

        return self.stats

    def is_mining(self) -> bool:
        return self.stats.status == MiningStatus.RUNNING

    def _notify_status_change(self):
        """Notify registered callbacks of status change."""
        for callback in self._status_callbacks:
            try:
                callback(self.stats.status)
            except Exception as e:
                logger.error(f"Status callback error: {e}")

    def _notify_stats_update(self):
        """Notify registered callbacks of stats update."""
        stats = self.get_stats()
        for callback in self._stats_callbacks:
            try:
                callback(stats)
            except Exception as e:
                logger.error(f"Stats callback error: {e}")

    async def _mining_loop(self):
        """
        The main mining loop.
        Continuously processes inference jobs and mines blocks.
        """
        while True:
            try:
                # Mine a block
                block = await self.consensus.mine_block()
                if block:
                    # Add block to blockchain
                    if self.blockchain.add_block(block):
                        self.stats.blocks_mined += 1
                        self.stats.total_reward_earned += (
                            CONFIG.monetary.initial_block_reward
                        )
                        self.stats.jobs_completed += 1
                        logger.info(
                            f"Block {block.header.height} mined! "
                            f"Total: {self.stats.blocks_mined}"
                        )
                    else:
                        self.stats.jobs_failed += 1
                else:
                    self.stats.jobs_failed += 1

                # Update stats
                self._notify_stats_update()

                # Wait before next mining cycle
                await asyncio.sleep(1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Mining loop error: {e}")
                self.stats.jobs_failed += 1
                await asyncio.sleep(5)  # Back off on error


# ---------------------------------------------------------------------------
# Mining Visualizer (Data for Dashboard)
# ---------------------------------------------------------------------------

class MiningVisualizer:
    """
    Provides formatted data for the mining dashboard visualization.
    Used by the UI to display hardware stats, model info, and TKC generation rate.
    """

    @staticmethod
    def format_hardware_info(stats: MiningStats) -> Dict[str, Any]:
        """Format hardware information for display."""
        vram_percent = 0
        if stats.gpu_vram_total_gb > 0:
            vram_percent = (stats.gpu_vram_used_gb / stats.gpu_vram_total_gb) * 100

        return {
            "backend": stats.backend,
            "cpu": {
                "name": stats.cpu_name,
                "threads": stats.cpu_threads,
            },
            "ram": {
                "total_gb": round(stats.ram_total_gb, 1),
            },
            "gpu": {
                "name": stats.gpu_name,
                "vram_bar": {
                    "used": stats.gpu_vram_used_gb,
                    "total": stats.gpu_vram_total_gb,
                    "percent": vram_percent,
                },
                "temperature": {
                    "current": stats.gpu_temperature_c,
                    "status": "normal" if stats.gpu_temperature_c < 80 else "hot",
                },
            } if stats.gpu_name != "N/A" else None,
        }

    @staticmethod
    def format_model_info(stats: MiningStats) -> Dict[str, Any]:
        """Format model information for display."""
        return {
            "name": stats.active_model,
            "parameters": f"{stats.model_params_b}B",
            "quantization": "Q4_0",
        }

    @staticmethod
    def format_performance(stats: MiningStats) -> Dict[str, Any]:
        """Format performance metrics for display."""
        return {
            "tkc_per_hour": f"{stats.tkc_generation_rate:.4f}",
            "blocks_mined": stats.blocks_mined,
            "total_earned": f"{stats.total_reward_earned / 1e9:.4f} TKC",
            "jobs_completed": stats.jobs_completed,
            "uptime": f"{stats.uptime_seconds / 3600:.1f} hours",
        }

    @staticmethod
    def get_dashboard_data(stats: MiningStats) -> Dict[str, Any]:
        """Get all dashboard data in one call."""
        return {
            "status": stats.status.value,
            "hardware": MiningVisualizer.format_hardware_info(stats),
            "model": MiningVisualizer.format_model_info(stats),
            "performance": MiningVisualizer.format_performance(stats),
            "instances": {
                "total": stats.instances_total,
                "healthy": stats.instances_healthy,
            },
            "network": {
                "difficulty": stats.network_difficulty,
                "height": stats.blockchain_height,
            },
        }
