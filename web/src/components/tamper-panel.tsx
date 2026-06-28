"use client";

import { useCallback, useEffect, useState } from "react";

// Live tamper-evidence. Fetches the primary demo manifest + its COSE signature (signed with the
// server's checkpoint key), lets you edit a SIGNED field, and re-verifies against /api/verify. Any
// change flips the signature to TAMPERED, on camera. The signature is checked server-side against
// the published checkpoint public key, so this is a real cryptographic check, not a UI trick.

type DemoManifest = {
  manifestId: string;
  assetSha256: string;
  createdAt: string;
  systemProvenance: { model?: string; [k: string]: unknown };
  [k: string]: unknown;
};
type Signed = { manifest: DemoManifest; signatureB64: string; publicKeyHex: string };
type Status = "valid" | "tampered" | "checking" | null;

export function TamperPanel() {
  const [signed, setSigned] = useState<Signed | null>(null);
  const [model, setModel] = useState("");
  const [assetSha, setAssetSha] = useState("");
  const [status, setStatus] = useState<Status>(null);
  const [error, setError] = useState(false);

  const verify = useCallback(async (s: Signed, m: string, sha: string) => {
    setStatus("checking");
    const manifest: DemoManifest = {
      ...s.manifest,
      assetSha256: sha,
      systemProvenance: { ...s.manifest.systemProvenance, model: m },
    };
    try {
      const r = await fetch("/api/verify", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ manifest, signatureB64: s.signatureB64 }),
      });
      const d = await r.json();
      setStatus(d.signatureValid ? "valid" : "tampered");
    } catch {
      setError(true);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch("/api/demo/signed-manifest");
        if (!r.ok) throw new Error(String(r.status));
        const d: Signed = await r.json();
        if (cancelled) return;
        setSigned(d);
        setModel(d.manifest.systemProvenance?.model ?? "");
        setAssetSha(d.manifest.assetSha256 ?? "");
        await verify(d, d.manifest.systemProvenance?.model ?? "", d.manifest.assetSha256 ?? "");
      } catch {
        if (!cancelled) setError(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [verify]);

  function reset() {
    if (!signed) return;
    setModel(signed.manifest.systemProvenance?.model ?? "");
    setAssetSha(signed.manifest.assetSha256 ?? "");
    void verify(signed, signed.manifest.systemProvenance?.model ?? "", signed.manifest.assetSha256 ?? "");
  }

  return (
    <section className="rounded-xl border border-white/15 bg-white/[0.03] p-5 backdrop-blur-md">
      <h2 className="mb-1 text-xs uppercase tracking-widest text-white/50">Tamper-evidence</h2>
      <p className="mb-3 text-[11px] text-white/55">
        Edit a signed field and re-verify. The COSE signature is checked server-side against the
        published checkpoint key, so any change breaks it.
      </p>

      {error && <p className="font-mono text-sm text-amber-400">Backend unreachable.</p>}
      {!error && !signed && <p className="font-mono text-sm text-white/50">Loading signed manifest…</p>}

      {signed && (
        <div className="space-y-3">
          <label className="block font-mono text-xs text-white/60">
            system provenance · model
            <input
              value={model}
              onChange={(e) => {
                setModel(e.target.value);
                setStatus(null);
              }}
              className="mt-1 w-full rounded border border-white/15 bg-black/40 px-2 py-1 text-white/90"
            />
          </label>
          <label className="block font-mono text-xs text-white/60">
            asset SHA-256
            <input
              value={assetSha}
              onChange={(e) => {
                setAssetSha(e.target.value);
                setStatus(null);
              }}
              className="mt-1 w-full rounded border border-white/15 bg-black/40 px-2 py-1 text-white/90"
            />
          </label>

          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => signed && verify(signed, model, assetSha)}
              className="rounded border border-white/20 px-3 py-1 font-mono text-xs text-white/80 hover:border-white/40"
            >
              Re-verify
            </button>
            <button
              type="button"
              onClick={reset}
              className="font-mono text-xs text-white/55 underline-offset-4 hover:underline"
            >
              reset
            </button>
            {status === "checking" && <span className="font-mono text-xs text-sky-300">checking…</span>}
            {status === "valid" && (
              <span className="font-mono text-xs text-emerald-300">✓ SIGNATURE VALID</span>
            )}
            {status === "tampered" && (
              <span className="font-mono text-xs text-rose-400">
                ✗ TAMPERED: the signature does not cover this manifest
              </span>
            )}
            {status === null && <span className="font-mono text-xs text-white/55">edited, re-verify</span>}
          </div>
        </div>
      )}
    </section>
  );
}
