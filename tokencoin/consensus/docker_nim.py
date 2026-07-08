"""
TokenCoin Ollama Docker Container Manager
==========================================
Manages Ollama Docker containers for distributed Proof-of-Useful-Work mining.
Supports CPU, NVIDIA GPU (CUDA), AMD GPU (ROCm), and Apple Silicon (Metal).

Handles:
  - Docker image pulling and verification
  - Container lifecycle (start, stop, restart)
  - GPU passthrough and resource limits
  - Health monitoring and auto-recovery
  - Model management
"""

import asyncio
import hashlib
import json
import logging
import os
import shutil
import struct
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple, Any
from pathlib import Path

from tokencoin.config import CONFIG

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Docker Status
# ---------------------------------------------------------------------------

class ContainerStatus(Enum):
    """Ollama container status."""
    NOT_FOUND = "not_found"
    PULLING = "pulling"
    PULLED = "pulled"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


@dataclass
class ContainerInfo:
    """Information about an Ollama container."""
    container_id: str = ""
    image: str = ""
    model_name: str = ""
    status: ContainerStatus = ContainerStatus.NOT_FOUND
    created_at: float = 0.0
    started_at: float = 0.0
    port: int = 0
    gpu_enabled: bool = False
    health_checks_passed: int = 0
    health_checks_failed: int = 0
    last_error: str = ""

    @property
    def is_healthy(self) -> bool:
        return (self.status == ContainerStatus.RUNNING and
                self.health_checks_failed < 3)


@dataclass
class OllamaDockerImage:
    """Ollama Docker image specification."""
    name: str
    image_tag: str
    internal_port: int = 11434
    health_endpoint: str = "/api/tags"
    env_vars: Dict[str, str] = field(default_factory=lambda: {
        "OLLAMA_HOST": "0.0.0.0",
        "OLLAMA_KEEP_ALIVE": "24h",
    })


# Pre-defined Ollama Docker images
OLLAMA_DOCKER_IMAGES: Dict[str, OllamaDockerImage] = {
    "ollama-cpu": OllamaDockerImage(
        name="ollama-cpu",
        image_tag="ollama/ollama:latest",
    ),
    "ollama-cuda": OllamaDockerImage(
        name="ollama-cuda",
        image_tag="ollama/ollama:latest",
        env_vars={
            "OLLAMA_HOST": "0.0.0.0",
            "OLLAMA_KEEP_ALIVE": "24h",
            "NVIDIA_VISIBLE_DEVICES": "all",
        },
    ),
    "ollama-rocm": OllamaDockerImage(
        name="ollama-rocm",
        image_tag="ollama/ollama:rocm",
    ),
}


# ---------------------------------------------------------------------------
# Docker Manager
# ---------------------------------------------------------------------------

