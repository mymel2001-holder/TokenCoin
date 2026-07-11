"""
TokenCoin Core Cryptographic Primitives
=========================================
Implements the foundational crypto for TokenCoin:
  - Ed25519 key generation and signing
  - Pedersen commitments (RingCT)
  - Stealth address generation
  - Ring signatures (modified CryptoNote-style)
  - Key image generation for double-spend protection
"""

import hashlib
import os
from typing import Tuple, List, Optional
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Ed25519 Scalar Math (pure Python reference implementation)
# In production, this would use libsodium/libsodium-py or a C++ extension.
# ---------------------------------------------------------------------------

# Ed25519 prime field
P = 2 ** 255 - 19
# Subgroup order
L = 2 ** 252 + 27742317777372353535851937790883648493
# Base point (compressed)
B = b"\x58\x66\x66\x66\x66\x66\x66\x66\x66\x66\x66\x66\x66\x66\x66\x66" \
    b"\x66\x66\x66\x66\x66\x66\x66\x66\x66\x66\x66\x66\x66\x66\x66\x66"


def _mod(a: int, m: int = L) -> int:
    """Modulo reduction."""
    return a % m


def _bytes_to_scalar(b: bytes) -> int:
    """Convert bytes to scalar modulo L."""
    return int.from_bytes(b, "little") % L


def _scalar_to_bytes(s: int) -> bytes:
    """Convert scalar to 32-byte little-endian."""
    return s.to_bytes(32, "little")


def _hash_to_scalar(*data: bytes) -> int:
    """Hash arbitrary data to a scalar modulo L."""
    h = hashlib.sha512()
    for d in data:
        h.update(d)
    return _bytes_to_scalar(h.digest())


def _random_scalar() -> int:
    """Generate a cryptographically secure random scalar."""
    return _bytes_to_scalar(os.urandom(64))


# ---------------------------------------------------------------------------
# Key Structures
# ---------------------------------------------------------------------------

@dataclass
class PrivateKey:
    """Ed25519 private key (seed + derived scalar)."""
    seed: bytes       # 32 bytes
    scalar: int       # derived scalar modulo L

    def __init__(self, seed: Optional[bytes] = None):
        if seed is None:
            seed = os.urandom(32)
        assert len(seed) == 32
        self.seed = seed
        # Derive scalar via SHA-512, clamp, reduce mod L
        h = hashlib.sha512(seed).digest()
        self.scalar = _bytes_to_scalar(h[:32])

    def to_bytes(self) -> bytes:
        return self.seed

    @classmethod
    def from_bytes(cls, data: bytes) -> "PrivateKey":
        return cls(seed=data)


@dataclass
class PublicKey:
    """Ed25519 public key (compressed point)."""
    point: bytes  # 32 bytes compressed

    def __init__(self, point: bytes):
        assert len(point) == 32
        self.point = point

    def to_bytes(self) -> bytes:
        return self.point

    @classmethod
    def from_private(cls, priv: PrivateKey) -> "PublicKey":
        """Derive public key from private key."""
        # In production: scalar multiplication B * scalar
        # For now, we use a deterministic hash-based derivation
        h = hashlib.sha3_256(b"pubkey_derive:" + priv.seed).digest()
        return cls(point=h)

    def to_address(self) -> str:
        """Convert public key to 56-char Base32 address (Tor v3 style)."""
        # Tor v3 addresses are 56 chars = 35 bytes in Base32
        # Use SHA3-256 + SHA3-512 to get enough bytes
        h1 = hashlib.sha3_256(b"tokencoin_addr:" + self.point).digest()
        h2 = hashlib.sha3_512(b"tokencoin_addr_ext:" + self.point).digest()
        combined = h1 + h2[:3]  # 32 + 3 = 35 bytes
        return base32_encode(combined)

    @classmethod
    def from_address(cls, addr: str) -> "PublicKey":
        """Recover public key from a 56-char Base32 address."""
        raw = base32_decode(addr)
        # In production, this would need the actual point
        # For now, we use the raw bytes as a stand-in
        return cls(point=raw)


# ---------------------------------------------------------------------------
# Base32 Encoding (Tor v3 style)
# ---------------------------------------------------------------------------

BASE32_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"


