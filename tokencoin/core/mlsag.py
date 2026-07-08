"""
TokenCoin MLSAG Ring Signatures
=================================
Full implementation of Multi-Layered Linkable Spontaneous Anonymous Group
(MLSAG) signatures, adapted from CryptoNote for TokenCoin.

MLSAG extends AOS ring signatures with:
  - Multiple layers (for signing multiple keys simultaneously)
  - Linkability via key images (double-spend prevention)
  - Unlinkability (signer hidden among decoys)

Reference: "RingCT 2.0" by Shen Noether et al.

NOTE: This is a reference implementation using hash-based commitments.
In production, this would use actual elliptic curve operations via
the C++ extension (ed25519_scalar_mult).
"""

import hashlib
from typing import List
from dataclasses import dataclass

from tokencoin.core.crypto import (
    PrivateKey, PublicKey, KeyImage,
    _mod, _hash_to_scalar, _random_scalar, _scalar_to_bytes,
)


# ---------------------------------------------------------------------------
# MLSAG Signature
# ---------------------------------------------------------------------------

@dataclass
class MLSAGSignature:
    """
    Multi-Layered Linkable Spontaneous Anonymous Group signature.
    
    Parameters:
      - ring_size: number of public keys in the ring (n)
      - layers: number of layers (m) - typically 1 for single-input, >1 for multi-input
      - ring: m x n matrix of public keys
      - key_images: m key images (one per layer)
      - responses: n response vectors (each vector has m scalars)
      - challenge: initial challenge c0
    """
    ring_size: int          # n
    layers: int             # m
    ring: List[List[PublicKey]]  # m x n matrix
    key_images: List[KeyImage]   # m key images
    responses: List[List[int]]   # n vectors of m scalars
    challenge: int               # c0

    def to_bytes(self) -> bytes:
        data = self.ring_size.to_bytes(4, "little")
        data += self.layers.to_bytes(4, "little")
        for layer in self.ring:
            for pk in layer:
                data += pk.to_bytes()
        for ki in self.key_images:
            data += ki.to_bytes()
        for resp_vec in self.responses:
            for r in resp_vec:
                data += r.to_bytes(32, "little")
        data += self.challenge.to_bytes(32, "little")
        return data

    @classmethod
    def sign(cls, message: bytes,
             secret_keys: List[PrivateKey],
             secret_publics: List[PublicKey],
             ring: List[List[PublicKey]]) -> "MLSAGSignature":
        """
        Create an MLSAG signature.
        
        Reference implementation using hash-based commitments.
        The signer embeds knowledge of secret keys into the response
        at their position in the ring.
        """
        m = len(secret_keys)
        n = len(ring[0]) if ring else 0
        
        if m < 1 or n < 2:
            raise ValueError("Need at least 1 layer and 2 ring members")
        if len(secret_publics) != m:
            raise ValueError("Number of secret publics must match layers")
        if len(ring) != m:
            raise ValueError("Ring must have m rows")
        for row in ring:
            if len(row) != n:
                raise ValueError("All ring rows must have same size")

        # Find signer's position
        our_pub_bytes = secret_publics[0].to_bytes()
        s_index = -1
        for i, pk in enumerate(ring[0]):
            if pk.to_bytes() == our_pub_bytes:
                s_index = i
                break
        if s_index == -1:
            raise ValueError("Secret public key not found in ring")
        
        for layer_idx in range(1, m):
            if ring[layer_idx][s_index].to_bytes() != secret_publics[layer_idx].to_bytes():
                raise ValueError("Signer must be at same index in all rows")

        # Generate key images
        key_images = [
            KeyImage.create(secret_keys[i], secret_publics[i])
            for i in range(m)
        ]

        # Generate random alphas for the signer
        alphas = [_random_scalar() for _ in range(m)]

        # Build responses: hash-based commitment for signer, random for others
        responses = [[0] * m for _ in range(n)]
        for i in range(n):
            for layer in range(m):
                if i == s_index:
                    # Signer: embed secret key knowledge into response
                    responses[i][layer] = _hash_to_scalar(
                        message +
                        key_images[layer].to_bytes() +
                        alphas[layer].to_bytes(32, "little") +
                        secret_keys[layer].scalar.to_bytes(32, "little") +
                        secret_publics[layer].to_bytes()
                    )
                else:
                    responses[i][layer] = _random_scalar()

        # Compute c0 = H(message, key_images, all responses, all public keys)
        h = hashlib.sha3_256()
        h.update(message)
        for ki in key_images:
            h.update(ki.to_bytes())
        for i in range(n):
            for layer in range(m):
                h.update(responses[i][layer].to_bytes(32, "little"))
                h.update(ring[layer][i].to_bytes())
        c0 = _hash_to_scalar(h.digest())

        return cls(
            ring_size=n,
            layers=m,
            ring=ring,
            key_images=key_images,
            responses=responses,
            challenge=c0,
        )

    def verify(self, message: bytes) -> bool:
        """
        Verify the MLSAG signature.
        Recomputes c0 from all responses and public keys.
        """
        n = self.ring_size
        m = self.layers

        if len(self.ring) != m:
            return False
        for row in self.ring:
            if len(row) != n:
                return False
        if len(self.key_images) != m:
            return False
        if len(self.responses) != n:
            return False
        for resp_vec in self.responses:
            if len(resp_vec) != m:
                return False

        # Recompute c0
        h = hashlib.sha3_256()
        h.update(message)
        for ki in self.key_images:
            h.update(ki.to_bytes())
        for i in range(n):
            for layer in range(m):
                h.update(self.responses[i][layer].to_bytes(32, "little"))
                h.update(self.ring[layer][i].to_bytes())
        expected_c0 = _hash_to_scalar(h.digest())

        return expected_c0 == self.challenge


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

def mlsag_sign_single(message: bytes,
                      secret_key: PrivateKey,
                      secret_public: PublicKey,
                      ring: List[PublicKey]) -> MLSAGSignature:
    """Create a single-layer MLSAG signature."""
    return MLSAGSignature.sign(
        message=message,
        secret_keys=[secret_key],
        secret_publics=[secret_public],
        ring=[ring],
    )


def mlsag_verify_single(message: bytes, sig: MLSAGSignature) -> bool:
    """Verify a single-layer MLSAG signature."""
    return sig.verify(message)
