"""
Tests for TokenCoin cryptographic primitives.
"""

import unittest
import hashlib
import os

from tokencoin.core.crypto import (
    PrivateKey, PublicKey, KeyPair,
    PedersenCommitment, StealthAddress,
    KeyImage, RingSignature, RangeProof,
    base32_encode, base32_decode,
    _hash_to_scalar, _random_scalar,
)


class TestBase32(unittest.TestCase):
    """Test Base32 encoding/decoding."""

    def test_encode_decode(self):
        """Test that encoding then decoding returns original data."""
        original = os.urandom(35)  # Tor v3 address size
        encoded = base32_encode(original)
        decoded = base32_decode(encoded)
        self.assertEqual(original, decoded)

    def test_address_length(self):
        """Test that encoded address is 56 characters."""
        data = os.urandom(35)
        encoded = base32_encode(data)
        self.assertEqual(len(encoded), 56)

    def test_invalid_characters(self):
        """Test that invalid characters raise ValueError."""
        with self.assertRaises(ValueError):
            base32_decode("invalid!char")


class TestKeyPair(unittest.TestCase):
    """Test key pair generation and address derivation."""

    def test_generate(self):
        """Test key pair generation."""
        kp = KeyPair.generate()
        self.assertIsNotNone(kp.private_key)
        self.assertIsNotNone(kp.public_key)
        self.assertEqual(len(kp.private_key.seed), 32)
        self.assertEqual(len(kp.public_key.point), 32)

    def test_deterministic_seed(self):
        """Test that same seed produces same key pair."""
        # Use exactly 32 bytes
        seed = bytes(range(32))  # 0, 1, 2, ..., 31 - exactly 32 bytes
        kp1 = KeyPair.generate(seed)
        kp2 = KeyPair.generate(seed)
        self.assertEqual(kp1.private_key.seed, kp2.private_key.seed)
        self.assertEqual(kp1.public_key.point, kp2.public_key.point)

    def test_address_format(self):
        """Test that address is 56-char Base32."""
        kp = KeyPair.generate()
        addr = kp.to_address()
        self.assertEqual(len(addr), 56)
        # All characters should be valid Base32
        valid_chars = set("abcdefghijklmnopqrstuvwxyz234567")
        self.assertTrue(all(c in valid_chars for c in addr))


class TestPedersenCommitment(unittest.TestCase):
    """Test Pedersen commitments."""

    def test_create_commitment(self):
        """Test commitment creation."""
        amount = 1000
        comm = PedersenCommitment.create(amount)
        self.assertIsNotNone(comm)
        self.assertEqual(len(comm.commitment), 32)

    def test_different_amounts(self):
        """Test that different amounts produce different commitments."""
        comm1 = PedersenCommitment.create(100)
        comm2 = PedersenCommitment.create(200)
        self.assertNotEqual(comm1.commitment, comm2.commitment)

    def test_same_amount_different_blinding(self):
        """Test that same amount with different blinding produces different commitments."""
        comm1 = PedersenCommitment.create(100, blinding=12345)
        comm2 = PedersenCommitment.create(100, blinding=67890)
        self.assertNotEqual(comm1.commitment, comm2.commitment)


class TestStealthAddress(unittest.TestCase):
    """Test stealth address generation and recovery."""

    def test_create_and_recover(self):
        """Test creating a stealth address and recovering it."""
        recipient = KeyPair.generate()
        sender = KeyPair.generate()

        # Create stealth address
        stealth = StealthAddress.create(
            recipient_view_key=recipient.public_key,
            recipient_spend_key=recipient.public_key,
        )
        self.assertIsNotNone(stealth)
        self.assertEqual(len(stealth.ephemeral), 32)

        # Recover the private key
        recovered = stealth.recover(
            view_priv=recipient.private_key,
            spend_priv=recipient.private_key,
        )
        self.assertIsNotNone(recovered)

    def test_wrong_recipient_cannot_recover(self):
        """Test that wrong recipient cannot recover the stealth address."""
        recipient = KeyPair.generate()
        wrong_recipient = KeyPair.generate()
        sender = KeyPair.generate()

        stealth = StealthAddress.create(
            recipient_view_key=recipient.public_key,
            recipient_spend_key=recipient.public_key,
        )

        # Wrong recipient should not be able to recover
        recovered = stealth.recover(
            view_priv=wrong_recipient.private_key,
            spend_priv=wrong_recipient.private_key,
        )
        self.assertIsNone(recovered)


class TestKeyImage(unittest.TestCase):
    """Test key image generation."""

    def test_create_key_image(self):
        """Test key image creation."""
        kp = KeyPair.generate()
        key_image = KeyImage.create(kp.private_key, kp.public_key)
        self.assertIsNotNone(key_image)
        self.assertEqual(len(key_image.image), 32)

    def test_deterministic(self):
        """Test that same key pair produces same key image."""
        kp = KeyPair.generate()
        ki1 = KeyImage.create(kp.private_key, kp.public_key)
        ki2 = KeyImage.create(kp.private_key, kp.public_key)
        self.assertEqual(ki1.image, ki2.image)


class TestRingSignature(unittest.TestCase):
    """Test ring signatures."""

    def test_sign_and_verify(self):
        """Test signing and verifying a ring signature."""
        # Create a ring of public keys
        ring = [KeyPair.generate().public_key for _ in range(5)]
        signer = KeyPair.generate()
        ring.append(signer.public_key)  # Add signer to ring

        message = b"test_message_for_ring_signature"

        # Sign
        sig = RingSignature.sign(
            message=message,
            secret_key=signer.private_key,
            secret_public=signer.public_key,
            ring=ring,
        )
        self.assertIsNotNone(sig)
        self.assertEqual(sig.ring_size, len(ring))

        # Verify
        self.assertTrue(sig.verify(message))

    def test_verify_wrong_message(self):
        """Test that wrong message fails verification."""
        ring = [KeyPair.generate().public_key for _ in range(3)]
        signer = KeyPair.generate()
        ring.append(signer.public_key)

        sig = RingSignature.sign(
            message=b"original_message",
            secret_key=signer.private_key,
            secret_public=signer.public_key,
            ring=ring,
        )

        self.assertFalse(sig.verify(b"wrong_message"))


class TestRangeProof(unittest.TestCase):
    """Test range proofs."""

    def test_prove_and_verify(self):
        """Test proving and verifying a range proof."""
        amount = 500
        blinding = 12345
        proof = RangeProof.prove(amount, blinding)
        self.assertIsNotNone(proof)
        self.assertTrue(proof.verify())

    def test_negative_amount(self):
        """Test that negative amount raises error."""
        with self.assertRaises(ValueError):
            RangeProof.prove(-1, 12345)

    def test_out_of_range(self):
        """Test that amount out of range raises error."""
        with self.assertRaises(ValueError):
            RangeProof.prove(1 << 65, 12345)  # 65 bits > 64 bits


if __name__ == "__main__":
    unittest.main()