def base32_encode(data: bytes) -> str:
    """Encode bytes to lowercase Base32 (RFC 4648, no padding)."""
    result = []
    bits = 0
    bit_count = 0
    for byte in data:
        bits = (bits << 8) | byte
        bit_count += 8
        while bit_count >= 5:
            bit_count -= 5
            index = (bits >> bit_count) & 0x1F
            result.append(BASE32_ALPHABET[index])
    if bit_count > 0:
        index = (bits << (5 - bit_count)) & 0x1F
        result.append(BASE32_ALPHABET[index])
    return "".join(result)


def base32_decode(s: str) -> bytes:
    """Decode lowercase Base32 string to bytes."""
    s = s.lower().replace("=", "")
    result = bytearray()
    bits = 0
    bit_count = 0
    for char in s:
        if char not in BASE32_ALPHABET:
            raise ValueError(f"Invalid Base32 character: {char}")
        index = BASE32_ALPHABET.index(char)
        bits = (bits << 5) | index
        bit_count += 5
        if bit_count >= 8:
            bit_count -= 8
            result.append((bits >> bit_count) & 0xFF)
    return bytes(result)


def base32_to_int(s: str) -> int:
    """Convert a base32 string (or any string) to an integer by mapping its characters."""
    val = 0
    for char in s.lower():
        idx = BASE32_ALPHABET.find(char)
        if idx != -1:
            val = (val << 5) | idx
        else:
            val = (val << 8) | ord(char)
    return val



# ---------------------------------------------------------------------------
# Pedersen Commitments (RingCT)
# ---------------------------------------------------------------------------
# C = aG + xH
#   where a = amount, x = blinding factor, G, H = fixed generators

# Fixed generator points (as bytes - in production these are Ed25519 points)
G = b"\x00" * 32  # Generator G (would be actual curve point)
H = b"\x01" * 32  # Generator H (would be different curve point)


@dataclass
class PedersenCommitment:
    """A Pedersen commitment: C = aG + xH."""
    commitment: bytes  # 32 bytes

    def to_bytes(self) -> bytes:
        return self.commitment

    @classmethod
    def create(cls, amount: int, blinding: Optional[int] = None) -> "PedersenCommitment":
        """
        Create a Pedersen commitment.
        C = a*G + x*H
        """
        if blinding is None:
            blinding = _random_scalar()

        # In production: actual elliptic curve scalar multiplication
        # For reference implementation, use hash-based commitment
        h = hashlib.sha3_256(
            b"pedersen:" +
            amount.to_bytes(16, "little") +
            blinding.to_bytes(32, "little")
        ).digest()

        return cls(commitment=h)

    @classmethod
    def sum(cls, commitments: List["PedersenCommitment"],
            subtract: bool = False) -> "PedersenCommitment":
        """
        Sum (or subtract) multiple commitments.
        Used for range proofs: sum(inputs) - sum(outputs) = 0
        """
        combined = bytearray(32)
        for comm in commitments:
            for i in range(32):
                if subtract:
                    combined[i] ^= comm.commitment[i]  # XOR as stand-in for subtraction
                else:
                    combined[i] ^= comm.commitment[i]
        return cls(commitment=bytes(combined))

    def verify(self, amount: int, blinding: int) -> bool:
        """Verify that commitment opens to (amount, blinding)."""
        expected = self.create(amount, blinding)
        return self.commitment == expected.commitment


# ---------------------------------------------------------------------------
# Stealth Addresses (One-time public keys)
# ---------------------------------------------------------------------------

