"""One-shot: create a least-privilege Backblaze B2 application key for the running API, restricted
to the dev bucket, in the style of the other configure_b2_* scripts. Reads the (parent) B2
credentials from .env; creating a key requires the parent key to have writeKeys.

The capability list is evidence-based: each capability below maps to a b2sdk call the runtime code
actually makes (file references in CAPABILITIES). Everything else is excluded, with the reason.

Restriction is BY BUCKET ONLY, never by namePrefix: the S3-compatible presigned PUT (rooted_api.byo)
is signed with this same key and needs writeFiles on byo/, while the demo seed and the live
generation write under assets/, manifests/, and signatures/; a key-level namePrefix would break one
prefix or the other, so the byo/ key shape is enforced server-side instead (byo.py's anchored
regex).

Safety: dry-run by default (prints the exact key request, the evidence per capability, and the
rotation steps; creates nothing); pass --apply to create the key. The secret is printed ONCE and
never written to a file.

Run: uv run python scripts/create_b2_scoped_key.py           # dry run (default)
     uv run python scripts/create_b2_scoped_key.py --apply   # actually create the key
"""

from __future__ import annotations

import sys
from pathlib import Path

import b2sdk.v2 as v2

_ENV = Path(__file__).resolve().parents[1] / ".env"

KEY_NAME = "rooted-api-scoped"

# (capability, evidence: the runtime code path that needs it). File references are the proof; if a
# path is removed, drop its capability here too (wired-or-cut applies to permissions as well).
CAPABILITIES: list[tuple[str, str]] = [
    (
        "listBuckets",
        "B2Storage.__init__ -> api.get_bucket_by_name -> b2_list_buckets "
        "(packages/storage/rooted_storage/storage.py); also bucket.get_fresh_state() behind the "
        "GET /demo/storage b2Depth live read",
    ),
    (
        "readFiles",
        "storage.get/exists/size -> download_file_by_name + get_file_info_by_name "
        "(storage.py); callers: the B2 event-webhook fetch (rooted_api/b2_events.py), BYO "
        "register (rooted_api/byo.py), the /status probe, /demo/storage presence checks, and "
        "/demo/rebuild asset fetches",
    ),
    (
        "writeFiles",
        "storage.put -> bucket.upload_bytes (storage.py); callers: the demo seed "
        "(rooted_api/demo.py) and the live generation store (rooted_api/generate.py). ALSO the "
        "S3 presigned PUT (rooted_api/byo.py) is signed with this key and B2 enforces "
        "writeFiles at PUT time",
    ),
    (
        "listFiles",
        "storage.list_keys -> bucket.ls (storage.py); required by /demo/rebuild "
        "(rooted_api/demo.py), which walks manifests/ to rebuild the recovery index from B2 "
        "alone (no fallback path)",
    ),
    (
        "readBucketEncryption",
        "the GET /demo/storage b2Depth section reads the bucket's default encryption live as "
        "evidence SSE-B2 is active; without this capability it honestly reports unknown "
        "(read-only, evidence-only; drop it if that panel is removed)",
    ),
    (
        "readBucketLifecycleRules",
        "the same b2Depth section reads the bucket's lifecycle rules live; B2 lists this as a "
        "distinct read capability (seen in the live authorize response), so it is granted to keep "
        "that read authorized under a restricted key (read-only, evidence-only; drop it with the "
        "panel)",
    ),
]

# (capability, why it is NOT granted).
EXCLUDED: list[tuple[str, str]] = [
    (
        "deleteFiles",
        "no runtime code path deletes from the dev bucket (B2Storage.delete has no runtime "
        "caller); the registry is append-only, and lifecycle rules do the byo/ + ingest/ cleanup",
    ),
    (
        "writeFileRetentions / readFileRetentions",
        "Object Lock is used only on the LOCKED checkpoint bucket (B2_BUCKET_LOCKED), which this "
        "key does not cover",
    ),
    (
        "writeBuckets / readBucketRetentions / readBucketNotifications / writeBucketNotifications",
        "bucket configuration is done by the one-shot scripts/ under the operator's parent key, "
        "never by the API at runtime",
    ),
    (
        "shareFiles / writeKeys / deleteKeys / listKeys / readBucketReplications / ...",
        "unused by any code path",
    ),
]


def _env() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in _ENV.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.split(" #", 1)[0].strip().strip('"').strip("'")
    return out


