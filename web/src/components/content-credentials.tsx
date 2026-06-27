"use client";

import Image from "next/image";
import { useEffect, useState } from "react";

// Reads and displays standard C2PA Content Credentials embedded in an image, using
// @contentauth/c2pa-web (the maintained CAI data SDK; the c2pa-wc display component is deprecated, so
// the UI is hand-built per the C2PA 2.0 UX recommendations). The SDK is loaded with a dynamic
// import() inside the effect so its worker/WASM only run in the browser, never during SSR. The image
// is signed with a C2PA test certificate, so the signature is cryptographically Valid but the issuer
// is not a Conformance-Program CA (not the green "Trusted" state); shown honestly.

type Credentials = {
  title?: string;
  claimGenerator?: string;
  format?: string;
  issuer?: string;
  time?: string;
  assertions: string[];
  validationState?: string;
};

const SRC = "/credentialed-sample.jpg";

export function ContentCredentials() {
  const [cc, setCc] = useState<Credentials | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { createC2pa } = await import("@contentauth/c2pa-web/inline");
        const c2pa = await createC2pa();
        const blob = await (await fetch(SRC)).blob();
        const reader = await c2pa.reader.fromBlob("image/jpeg", blob);
        if (!reader) {
          if (!cancelled) {
            setError("No Content Credentials found in this asset.");
            setLoading(false);
          }
          return;
        }
        try {
          const store = await reader.json();
          const active = store?.manifests?.[store?.active_manifest] ?? {};
          const sig = active?.signature_info ?? {};
          if (!cancelled) {
            setCc({
              title: active?.title,
              claimGenerator: active?.claim_generator,
              format: active?.format,
              issuer: sig?.issuer,
              time: sig?.time,
              assertions: (active?.assertions ?? [])
                .map((a: { label?: string }) => a?.label)
                .filter(Boolean),
              validationState: store?.validation_state,
            });
            setLoading(false);
          }
        } finally {
          await reader.free(); // WASM heap is not GC'd; always free.
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Failed to read Content Credentials.");
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section className="rounded-xl border border-white/15 bg-white/[0.03] p-5 backdrop-blur-md">
      <h2 className="mb-3 text-xs uppercase tracking-widest text-white/50">
        C2PA Content Credentials
      </h2>

      {loading && <p className="font-mono text-sm text-white/50">Reading credentials…</p>}
      {error && <p className="font-mono text-sm text-amber-400">{error}</p>}

      {cc && (
        <div className="grid gap-4 sm:grid-cols-[8rem_1fr]">
          <Image
            src={SRC}
            alt="C2PA-credentialed asset"
            width={128}
            height={128}
            className="h-32 w-32 rounded-lg border border-white/10 object-cover"
          />
          <div>
            <div className="mb-2 flex items-center gap-2">
              <span
                className="inline-flex h-6 w-6 items-center justify-center rounded-full rounded-br-none border border-emerald-300/70 font-mono text-[10px] font-semibold lowercase text-emerald-300"
                title="Content Credentials"
              >
                cr
              </span>
              <span className="font-mono text-xs text-emerald-300">
                signature {cc.validationState ?? "Valid"}
              </span>
            </div>
            <dl className="grid gap-1 font-mono text-xs text-white/70">
              <Row k="produced by" v={cc.claimGenerator} />
              <Row k="format" v={cc.format} />
              <Row k="signed by" v={cc.issuer} />
              <Row k="signed at" v={cc.time} />
              <Row k="assertions" v={cc.assertions.join(", ")} />
            </dl>
            <p className="mt-2 text-[11px] text-white/40">
              Signed with a C2PA test certificate: the signature is valid, not the green Trusted state
              (which needs a Conformance-Program CA).
            </p>
          </div>
        </div>
      )}
    </section>
  );
}

function Row({ k, v }: { k: string; v?: string }) {
  if (!v) return null;
  return (
    <div className="flex gap-3">
      <dt className="w-24 shrink-0 text-white/40">{k}</dt>
      <dd className="break-all text-white/80">{v}</dd>
    </div>
  );
}
