"""End-to-end ingest -> recover loop with fakes: the demo's core path, credential-free."""

from __future__ import annotations

from rooted_provenance.merkle import TransparencyLog
from rooted_provenance.resolver import InMemoryIndex, Resolver
from rooted_provenance.signing import generate_keypair, verify_manifest
from rooted_provenance.watermark import FakeWatermarker
from rooted_storage.storage import InMemoryStorage, asset_key
from rooted_worker.generator import FakeGenerator
from rooted_worker.pipeline import IngestPipeline


def test_ingest_then_recover_end_to_end() -> None:
    storage = InMemoryStorage()
    watermarker = FakeWatermarker(decoded_id="RT01")
    resolver = Resolver(InMemoryIndex(), watermarker)
    log = TransparencyLog()
    priv, pub = generate_keypair()
    pipeline = IngestPipeline(FakeGenerator(), storage, watermarker, resolver, log, priv)

    result = pipeline.run("a white ceramic mug under studio light", watermark_id="RT01")

    # the manifest is signed, stored, and logged
    assert verify_manifest(result.cose_signature, result.manifest, pub) is True
    assert result.merkle_index == 1
    assert storage.exists(asset_key(result.manifest.asset_sha256))
    assert result.manifest.personal_provenance["prompt"]  # captured, redacted later on read

    # the asset is now RECOVERABLE: the same generated (watermarked) image resolves to the manifest
    same_image = FakeGenerator().generate("a white ceramic mug under studio light").image
    recovered = resolver.resolve_by_content(same_image)
    assert recovered.matches
    assert recovered.matches[0].manifest_id == result.manifest.manifest_id


def test_two_ingests_grow_the_log() -> None:
    resolver = Resolver(InMemoryIndex(), FakeWatermarker())
    log = TransparencyLog()
    priv, _ = generate_keypair()
    pipeline = IngestPipeline(
        FakeGenerator(), InMemoryStorage(), FakeWatermarker(), resolver, log, priv
    )
    a = pipeline.run("prompt a", watermark_id="RT0A")
    b = pipeline.run("prompt b", watermark_id="RT0B")
    assert a.merkle_index == 1
    assert b.merkle_index == 2
    assert a.manifest.manifest_id != b.manifest.manifest_id