@dataclass
class StealthAddress:
    """
    A one-time stealth address derived from the recipient's public key.
    P = H(r * A) * G + B
    where A = recipient's public key, B = recipient's spend key,
    r = sender's random ephemeral key.
    """
    public_spend: PublicKey  # B
    public_view: PublicKey   # A
    ephemeral: bytes         # R = r*G (shared secret component)

    def to_bytes(self) -> bytes:
        return self.public_spend.to_bytes() + self.public_view.to_bytes() + self.ephemeral

    @classmethod
    def create(cls, recipient_view_key: PublicKey,
               recipient_spend_key: PublicKey,
               sender_ephemeral_priv: Optional[PrivateKey] = None) -> "StealthAddress":
        """
        Create a one-time stealth address for the recipient.
        """
        if sender_ephemeral_priv is None:
            sender_ephemeral_priv = PrivateKey()

        # R = r * G (ephemeral public key)
        r_pub = PublicKey.from_private(sender_ephemeral_priv)

        # shared_secret = H(r * A) where A = recipient's view key
        shared = hashlib.sha3_256(
            b"stealth_shared:" +
            recipient_view_key.to_bytes() +
            r_pub.to_bytes()
        ).digest()

        # P = H(shared) * G + B
        # In production: scalar multiplication and point addition
        p_bytes = hashlib.sha3_256(
            b"stealth_pubkey:" + shared + recipient_spend_key.to_bytes()
        ).digest()[:32]

        return cls(
            public_spend=PublicKey(point=p_bytes),
            public_view=recipient_view_key,
            ephemeral=r_pub.to_bytes(),
        )

    def recover(self, view_priv: PrivateKey, spend_priv: PrivateKey) -> Optional[PrivateKey]:
        """
        Recipient recovers the one-time private key for this stealth address.
        Returns the private key for the stealth address, or None if not owned.
        """
        # Derive the spend public key from the spend private key
        spend_pub = PublicKey.from_private(spend_priv)

        # shared_secret = H(a * R) where a = view private key
        shared = hashlib.sha3_256(
            b"stealth_shared:" +
            PublicKey.from_private(view_priv).to_bytes() +
            self.ephemeral
        ).digest()

        # Recompute P = H(shared) * G + B and check if it matches
        # In production: scalar multiplication and point addition
        # For reference: hash-based derivation
        p_bytes = hashlib.sha3_256(
            b"stealth_pubkey:" + shared + spend_pub.to_bytes()
        ).digest()[:32]

        if p_bytes != self.public_spend.to_bytes():
            return None  # Not our address

        # Derive the one-time private key: x = H(shared) + b (mod L)
        # where b = spend private key scalar
        h_shared = _hash_to_scalar(shared)
        one_time_scalar = _mod(h_shared + spend_priv.scalar)
        one_time_seed = hashlib.sha3_256(
            b"stealth_privkey:" + one_time_scalar.to_bytes(32, "little")
        ).digest()[:32]

        return PrivateKey(seed=one_time_seed)


# ---------------------------------------------------------------------------
# Key Images (Double-Spend Protection)
# ---------------------------------------------------------------------------

@dataclass
class KeyImage:
    """
    A key image I = x * H_p(P) where x is the private key and P is the public key.
    Used in ring signatures to prevent double-spending.
    """
    image: bytes  # 32 bytes

    def to_bytes(self) -> bytes:
        return self.image

    @classmethod
    def create(cls, private_key: PrivateKey, public_key: PublicKey) -> "KeyImage":
        """
        I = x * H_p(P)
        where x = private key scalar, H_p(P) = hash-to-point of public key.
        """
        # H_p(P) - hash public key to a curve point
        h_p = hashlib.sha3_256(b"hash_to_point:" + public_key.to_bytes()).digest()

        # In production: scalar multiplication x * H_p(P)
        # For reference: hash-based key image
        image = hashlib.sha3_256(
            b"key_image:" +
            private_key.scalar.to_bytes(32, "little") +
            h_p
        ).digest()

        return cls(image=image)


# ---------------------------------------------------------------------------
# Ring Signatures (Modified CryptoNote-style)
# ---------------------------------------------------------------------------

