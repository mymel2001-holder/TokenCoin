"""
TokenCoin Distributed Ollama Miner
====================================
Manages local and remote Ollama instances for Proof-of-Useful-Work mining.
Supports CPU, NVIDIA GPU (CUDA), AMD GPU (ROCm), and Apple Silicon (Metal).

Key features:
  - Automatic hardware detection (CPU, GPU type, VRAM/RAM)
  - Local Ollama daemon management (start/stop/health)
  - Remote Ollama instance connection (distributed mining cluster)
  - Model pulling and management
  - Inference job submission and result collection
  - Graceful fallback between hardware backends
"""

import asyncio
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple, Any, Callable
from pathlib import Path

import aiohttp

from tokencoin.config import CONFIG

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hardware Backend Detection
# ---------------------------------------------------------------------------

class HardwareBackend(Enum):
    """Supported hardware backends for running Ollama models."""
    CPU = "cpu"
    CUDA = "cuda"       # NVIDIA GPU
    ROCM = "rocm"       # AMD GPU
    METAL = "metal"     # Apple Silicon
    VULKAN = "vulkan"   # Vulkan (cross-platform GPU)
    UNKNOWN = "unknown"


@dataclass
class HardwareInfo:
    """Detected hardware capabilities of the mining node."""
    backend: HardwareBackend = HardwareBackend.UNKNOWN
    backend_version: str = ""
    
    # CPU info
    cpu_name: str = "Unknown"
    cpu_cores: int = 0
    cpu_threads: int = 0
    ram_total_gb: float = 0.0
    ram_free_gb: float = 0.0
    
    # GPU info (if available)
    gpu_name: str = ""
    gpu_count: int = 0
    vram_total_gb: int = 0
    vram_free_gb: int = 0
    gpu_temperature_c: float = 0.0
    
    # Ollama-specific
    ollama_version: str = ""
    ollama_available: bool = False
    
    @property
    def has_gpu(self) -> bool:
        return self.backend in (HardwareBackend.CUDA, HardwareBackend.ROCM,
                                HardwareBackend.METAL, HardwareBackend.VULKAN)
    
    @property
    def effective_memory_gb(self) -> float:
        """Return usable memory for model loading (VRAM if GPU, RAM if CPU)."""
        if self.has_gpu and self.vram_total_gb > 0:
            return float(self.vram_total_gb)
        return self.ram_total_gb
    
    def can_run_model(self, model: "OllamaModel") -> bool:
        """Check if hardware can run a given model based on memory requirements."""
        return self.effective_memory_gb >= model.min_memory_gb
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "backend": self.backend.value,
            "backend_version": self.backend_version,
            "cpu": {
                "name": self.cpu_name,
                "cores": self.cpu_cores,
                "threads": self.cpu_threads,
            },
            "ram": {
                "total_gb": round(self.ram_total_gb, 1),
                "free_gb": round(self.ram_free_gb, 1),
            },
            "gpu": {
                "name": self.gpu_name,
                "count": self.gpu_count,
                "vram_total_gb": self.vram_total_gb,
                "vram_free_gb": self.vram_free_gb,
                "temperature_c": self.gpu_temperature_c,
            } if self.has_gpu else None,
            "ollama": {
                "version": self.ollama_version,
                "available": self.ollama_available,
            },
        }


