"""One-off builder for the Genblaze AssemblyAI STT transcript fixtures (run once, commit outputs).

The inverse of make_genblaze_b2_sample.py. Instead of generating an image, this CONSUMES a real
AI-generated speech clip (Rooted's own /demo/speech, an ElevenLabs clip) and runs Genblaze's NEW
AssemblyAI speech-to-text connector to produce a hash-verified TEXT transcript with word-level
timings. The run is written to Backblaze B2 via Genblaze's OWN ObjectStorageSink (the same dual-axis
as the image fixture: the SDK persists its provenance to Backblaze), and the native hash-verified
manifest + the plain transcript are captured as committed fixtures. The /demo/transcript endpoint
reads these to show the Genblaze transcript-integrity manifest reconciled with Rooted's signed C2PA
manifest (Genblaze proves the transcript's integrity; Rooted adds the COSE signature, the C2PA
claim, and the transparency proof).

This couples three rubric axes on one artifact: the audio is real AI-generated media, the transcript
is produced by Genblaze's newest connector, and both the audio source and the transcript run live in
Backblaze B2.

Run (needs ASSEMBLYAI_API_KEY + B2 creds in .env; the speech route must be deployed):
    uv run --with genblaze-assemblyai python api/rooted_api/make_genblaze_transcript_sample.py
"""

from __future__ import annotations

from pathlib import Path

from genblaze_core import Modality, Pipeline
from genblaze_core.storage.sink import ObjectStorageSink
from genblaze_s3 import S3StorageBackend

# Rooted's own real AI speech asset, served over public https so AssemblyAI can fetch it (the
# connector's SSRF validator accepts https:// only for a remote URL). Swap to a local file:// path
# only if the connector uploads local files in your version.
AUDIO_URL = "https://rooted-api-ubvc.onrender.com/demo/speech"

_ASSETS = Path(__file__).parent / "assets"


def _env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (Path(__file__).resolve().parents[2] / ".env").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        # Strip only a " #" inline comment so a value containing '#' (e.g. a key) is preserved.
        env[k.strip()] = v.split(" #", 1)[0].strip().strip('"').strip("'")
    return env


def main() -> None:
    from genblaze_assemblyai import AssemblyAIProvider

    env = _env()
    run, manifest = (
        Pipeline("rooted-genblaze-transcript")
        .step(
            AssemblyAIProvider(api_key=env["ASSEMBLYAI_API_KEY"]),
            model="universal-3-pro",  # speech_models: universal-3-pro | universal-2
            prompt=AUDIO_URL,  # resolved as the audio URL to transcribe
            modality=Modality.TEXT,
        )
        .run(timeout=300, max_retries=1, raise_on_failure=True)
    )
    assert manifest.verify_hash(), "Genblaze transcript manifest failed canonical_hash verification"

    step = run.steps[0]
    asset = step.assets[0]
    text = asset.metadata["text"]
    word_timings = asset.audio.word_timings if asset.audio else []
    assert text, "transcript text is empty (is the audio actually speech?)"
    assert word_timings, "word_timings is empty"

    # Write the run to B2 via Genblaze's OWN ObjectStorageSink (the dual-axis: Genblaze persists its
    # transcript asset + manifest to Backblaze through its native S3 backend).
    backend = S3StorageBackend.for_backblaze(
        bucket=env["B2_BUCKET_DEV"],
        region="us-east-005",
        key_id=env["B2_KEY_ID"],
        app_key=env["B2_APP_KEY"],
    )
    ObjectStorageSink(backend).write_run(run, manifest)

    _ASSETS.mkdir(exist_ok=True)
    (_ASSETS / "genblaze-transcript-manifest.json").write_text(manifest.model_dump_json(indent=2))
    (_ASSETS / "genblaze-transcript.txt").write_text(text)

    first6 = [(w.word, round(w.start, 2), round(w.end, 2)) for w in word_timings[:6]]
    print("OK")
    print("transcript:", text)
    print("word count:", len(word_timings))
    print("first word timings:", first6)
    print("manifest output asset sha256:", asset.sha256)
    print("canonical_hash:", manifest.canonical_hash)
    print("run_id:", run.run_id)
    print("verify_hash:", manifest.verify_hash())


if __name__ == "__main__":
    main()
