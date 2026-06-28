"""Merkle log: inclusion proofs, signed checkpoints, consistency under growth, leaf->index map, and
rehydration from a durable leaf store (so proofs survive a restart)."""

from __future__ import annotations

from rooted_provenance.merkle import InMemoryLeafStore, TransparencyLog, verify_checkpoint
from rooted_provenance.signing import generate_keypair


def test_append_and_inclusion_proof() -> None:
    log = TransparencyLog()
    idx = log.append("urn:c2pa:a", "hash-a")
    log.append("urn:c2pa:b", "hash-b")
    assert log.size == 2
    root = log.root()
    proof = log.prove_inclusion(idx, size=2)
    assert log.verify_inclusion(idx, proof, root) is True


def test_index_for_maps_manifest_to_leaf() -> None:
    log = TransparencyLog()
    pos = log.append("urn:c2pa:a", "hash-a")
    assert log.index_for("urn:c2pa:a") == pos
    assert log.index_for("urn:c2pa:absent") is None


def test_inclusion_fails_against_wrong_root() -> None:
    log = TransparencyLog()
    idx = log.append("urn:c2pa:a", "hash-a")
    log.append("urn:c2pa:b", "hash-b")
    proof = log.prove_inclusion(idx, size=2)
    assert log.verify_inclusion(idx, proof, b"\x00" * 32) is False


def test_signed_checkpoint_verifies_and_tamper_fails() -> None:
    log = TransparencyLog()
    log.append("urn:c2pa:a", "hash-a")
    log.append("urn:c2pa:b", "hash-b")
    priv, pub = generate_keypair()
    cp = log.checkpoint(epoch=1, priv=priv, signed_at="2026-06-25T00:00:00Z")
    assert verify_checkpoint(cp, pub) is True
    tampered = cp.model_copy(update={"root_hash": "deadbeef"})
    assert verify_checkpoint(tampered, pub) is False


def test_consistency_proof_under_growth() -> None:
    log = TransparencyLog()
    log.append("urn:c2pa:a", "hash-a")
    log.append("urn:c2pa:b", "hash-b")
    prior_root = log.root(size=2)
    log.append("urn:c2pa:c", "hash-c")
    current_root = log.root()
    proof = log.prove_consistency(2)
    assert log.verify_consistency(prior_root, current_root, proof) is True


def test_snapshot_is_internally_consistent() -> None:
    # snapshot() returns (entries, size, root) read in one synchronous pass, so the leaf list,
    # the tree size, and the root cannot disagree under concurrent appends.
    log = TransparencyLog()
    log.append("urn:c2pa:a", "hash-a")
    log.append("urn:c2pa:b", "hash-b")
    entries, size, root = log.snapshot()
    assert size == 2
    assert len(entries) == 2
    assert entries == [(0, "urn:c2pa:a", "hash-a"), (1, "urn:c2pa:b", "hash-b")]
    assert root == log.root(2)


def test_signed_proof_pins_proof_and_checkpoint_to_one_state() -> None:
    # signed_proof returns the proof and the signed checkpoint computed under one lock, so the
    # checkpoint pins the exact root the proof was built against (no divergence under a race).
    priv, pub = generate_keypair()
    log = TransparencyLog()
    log.append("urn:c2pa:a", "hash-a")
    idx = log.append("urn:c2pa:b", "hash-b")
    size, root, proof, cp, verified = log.signed_proof(idx, priv, "2026-06-28T00:00:00Z")
    assert verified is True
    assert size == 2
    assert cp.tree_size == size
    assert cp.root_hash == root.hex()  # the checkpoint pins the SAME root as the proof
    assert verify_checkpoint(cp, pub) is True
    assert log.verify_inclusion(idx, proof, root) is True


def test_rehydrates_tree_and_index_from_leaf_store() -> None:
    store = InMemoryLeafStore()
    log = TransparencyLog(store)
    log.append("urn:c2pa:a", "hash-a")
    log.append("urn:c2pa:b", "hash-b")
    root_before = log.root()
    # A "restart": a fresh log over the SAME store rebuilds the tree and the index from the
    # persisted leaves, so inclusion proofs still resolve instead of 404ing against an empty tree.
    reborn = TransparencyLog(store)
    assert reborn.size == 2
    assert reborn.root() == root_before
    assert reborn.index_for("urn:c2pa:a") == 1
    assert reborn.index_for("urn:c2pa:b") == 2
    proof = reborn.prove_inclusion(1, size=2)
    assert reborn.verify_inclusion(1, proof, reborn.root()) is True