class DockerManager:
    """
    Manages Docker operations for Ollama containers.
    Supports CPU, NVIDIA GPU (via nvidia-container-toolkit),
    and AMD GPU (via rocm) passthrough.
    """

    def __init__(self):
        self._available = self._check_docker()
        self._containers: Dict[str, ContainerInfo] = {}
        self._port_counter = 11434

    @staticmethod
    def _check_docker() -> bool:
        """Check if Docker is available."""
        try:
            result = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                logger.warning("Docker not available")
                return False

            logger.info(f"Docker {result.stdout.strip()} detected")
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.warning(f"Docker check failed: {e}")
            return False

    def is_available(self) -> bool:
        return self._available

    def _detect_gpu_runtime(self) -> str:
        """Detect the best GPU runtime for Docker."""
        # Check for NVIDIA
        try:
            result = subprocess.run(
                ["docker", "info", "--format", "{{.Runtimes}}"],
                capture_output=True, text=True, timeout=5
            )
            if "nvidia" in result.stdout.lower():
                return "nvidia"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return ""

    async def pull_image(self, image: OllamaDockerImage) -> bool:
        """Pull an Ollama Docker image."""
        if not self._available:
            logger.error("Docker not available")
            return False

        logger.info(f"Pulling Ollama image: {image.image_tag}")
        try:
            process = await asyncio.create_subprocess_exec(
                "docker", "pull", image.image_tag,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=300
            )
            if process.returncode == 0:
                logger.info(f"Successfully pulled {image.image_tag}")
                return True
            else:
                logger.error(f"Failed to pull {image.image_tag}: {stderr.decode()}")
                return False
        except (asyncio.TimeoutError, FileNotFoundError) as e:
            logger.error(f"Pull failed: {e}")
            return False

    async def start_container(self, image_name: str = "ollama-cpu",
                               model_name: Optional[str] = None) -> Optional[ContainerInfo]:
        """Start an Ollama Docker container."""
        if not self._available:
            logger.error("Docker not available")
            return None

        image = OLLAMA_DOCKER_IMAGES.get(image_name)
        if not image:
            logger.error(f"Unknown image: {image_name}")
            return None

        # Check if already running
        if image_name in self._containers:
            existing = self._containers[image_name]
            if existing.is_healthy:
                logger.info(f"Container for {image_name} already running")
                return existing
            await self.stop_container(image_name)

        # Assign port
        self._port_counter += 1
        host_port = self._port_counter

        # Build docker run command
        cmd = [
            "docker", "run",
            "-d",  # Detached
            "-p", f"{host_port}:{image.internal_port}",
            "--name", f"tokencoin-ollama-{image_name}",
            "--restart", "unless-stopped",
        ]

        # Add GPU support if available
        gpu_runtime = self._detect_gpu_runtime()
        if gpu_runtime == "nvidia" and "cuda" in image_name:
            cmd.extend(["--gpus", "all"])
        elif "rocm" in image_name:
            cmd.extend(["--device", "/dev/kfd", "--device", "/dev/dri"])

        # Add environment variables
        for key, value in image.env_vars.items():
            cmd.extend(["-e", f"{key}={value}"])

        # Add volume for model storage
        model_cache = os.path.join(CONFIG.data_dir, "ollama_models")
        os.makedirs(model_cache, exist_ok=True)
        cmd.extend(["-v", f"{model_cache}:/root/.ollama"])

        # Add image tag
        cmd.append(image.image_tag)

        logger.info(f"Starting Ollama container for {image_name} on port {host_port}")
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=60
            )

            if process.returncode != 0:
                logger.error(f"Failed to start container: {stderr.decode()}")
                return None

            container_id = stdout.decode().strip()[:12]

            info = ContainerInfo(
                container_id=container_id,
                image=image.image_tag,
                model_name=model_name or "",
                status=ContainerStatus.STARTING,
                created_at=time.time(),
                port=host_port,
                gpu_enabled=gpu_runtime == "nvidia" or "rocm" in image_name,
            )
            self._containers[image_name] = info

            # Wait for container to be ready
            await asyncio.sleep(5)
            info.status = ContainerStatus.RUNNING
            logger.info(f"Ollama container {container_id} started for {image_name}")
            return info

        except (asyncio.TimeoutError, FileNotFoundError) as e:
            logger.error(f"Container start failed: {e}")
            return None

    async def stop_container(self, image_name: str) -> bool:
        """Stop an Ollama container."""
        info = self._containers.get(image_name)
        if not info or not info.container_id:
            return False

        logger.info(f"Stopping Ollama container {info.container_id}")
        try:
            process = await asyncio.create_subprocess_exec(
                "docker", "stop", info.container_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(process.communicate(), timeout=30)

            # Remove container
            await asyncio.create_subprocess_exec(
                "docker", "rm", info.container_id,
            )
            info.status = ContainerStatus.STOPPED
            logger.info(f"Container {info.container_id} stopped")
            return True
        except Exception as e:
            logger.error(f"Failed to stop container: {e}")
            return False

    async def health_check(self, image_name: str) -> bool:
        """Check if an Ollama container is healthy."""
        info = self._containers.get(image_name)
        if not info or not info.container_id:
            return False

        try:
            process = await asyncio.create_subprocess_exec(
                "docker", "inspect",
                "--format", "{{.State.Status}}",
                info.container_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=10
            )
            status = stdout.decode().strip()

            if status == "running":
                info.health_checks_passed += 1
                info.status = ContainerStatus.HEALTHY
                return True
            else:
                info.health_checks_failed += 1
                info.status = ContainerStatus.UNHEALTHY
                if info.health_checks_failed >= 3:
                    logger.warning(f"Container {info.container_id} unhealthy, restarting")
                    await self.restart_container(image_name)
                return False
        except Exception as e:
            info.health_checks_failed += 1
            logger.error(f"Health check failed: {e}")
            return False

    async def restart_container(self, image_name: str) -> bool:
        """Restart an Ollama container."""
        await self.stop_container(image_name)
        result = await self.start_container(image_name)
        return result is not None

    async def get_container_logs(self, image_name: str,
                                  lines: int = 50) -> List[str]:
        """Get recent container logs."""
        info = self._containers.get(image_name)
        if not info or not info.container_id:
            return []

        try:
            process = await asyncio.create_subprocess_exec(
                "docker", "logs", "--tail", str(lines), info.container_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=10
            )
            return stdout.decode().splitlines()
        except Exception:
            return []

    def get_container_info(self, image_name: str) -> Optional[ContainerInfo]:
        return self._containers.get(image_name)

    def list_containers(self) -> List[ContainerInfo]:
        return list(self._containers.values())

    async def cleanup_all(self):
        """Stop and remove all Ollama containers."""
        for image_name in list(self._containers.keys()):
            await self.stop_container(image_name)
        self._containers.clear()
