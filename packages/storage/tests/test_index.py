"""PostgresIndex: the recovery Index protocol on real Postgres (bit(256) PDQ, native bit_count
Hamming).

Runs against ROOTED_TEST_DATABASE_URL if set, otherwise against an ephemeral pgserver-bundled
Postgres (no Docker, no credentials), so the database path is exercised for real, never mocked.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

import numpy as np
import pytest
from PIL import Image

from rooted_provenance.models import (
    ALG_TRUSTMARK_P,
    PDQ_HAMMING_THRESHOLD,
    Manifest,
    SoftBinding,
)
from rooted_provenance.resolver import Resolver
from rooted_provenance.watermark import FakeWatermarker
from rooted_storage.index import PostgresIndex

try:
    import pgserver
except Exception:  # pragma: no cover - platform without a pgserver wheel
    pgserver = None


def _img(seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, (256, 256, 3), dtype=np.uint8)
    return Image.fromarray(arr).resize((64, 64)).resize((256, 256))


def _manifest(n: int) -> Manifest:
    return Manifest(
        manifest_id=f"urn:c2pa:pg-{n}",
        asset_sha256=f"{n:064d}",
        created_at="2026-06-25T00:00:00Z",
        system_provenance={"model": "seedream"},
        personal_provenance={"prompt": "secret"},
        soft_bindings=[SoftBinding(alg=ALG_TRUSTMARK_P, value=f"RT{n}")],
    )


@pytest.fixture(scope="module")
def _conninfo() -> Iterator[str]:
    url = os.environ.get("ROOTED_TEST_DATABASE_URL")
    if url:
        yield url
        return
    if pgserver is None:
        pytest.skip("set ROOTED_TEST_DATABASE_URL or install pgserver to run PostgresIndex tests")
    server = pgserver.get_server(tempfile.mkdtemp())
    try:
        yield server.get_uri()
    finally:
        server.cleanup()


@pytest.fixture
def index(_conninfo: str) -> Iterator[PostgresIndex]:
    idx = PostgresIndex(_conninfo)
    idx.create_schema()
    idx.clear()
    yield idx
    idx.close()


def test_manifest_roundtrip(index: PostgresIndex) -> None:
    m = _manifest(1)
    index.put_manifest(m)
    assert index.get_manifest(m.manifest_id) == m
    assert index.get_manifest("urn:c2pa:absent") is None


def test_manifest_upsert_does_not_duplicate(index: PostgresIndex) -> None:
    m = _manifest(1)
    index.put_manifest(m)
    index.put_manifest(m.model_copy(update={"system_provenance": {"model": "flux"}}))
    got = index.get_manifest(m.manifest_id)
    assert got is not None and got.system_provenance == {"model": "flux"}


def test_watermark_binding(index: PostgresIndex) -> None:
    index.put_watermark_binding("RT7", "urn:c2pa:pg-7")
    assert index.manifest_for_watermark("RT7") == "urn:c2pa:pg-7"
    assert index.manifest_for_watermark("RTX") is None


def test_nearest_fingerprint_threshold(index: PostgresIndex) -> None:
    base = "0" * 256
    near = "0" * 246 + "1" * 10  # hamming distance 10 from base
    far = "0" * 216 + "1" * 40  # hamming distance 40 from base
    index.put_fingerprint(near, "near")
    index.put_fingerprint(far, "far")
    assert index.nearest_fingerprint(base, PDQ_HAMMING_THRESHOLD) == ("near", 10)
    assert index.nearest_fingerprint(base, 5) is None  # nearest (10) exceeds the threshold
    assert index.nearest_fingerprint(base, 10) == ("near", 10)  # boundary is inclusive


def test_fingerprints_for(index: PostgresIndex) -> None:
    index.put_fingerprint("0" * 256, "m")
    index.put_fingerprint("1" * 256, "m")
    assert set(index.fingerprints_for("m")) == {"0" * 256, "1" * 256}
    assert index.fingerprints_for("other") == []


def test_full_recovery_round_trip_via_resolver(index: PostgresIndex) -> None:
    resolver = Resolver(index, FakeWatermarker(decoded_id=None))  # stripped watermark -> PDQ path
    m, img = _manifest(2), _img(2)
    resolver.register(m, img, watermark_id="RT2")
    result = resolver.resolve_by_content(img)
    assert result.matches[0].manifest_id == m.manifest_id
    assert result.matches[0].similarity_score is not None
    assert resolver.resolve_by_content(_img(987)).matches == []  # unrelated asset


def test_resolve_by_binding_via_resolver(index: PostgresIndex) -> None:
    resolver = Resolver(index, FakeWatermarker())
    m, img = _manifest(3), _img(3)
    resolver.register(m, img, watermark_id="RT3")
    out = resolver.resolve_by_binding(ALG_TRUSTMARK_P, "RT3")
    assert out.matches[0].manifest_id == m.manifest_id


def test_register_populates_manifest_binding_and_fingerprint(index: PostgresIndex) -> None:
    m = _manifest(4)
    index.register(m, "RT4", "0" * 256)
    assert index.get_manifest(m.manifest_id) == m
    assert index.manifest_for_watermark("RT4") == m.manifest_id
    assert index.fingerprints_for(m.manifest_id) == ["0" * 256]


def test_register_is_atomic_and_rolls_back_on_failure(index: PostgresIndex) -> None:
    m = _manifest(5)
    # A bad PDQ (not a 256-bit string) fails the bit(256) cast after the manifest insert; the whole
    # register must roll back so no orphaned, unrecoverable manifest is left behind.
    with pytest.raises(Exception):  # noqa: B017 - psycopg raises a DataError subclass here
        index.register(m, "RT5", "not-256-bits")
    assert index.get_manifest(m.manifest_id) is None
    assert index.manifest_for_watermark("RT5") is None
    assert index.fingerprints_for(m.manifest_id) == []
