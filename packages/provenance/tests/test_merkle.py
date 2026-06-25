"""Merkle log: inclusion proofs, signed checkpoints, consistency under growth, tamper detection."""

from __future__ import annotations

from rooted_provenance.merkle import TransparencyLog, verify_checkpoint
from rooted_provenance.signing import generate_keypair


def test_append_and_inclusion_proof() -> None:
    log = TransparencyLog()
    idx = log.append("hash-a")
    log.append("hash-b")
    assert log.size == 2
    root = log.root()
    proof = log.prove_inclusion(idx, size=2)
    assert log.verify_inclusion(idx, proof, root) is True


def test_inclusion_fails_against_wrong_root() -> None:
    log = TransparencyLog()
    idx = log.append("hash-a")
    log.append("hash-b")
    proof = log.prove_inclusion(idx, size=2)
    assert log.verify_inclusion(idx, proof, b"\x00" * 32) is False


def test_signed_checkpoint_verifies_and_tamper_fails() -> None:
    log = TransparencyLog()
    log.append("hash-a")
    log.append("hash-b")
    priv, pub = generate_keypair()
    cp = log.checkpoint(epoch=1, priv=priv, signed_at="2026-06-25T00:00:00Z")
    assert verify_checkpoint(cp, pub) is True
    tampered = cp.model_copy(update={"root_hash": "deadbeef"})
    assert verify_checkpoint(tampered, pub) is False


def test_consistency_proof_under_growth() -> None:
    log = TransparencyLog()
    log.append("hash-a")
    log.append("hash-b")
    prior_root = log.root(size=2)
    log.append("hash-c")
    current_root = log.root()
    proof = log.prove_consistency(2)
    assert log.verify_consistency(prior_root, current_root, proof) is True
