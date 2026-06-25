"""Storage contract via the in-memory fake, plus the content-addressable key helpers."""

from __future__ import annotations

import pytest

from rooted_storage.storage import (
    InMemoryStorage,
    ObjectLockedError,
    Storage,
    asset_key,
    checkpoint_key,
    manifest_key,
)


def test_fake_satisfies_protocol() -> None:
    assert isinstance(InMemoryStorage(), Storage)


def test_put_get_exists_delete_roundtrip() -> None:
    s = InMemoryStorage()
    key = s.put("k1", b"hello")
    assert key == "k1"
    assert s.exists("k1")
    assert s.get("k1") == b"hello"
    s.delete("k1")
    assert not s.exists("k1")


def test_object_lock_blocks_delete_and_overwrite() -> None:
    s = InMemoryStorage()
    s.put(
        "merkle/checkpoints/epoch_00000001.cbor", b"checkpoint", object_lock=True, retain_days=365
    )
    with pytest.raises(ObjectLockedError):
        s.delete("merkle/checkpoints/epoch_00000001.cbor")
    with pytest.raises(ObjectLockedError):
        s.put("merkle/checkpoints/epoch_00000001.cbor", b"tampered", object_lock=True)


def test_object_lock_requires_retain_days() -> None:
    # Object Lock without a retention period is a silent no-op risk: the fake and the real B2
    # backend must both refuse it so a checkpoint is never written deletable.
    with pytest.raises(ValueError):
        InMemoryStorage().put("k", b"x", object_lock=True)


def test_missing_key_raises() -> None:
    with pytest.raises(KeyError):
        InMemoryStorage().get("nope")


def test_key_helpers_are_content_addressable() -> None:
    sha = "ab" + "c" * 62
    assert asset_key(sha) == f"assets/ab/cc/{sha}"
    assert manifest_key("urn:c2pa:abc-123") == "manifests/urn_c2pa_abc-123.json"
    assert checkpoint_key(1) == "merkle/checkpoints/epoch_00000001.cbor"
