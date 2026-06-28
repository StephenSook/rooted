"""Storage contract via the in-memory fake, plus the content-addressable key helpers."""

from __future__ import annotations

import pytest

from rooted_storage.storage import (
    InMemoryStorage,
    ObjectLockedError,
    RetentionInfo,
    Storage,
    asset_key,
    checkpoint_key,
    manifest_key,
    signature_key,
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
    key = checkpoint_key(1)
    s.put(key, b"checkpoint", object_lock=True, retain_days=365)
    with pytest.raises(ObjectLockedError):
        s.delete(key)
    with pytest.raises(ObjectLockedError):
        s.put(key, b"tampered", object_lock=True)


def test_parse_retention_matches_real_b2sdk_shape() -> None:
    # The read-back parser must match the ACTUAL b2sdk FileRetentionSetting attribute names
    # (.retain_until epoch millis, .mode.value "compliance"), so the real B2 path reports a sealed
    # object as immutable. The in-memory contract test below cannot catch a wrong attribute name.
    from b2sdk.v2 import NO_RETENTION_FILE_SETTING, FileRetentionSetting, RetentionMode

    from rooted_storage.storage import _parse_retention

    locked = _parse_retention(FileRetentionSetting(RetentionMode.COMPLIANCE, 1893456000000))
    assert locked.mode == "compliance"
    assert locked.retain_until_ms == 1893456000000
    none = _parse_retention(NO_RETENTION_FILE_SETTING)
    assert none.mode == "none"
    assert none.retain_until_ms is None
    assert _parse_retention(None).mode == "none"


def test_retention_reads_back_compliance_until() -> None:
    # The lock contract is observable: a locked object reports compliance mode and a future
    # retain-until, an unlocked object reports none, and an absent object reports None.
    s = InMemoryStorage()
    s.put(checkpoint_key(2), b"cp", object_lock=True, retain_days=1)
    r = s.retention(checkpoint_key(2))
    assert isinstance(r, RetentionInfo)
    assert r.mode == "compliance"
    assert r.retain_until_ms is not None and r.retain_until_ms > 0
    s.put("plain", b"x")
    plain = s.retention("plain")
    assert plain is not None and plain.mode == "none"
    assert s.retention("absent") is None


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
    assert signature_key("urn:c2pa:abc-123") == "signatures/urn_c2pa_abc-123.cose"
    assert checkpoint_key(1) == "merkle/checkpoints/epoch_00000001.json"
