"""Storage for Rooted: a Protocol, an in-memory fake, and the Backblaze B2 backend.

Keys are content-addressable, so an asset's SHA-256 (and the manifest id) is the natural address.
Object Lock (compliance retention) makes the Merkle checkpoints tamper-evident: once written they
cannot be deleted until retention expires, which is exactly what an audit trail needs.
"""

from __future__ import annotations

import io
import time
from typing import Protocol, runtime_checkable

ASSET_PREFIX = "assets"
MANIFEST_PREFIX = "manifests"
SIGNATURE_PREFIX = "signatures"
CHECKPOINT_PREFIX = "merkle/checkpoints"


def asset_key(sha256: str) -> str:
    """Content-addressable asset key, sharded by the first hash bytes to spread the keyspace."""
    return f"{ASSET_PREFIX}/{sha256[:2]}/{sha256[2:4]}/{sha256}"


def manifest_key(manifest_id: str) -> str:
    return f"{MANIFEST_PREFIX}/{manifest_id.replace(':', '_')}.json"


def signature_key(manifest_id: str) -> str:
    """B2 key for the manifest's COSE signature, so the signature is durable, not caller-held."""
    return f"{SIGNATURE_PREFIX}/{manifest_id.replace(':', '_')}.cose"


def checkpoint_key(epoch: int) -> str:
    return f"{CHECKPOINT_PREFIX}/epoch_{epoch:08d}.cbor"


class ObjectLockedError(Exception):
    """Raised when a delete is attempted on an Object-Lock-retained object."""


@runtime_checkable
class Storage(Protocol):
    def put(
        self, key: str, data: bytes, *, object_lock: bool = False, retain_days: int | None = None
    ) -> str: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...


class InMemoryStorage:
    """Credential-free fake. Models Object Lock by refusing to delete or overwrite locked keys."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}
        self._locked: set[str] = set()

    def put(
        self, key: str, data: bytes, *, object_lock: bool = False, retain_days: int | None = None
    ) -> str:
        if key in self._locked:
            raise ObjectLockedError(f"{key} is under Object Lock and cannot be overwritten")
        if object_lock and not retain_days:
            raise ValueError("object_lock requires retain_days")
        self._data[key] = bytes(data)
        if object_lock:
            self._locked.add(key)
        return key

    def get(self, key: str) -> bytes:
        if key not in self._data:
            raise KeyError(key)
        return self._data[key]

    def exists(self, key: str) -> bool:
        return key in self._data

    def delete(self, key: str) -> None:
        if key in self._locked:
            raise ObjectLockedError(f"{key} is under Object Lock and cannot be deleted")
        self._data.pop(key, None)


class B2Storage:
    """Real Backblaze B2 backend (b2sdk). Needs credentials, so it is verified with a live smoke
    test once keys land, not in unit tests. The InMemoryStorage fake covers the contract in CI.
    """

    def __init__(self, key_id: str, app_key: str, bucket_name: str) -> None:
        from b2sdk.v2 import B2Api, InMemoryAccountInfo

        api = B2Api(InMemoryAccountInfo())
        api.authorize_account("production", key_id, app_key)
        self._bucket = api.get_bucket_by_name(bucket_name)

    def put(
        self, key: str, data: bytes, *, object_lock: bool = False, retain_days: int | None = None
    ) -> str:
        kwargs: dict = {}
        if object_lock:
            if not retain_days:
                raise ValueError("object_lock requires retain_days")
            from b2sdk.v2 import FileRetentionSetting, RetentionMode

            until_ms = int((time.time() + retain_days * 86400) * 1000)
            kwargs["file_retention"] = FileRetentionSetting(RetentionMode.COMPLIANCE, until_ms)
        self._bucket.upload_bytes(bytes(data), key, **kwargs)
        return key

    def get(self, key: str) -> bytes:
        buf = io.BytesIO()
        self._bucket.download_file_by_name(key).save(buf)
        return buf.getvalue()

    def exists(self, key: str) -> bool:
        from b2sdk.v2.exception import FileNotPresent

        try:
            self._bucket.get_file_info_by_name(key)
            return True
        except FileNotPresent:
            # Only "absent" returns False. Connection/auth errors propagate so an outage is never
            # mistaken for a missing object (which would corrupt recovery and overwrite logic).
            return False

    def delete(self, key: str) -> None:
        info = self._bucket.get_file_info_by_name(key)
        self._bucket.delete_file_version(info.id_, key)