def detect_hardware() -> HardwareInfo:
    """
    Detect the local machine's hardware capabilities.
    Checks for NVIDIA GPU (nvidia-smi), AMD GPU (rocm-smi),
    Apple Silicon (Metal), and falls back to CPU.
    """
    info = HardwareInfo()
    
    # --- CPU / RAM detection ---
    try:
        if sys.platform == "darwin":
            # macOS
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                info.ram_total_gb = int(result.stdout.strip()) / (1024**3)
            
            result = subprocess.run(
                ["sysctl", "-n", "hw.ncpu"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                info.cpu_threads = int(result.stdout.strip())
            
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                info.cpu_name = result.stdout.strip()
            
            # Check for Apple Silicon
            result = subprocess.run(
                ["sysctl", "-n", "hw.optional.arm64"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip() == "1":
                info.cpu_name = "Apple Silicon"
                
        elif sys.platform == "linux":
            # Linux
            result = subprocess.run(
                ["nproc"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                info.cpu_threads = int(result.stdout.strip())
            
            # Read CPU info
            try:
                with open("/proc/cpuinfo") as f:
                    for line in f:
                        if line.startswith("model name"):
                            info.cpu_name = line.split(":")[1].strip()
                            break
            except (FileNotFoundError, IOError):
                pass
            
            # Read RAM info
            try:
                with open("/proc/meminfo") as f:
                    for line in f:
                        if line.startswith("MemTotal"):
                            info.ram_total_gb = int(line.split()[1]) / 1024 / 1024
                        elif line.startswith("MemAvailable"):
                            info.ram_free_gb = int(line.split()[1]) / 1024 / 1024
                            break
            except (FileNotFoundError, IOError):
                pass
                
        elif sys.platform == "win32":
            # Windows
            result = subprocess.run(
                ["wmic", "cpu", "get", "name", "/format:value"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if "=" in line:
                        info.cpu_name = line.split("=", 1)[1].strip()
                        break
            
            result = subprocess.run(
                ["wmic", "ComputerSystem", "get", "TotalPhysicalMemory"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if line.strip().isdigit():
                        info.ram_total_gb = int(line.strip()) / (1024**3)
                        break
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError) as e:
        logger.debug(f"CPU/RAM detection failed: {e}")
    
    info.cpu_cores = max(1, os.cpu_count() or 1)
    if info.cpu_threads == 0:
        info.cpu_threads = info.cpu_cores
    
    # --- GPU detection ---
    
    # 1. NVIDIA GPU (CUDA)
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            lines = result.stdout.strip().splitlines()
            info.gpu_count = len(lines)
            first = lines[0].split(", ")
            if len(first) >= 1:
                info.gpu_name = first[0]
            if len(first) >= 2:
                try:
                    info.vram_total_gb = int(float(first[1])) // 1024
                except ValueError:
                    pass
            if len(first) >= 3:
                try:
                    info.vram_free_gb = int(float(first[2])) // 1024
                except ValueError:
                    pass
            if len(first) >= 4:
                try:
                    info.gpu_temperature_c = float(first[3])
                except ValueError:
                    pass
            
            info.backend = HardwareBackend.CUDA
            # Get CUDA version
            cuda_result = subprocess.run(
                ["nvidia-smi", "--query", "--display=DRIVER_VERSION"],
                capture_output=True, text=True, timeout=5
            )
            if cuda_result.returncode == 0:
                for line in cuda_result.stdout.splitlines():
                    if "CUDA Version" in line:
                        info.backend_version = line.split(":")[1].strip()
                        break
            
            logger.info(f"Detected NVIDIA GPU: {info.gpu_name} "
                       f"({info.vram_total_gb}GB VRAM, CUDA {info.backend_version})")
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError) as e:
        logger.debug(f"NVIDIA GPU detection failed: {e}")
    
    # 2. AMD GPU (ROCm) - only if no NVIDIA detected
    if info.backend == HardwareBackend.UNKNOWN:
        try:
            result = subprocess.run(
                ["rocm-smi", "--showproductname", "--json"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                try:
                    rocm_data = json.loads(result.stdout)
                    if isinstance(rocm_data, dict):
                        info.gpu_count = len(rocm_data)
                        for gpu_id, gpu_info in rocm_data.items():
                            if isinstance(gpu_info, dict):
                                info.gpu_name = gpu_info.get("Card series", 
                                                             gpu_info.get("Product name", "AMD GPU"))
                                break
                except json.JSONDecodeError:
                    pass
                
                # Get VRAM info
                vram_result = subprocess.run(
                    ["rocm-smi", "--showmeminfo", "vram", "--json"],
                    capture_output=True, text=True, timeout=5
                )
                if vram_result.returncode == 0:
                    try:
                        vram_data = json.loads(vram_result.stdout)
                        if isinstance(vram_data, dict):
                            for gpu_id, gpu_info in vram_data.items():
                                if isinstance(gpu_info, dict):
                                    total = gpu_info.get("VRAM Total", "0 MB")
                                    info.vram_total_gb = int(total.split()[0]) // 1024
                                    free = gpu_info.get("VRAM Free", "0 MB")
                                    info.vram_free_gb = int(free.split()[0]) // 1024
                                    break
                    except (json.JSONDecodeError, ValueError, IndexError):
                        pass
                
                info.backend = HardwareBackend.ROCM
                logger.info(f"Detected AMD GPU: {info.gpu_name} "
                           f"({info.vram_total_gb}GB VRAM, ROCm)")
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.debug(f"AMD GPU detection failed: {e}")
    
    # 3. Apple Silicon (Metal)
    if info.backend == HardwareBackend.UNKNOWN and sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "hw.optional.arm64"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip() == "1":
                info.backend = HardwareBackend.METAL
                info.gpu_name = "Apple Silicon (M-series)"
                info.gpu_count = 1
                
                # Apple Silicon has unified memory - use RAM as effective VRAM
                info.vram_total_gb = int(info.ram_total_gb)
                info.vram_free_gb = int(info.ram_free_gb) if info.ram_free_gb > 0 else int(info.ram_total_gb * 0.5)
                
                logger.info(f"Detected Apple Silicon: {info.ram_total_gb:.0f}GB unified memory")
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.debug(f"Apple Silicon detection failed: {e}")
    
    # 4. Vulkan (cross-platform GPU support)
    if info.backend == HardwareBackend.UNKNOWN:
        try:
            result = subprocess.run(
                ["vulkaninfo", "--summary"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and "GPU" in result.stdout:
                info.backend = HardwareBackend.VULKAN
                info.gpu_name = "Vulkan-compatible GPU"
                logger.info("Detected Vulkan-compatible GPU")
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.debug(f"Vulkan detection failed: {e}")
    
    # 5. Fallback to CPU
    if info.backend == HardwareBackend.UNKNOWN:
        info.backend = HardwareBackend.CPU
        logger.info(f"No GPU detected, using CPU ({info.cpu_threads} threads, "
                   f"{info.ram_total_gb:.0f}GB RAM)")
    
    # --- Ollama availability check ---
    info.ollama_available = _check_ollama_available()
    if info.ollama_available:
        info.ollama_version = _get_ollama_version()
    
    return info


def _check_ollama_available() -> bool:
    """Check if Ollama is installed and available."""
    try:
        result = subprocess.run(
            ["ollama", "--version"],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def _get_ollama_version() -> str:
    """Get the installed Ollama version."""
    try:
        result = subprocess.run(
            ["ollama", "--version"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except FileNotFoundError:
        pass
    return ""


# ---------------------------------------------------------------------------
# Ollama Model Registry
# ---------------------------------------------------------------------------

@dataclass
class OllamaModel:
    """Represents an Ollama model that can be used for mining."""
    name: str
    tag: str = "latest"
    min_memory_gb: float = 4.0  # Minimum RAM/VRAM required
    inference_type: str = "llm"  # "llm", "embedding", "vision"
    parameters_billions: float = 0.0
    quantization: str = "q4_0"  # Default quantization
    
    @property
    def full_name(self) -> str:
        """Full model name with tag."""
        return f"{self.name}:{self.tag}"
    
    def is_compatible(self, hardware: HardwareInfo) -> bool:
        """Check if this model can run on the given hardware."""
        return hardware.effective_memory_gb >= self.min_memory_gb
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.full_name,
            "min_memory_gb": self.min_memory_gb,
            "type": self.inference_type,
            "parameters_b": self.parameters_billions,
            "quantization": self.quantization,
        }


# Pre-defined Ollama models suitable for mining
# These are models that can run on various hardware backends
OLLAMA_MODELS: Dict[str, OllamaModel] = {
    # Small models (CPU-friendly, 4-8GB RAM)
    "phi3-mini": OllamaModel(
        name="phi3",
        tag="mini",
        min_memory_gb=4.0,
        inference_type="llm",
        parameters_billions=3.8,
        quantization="q4_0",
    ),
    "tinyllama": OllamaModel(
        name="tinyllama",
        tag="latest",
        min_memory_gb=3.0,
        inference_type="llm",
        parameters_billions=1.1,
        quantization="q4_0",
    ),
    "phi3-small": OllamaModel(
        name="phi3",
        tag="small",
        min_memory_gb=6.0,
        inference_type="llm",
        parameters_billions=7.0,
        quantization="q4_0",
    ),
    
    # Medium models (GPU recommended, 8-16GB VRAM)
    "llama3.2-3b": OllamaModel(
        name="llama3.2",
        tag="3b",
        min_memory_gb=4.0,
        inference_type="llm",
        parameters_billions=3.0,
        quantization="q4_0",
    ),
    "mistral-7b": OllamaModel(
        name="mistral",
        tag="7b",
        min_memory_gb=8.0,
        inference_type="llm",
        parameters_billions=7.0,
        quantization="q4_0",
    ),
    "llama3.1-8b": OllamaModel(
        name="llama3.1",
        tag="8b",
        min_memory_gb=8.0,
        inference_type="llm",
        parameters_billions=8.0,
        quantization="q4_0",
    ),
    "gemma2-9b": OllamaModel(
        name="gemma2",
        tag="9b",
        min_memory_gb=10.0,
        inference_type="llm",
        parameters_billions=9.0,
        quantization="q4_0",
    ),
    
    # Large models (high-end GPU, 16-32GB VRAM)
    "llama3.1-70b": OllamaModel(
        name="llama3.1",
        tag="70b",
        min_memory_gb=40.0,
        inference_type="llm",
        parameters_billions=70.0,
        quantization="q4_0",
    ),
    "mixtral-8x7b": OllamaModel(
        name="mixtral",
        tag="8x7b",
        min_memory_gb=32.0,
        inference_type="llm",
        parameters_billions=47.0,
        quantization="q4_0",
    ),
    
    # Embedding models (lightweight, good for CPU)
    "nomic-embed-text": OllamaModel(
        name="nomic-embed-text",
        tag="latest",
        min_memory_gb=2.0,
        inference_type="embedding",
        parameters_billions=0.14,
        quantization="q4_0",
    ),
    "all-minilm": OllamaModel(
        name="all-minilm",
        tag="latest",
        min_memory_gb=1.0,
        inference_type="embedding",
        parameters_billions=0.03,
        quantization="q4_0",
    ),
}


# ---------------------------------------------------------------------------
# Ollama Instance Manager
# ---------------------------------------------------------------------------

class OllamaInstanceStatus(Enum):
    """Status of an Ollama instance."""
    UNKNOWN = "unknown"
    INSTALLING = "installing"
    INSTALLED = "installed"
    STARTING = "starting"
    RUNNING = "running"
    BUSY = "busy"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"
    UNREACHABLE = "unreachable"


@dataclass
class OllamaInstance:
    """
    Represents a single Ollama instance (local or remote).
    Each instance can serve one model at a time for mining.
    """
    instance_id: str
    host: str = "127.0.0.1"
    port: int = 11434
    status: OllamaInstanceStatus = OllamaInstanceStatus.UNKNOWN
    hardware: Optional[HardwareInfo] = None
    active_model: Optional[str] = None
    is_local: bool = True
    last_seen: float = 0.0
    jobs_completed: int = 0
    jobs_failed: int = 0
    total_processing_time_ms: float = 0.0
    
    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"
    
    @property
    def is_healthy(self) -> bool:
        return self.status == OllamaInstanceStatus.RUNNING
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "host": self.host,
            "port": self.port,
            "status": self.status.value,
            "is_local": self.is_local,
            "active_model": self.active_model,
            "jobs_completed": self.jobs_completed,
            "jobs_failed": self.jobs_failed,
            "hardware": self.hardware.to_dict() if self.hardware else None,
        }


class OllamaManager:
    """
    Manages one or more Ollama instances for distributed mining.
    Handles:
      - Local Ollama daemon lifecycle
      - Remote instance registration and health checks
      - Model pulling and management
      - Job distribution across instances
    """
    
    def __init__(self):
        self.hardware = detect_hardware()
        self.instances: Dict[str, OllamaInstance] = {}
        self._local_instance: Optional[OllamaInstance] = None
        self._ollama_available = self.hardware.ollama_available
        self._server_process: Optional[asyncio.subprocess.Process] = None
        self._health_check_task: Optional[asyncio.Task] = None
        self._running = False
        
        # Create local instance
        local_id = hashlib.sha3_256(
            f"local_{platform.node()}_{int(time.time())}".encode()
        ).hexdigest()[:12]
        self._local_instance = OllamaInstance(
            instance_id=local_id,
            host="127.0.0.1",
            port=CONFIG.ollama.default_port,
            is_local=True,
            hardware=self.hardware,
            status=OllamaInstanceStatus.INSTALLED if self._ollama_available
                   else OllamaInstanceStatus.UNKNOWN,
        )
        self.instances[local_id] = self._local_instance
    
    # ------------------------------------------------------------------
    # Local Ollama Daemon Management
    # ------------------------------------------------------------------
    
    async def start_local_daemon(self) -> bool:
        """
        Start the local Ollama daemon if it's not already running.
        On macOS: 'ollama serve' runs as a background service.
        On Linux: can run 'ollama serve' directly.
        """
        if not self._ollama_available:
            logger.error("Ollama is not installed. Install it from https://ollama.ai")
            return False
        
        # Check if already running
        if await self._check_local_health():
            logger.info("Local Ollama daemon is already running")
            self._local_instance.status = OllamaInstanceStatus.RUNNING
            return True
        
        logger.info("Starting local Ollama daemon...")
        self._local_instance.status = OllamaInstanceStatus.STARTING
        
        try:
            # Start ollama serve in background
            self._server_process = await asyncio.create_subprocess_exec(
                "ollama", "serve",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            
            # Wait for daemon to be ready
            for attempt in range(10):
                await asyncio.sleep(2)
                if await self._check_local_health():
                    self._local_instance.status = OllamaInstanceStatus.RUNNING
                    logger.info("Local Ollama daemon started successfully")
                    return True
                logger.debug(f"Waiting for Ollama daemon... (attempt {attempt + 1}/10)")
            
            logger.error("Ollama daemon failed to start within timeout")
            self._local_instance.status = OllamaInstanceStatus.ERROR
            return False
            
        except FileNotFoundError:
            logger.error("Ollama binary not found. Install from https://ollama.ai")
            self._local_instance.status = OllamaInstanceStatus.ERROR
            return False
        except Exception as e:
            logger.error(f"Failed to start Ollama daemon: {e}")
            self._local_instance.status = OllamaInstanceStatus.ERROR
            return False
    
    async def stop_local_daemon(self):
        """Stop the local Ollama daemon."""
        if self._server_process:
            logger.info("Stopping local Ollama daemon...")
            self._local_instance.status = OllamaInstanceStatus.STOPPING
            try:
                self._server_process.terminate()
                await asyncio.wait_for(self._server_process.wait(), timeout=10)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._server_process.kill()
                except ProcessLookupError:
                    pass
            self._server_process = None
            self._local_instance.status = OllamaInstanceStatus.STOPPED
            logger.info("Local Ollama daemon stopped")
    
    async def _check_local_health(self) -> bool:
        """Check if the local Ollama daemon is responding."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._local_instance.base_url}/api/tags",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        self._local_instance.last_seen = time.time()
                        return True
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass
        return False
    
    # ------------------------------------------------------------------
    # Remote Instance Management
    # ------------------------------------------------------------------
    
    def add_remote_instance(self, host: str, port: int = 11434) -> str:
        """
        Register a remote Ollama instance for distributed mining.
        Returns the instance ID.
        """
        instance_id = hashlib.sha3_256(
            f"remote_{host}:{port}_{time.time()}".encode()
        ).hexdigest()[:12]
        
        instance = OllamaInstance(
            instance_id=instance_id,
            host=host,
            port=port,
            is_local=False,
            status=OllamaInstanceStatus.UNKNOWN,
        )
        self.instances[instance_id] = instance
        logger.info(f"Added remote Ollama instance: {host}:{port} ({instance_id})")
        return instance_id
    
    def remove_remote_instance(self, instance_id: str) -> bool:
        """Remove a remote Ollama instance."""
        if instance_id in self.instances and not self.instances[instance_id].is_local:
            del self.instances[instance_id]
            logger.info(f"Removed remote instance: {instance_id}")
            return True
        return False
    
    async def check_instance_health(self, instance: OllamaInstance) -> bool:
        """Check health of any Ollama instance (local or remote)."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{instance.base_url}/api/tags",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        instance.status = OllamaInstanceStatus.RUNNING
                        instance.last_seen = time.time()
                        
                        # Try to detect hardware info from remote
                        try:
                            data = await resp.json()
                            instance.active_model = None
                        except (json.JSONDecodeError, aiohttp.ClientError):
                            pass
                        
                        return True
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.debug(f"Instance {instance.instance_id} health check failed: {e}")
        
        instance.status = OllamaInstanceStatus.UNREACHABLE
        return False
    
    async def health_check_all(self) -> Dict[str, bool]:
        """Check health of all registered instances."""
        results = {}
        tasks = []
        for inst_id, instance in self.instances.items():
            tasks.append(self.check_instance_health(instance))
        
        health_results = await asyncio.gather(*tasks, return_exceptions=True)
        for inst_id, result in zip(self.instances.keys(), health_results):
            results[inst_id] = bool(result) if not isinstance(result, Exception) else False
        
        return results
    
    def get_healthy_instances(self) -> List[OllamaInstance]:
        """Get all instances that are currently healthy."""
        return [inst for inst in self.instances.values() if inst.is_healthy]
    
    # ------------------------------------------------------------------
    # Model Management
    # ------------------------------------------------------------------
    
    async def pull_model(self, model: OllamaModel,
                         instance: Optional[OllamaInstance] = None) -> bool:
        """
        Pull a model on a specific instance (or local by default).
        """
        target = instance or self._local_instance
        if not target:
            logger.error("No instance available to pull model")
            return False
        
        logger.info(f"Pulling model {model.full_name} on {target.instance_id}...")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{target.base_url}/api/pull",
                    json={"name": model.full_name, "stream": False},
                    timeout=aiohttp.ClientTimeout(total=600),  # 10 min timeout for large models
                ) as resp:
                    if resp.status == 200:
                        logger.info(f"Model {model.full_name} pulled successfully")
                        return True
                    else:
                        error_text = await resp.text()
                        logger.error(f"Failed to pull model: {error_text}")
                        return False
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error(f"Failed to pull model {model.full_name}: {e}")
            return False
    
    async def list_models(self, instance: Optional[OllamaInstance] = None) -> List[Dict[str, Any]]:
        """List available models on an instance."""
        target = instance or self._local_instance
        if not target:
            return []
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{target.base_url}/api/tags",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("models", [])
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.debug(f"Failed to list models: {e}")
        
        return []
    
    async def delete_model(self, model_name: str,
                           instance: Optional[OllamaInstance] = None) -> bool:
        """Delete a model from an instance."""
        target = instance or self._local_instance
        if not target:
            return False
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(
                    f"{target.base_url}/api/delete",
                    json={"name": model_name},
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    return resp.status == 200
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False
    
    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------
    
    async def generate(self, model: OllamaModel, prompt: str,
                       instance: Optional[OllamaInstance] = None,
                       options: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """
        Send a generation request to an Ollama instance.
        This is the core "useful work" in PoUW.
        """
        target = instance or self._local_instance
        if not target or not target.is_healthy:
            logger.error(f"Instance {target.instance_id if target else 'None'} not healthy")
            return None
        
        payload = {
            "model": model.full_name,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": CONFIG.ollama.max_tokens_per_job,
                "temperature": CONFIG.ollama.inference_temperature,
                "seed": int(time.time() * 1000) % (2**31),  # Deterministic seed
                **(options or {}),
            },
        }
        
        start_time = time.time()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{target.base_url}/api/generate",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=CONFIG.ollama.job_timeout_seconds),
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        processing_time = (time.time() - start_time) * 1000
                        
                        target.jobs_completed += 1
                        target.total_processing_time_ms += processing_time
                        
                        return {
                            "response": result.get("response", ""),
                            "total_duration": result.get("total_duration", 0),
                            "load_duration": result.get("load_duration", 0),
                            "prompt_eval_count": result.get("prompt_eval_count", 0),
                            "eval_count": result.get("eval_count", 0),
                            "eval_duration": result.get("eval_duration", 0),
                            "processing_time_ms": processing_time,
                            "instance_id": target.instance_id,
                        }
                    else:
                        error_text = await resp.text()
                        logger.error(f"Generation failed: {error_text}")
                        target.jobs_failed += 1
                        return None
                        
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error(f"Generation request failed: {e}")
            target.jobs_failed += 1
            target.status = OllamaInstanceStatus.UNREACHABLE
            return None
    
    async def embed(self, model: OllamaModel, text: str,
                    instance: Optional[OllamaInstance] = None) -> Optional[List[float]]:
        """
        Generate embeddings using an Ollama instance.
        Lighter-weight than generation, good for CPU mining.
        """
        target = instance or self._local_instance
        if not target or not target.is_healthy:
            return None
        
        payload = {
            "model": model.full_name,
            "prompt": text,
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{target.base_url}/api/embeddings",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        target.jobs_completed += 1
                        return result.get("embedding", [])
                    else:
                        target.jobs_failed += 1
                        return None
        except (aiohttp.ClientError, asyncio.TimeoutError):
            target.jobs_failed += 1
            return None
    
    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    
    async def start(self):
        """Start the Ollama manager and health check loop."""
        self._running = True
        
        # Start local daemon if Ollama is installed
        if self._ollama_available:
            await self.start_local_daemon()
        
        # Start background health check loop
        self._health_check_task = asyncio.create_task(self._health_loop())
        logger.info("Ollama manager started")
    
    async def stop(self):
        """Stop the Ollama manager and all instances."""
        self._running = False
        
        if self._health_check_task:
            self._health_check_task.cancel()
            self._health_check_task = None
        
        await self.stop_local_daemon()
        logger.info("Ollama manager stopped")
    
    async def _health_loop(self):
        """Background task that periodically checks instance health."""
        while self._running:
            await self.health_check_all()
            await asyncio.sleep(CONFIG.ollama.health_check_interval)
    
    def get_best_instance(self, model: OllamaModel) -> Optional[OllamaInstance]:
        """
        Select the best instance for running a given model.
        Prefers local instances with sufficient memory, then remote.
        """
        healthy = self.get_healthy_instances()
        if not healthy:
            return None
        
        # Filter by compatibility
        compatible = [
            inst for inst in healthy
            if inst.hardware and inst.hardware.can_run_model(model)
        ]
        
        if not compatible:
            # If no compatible instance, try any healthy one
            compatible = healthy
        
        # Prefer local, then least loaded
        compatible.sort(key=lambda i: (
            not i.is_local,  # Local first
            i.jobs_completed,  # Then least loaded
        ))
        
        return compatible[0] if compatible else None
    
    def get_stats(self) -> Dict[str, Any]:
        """Get aggregated statistics for all instances."""
        total_jobs = sum(i.jobs_completed for i in self.instances.values())
        total_failed = sum(i.jobs_failed for i in self.instances.values())
        healthy_count = sum(1 for i in self.instances.values() if i.is_healthy)
        
        return {
            "hardware": self.hardware.to_dict(),
            "instances": {
                "total": len(self.instances),
                "healthy": healthy_count,
                "unhealthy": len(self.instances) - healthy_count,
            },
            "jobs": {
                "completed": total_jobs,
                "failed": total_failed,
                "success_rate": (total_jobs / (total_jobs + total_failed) * 100)
                                if (total_jobs + total_failed) > 0 else 0,
            },
            "local_instance": self._local_instance.to_dict() if self._local_instance else None,
        }
