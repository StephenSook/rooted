"""Merkle transparency log: append-only proof that a manifest existed and was not altered.

Each manifest's canonical hash becomes a leaf. Periodically the signed tree head (a checkpoint) is
written to B2 under Object Lock, so the ledger is tamper-evident: no one, including the operator,
can rewrite history without contradicting a checkpoint that physically cannot be deleted.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pymerkle import (
    InmemoryTree,
    InvalidProof,
    MerkleProof,
    verify_consistency,
    verify_inclusion,
)

from .models import MerkleCheckpoint


def _checkpoint_head(epoch: int, tree_size: int, root_hex: str) -> bytes:
    """The exact bytes a checkpoint signs over."""
    return f"rooted-checkpoint:{epoch}:{tree_size}:{root_hex}".encode()


class LeafStore(Protocol):
    """Durable, ordered record of the leaves so the in-memory tree can be rebuilt after a restart.

    Implemented in-memory here and by PostgresTransparencyStore for the deployed path.
    """

    def append(self, manifest_id: str, leaf_hash: str) -> None: ...
    def all(self) -> list[tuple[str, str]]: ...  # (manifest_id, leaf_hash) in append order


@dataclass
class InMemoryLeafStore:
    """Non-durable leaf store for the credential-free demo and tests."""

    leaves: list[tuple[str, str]] = field(default_factory=list)

    def append(self, manifest_id: str, leaf_hash: str) -> None:
        self.leaves.append((manifest_id, leaf_hash))

    def all(self) -> list[tuple[str, str]]:
        return list(self.leaves)


class TransparencyLog:
    """An in-memory Merkle tree plus a manifest-id -> leaf-index map, both rebuilt on construction
    from a durable LeafStore. Persisting the ordered leaves (and replaying them) means inclusion
    proofs survive a restart or a second instance, instead of resetting to an empty tree."""

    def __init__(self, leaf_store: LeafStore | None = None) -> None:
        self._tree = InmemoryTree(algorithm="sha256")
        self._index: dict[str, int] = {}
        self._store: LeafStore = leaf_store if leaf_store is not None else InMemoryLeafStore()
        for manifest_id, leaf_hash in self._store.all():
            self._index[manifest_id] = self._tree.append_entry(leaf_hash.encode())

    def append(self, manifest_id: str, leaf_hash: str) -> int:
        """Add a manifest's canonical hash as a leaf (persisting it); return its 1-based index."""
        position = self._tree.append_entry(leaf_hash.encode())
        self._index[manifest_id] = position
        self._store.append(manifest_id, leaf_hash)
        return position

    def index_for(self, manifest_id: str) -> int | None:
        return self._index.get(manifest_id)

    def entries(self) -> list[tuple[int, str, str]]:
        """The ordered log entries as (leaf_index, manifest_id, leaf_hash). A transparency log is
        meant to be auditable, so exposing the append-ordered leaves is by design."""
        return [(i, mid, leaf_hash) for i, (mid, leaf_hash) in enumerate(self._store.all())]

    def close(self) -> None:
        """Release the leaf store's resources (e.g. a Postgres pool); a no-op for in-memory."""
        close = getattr(self._store, "close", None)
        if callable(close):
            close()

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
        except InvalidProof:
            return False

    def prove_consistency(self, prior_size: int, size: int | None = None) -> MerkleProof:
        return self._tree.prove_consistency(prior_size, size)

    def verify_consistency(
        self, prior_root: bytes, current_root: bytes, proof: MerkleProof
    ) -> bool:
        try:
            verify_consistency(prior_root, current_root, proof)
            return True
        except InvalidProof:
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
    except (InvalidSignature, binascii.Error):
        # bad signature or malformed base64; a None/non-str field is a wiring bug and should crash.
        return False
