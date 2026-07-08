"""
Tests for TokenCoin ledger (blockchain, transactions).
"""

import unittest
import hashlib
import time

from tokencoin.ledger import (
    Transaction, TxInput, TxOutput, TxType,
    Block, BlockHeader, Blockchain,
    RingCTBuilder, HorizonPrivacy,
)
from tokencoin.core.crypto import (
    KeyPair, PedersenCommitment, RangeProof,
    StealthAddress, KeyImage, RingSignature,
    _random_scalar,
)


class TestTransaction(unittest.TestCase):
    """Test transaction creation and validation."""

    def test_create_transaction(self):
        """Test basic transaction creation."""
        tx = Transaction(
            tx_type=TxType.REGULAR,
            fee=1000,
        )
        self.assertIsNotNone(tx)
        self.assertEqual(tx.tx_type, TxType.REGULAR)
        self.assertEqual(tx.fee, 1000)

    def test_transaction_hash(self):
        """Test transaction hash computation."""
        tx1 = Transaction(nonce=12345)
        tx2 = Transaction(nonce=67890)
        self.assertNotEqual(tx1.hash(), tx2.hash())

    def test_coinbase_validation(self):
        """Test coinbase transaction validation."""
        tx = Transaction(
            tx_type=TxType.COINBASE,
            outputs=[
                TxOutput(
                    stealth_address=StealthAddress(
                        public_spend=KeyPair.generate().public_key,
                        public_view=KeyPair.generate().public_key,
                        ephemeral=b"\x00" * 32,
                    ),
                    commitment=PedersenCommitment.create(12),
                    range_proof=RangeProof.prove(12, _random_scalar()),
                )
            ],
            fee=0,
        )
        self.assertTrue(tx.validate_basic())

    def test_invalid_version(self):
        """Test that invalid version fails validation."""
        tx = Transaction(version=999)
        self.assertFalse(tx.validate_basic())


class TestBlock(unittest.TestCase):
    """Test block creation and validation."""

    def test_create_block(self):
        """Test basic block creation."""
        block = Block(
            header=BlockHeader(
                height=0,
                timestamp=time.time(),
            )
        )
        self.assertIsNotNone(block)
        self.assertEqual(block.header.height, 0)

    def test_block_hash(self):
        """Test block hash computation."""
        block1 = Block(header=BlockHeader(height=0, nonce=123))
        block2 = Block(header=BlockHeader(height=0, nonce=456))
        self.assertNotEqual(block1.hash(), block2.hash())

    def test_merkle_root(self):
        """Test Merkle root computation."""
        block = Block()
        # Empty block
        root = block.compute_merkle_root()
        self.assertEqual(len(root), 32)

        # Block with transactions
        tx1 = Transaction(nonce=1)
        tx2 = Transaction(nonce=2)
        block.transactions = [tx1, tx2]
        root = block.compute_merkle_root()
        self.assertIsNotNone(root)

    def test_genesis_validation(self):
        """Test genesis block validation."""
        blockchain = Blockchain()
        genesis = blockchain.create_genesis_block()
        self.assertTrue(genesis.validate())
        self.assertEqual(genesis.header.height, 0)


class TestBlockchain(unittest.TestCase):
    """Test blockchain operations."""

    def setUp(self):
        self.blockchain = Blockchain()
        self.blockchain.initialize()

    def test_initialization(self):
        """Test blockchain initialization."""
        self.assertEqual(self.blockchain.state.height, 0)
        self.assertIsNotNone(self.blockchain.get_latest_block())

    def test_add_block(self):
        """Test adding a block to the chain."""
        prev = self.blockchain.get_latest_block()
        block = Block(
            header=BlockHeader(
                height=1,
                prev_hash=prev.hash(),
                merkle_root=hashlib.sha3_256(b"test").digest(),
            ),
            transactions=[
                Transaction(
                    tx_type=TxType.COINBASE,
                    outputs=[
                        TxOutput(
                            stealth_address=StealthAddress(
                                public_spend=KeyPair.generate().public_key,
                                public_view=KeyPair.generate().public_key,
                                ephemeral=b"\x00" * 32,
                            ),
                            commitment=PedersenCommitment.create(12),
                            range_proof=RangeProof.prove(12, _random_scalar()),
                        )
                    ],
                    fee=0,
                )
            ],
        )
        block.header.merkle_root = block.compute_merkle_root()
        self.assertTrue(self.blockchain.add_block(block))
        self.assertEqual(self.blockchain.state.height, 1)

    def test_mempool(self):
        """Test mempool operations."""
        # Create a valid transaction with outputs
        tx = Transaction(
            tx_type=TxType.COINBASE,
            outputs=[
                TxOutput(
                    stealth_address=StealthAddress(
                        public_spend=KeyPair.generate().public_key,
                        public_view=KeyPair.generate().public_key,
                        ephemeral=b"\x00" * 32,
                    ),
                    commitment=PedersenCommitment.create(12),
                    range_proof=RangeProof.prove(12, _random_scalar()),
                )
            ],
            fee=0,
        )
        self.assertTrue(self.blockchain.add_to_mempool(tx))
        self.assertEqual(len(self.blockchain.mempool), 1)

        # Clear mempool
        self.blockchain.clear_mempool({tx.hash().hex()})
        self.assertEqual(len(self.blockchain.mempool), 0)


class TestHorizonPrivacy(unittest.TestCase):
    """Test horizon privacy (single-hop graph visibility)."""

    def test_encrypt_decrypt_view_keys(self):
        """Test encrypting and decrypting view keys."""
        sender = KeyPair.generate()
        recipient = KeyPair.generate()
        tx = Transaction()

        encrypted = HorizonPrivacy.encrypt_view_keys(
            tx, sender.public_key, recipient.public_key
        )
        self.assertIsNotNone(encrypted)
        self.assertGreater(len(encrypted), 0)

        # Verify horizon
        tx.encrypted_view_keys = encrypted
        self.assertTrue(HorizonPrivacy.verify_horizon(tx))


if __name__ == "__main__":
    unittest.main()
