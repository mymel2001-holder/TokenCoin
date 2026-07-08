"""
TokenCoin Tor Daemon Integration
==================================
Integrates with the Tor daemon via the stem library for:
  - Hidden service creation (v3 onion addresses)
  - SOCKS5 proxy for outbound connections
  - Circuit management for P2P communication
  - Bandwidth monitoring and stream isolation

Requires: pip install stem
"""

import asyncio
import logging
import os
import socket
import struct
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple, Any
from pathlib import Path

from tokencoin.config import CONFIG

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tor Status
# ---------------------------------------------------------------------------

class TorStatus(Enum):
    """Tor daemon status."""
    NOT_INSTALLED = "not_installed"
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    ERROR = "error"


@dataclass
class TorConfig:
    """Tor daemon configuration."""
    data_dir: str = ""
    control_port: int = 0
    socks_port: int = 0
    hidden_service_dir: str = ""
    hashed_control_password: str = ""
    cookie_auth: bool = True

    def to_torrc(self) -> str:
        """Generate torrc configuration content."""
        lines = [
            f"DataDirectory {self.data_dir}",
            f"ControlPort {self.control_port}",
            f"SOCKSPort {self.socks_port}",
            f"HiddenServiceDir {self.hidden_service_dir}",
            f"HiddenServicePort 80 127.0.0.1:{CONFIG.network.p2p_port}",
            "HiddenServiceVersion 3",
            "CircuitBuildTimeout 30",
            "LearnCircuitBuildTimeout 1",
            "MaxCircuitDirtiness 600",
        ]
        if self.cookie_auth:
            lines.append("CookieAuthentication 1")
        else:
            lines.append(f"HashedControlPassword {self.hashed_control_password}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tor Manager
# ---------------------------------------------------------------------------

class TorManager:
    """
    Manages the Tor daemon lifecycle.
    Handles starting/stopping Tor, creating hidden services,
    and managing circuits.
    """

    def __init__(self):
        self.status = TorStatus.STOPPED
        self.config: Optional[TorConfig] = None
        self._process: Optional[asyncio.subprocess.Process] = None
        self._torrc_path: Optional[str] = None
        self._onion_address: Optional[str] = None
        self._controller = None  # stem Controller (lazy import)

    async def start(self) -> bool:
        """Start the Tor daemon with a hidden service."""
        try:
            import stem
            import stem.control
            import stem.process
        except ImportError:
            logger.error("stem library not installed. Run: pip install stem")
            self.status = TorStatus.NOT_INSTALLED
            return False

        # Create temporary directories
        data_dir = tempfile.mkdtemp(prefix="tokencoin_tor_")
        hs_dir = tempfile.mkdtemp(prefix="tokencoin_hs_")

        self.config = TorConfig(
            data_dir=data_dir,
            control_port=CONFIG.network.tor_control_port,
            socks_port=CONFIG.network.tor_socks_port,
            hidden_service_dir=hs_dir,
            cookie_auth=True,
        )

        # Write torrc
        self._torrc_path = os.path.join(data_dir, "torrc")
        with open(self._torrc_path, "w") as f:
            f.write(self.config.to_torrc())

        # Start Tor process
        self.status = TorStatus.STARTING
        try:
            self._process = await asyncio.create_subprocess_exec(
                "tor", "-f", self._torrc_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            logger.info("Tor daemon starting...")

            # Wait for Tor to be ready
            await asyncio.sleep(3)

            # Connect to control port
            self._controller = stem.control.Controller.from_port(
                port=self.config.control_port
            )
            self._controller.authenticate()

            # Get our onion address
            self._onion_address = self._controller.get_info("onion_address")
            logger.info(f"Tor hidden service ready: {self._onion_address}.onion")

            self.status = TorStatus.RUNNING
            return True

        except Exception as e:
            logger.error(f"Failed to start Tor: {e}")
            self.status = TorStatus.ERROR
            return False

    async def stop(self):
        """Stop the Tor daemon."""
        if self._controller:
            try:
                self._controller.close()
            except Exception:
                pass
            self._controller = None

        if self._process:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=10)
            except asyncio.TimeoutError:
                self._process.kill()
            self._process = None

        # Cleanup temp directories
        if self.config:
            for d in [self.config.data_dir, self.config.hidden_service_dir]:
                if os.path.exists(d):
                    import shutil
                    shutil.rmtree(d, ignore_errors=True)

        self.status = TorStatus.STOPPED
        logger.info("Tor daemon stopped")

    def get_onion_address(self) -> Optional[str]:
        """Get the .onion address of this node."""
        return self._onion_address

    def get_tkc_address(self) -> Optional[str]:
        """Get the TokenCoin address (56-char Base32 without .onion)."""
        if self._onion_address:
            return self._onion_address.replace(".onion", "")
        return None

    async def create_circuit(self, destination: str) -> Optional[int]:
        """
        Create a Tor circuit to a destination.
        Returns the circuit ID.
        """
        if not self._controller:
            return None
        try:
            circuit_id = self._controller.new_circuit(
                path=[destination],
                purpose="tokencoin_p2p",
            )
            return circuit_id
        except Exception as e:
            logger.error(f"Failed to create circuit: {e}")
            return None

    async def close_circuit(self, circuit_id: int):
        """Close a Tor circuit."""
        if self._controller:
            try:
                self._controller.close_circuit(circuit_id)
            except Exception:
                pass

    def is_running(self) -> bool:
        return self.status == TorStatus.RUNNING


# ---------------------------------------------------------------------------
# Tor SOCKS5 Proxy Client
# ---------------------------------------------------------------------------

class TorSOCKS5Client:
    """
    SOCKS5 proxy client for routing connections through Tor.
    Used for outbound P2P connections to .onion addresses.
    """

    def __init__(self, socks_port: int = 9050):
        self.socks_port = socks_port

    async def connect(self, onion_host: str, onion_port: int) -> Optional[asyncio.StreamWriter]:
        """
        Connect to a .onion address through the Tor SOCKS5 proxy.
        """
        try:
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", self.socks_port
            )

            # SOCKS5 handshake
            # Version 5, 1 auth method (no auth)
            writer.write(bytes([0x05, 0x01, 0x00]))
            await writer.drain()

            resp = await reader.readexactly(2)
            if resp[0] != 0x05 or resp[1] != 0x00:
                logger.error("SOCKS5 handshake failed")
                writer.close()
                return None

            # Connect command
            # SOCKS5, CONNECT, RESERVED, ATYP (0x03 = domain), domain length, domain, port
            addr_bytes = onion_host.encode()
            cmd = bytes([0x05, 0x01, 0x00, 0x03, len(addr_bytes)]) + addr_bytes
            cmd += struct.pack("!H", onion_port)

            writer.write(cmd)
            await writer.drain()

            resp = await reader.readexactly(4)
            if resp[1] != 0x00:  # Check reply field
                logger.error(f"SOCKS5 connect failed: {resp[1]}")
                writer.close()
                return None

            # Read remaining response (varies by address type)
            if resp[3] == 0x01:  # IPv4
                await reader.readexactly(6)
            elif resp[3] == 0x03:  # Domain
                domain_len = (await reader.readexactly(1))[0]
                await reader.readexactly(domain_len + 2)
            elif resp[3] == 0x04:  # IPv6
                await reader.readexactly(18)

            return writer

        except Exception as e:
            logger.error(f"SOCKS5 connection failed: {e}")
            return None
