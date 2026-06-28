"""Tests for the Object-Lock checkpoint surface (/transparency/checkpoint/object).

A signed Merkle checkpoint is sealed under compliance Object Lock and read back: the modeled path
(no locked bucket) runs the same write/read/verify/lock contract against the in-memory model and
labels itself, and the real-bucket path is exercised with an in-memory store injected as the locked
backend (the same Storage protocol B2 implements). Network-free.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from rooted_api import sbr
from rooted_api.checkpoint import seal_checkpoint
from rooted_api.main import app
from rooted_provenance.merkle import TransparencyLog
from rooted_provenance.models import MerkleCheckpoint
from rooted_provenance.resolver import InMemoryIndex, Resolver
from rooted_provenance.watermark import FakeWatermarker
from rooted_storage.storage import InMemoryStorage, ObjectLockedError, checkpoint_key


def _log_with_leaves(n: int) -> TransparencyLog:
    log = TransparencyLog()
    for i in range(n):
        log.append(f"urn:c2pa:demo-{i}", f"{i:064x}")
    return log


@pytest.fixture
def reset() -> Iterator[None]:
    yield
    sbr.set_resolver(None)
    sbr.set_log(None)
    sbr.set_storage(None)
    sbr.set_locked_storage(None)


def test_checkpoint_object_modeled_when_no_locked_bucket(reset: None) -> None:
    sbr.set_resolver(Resolver(InMemoryIndex(), FakeWatermarker()))
    sbr.set_log(_log_with_leaves(3))
    sbr.set_storage(None)
    sbr.set_locked_storage(None)  # no B2_BUCKET_LOCKED -> the surface models Object Lock, labeled
    with TestClient(app) as c:
        d = c.get("/transparency/checkpoint/object").json()
    assert d["backend"] == "in-memory"
    assert d["modeled"] is True
    assert d["immutable"] is True
    assert d["retentionMode"] == "compliance"
    assert d["retainUntil"] is not None
    assert d["signatureVerified"] is True
    assert d["checkpoint"]["treeSize"] == 3
    assert d["keySource"] in {"configured", "ephemeral"}


def test_checkpoint_object_real_locked_bucket(reset: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("B2_BUCKET_LOCKED", "rooted-checkpoints")
    sbr.set_resolver(Resolver(InMemoryIndex(), FakeWatermarker()))
    sbr.set_log(_log_with_leaves(2))
    sbr.set_storage(None)
    # an in-memory store stands in for the fileLock-enabled B2 bucket (the same Storage protocol)
    locked = InMemoryStorage()
    sbr.set_locked_storage(locked)
    with TestClient(app) as c:
        d = c.get("/transparency/checkpoint/object").json()
        # idempotent: a second read does not re-write the compliance-retained object
        assert c.get("/transparency/checkpoint/object").status_code == 200
    assert d["backend"] == "backblaze-b2"
    assert d["modeled"] is False
    assert d["bucket"] == "rooted-checkpoints"
    assert d["immutable"] is True
    assert d["signatureVerified"] is True
    # the checkpoint was actually sealed under Object Lock and cannot be deleted or overwritten
    key = checkpoint_key(d["checkpoint"]["epoch"])
    assert locked.exists(key)
    with pytest.raises(ObjectLockedError):
        locked.delete(key)
    with pytest.raises(ObjectLockedError):
        locked.put(key, b"tampered", object_lock=True, retain_days=1)


def test_seal_checkpoint_is_idempotent_and_immutable() -> None:
    cp = MerkleCheckpoint(
        epoch=5, tree_size=5, root_hash="ab" * 32, signed_at="t", signature_b64="x"
    )
    s = InMemoryStorage()
    k1 = seal_checkpoint(s, cp, 30)
    # already exists, so it is left as-is (no overwrite, hence no ObjectLockedError)
    k2 = seal_checkpoint(s, cp, 30)
    assert k1 == k2 == checkpoint_key(5)
    with pytest.raises(ObjectLockedError):
        s.delete(k1)