def plan(bucket_name: str) -> dict[str, object]:
    """Pure planning: the exact key request that --apply would send (name, capabilities, bucket
    restriction, no namePrefix), for printing and for tests."""
    return {
        "keyName": KEY_NAME,
        "capabilities": [cap for cap, _ in CAPABILITIES],
        "bucketName": bucket_name,
        "namePrefix": None,
    }


def _print_plan(bucket_name: str) -> None:
    request = plan(bucket_name)
    print(f"key to create: {request['keyName']}")
    print(f"restricted to bucket: {bucket_name} (no namePrefix restriction, see the note below)")
    print("capabilities (each evidence-based):")
    for cap, evidence in CAPABILITIES:
        print(f"  + {cap}: {evidence}")
    print("excluded, with reasons:")
    for cap, reason in EXCLUDED:
        print(f"  - {cap}: {reason}")
    print()
    print(
        "NOTE (S3 presign): the S3-compatible presigned PUT uses this same key, and S3 needs "
        "writeFiles on the byo/ prefix. A key-level namePrefix restriction would break the OTHER "
        "prefixes (assets/, manifests/, signatures/, ingest/), so the restriction is by bucket "
        "only; the byo/ key shape stays enforced server-side (rooted_api/byo.py)."
    )
    print(
        "WARNING (locked bucket): the API also uses B2_BUCKET_LOCKED (WORM checkpoints, "
        "rooted_api/checkpoint.py) through the SAME B2_KEY_ID/B2_APP_KEY. Rotating the env to "
        "this dev-only key degrades the checkpoint surfaces to their labeled in-memory fallback. "
        "If that is not acceptable, create the key with BOTH bucket ids instead "
        "(api.create_key(bucket_ids=[dev, locked]) plus writeFileRetentions + "
        "readFileRetentions), or keep a second key for the locked bucket."
    )


def _print_rotation_steps() -> None:
    print("rotation steps (after --apply):")
    print("  1. copy the printed applicationKeyId + applicationKey to your secret store NOW;")
    print("     they are shown once and never written to a file by this script")
    print("  2. on the rooted-api Render service, set B2_KEY_ID / B2_APP_KEY to the new pair,")
    print("     then redeploy")
    print("  3. verify live: GET /status (storage.demoAssetPresent true), GET /demo/storage")
    print("     (present all true; b2Depth encryption/lifecycle read), the BYO upload loop")
    print("     (POST /demo/byo/upload-url -> PUT -> register), and GET /demo/rebuild")
    print("  4. only after verification, revoke the old key in the Backblaze console")
    print("     (or `b2 key delete <old-key-id>`)")


def main() -> None:
    apply = "--apply" in sys.argv[1:]
    env = _env()
    bucket_name = env["B2_BUCKET_DEV"]
    locked_name = env.get("B2_BUCKET_LOCKED", "rooted-locked")
    if bucket_name == locked_name or bucket_name == "rooted-locked":
        raise SystemExit(
            f"refusing: B2_BUCKET_DEV is {bucket_name!r}, which is (or is named like) the "
            "Object-Lock bucket; this key is for the dev bucket only"
        )

    api = v2.B2Api(v2.InMemoryAccountInfo())
    api.authorize_account("production", env["B2_KEY_ID"], env["B2_APP_KEY"])
    allowed = api.account_info.get_allowed()
    print(f"authorized with the parent key; its capabilities: {allowed.get('capabilities')}")
    print()
    _print_plan(bucket_name)
    print()
    _print_rotation_steps()
    if not apply:
        print()
        print("dry run (default): no key was created. Re-run with --apply to create it.")
        return

    bucket = api.get_bucket_by_name(bucket_name)
    created = api.create_key(
        capabilities=[cap for cap, _ in CAPABILITIES],
        key_name=KEY_NAME,
        bucket_ids=[bucket.id_],
    )
    print()
    print("KEY CREATED. The secret below is shown ONCE; B2 will never show it again.")
    print("Store it in your secret manager immediately. Do NOT write it to a file or commit it.")
    print(f"  applicationKeyId (B2_KEY_ID):  {created.id_}")
    print(f"  applicationKey   (B2_APP_KEY): {created.application_key}")
    print(f"  restricted to bucket id(s):    {created.bucket_ids}")
    print(f"  capabilities:                  {created.capabilities}")


if __name__ == "__main__":
    main()
