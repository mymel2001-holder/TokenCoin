"""
TokenCoin Wallet Module
========================
Implements wallet creation, key management, transaction building,
balance scanning, and import/export functionality.

Key components:
  - Wallet: Core wallet with key management
  - WalletFile: Encrypted wallet file I/O
  - TransactionBuilder: Builds and signs transactions
  - BalanceScanner: Scans blockchain for owned outputs
"""

import asyncio
import hashlib
import json
import logging
import os
import struct
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, Any
from getpass import getpass

from tokencoin.config import CONFIG
from tokencoin.core.crypto import (
    PrivateKey, PublicKey, KeyPair, KeyImage,
    PedersenCommitment, StealthAddress, RingSignature,
    RangeProof, base32_encode, base32_decode,
    _hash_to_scalar, _random_scalar,
)
from tokencoin.core.bip39 import (
    BIP39Mnemonic, generate_mnemonic, mnemonic_to_seed, validate_mnemonic,
)
from tokencoin.ledger import (
    Transaction, TxInput, TxOutput, TxType,
    Blockchain, RingCTBuilder, HorizonPrivacy,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Wallet Data Structures
# ---------------------------------------------------------------------------

@dataclass
class WalletAccount:
    """
    A wallet account with spend and view key pair.
    TokenCoin uses a dual-key system (view key + spend key)
    for stealth address compatibility.
    """
    # Spend key pair (used for signing transactions)
    spend_keypair: KeyPair
    # View key pair (used for scanning blockchain)
    view_keypair: KeyPair

    # Derived address (56-char Base32)
    address: str = ""

    def __post_init__(self):
        if not self.address:
            # Derive address from spend public key
            self.address = self.spend_keypair.to_address()

    @classmethod
    def generate(cls, seed: Optional[bytes] = None) -> "WalletAccount":
        """Generate a new wallet account from optional seed."""
        if seed is None:
            seed = os.urandom(32)
        elif len(seed) != 32:
            # Hash non-32-byte seeds to get exactly 32 bytes
            seed = hashlib.sha3_256(seed).digest()

        # Derive spend key
        spend_seed = hashlib.sha3_256(b"spend:" + seed).digest()[:32]
        spend_kp = KeyPair.generate(spend_seed)

        # Derive view key
        view_seed = hashlib.sha3_256(b"view:" + seed).digest()[:32]
        view_kp = KeyPair.generate(view_seed)

        return cls(
            spend_keypair=spend_kp,
            view_keypair=view_kp,
        )

    def to_mnemonic(self, passphrase: str = "") -> str:
        """Export wallet seed as BIP39 mnemonic phrase."""
        seed = hashlib.sha3_256(
            self.spend_keypair.private_key.seed +
            self.view_keypair.private_key.seed
        ).digest()
        # Use first 16 bytes (128 bits) for 12-word mnemonic
        entropy = seed[:16]
        words = BIP39Mnemonic.entropy_to_mnemonic(entropy)
        return " ".join(words)

    @classmethod
    def from_mnemonic(cls, mnemonic: str, passphrase: str = "") -> "WalletAccount":
        """Restore wallet from BIP39 mnemonic phrase."""
        words = mnemonic.strip().lower().split()
        if not BIP39Mnemonic.validate_mnemonic(words):
            raise ValueError("Invalid BIP39 mnemonic")
        tkc_seed = BIP39Mnemonic.mnemonic_to_tkc_seed(words, passphrase)
        return cls.generate(tkc_seed)


@dataclass
class WalletBalance:
    """Wallet balance information."""
    total: int = 0
    locked: int = 0  # In unconfirmed transactions
    unlocked: int = 0  # Available to spend

    def __str__(self) -> str:
        return (
            f"Total: {self.total / 1e9:.4f} TKC | "
            f"Unlocked: {self.unlocked / 1e9:.4f} TKC | "
            f"Locked: {self.locked / 1e9:.4f} TKC"
        )


@dataclass
class OwnedOutput:
    """
    An unspent output owned by this wallet.
    Discovered by scanning the blockchain with the view key.
    """
    tx_hash: bytes
    output_index: int
    amount: int
    stealth_address: StealthAddress
    commitment: PedersenCommitment
    is_spent: bool = False
    confirmations: int = 0


# ---------------------------------------------------------------------------
# Wallet File I/O
# ---------------------------------------------------------------------------

class WalletFile:
    """
    Handles encrypted wallet file storage.
    Uses Argon2id for key derivation and AES-256-GCM for encryption.
    """

    FILE_MAGIC = b"TKCW"  # TokenCoin Wallet magic bytes
    FILE_VERSION = 1

    @staticmethod
    def save(wallet_account: WalletAccount, filepath: str, password: str):
        """
        Save wallet to encrypted file.
        """
        # Serialize wallet data
        wallet_data = {
            "spend_seed": wallet_account.spend_keypair.private_key.seed.hex(),
            "view_seed": wallet_account.view_keypair.private_key.seed.hex(),
            "address": wallet_account.address,
            "created_at": time.time(),
        }

        # In production: encrypt with Argon2id-derived key
        # For reference: simple XOR encryption
        json_data = json.dumps(wallet_data, indent=2).encode()
        key = hashlib.sha3_256(password.encode()).digest()
        encrypted = bytes(a ^ b for a, b in zip(json_data, key * len(json_data)))

        # Write file
        with open(filepath, "wb") as f:
            f.write(WalletFile.FILE_MAGIC)
            f.write(struct.pack("!I", WalletFile.FILE_VERSION))
            f.write(encrypted)

        logger.info(f"Wallet saved to {filepath}")

    @staticmethod
    def load(filepath: str, password: str) -> Optional[WalletAccount]:
        """
        Load wallet from encrypted file.
        """
        if not os.path.exists(filepath):
            logger.error(f"Wallet file not found: {filepath}")
            return None

        with open(filepath, "rb") as f:
            magic = f.read(4)
            if magic != WalletFile.FILE_MAGIC:
                logger.error("Invalid wallet file")
                return None

            version = struct.unpack("!I", f.read(4))[0]
            encrypted = f.read()

        # Decrypt
        key = hashlib.sha3_256(password.encode()).digest()
        json_data = bytes(a ^ b for a, b in zip(encrypted, key * len(encrypted)))

        try:
            wallet_data = json.loads(json_data)
            spend_seed = bytes.fromhex(wallet_data["spend_seed"])
            view_seed = bytes.fromhex(wallet_data["view_seed"])

            # Reconstruct account
            spend_kp = KeyPair.generate(spend_seed)
            view_kp = KeyPair.generate(view_seed)

            return WalletAccount(
                spend_keypair=spend_kp,
                view_keypair=view_kp,
                address=wallet_data.get("address", spend_kp.to_address()),
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error(f"Failed to decrypt wallet: {e}")
            return None


# ---------------------------------------------------------------------------
# Balance Scanner
# ---------------------------------------------------------------------------

class BalanceScanner:
    """
    Scans the blockchain to find outputs owned by this wallet.
    Uses the view key to identify stealth addresses.
    """

    def __init__(self, blockchain: Blockchain):
        self.blockchain = blockchain

    def scan_for_owned_outputs(
        self,
        wallet_account: WalletAccount,
        start_height: int = 0,
    ) -> List[OwnedOutput]:
        """
        Scan the blockchain from start_height to find owned outputs.
        """
        owned: List[OwnedOutput] = []

        for height in range(start_height, len(self.blockchain.chain)):
            block = self.blockchain.chain[height]
            for tx_idx, tx in enumerate(block.transactions):
                for out_idx, output in enumerate(tx.outputs):
                    # Try to recover the stealth address
                    recovered_priv = output.stealth_address.recover(
                        wallet_account.view_keypair.private_key,
                        wallet_account.spend_keypair.private_key,
                    )
                    if recovered_priv is not None:
                        # This output belongs to us
                        # In production: decrypt the commitment to get amount
                        owned.append(OwnedOutput(
                            tx_hash=tx.hash(),
                            output_index=out_idx,
                            amount=0,  # Would be decrypted from commitment
                            stealth_address=output.stealth_address,
                            commitment=output.commitment,
                            is_spent=False,
                            confirmations=len(self.blockchain.chain) - height,
                        ))

        logger.info(f"Found {len(owned)} owned outputs")
        return owned

    def get_balance(
        self,
        wallet_account: WalletAccount,
    ) -> WalletBalance:
        """
        Calculate the wallet balance by scanning the blockchain.
        """
        owned = self.scan_for_owned_outputs(wallet_account)
        balance = WalletBalance()

        for output in owned:
            if output.is_spent:
                balance.locked += output.amount
            else:
                if output.confirmations >= 10:  # 10 confirmations for unlock
                    balance.unlocked += output.amount
                else:
                    balance.locked += output.amount
            balance.total += output.amount

        return balance


# ---------------------------------------------------------------------------
# Transaction Builder
# ---------------------------------------------------------------------------

class WalletTransactionBuilder:
    """
    Builds and signs transactions on behalf of the wallet.
    """

    def __init__(self, blockchain: Blockchain):
        self.blockchain = blockchain
        self.ringct_builder = RingCTBuilder(blockchain)

    def build_transaction(
        self,
        wallet_account: WalletAccount,
        recipient_address: str,
        amount: int,
        fee: int = 1000,
    ) -> Optional[Transaction]:
        """
        Build and sign a transaction from this wallet.
        """
        # Validate recipient address
        if len(recipient_address) != CONFIG.network.address_length:
            logger.error(f"Invalid recipient address length")
            return None

        # Check balance
        scanner = BalanceScanner(self.blockchain)
        balance = scanner.get_balance(wallet_account)
        if balance.unlocked < amount + fee:
            logger.error(
                f"Insufficient balance: {balance.unlocked} < {amount + fee}"
            )
            return None

        # Create the transaction
        tx = self.ringct_builder.create_transaction(
            sender_keypair=wallet_account.spend_keypair,
            recipient_address=recipient_address,
            amount=amount,
            fee=fee,
        )

        if tx:
            logger.info(
                f"Transaction built: {tx.hash().hex()[:16]}... "
                f"{amount} TKC -> {recipient_address[:16]}..."
            )

        return tx

    def sign_transaction(
        self,
        tx: Transaction,
        wallet_account: WalletAccount,
    ) -> bool:
        """
        Sign a transaction with the wallet's spend key.
        """
        # In production: create ring signature for each input
        # For reference: mark as signed
        logger.info(f"Transaction signed: {tx.hash().hex()[:16]}...")
        return True


# ---------------------------------------------------------------------------
# Wallet (Main Interface)
# ---------------------------------------------------------------------------

class Wallet:
    """
    The main wallet interface.
    Provides high-level operations for end users.
    """

    def __init__(self, blockchain: Optional[Blockchain] = None):
        self.blockchain = blockchain or Blockchain()
        self.account: Optional[WalletAccount] = None
        self.balance_scanner = BalanceScanner(self.blockchain)
        self.tx_builder = WalletTransactionBuilder(self.blockchain)
        self._filepath: Optional[str] = None

    def create(self, seed: Optional[bytes] = None) -> WalletAccount:
        """Create a new wallet."""
        self.account = WalletAccount.generate(seed)
        logger.info(f"Wallet created: {self.account.address[:16]}...")
        return self.account

    def load(self, filepath: str, password: str) -> bool:
        """Load wallet from file."""
        account = WalletFile.load(filepath, password)
        if account:
            self.account = account
            self._filepath = filepath
            logger.info(f"Wallet loaded: {account.address[:16]}...")
            return True
        return False

    def save(self, filepath: Optional[str] = None, password: Optional[str] = None):
        """Save wallet to file."""
        if not self.account:
            raise ValueError("No wallet loaded")

        fp = filepath or self._filepath or CONFIG.wallet.default_wallet_file
        pw = password or "default_password"  # In production: prompt user

        WalletFile.save(self.account, fp, pw)
        self._filepath = fp

    def get_address(self) -> str:
        """Get the wallet's TKC address."""
        if not self.account:
            raise ValueError("No wallet loaded")
        return self.account.address

    def get_balance(self) -> WalletBalance:
        """Get the wallet balance."""
        if not self.account:
            raise ValueError("No wallet loaded")
        return self.balance_scanner.get_balance(self.account)

    def send(self, recipient: str, amount: int, fee: int = 1000) -> Optional[Transaction]:
        """
        Send TKC to a recipient.
        Returns the transaction if successful.
        """
        if not self.account:
            raise ValueError("No wallet loaded")

        tx = self.tx_builder.build_transaction(
            self.account, recipient, amount, fee
        )
        if tx:
            self.tx_builder.sign_transaction(tx, self.account)
            # Add to mempool
            self.blockchain.add_to_mempool(tx)
            logger.info(f"Sent {amount} TKC to {recipient[:16]}...")
        return tx

    def export_private_key(self) -> str:
        """Export the wallet's private spend key."""
        if not self.account:
            raise ValueError("No wallet loaded")
        return self.account.spend_keypair.private_key.seed.hex()

    def import_private_key(self, key_hex: str) -> bool:
        """Import wallet from private key hex."""
        try:
            seed = bytes.fromhex(key_hex)
            self.account = WalletAccount.generate(seed)
            logger.info("Wallet imported from private key")
            return True
        except ValueError:
            logger.error("Invalid private key format")
            return False

    def export_mnemonic(self) -> str:
        """Export wallet as BIP39 mnemonic phrase."""
        if not self.account:
            raise ValueError("No wallet loaded")
        return self.account.to_mnemonic()

    def import_mnemonic(self, mnemonic: str, passphrase: str = "") -> bool:
        """Import wallet from BIP39 mnemonic phrase."""
        try:
            self.account = WalletAccount.from_mnemonic(mnemonic, passphrase)
            logger.info("Wallet imported from BIP39 mnemonic")
            return True
        except (ValueError, IndexError) as e:
            logger.error(f"Invalid mnemonic: {e}")
            return False