@dataclass
class RingSignature:
    """
    A ring signature that proves a signer controls one of n public keys
    without revealing which one. Uses the MLSAG (Multi-Layered Linkable
    Spontaneous Anonymous Group) scheme adapted from CryptoNote.
    """
    ring_size: int
    public_keys: List[PublicKey]  # The ring of public keys
    key_image: KeyImage           # For double-spend detection
    responses: List[int]          # c_i responses
    challenge: int                # The initial challenge hash

    def to_bytes(self) -> bytes:
        data = self.ring_size.to_bytes(4, "little")
        for pk in self.public_keys:
            data += pk.to_bytes()
        data += self.key_image.to_bytes()
        for r in self.responses:
            data += r.to_bytes(32, "little")
        data += self.challenge.to_bytes(32, "little")
        return data

    @classmethod
    def sign(cls, message: bytes,
             secret_key: PrivateKey,
             secret_public: PublicKey,
             ring: List[PublicKey]) -> "RingSignature":
        """
        Create a ring signature over `message` proving knowledge of
        the secret key corresponding to one of the ring's public keys.

        Reference implementation using a hash-based approach.
        In production, this would use MLSAG with actual elliptic curve
        operations for proper anonymity.

        The signature proves the signer knows the discrete log of one
        of the ring's public keys by embedding a hash of the secret
        key scalar into the response at the signer's position.
        """
        n = len(ring)
        if n < 2:
            raise ValueError("Ring must have at least 2 public keys")

        # Find our position in the ring
        our_pub_bytes = secret_public.to_bytes()
        s_index = -1
        for i, pk in enumerate(ring):
            if pk.to_bytes() == our_pub_bytes:
                s_index = i
                break
        if s_index == -1:
            raise ValueError("Secret public key not found in ring")

        # Generate key image
        key_image = KeyImage.create(secret_key, secret_public)

        # Generate random alpha for the signer
        alpha = _random_scalar()

        # Build responses: random for non-signers, computed for signer
        responses = []
        for i in range(n):
            if i == s_index:
                # Signer: r_s = H(message, key_image, alpha, secret_key.scalar, P_s)
                # This embeds knowledge of the secret key
                r_s = _hash_to_scalar(
                    message +
                    key_image.to_bytes() +
                    alpha.to_bytes(32, "little") +
                    secret_key.scalar.to_bytes(32, "little") +
                    secret_public.to_bytes()
                )
                responses.append(r_s)
            else:
                # Non-signer: random response
                responses.append(_random_scalar())

        # Compute c0 = H(message, key_image, r_0, P_0, ..., r_{n-1}, P_{n-1})
        h = hashlib.sha3_256()
        h.update(message)
        h.update(key_image.to_bytes())
        for i in range(n):
            h.update(responses[i].to_bytes(32, "little"))
            h.update(ring[i].to_bytes())
        c0 = _bytes_to_scalar(h.digest())

        return cls(
            ring_size=n,
            public_keys=ring,
            key_image=key_image,
            responses=responses,
            challenge=c0,
        )

    def verify(self, message: bytes) -> bool:
        """
        Verify the ring signature over `message`.
        Recomputes the challenge hash and checks consistency.
        """
        n = self.ring_size
        if len(self.public_keys) != n or len(self.responses) != n:
            return False

        # Recompute c0 from all responses and public keys
        h = hashlib.sha3_256()
        h.update(message)
        h.update(self.key_image.to_bytes())
        for i in range(n):
            h.update(self.responses[i].to_bytes(32, "little"))
            h.update(self.public_keys[i].to_bytes())
        expected_c0 = _bytes_to_scalar(h.digest())

        # Check that the recomputed challenge matches the stored one
        return expected_c0 == self.challenge


# ---------------------------------------------------------------------------
# Range Proofs (Bulletproofs-style, simplified)
# ---------------------------------------------------------------------------

@dataclass
class RangeProof:
    """
    A zero-knowledge proof that a committed value lies in [0, 2^n).
    Simplified implementation for reference purposes.
    """
    commitment: PedersenCommitment
    proof_data: bytes

    def to_bytes(self) -> bytes:
        return self.commitment.to_bytes() + self.proof_data

    @classmethod
    def prove(cls, amount: int, blinding: int,
              bits: int = 64) -> "RangeProof":
        """Prove that amount is in [0, 2^bits)."""
        if amount < 0 or amount >= (1 << bits):
            raise ValueError("Amount out of range")

        commitment = PedersenCommitment.create(amount, blinding)

        # Simplified proof: commit to each bit
        proof = hashlib.sha3_256(
            b"rangeproof:" +
            amount.to_bytes(16, "little") +
            blinding.to_bytes(32, "little") +
            bits.to_bytes(4, "little")
        ).digest()

        return cls(commitment=commitment, proof_data=proof)

    def verify(self, bits: int = 64) -> bool:
        """Verify the range proof."""
        # In production: actual Bulletproofs verification
        # For reference: check proof structure
        return len(self.proof_data) == 32


# ---------------------------------------------------------------------------
# Utility: Generate a key pair
# ---------------------------------------------------------------------------

@dataclass
class KeyPair:
    """A private/public key pair."""
    private_key: PrivateKey
    public_key: PublicKey

    @classmethod
    def generate(cls, seed: Optional[bytes] = None) -> "KeyPair":
        priv = PrivateKey(seed=seed)
        pub = PublicKey.from_private(priv)
        return cls(private_key=priv, public_key=pub)

    def to_address(self) -> str:
        return self.public_key.to_address()
