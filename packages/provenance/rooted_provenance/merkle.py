"""Merkle transparency log: append-only proof that a manifest existed and was not altered.

Each manifest's canonical hash becomes a leaf. Periodically the signed tree head (a checkpoint) is
written to B2 under Object Lock, so the ledger is tamper-evident: no one, including the operator,
can rewrite history without contradicting a checkpoint that physically cannot be deleted.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pymerkle import InmemoryTree, MerkleProof, verify_consistency, verify_inclusion

from .models import MerkleCheckpoint


def _checkpoint_head(epoch: int, tree_size: int, root_hex: str) -> bytes:
    """The exact bytes a checkpoint signs over."""
    return f"rooted-checkpoint:{epoch}:{tree_size}:{root_hex}".encode()


class TransparencyLog:
    """A thin wrapper over an in-memory Merkle tree. SqliteTree is the persistent swap for prod."""

    def __init__(self) -> None:
        self._tree = InmemoryTree(algorithm="sha256")

    def append(self, manifest_hash_hex: str) -> int:
        """Add a manifest's canonical hash as a leaf; return its 1-based index."""
        return self._tree.append_entry(manifest_hash_hex.encode())

    @property
    def size(self) -> int:
        return self._tree.get_size()

    def root(self, size: int | None = None) -> bytes:
        return self._tree.get_state(size)

    def prove_inclusion(self, index: int, size: int | None = None) -> MerkleProof:
        return self._tree.prove_inclusion(index, size)

    def verify_inclusion(self, index: int, proof: MerkleProof, root: bytes) -> bool:
        base = self._tree.get_leaf(index)
        try:
            verify_inclusion(base, root, proof)
            return True
        except Exception:
            return False

    def prove_consistency(self, prior_size: int, size: int | None = None) -> MerkleProof:
        return self._tree.prove_consistency(prior_size, size)

    def verify_consistency(
        self, prior_root: bytes, current_root: bytes, proof: MerkleProof
    ) -> bool:
        try:
            verify_consistency(prior_root, current_root, proof)
            return True
        except Exception:
            return False

    def checkpoint(self, epoch: int, priv: Ed25519PrivateKey, signed_at: str) -> MerkleCheckpoint:
        size = self.size
        root_hex = self.root().hex()
        signature = priv.sign(_checkpoint_head(epoch, size, root_hex))
        return MerkleCheckpoint(
            epoch=epoch,
            tree_size=size,
            root_hash=root_hex,
            signed_at=signed_at,
            signature_b64=base64.b64encode(signature).decode(),
        )


def verify_checkpoint(cp: MerkleCheckpoint, pub: Ed25519PublicKey) -> bool:
    head = _checkpoint_head(cp.epoch, cp.tree_size, cp.root_hash)
    try:
        pub.verify(base64.b64decode(cp.signature_b64), head)
        return True
    except Exception:
        return False
