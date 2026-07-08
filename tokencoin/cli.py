"""
TokenCoin CLI - Command Line Interface
========================================
Provides a command-line interface for wallet management,
mining control, and blockchain interaction.

Usage:
    tokencoin wallet create
    tokencoin wallet load <file>
    tokencoin wallet balance
    tokencoin wallet send <address> <amount>
    tokencoin wallet export
    tokencoin wallet import <key>
    tokencoin mine start [model]
    tokencoin mine stop
    tokencoin mine status
    tokencoin blockchain info
    tokencoin blockchain height
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Optional

from tokencoin.config import CONFIG
from tokencoin.core.crypto import KeyPair
from tokencoin.ledger import Blockchain
from tokencoin.wallet import Wallet, WalletBalance
from tokencoin.mining import Miner, MiningStatus
from tokencoin.api import OpenAIServer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CLI Handlers
# ---------------------------------------------------------------------------

class CLI:
    """TokenCoin CLI application."""

    def __init__(self):
        self.blockchain = Blockchain()
        self.wallet = Wallet(self.blockchain)
        self.miner = Miner(self.blockchain)
        self.api_server: Optional[OpenAIServer] = None
        self._running = False

    def handle_wallet_create(self, args):
        """Create a new wallet."""
        account = self.wallet.create()
        print(f"\n{'='*60}")
        print(f"  TokenCoin Wallet Created")
        print(f"{'='*60}")
        print(f"  Address: {account.address}")
        print(f"  Spend Key: {account.spend_keypair.private_key.seed.hex()[:32]}...")
        print(f"  View Key:  {account.view_keypair.private_key.seed.hex()[:32]}...")
        print(f"{'='*60}")
        print(f"  IMPORTANT: Save your private keys securely!")
        print(f"  Without them, you cannot access your funds.")
        print(f"{'='*60}\n")

        # Auto-save
        save = input("Save wallet to file? (y/N): ").lower()
        if save == "y":
            filepath = input(f"File path [{CONFIG.wallet.default_wallet_file}]: ").strip()
            if not filepath:
                filepath = CONFIG.wallet.default_wallet_file
            password = input("Password: ")
            self.wallet.save(filepath, password)
            print(f"Wallet saved to {filepath}")

    def handle_wallet_load(self, args):
        """Load a wallet from file."""
        filepath = args.file
        if not os.path.exists(filepath):
            print(f"Error: File not found: {filepath}")
            return

        password = input("Password: ")
        if self.wallet.load(filepath, password):
            print(f"Wallet loaded: {self.wallet.get_address()[:16]}...")
        else:
            print("Error: Failed to load wallet. Wrong password?")

    def handle_wallet_balance(self, args):
        """Show wallet balance."""
        try:
            balance = self.wallet.get_balance()
            address = self.wallet.get_address()
            print(f"\n{'='*60}")
            print(f"  Wallet: {address}")
            print(f"{'='*60}")
            print(f"  {balance}")
            print(f"{'='*60}\n")
        except ValueError as e:
            print(f"Error: {e}")

    def handle_wallet_send(self, args):
        """Send TKC to an address."""
        try:
            tx = self.wallet.send(args.address, args.amount)
            if tx:
                print(f"Transaction sent: {tx.hash().hex()[:32]}...")
                print(f"Amount: {args.amount} TKC")
                print(f"To: {args.address[:16]}...")
            else:
                print("Error: Transaction failed. Check balance and address.")
        except ValueError as e:
            print(f"Error: {e}")

    def handle_wallet_export(self, args):
        """Export wallet private key."""
        try:
            key = self.wallet.export_private_key()
            mnemonic = self.wallet.export_mnemonic()
            print(f"\n{'='*60}")
            print(f"  Wallet Export")
            print(f"{'='*60}")
            print(f"  Private Key (hex): {key}")
            print(f"  Mnemonic Seed:     {mnemonic}")
            print(f"{'='*60}")
            print(f"  WARNING: Anyone with these can access your funds!")
            print(f"{'='*60}\n")
        except ValueError as e:
            print(f"Error: {e}")

    def handle_wallet_import(self, args):
        """Import wallet from private key."""
        if self.wallet.import_private_key(args.key):
            print(f"Wallet imported: {self.wallet.get_address()[:16]}...")
        else:
            print("Error: Invalid private key")

    def handle_mine_start(self, args):
        """Start mining."""
        if not self.wallet.account:
            print("Error: No wallet loaded. Create or load a wallet first.")
            return

        model = args.model or CONFIG.ollama.mining_model
        self.miner.initialize(self.wallet.account.spend_keypair)

        print(f"Starting miner with model: {model}")
        print("Press Ctrl+C to stop mining...")

        async def run():
            success = await self.miner.start(model)
            if success:
                # Run and show stats
                try:
                    while self.miner.is_mining():
                        stats = self.miner.get_stats()
                        print(f"\r  [Mining] Backend: {stats.backend.upper()} | "
                              f"Blocks: {stats.blocks_mined} | "
                              f"Jobs: {stats.jobs_completed} | "
                              f"Rate: {stats.tkc_generation_rate:.4f} TKC/h",
                              end="", flush=True)
                        await asyncio.sleep(2)
                except KeyboardInterrupt:
                    await self.miner.stop()
                    print("\nMining stopped.")
            else:
                print("Error: Failed to start mining. Is Ollama installed?")
                print("Install from: https://ollama.com")

        asyncio.run(run())

    def handle_mine_stop(self, args):
        """Stop mining."""
        async def run():
            await self.miner.stop()
            print("Mining stopped.")
        asyncio.run(run())

    def handle_mine_status(self, args):
        """Show mining status."""
        stats = self.miner.get_stats()
        print(f"\n{'='*60}")
        print(f"  Mining Status: {stats.status.value.upper()}")
        print(f"{'='*60}")
        print(f"  Backend: {stats.backend.upper()}")
        if stats.gpu_name != "N/A":
            print(f"  GPU:     {stats.gpu_name} ({stats.gpu_vram_used_gb}/{stats.gpu_vram_total_gb} GB)")
        else:
            print(f"  CPU:     {stats.cpu_name} ({stats.cpu_threads} threads, {stats.ram_total_gb:.0f}GB RAM)")
        print(f"  Model:   {stats.active_model} ({stats.model_params_b}B params)")
        print(f"  Blocks:  {stats.blocks_mined}")
        print(f"  Earned:  {stats.total_reward_earned / 1e9:.4f} TKC")
        print(f"  Rate:    {stats.tkc_generation_rate:.4f} TKC/h")
        print(f"  Uptime:  {stats.uptime_seconds / 3600:.1f}h")
        print(f"  Instances: {stats.instances_healthy}/{stats.instances_total} healthy")
        print(f"{'='*60}\n")

    def handle_api_start(self, args):
        """Start the OpenAI-compatible API server."""
        port = args.port or 8080
        host = args.host or "0.0.0.0"

        print(f"Starting OpenAI-compatible API server on {host}:{port}...")
        print(f"  POST /v1/chat/completions - Chat completions")
        print(f"  POST /v1/embeddings - Embeddings")
        print(f"  GET  /v1/models - List models")
        print(f"  GET  /v1/health - Health check")
        print("Press Ctrl+C to stop...")

        async def run():
            # Create and start the API server
            ollama_mgr = self.miner.consensus.orchestrator.manager
            self.api_server = OpenAIServer(ollama_mgr)
            await self.api_server.start(host=host, port=port)

            try:
                while True:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                pass
            finally:
                await self.api_server.stop()

        asyncio.run(run())

    def handle_blockchain_info(self, args):
        """Show blockchain information."""
        state = self.blockchain.state
        latest = self.blockchain.get_latest_block()
        print(f"\n{'='*60}")
        print(f"  TokenCoin Blockchain")
        print(f"{'='*60}")
        print(f"  Height:     {state.height}")
        print(f"  Supply:     {state.total_supply / 1e9:.4f} / {CONFIG.monetary.max_supply / 1e9:.0f} TKC")
        print(f"  Difficulty: {state.difficulty}")
        print(f"  Mempool:    {len(self.blockchain.mempool)} transactions")
        print(f"  Peers:      {len(self.blockchain.chain)} blocks")
        if latest:
            print(f"  Latest:     Block #{latest.header.height} ({latest.hash().hex()[:16]}...)")
        print(f"{'='*60}\n")

    def handle_blockchain_height(self, args):
        """Show blockchain height."""
        print(f"Blockchain height: {self.blockchain.state.height}")

    def run(self):
        """Run the CLI."""
        parser = argparse.ArgumentParser(
            description="TokenCoin (TKC) - Privacy-First AI Cryptocurrency"
        )
        subparsers = parser.add_subparsers(dest="command", help="Available commands")

        # Wallet commands
        wallet_parser = subparsers.add_parser("wallet", help="Wallet management")
        wallet_sub = wallet_parser.add_subparsers(dest="wallet_cmd")

        wallet_create = wallet_sub.add_parser("create", help="Create a new wallet")
        wallet_create.set_defaults(handler=self.handle_wallet_create)

        wallet_load = wallet_sub.add_parser("load", help="Load wallet from file")
        wallet_load.add_argument("file", help="Wallet file path")
        wallet_load.set_defaults(handler=self.handle_wallet_load)

        wallet_balance = wallet_sub.add_parser("balance", help="Show wallet balance")
        wallet_balance.set_defaults(handler=self.handle_wallet_balance)

        wallet_send = wallet_sub.add_parser("send", help="Send TKC")
        wallet_send.add_argument("address", help="Recipient TKC address")
        wallet_send.add_argument("amount", type=int, help="Amount in TKC")
        wallet_send.set_defaults(handler=self.handle_wallet_send)

        wallet_export = wallet_sub.add_parser("export", help="Export private key")
        wallet_export.set_defaults(handler=self.handle_wallet_export)

        wallet_import = wallet_sub.add_parser("import", help="Import private key")
        wallet_import.add_argument("key", help="Private key hex")
        wallet_import.set_defaults(handler=self.handle_wallet_import)

        # Mining commands
        mine_parser = subparsers.add_parser("mine", help="Mining control")
        mine_sub = mine_parser.add_subparsers(dest="mine_cmd")

        mine_start = mine_sub.add_parser("start", help="Start mining")
        mine_start.add_argument("--model", "-m", default=None,
                                help="Ollama model to use (default: phi3-mini)")
        mine_start.set_defaults(handler=self.handle_mine_start)

        mine_stop = mine_sub.add_parser("stop", help="Stop mining")
        mine_stop.set_defaults(handler=self.handle_mine_stop)

        mine_status = mine_sub.add_parser("status", help="Show mining status")
        mine_status.set_defaults(handler=self.handle_mine_status)

        # API server commands
        api_parser = subparsers.add_parser("api", help="OpenAI-compatible API server")
        api_sub = api_parser.add_subparsers(dest="api_cmd")

        api_start = api_sub.add_parser("start", help="Start the API server")
        api_start.add_argument("--host", default="0.0.0.0",
                               help="Host to bind to (default: 0.0.0.0)")
        api_start.add_argument("--port", "-p", type=int, default=8080,
                               help="Port to listen on (default: 8080)")
        api_start.set_defaults(handler=self.handle_api_start)

        # Blockchain commands
        bc_parser = subparsers.add_parser("blockchain", help="Blockchain info")
        bc_sub = bc_parser.add_subparsers(dest="bc_cmd")

        bc_info = bc_sub.add_parser("info", help="Show blockchain info")
        bc_info.set_defaults(handler=self.handle_blockchain_info)

        bc_height = bc_sub.add_parser("height", help="Show blockchain height")
        bc_height.set_defaults(handler=self.handle_blockchain_height)

        args = parser.parse_args()

        if not args.command:
            parser.print_help()
            return

        # Initialize blockchain
        self.blockchain.initialize()

        # Route to handler
        if hasattr(args, "handler"):
            args.handler(args)
        else:
            parser.print_help()


def main():
    """Main entry point."""
    logging.basicConfig(
        level=getattr(logging, CONFIG.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    cli = CLI()
    cli.run()


if __name__ == "__main__":
    main()
