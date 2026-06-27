import { RecoverPanel } from "@/components/recover-panel";
import { SupportedAlgorithms } from "@/components/supported-algorithms";

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col justify-center gap-8 px-6 py-16">
      <header className="space-y-3">
        <p className="font-mono text-xs uppercase tracking-[0.3em] text-white/40">Rooted</p>
        <h1 className="text-balance text-4xl font-semibold sm:text-5xl">
          Recover stripped C2PA provenance.
        </h1>
        <p className="max-w-xl text-white/60">
          A vendor-neutral C2PA Soft Binding Resolution server on Backblaze B2. It matches an
          invisible watermark or a perceptual-hash fingerprint to return the recovered, signed
          manifest, with a tamper-evident transparency-log proof. Provenance proves origin, not
          truth.
        </p>
      </header>

      <RecoverPanel />

      <SupportedAlgorithms />

      <p className="font-mono text-xs text-white/30">
        Next 15, React 19, Tailwind v4, R3F, typed against the FastAPI SBR API.
      </p>
    </main>
  );
}
