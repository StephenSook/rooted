"use client";

import dynamic from "next/dynamic";

import { usePrefersReducedMotion } from "@/lib/use-reduced-motion-pref";

// The galaxy WebGL backdrop is deferred two ways from this client boundary:
//  1. dynamic(ssr:false) so the ~876KB of three.js + r3f + drei never blocks first paint or
//     hydration. The backdrop is a fixed -z-10 aria-hidden decoration, so a slightly later mount is
//     invisible and the DOM UI (the load-bearing recovery loop) is unaffected.
//  2. under prefers-reduced-motion we render a cheap static gradient with the same warm-core /
//     cool-rim palette and never import three.js at all, so a reduced-motion or low-power device
//     pays no GL-context or per-frame buffer cost.
const Scene = dynamic(() => import("./scene").then((m) => m.Scene), { ssr: false });

const STATIC_BACKDROP =
  "radial-gradient(60% 50% at 50% 42%, rgba(255,168,86,0.10), rgba(78,120,200,0.06) 45%, #05060a 80%)";

export function SceneBackdrop() {
  const reduced = usePrefersReducedMotion();
  if (reduced) {
    return (
      <div
        className="pointer-events-none fixed inset-0 -z-10"
        aria-hidden="true"
        style={{ background: STATIC_BACKDROP }}
      />
    );
  }
  return <Scene />;
}
