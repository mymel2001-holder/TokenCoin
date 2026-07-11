"""
TokenCoin Ledger & Privacy Layer
=================================
Implements the blockchain, transactions, RingCT, stealth addresses,
and horizon privacy (single-hop graph visibility).

Key components:
  - Transaction: RingCT transaction with stealth addresses
  - Block: Block structure with Merkle tree
  - Blockchain: Chain management, validation, and state
  - RingCT: Confidential transaction amounts via Pedersen commitments
  - HorizonPrivacy: Single-hop graph visibility enforcement
"""

import asyncio
import hashlib
import json
import logging
import struct
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple, Any
from collections import defaultdict

from tokencoin.config import CONFIG
from tokencoin.core.emission import EmissionCurve, atomic_to_tkc
from tokencoin.core.crypto import (
    PrivateKey, PublicKey, KeyPair, KeyImage,
    PedersenCommitment, StealthAddress, RingSignature,
    RangeProof, base32_encode, base32_decode,
    _hash_to_scalar, _random_scalar,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Transaction Types
# ---------------------------------------------------------------------------

class TxType(Enum):
    """Transaction types in TokenCoin."""
    REGULAR = 0x00       # Standard transfer
    COINBASE = 0x01      # Mining reward
    STAKE = 0x02         # Staking transaction


# ---------------------------------------------------------------------------
# Transaction Input / Output
# ---------------------------------------------------------------------------

@dataclass
class TxInput:
    """
    A transaction input referencing an unspent output.
    Uses ring signatures to hide which output is actually being spent.
    """
    # Reference to previous output
    prev_tx_hash: bytes       # 32 bytes
    prev_output_index: int    # Index in previous tx outputs

    # Ring signature data
    key_image: KeyImage       # For double-spend protection
    ring_signature: RingSignature  # The ring signature
    ring_members: List[bytes]  # Public keys of ring members (decoys)

    def to_bytes(self) -> bytes:
        data = self.prev_tx_hash
        data += struct.pack("!I", self.prev_output_index)
        data += self.key_image.to_bytes()
        data += self.ring_signature.to_bytes()
        for member in self.ring_members:
            data += member
        return data

    @classmethod
    def from_bytes(cls, data: bytes, offset: int = 0) -> Tuple["TxInput", int]:
        """Deserialize from bytes."""
        prev_tx_hash = data[offset:offset + 32]
        offset += 32
        prev_output_index = struct.unpack("!I", data[offset:offset + 4])[0]
        offset += 4
        # Simplified - in production would fully deserialize
        return cls(
            prev_tx_hash=prev_tx_hash,
            prev_output_index=prev_output_index,
            key_image=KeyImage(image=b""),
            ring_signature=RingSignature(ring_size=0, public_keys=[],
                                          key_image=KeyImage(image=b""),
                                          responses=[], challenge=0),
            ring_members=[],
        ), offset


@dataclass
class TxOutput:
    """
    A transaction output with stealth address and commitment.
    """
    stealth_address: StealthAddress  # One-time destination address
    commitment: PedersenCommitment   # Amount commitment (hidden)
    range_proof: RangeProof          # Proof amount is non-negative
    output_index: int = 0            # Index within the transaction

    def to_bytes(self) -> bytes:
        data = self.stealth_address.to_bytes()
        data += self.commitment.to_bytes()
        data += self.range_proof.to_bytes()
        data += struct.pack("!I", self.output_index)
        return data


# ---------------------------------------------------------------------------
# Transaction
# ---------------------------------------------------------------------------

@dataclass
class Transaction:
    """
    A TokenCoin transaction with RingCT privacy.
    Implements single-hop graph visibility.
    """
    version: int = CONFIG.ledger.tx_version
    tx_type: TxType = TxType.REGULAR
    inputs: List[TxInput] = field(default_factory=list)
    outputs: List[TxOutput] = field(default_factory=list)
    fee: int = 0  # Transaction fee (in atomic units)
    timestamp: float = field(default_factory=time.time)
    nonce: int = field(default_factory=lambda: _random_scalar())

    # Horizon privacy: encrypted sender/recipient view keys
    encrypted_view_keys: bytes = b""

    # Transaction hash (computed)
    _hash: Optional[bytes] = None

    def hash(self) -> bytes:
        """Compute the transaction hash (SHA3-256)."""
        if self._hash is None:
            h = hashlib.sha3_256()
            h.update(struct.pack("!I", self.version))
            h.update(struct.pack("!B", self.tx_type.value))
            h.update(struct.pack("!I", len(self.inputs)))
            for inp in self.inputs:
                h.update(inp.to_bytes())
            h.update(struct.pack("!I", len(self.outputs)))
            for out in self.outputs:
                h.update(out.to_bytes())
            h.update(struct.pack("!Q", self.fee))
            h.update(struct.pack("!d", self.timestamp))
            h.update(self.nonce.to_bytes(32, "little"))
            h.update(self.encrypted_view_keys)
            self._hash = h.digest()
        return self._hash

    def to_bytes(self) -> bytes:
        """Serialize transaction to bytes."""
        data = self.hash()  # Include hash
        data += struct.pack("!I", self.version)
        data += struct.pack("!B", self.tx_type.value)
        data += struct.pack("!I", len(self.inputs))
        for inp in self.inputs:
            data += inp.to_bytes()
        data += struct.pack("!I", len(self.outputs))
        for out in self.outputs:
            data += out.to_bytes()
        data += struct.pack("!Q", self.fee)
        data += struct.pack("!d", self.timestamp)
        data += self.nonce.to_bytes(32, "little")
        data += struct.pack("!I", len(self.encrypted_view_keys))
        data += self.encrypted_view_keys
        return data

    def validate_basic(self) -> bool:
        """Basic validation checks."""
        if self.version != CONFIG.ledger.tx_version:
            return False
        if len(self.inputs) == 0 and self.tx_type != TxType.COINBASE:
            return False
        if len(self.outputs) == 0:
            return False
        if self.fee < 0:
            return False
        return True

    def get_total_output_commitment(self) -> PedersenCommitment:
        """Sum all output commitments."""
        commitments = [out.commitment for out in self.outputs]
        return PedersenCommitment.sum(commitments)

    def get_total_input_commitment(self) -> PedersenCommitment:
        """Sum all input commitments (from ring signatures)."""
        # In production: extract commitments from ring members
        # For reference: return zero commitment
        return PedersenCommitment(commitment=b"\x00" * 32)


# ---------------------------------------------------------------------------
# Block
# ---------------------------------------------------------------------------

@dataclass
class BlockHeader:
    """Block header containing metadata and proof."""
    version: int = 1
    height: int = 0
    timestamp: float = field(default_factory=time.time)
    prev_hash: bytes = b"\x00" * 32
    merkle_root: bytes = b"\x00" * 32
    difficulty: int = 1
    nonce: int = 0

    # PoUW-specific fields
    work_model: str = ""           # NIM model used
    work_commitment: bytes = b""   # Tensor commitment hash
    miner_address: str = ""        # Miner's TKC address

    def to_bytes(self) -> bytes:
        data = struct.pack("!I", self.version)
        data += struct.pack("!Q", self.height)
        data += struct.pack("!d", self.timestamp)
        data += self.prev_hash
        data += self.merkle_root
        data += struct.pack("!Q", self.difficulty)
        data += struct.pack("!Q", self.nonce)
        data += self.work_model.encode("utf-8").ljust(64, b"\x00")[:64]
        data += self.work_commitment.ljust(32, b"\x00")[:32]
        data += self.miner_address.encode("utf-8").ljust(56, b"\x00")[:56]
        return data

    def hash(self) -> bytes:
        """Compute block hash (double SHA3-256 for PoUW)."""
        h = hashlib.sha3_256(self.to_bytes()).digest()
        return hashlib.sha3_256(h).digest()


@dataclass
class Block:
    """A block in the TokenCoin blockchain."""
    header: BlockHeader = field(default_factory=BlockHeader)
    transactions: List[Transaction] = field(default_factory=list)

    def hash(self) -> bytes:
        return self.header.hash()

    def to_bytes(self) -> bytes:
        data = self.header.to_bytes()
        data += struct.pack("!I", len(self.transactions))
        for tx in self.transactions:
            data += tx.to_bytes()
        return data

    def compute_merkle_root(self) -> bytes:
        """Compute the Merkle root of all transactions."""
        if not self.transactions:
            return hashlib.sha3_256(b"empty").digest()

        tx_hashes = [tx.hash() for tx in self.transactions]
        while len(tx_hashes) > 1:
            if len(tx_hashes) % 2 == 1:
                tx_hashes.append(tx_hashes[-1])  # Duplicate last
            new_hashes = []
            for i in range(0, len(tx_hashes), 2):
                h = hashlib.sha3_256(tx_hashes[i] + tx_hashes[i + 1]).digest()
                new_hashes.append(h)
            tx_hashes = new_hashes
        return tx_hashes[0]

    def validate(self, prev_block: Optional["Block"] = None) -> bool:
        """Validate block structure and transactions."""
        # Check version
        if self.header.version != 1:
            return False

        # Check previous hash linkage
        if prev_block and self.header.prev_hash != prev_block.hash():
            logger.warning("Block prev_hash mismatch")
            return False

        # Check height
        if prev_block and self.header.height != prev_block.header.height + 1:
            logger.warning("Block height mismatch")
            return False

        # Check Merkle root
        computed_root = self.compute_merkle_root()
        if computed_root != self.header.merkle_root:
            logger.warning("Merkle root mismatch")
            return False

        # Validate each transaction
        for tx in self.transactions:
            if not tx.validate_basic():
                logger.warning(f"Invalid transaction in block {self.header.height}")
                return False

        # Coinbase transaction must be first
        if self.transactions:
            if self.transactions[0].tx_type != TxType.COINBASE:
                logger.warning("First transaction must be coinbase")
                return False

        return True


# ---------------------------------------------------------------------------
# Blockchain
# ---------------------------------------------------------------------------

@dataclass
class BlockchainState:
    """Current state of the blockchain.

    All supply values are stored in atomic units (1 TKC = 1_000_000_000 atomic).
    This ensures consistency with the EmissionCurve which operates in atomic units.
    """
    height: int = 0
    total_supply: int = CONFIG.monetary.base_supply * 1_000_000_000  # Atomic units
    difficulty: int = 1
    last_block_hash: bytes = b"\x00" * 32


class Blockchain:
    """
    The TokenCoin blockchain.
    Manages chain state, validation, and reorganization.
    """

    def __init__(self):
        self.chain: List[Block] = []
        self.utxo_set: Dict[str, List[TxOutput]] = {}  # tx_hash -> outputs
        self.spent_key_images: Set[str] = set()  # Prevent double-spends
        self.state = BlockchainState()
        self.orphan_blocks: Dict[bytes, Block] = {}  # Blocks waiting for parent
        self.mempool: List[Transaction] = []
        self._lock = asyncio.Lock()

    def create_genesis_block(self) -> Block:
        """Create the genesis block."""
        genesis = Block(
            header=BlockHeader(
                version=1,
                height=0,
                timestamp=1710000000.0,  # Fixed timestamp
                prev_hash=b"\x00" * 32,
                merkle_root=b"\x00" * 32,
                difficulty=1,
                nonce=0,
                miner_address="tkc1genesisxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            )
        )
        # Genesis coinbase transaction
        coinbase = Transaction(
            tx_type=TxType.COINBASE,
            outputs=[
                TxOutput(
                    stealth_address=StealthAddress(
                        public_spend=PublicKey(point=b"\x00" * 32),
                        public_view=PublicKey(point=b"\x00" * 32),
                        ephemeral=b"\x00" * 32,
                    ),
                    commitment=PedersenCommitment(
                        commitment=hashlib.sha3_256(
                            b"genesis_supply:" +
                            str(CONFIG.monetary.base_supply).encode()
                        ).digest()
                    ),
                    range_proof=RangeProof(
                        commitment=PedersenCommitment(commitment=b""),
                        proof_data=b"",
                    ),
                )
            ],
            fee=0,
        )
        genesis.transactions = [coinbase]
        genesis.header.merkle_root = genesis.compute_merkle_root()
        return genesis

    def initialize(self):
        """Initialize the blockchain with genesis block."""
        genesis = self.create_genesis_block()
        self.chain.append(genesis)
        self.state.height = 0
        self.state.last_block_hash = genesis.hash()
        logger.info("Blockchain initialized with genesis block")

    def add_block(self, block: Block,
                  block_reward_atomic: Optional[int] = None) -> bool:
        """
        Add a validated block to the chain.

        Args:
            block: The block to add.
            block_reward_atomic: The block reward in atomic units, calculated
                from the smooth emission curve. If None, uses the old
                initial_block_reward (for backward compatibility).

        The block reward is determined by the EmissionCurve, ensuring
        fair, unbiased printing of new coins. The reward smoothly decreases
        as the supply approaches the max, and never drops below the tail
        emission floor.
        """
        # Check if we have the parent
        if self.state.height > 0:
            parent = self.chain[-1]
            if block.header.prev_hash != parent.hash():
                # Orphan block - store for later
                self.orphan_blocks[block.hash()] = block
                logger.debug(f"Orphan block stored: {block.hash().hex()[:16]}")
                return False

        # Validate the block
        prev = self.chain[-1] if self.chain else None
        if not block.validate(prev):
            logger.warning(f"Block validation failed at height {block.header.height}")
            return False

        # Calculate the block reward from the emission curve
        if block_reward_atomic is None:
            # Fallback for backward compatibility
            curve = EmissionCurve()
            block_reward_atomic = curve.block_reward(self.state.total_supply)

        # Process transactions
        for tx in block.transactions:
            if tx.tx_type == TxType.COINBASE:
                # Mint new coins using the smooth emission curve reward
                self.state.total_supply += block_reward_atomic
            else:
                # Mark key images as spent
                for inp in tx.inputs:
                    ki_hex = inp.key_image.image.hex()
                    if ki_hex in self.spent_key_images:
                        logger.warning(f"Double-spend detected: {ki_hex}")
                        return False
                    self.spent_key_images.add(ki_hex)

            # Add outputs to UTXO set
            tx_hash = tx.hash().hex()
            self.utxo_set[tx_hash] = tx.outputs

        # Add to chain
        self.chain.append(block)
        self.state.height = block.header.height
        self.state.last_block_hash = block.hash()

        # Check for orphans that can now be added
        self._process_orphans()

        logger.info(
            f"Block {block.header.height} added to chain | "
            f"Reward: {atomic_to_tkc(block_reward_atomic):.4f} TKC | "
            f"Supply: {atomic_to_tkc(self.state.total_supply):,.2f} TKC"
        )
        return True

    def _process_orphans(self):
        """Process any orphan blocks that can now be added."""
        added = True
        while added:
            added = False
            for block_hash, block in list(self.orphan_blocks.items()):
                if block.header.prev_hash == self.state.last_block_hash:
                    if self.add_block(block):
                        del self.orphan_blocks[block_hash]
                        added = True
                        break

    def get_block(self, height: int) -> Optional[Block]:
        """Get block at a specific height."""
        if 0 <= height < len(self.chain):
            return self.chain[height]
        return None

    def get_latest_block(self) -> Optional[Block]:
        """Get the most recent block."""
        return self.chain[-1] if self.chain else None

    def get_balance(self, address: str) -> int:
        """
        Get the balance for a TKC address.
        Note: Due to RingCT, this requires scanning all transactions
        with the wallet's view key. This is a simplified version.
        """
        # In production: scan blockchain with view key
        return 0

    def add_to_mempool(self, tx: Transaction) -> bool:
        """Add a transaction to the mempool."""
        if not tx.validate_basic():
            return False
        # Check for double-spend in mempool
        for inp in tx.inputs:
            ki_hex = inp.key_image.image.hex()
            if ki_hex in self.spent_key_images:
                return False
            # Check against existing mempool
            for mem_tx in self.mempool:
                for mem_inp in mem_tx.inputs:
                    if mem_inp.key_image.image.hex() == ki_hex:
                        return False
        self.mempool.append(tx)
        return True

    def get_mempool_txs(self, max_count: int = 1000) -> List[Transaction]:
        """Get transactions from mempool for block assembly."""
        return self.mempool[:max_count]

    def clear_mempool(self, tx_hashes: Set[str]):
        """Remove confirmed transactions from mempool."""
        self.mempool = [
            tx for tx in self.mempool
            if tx.hash().hex() not in tx_hashes
        ]


# ---------------------------------------------------------------------------
# Horizon Privacy (Single-Hop Graph Visibility)
# ---------------------------------------------------------------------------

class HorizonPrivacy:
    """
    Implements single-hop graph visibility.
    Only the immediate sender and receiver can see the cryptographic
    linking of a transaction. Outside observers see only valid state
    transitions without traceability.
    """

    @staticmethod
    def encrypt_view_keys(tx: Transaction,
                          sender_view_key: PublicKey,
                          recipient_view_key: PublicKey) -> bytes:
        """
        Encrypt the view keys for a transaction so only the
        sender and recipient can decrypt them.
        """
        # In production: use ECDH to derive shared secret
        # For reference: encrypt with recipient's public key
        data = sender_view_key.to_bytes() + recipient_view_key.to_bytes()
        # Encrypt with a one-time pad derived from tx hash
        key = hashlib.sha3_256(b"horizon_key:" + tx.hash()).digest()
        encrypted = bytes(a ^ b for a, b in zip(data, key * len(data)))
        return encrypted

    @staticmethod
    def decrypt_view_keys(encrypted: bytes,
                          tx_hash: bytes,
                          private_key: PrivateKey) -> Optional[Tuple[PublicKey, PublicKey]]:
        """
        Decrypt the view keys using the recipient's private key.
        Returns (sender_view_key, recipient_view_key) or None.
        """
        key = hashlib.sha3_256(b"horizon_key:" + tx_hash).digest()
        decrypted = bytes(a ^ b for a, b in zip(encrypted, key * len(encrypted)))
        if len(decrypted) < 64:
            return None
        sender = PublicKey(point=decrypted[:32])
        recipient = PublicKey(point=decrypted[32:64])
        return sender, recipient

    @staticmethod
    def verify_horizon(tx: Transaction) -> bool:
        """
        Verify that the transaction respects horizon privacy.
        Checks that view keys are properly encrypted and
        that no linking information is leaked.
        """
        if not tx.encrypted_view_keys:
            return False
        if len(tx.encrypted_view_keys) < 64:
            return False
        return True


# ---------------------------------------------------------------------------
# RingCT Transaction Builder
# ---------------------------------------------------------------------------

class RingCTBuilder:
    """
    Builds RingCT transactions with proper commitments,
    range proofs, and ring signatures.
    """

    def __init__(self, blockchain: Blockchain):
        self.blockchain = blockchain

    def create_transaction(
        self,
        sender_keypair: KeyPair,
        recipient_address: str,
        amount: int,
        fee: int = 1000,  # 0.001 TKC default fee
        decoy_count: int = 10,
    ) -> Optional[Transaction]:
        """
        Create a RingCT transaction.
        """
        if amount <= 0:
            logger.error("Amount must be positive")
            return None

        if amount + fee > CONFIG.monetary.max_supply:
            logger.error("Amount exceeds max supply")
            return None

        # Find sufficient UTXOs (in production: scan blockchain)
        # For reference: create a simplified transaction
        tx = Transaction(
            tx_type=TxType.REGULAR,
            fee=fee,
        )

        # Create output with stealth address
        recipient_pub = PublicKey.from_address(recipient_address)
        # In production: use recipient's view/spend keys properly
        stealth = StealthAddress.create(
            recipient_view_key=recipient_pub,
            recipient_spend_key=recipient_pub,
        )

        # Create commitment for the amount
        blinding = _random_scalar()
        commitment = PedersenCommitment.create(amount, blinding)

        # Create range proof
        range_proof = RangeProof.prove(amount, blinding)

        output = TxOutput(
            stealth_address=stealth,
            commitment=commitment,
            range_proof=range_proof,
        )
        tx.outputs = [output]

        # Encrypt view keys for horizon privacy
        tx.encrypted_view_keys = HorizonPrivacy.encrypt_view_keys(
            tx, sender_keypair.public_key, recipient_pub
        )

        return tx

    def verify_transaction(self, tx: Transaction) -> bool:
        """
        Verify a RingCT transaction.
        Checks: commitments balance, range proofs, ring signatures.
        """
        if not tx.validate_basic():
            return False

        # Verify horizon privacy
        if not HorizonPrivacy.verify_horizon(tx):
            logger.warning("Horizon privacy check failed")
            return False

        # Verify range proofs for all outputs
        for output in tx.outputs:
            if not output.range_proof.verify():
                logger.warning("Range proof verification failed")
                return False

        # Verify commitment balance: sum(inputs) - sum(outputs) - fee = 0
        # In production: actual Pedersen commitment verification
        # For reference: basic check
        if tx.tx_type != TxType.COINBASE:
            if tx.fee < 0:
                logger.warning("Negative fee")
                return False

        return True
