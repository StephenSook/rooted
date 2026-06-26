"use client";

import { Canvas } from "@react-three/fiber";
import { PerformanceMonitor } from "@react-three/drei";
import { useState } from "react";

import { usePrefersReducedMotion } from "@/lib/use-reduced-motion-pref";
import { Galaxy } from "./galaxy";

// The single persistent Canvas: a fixed, full-screen, non-interactive backdrop behind the DOM. It
// lives in the root layout (a sibling of {children}) so it never unmounts across navigation. The
// functional UI is plain DOM on top of this, so the recovery result never depends on WebGL health
// (the demo-safe rule). Under reduced motion the frameloop is "demand": one static render, no loop.
export function Scene() {
  const reduced = usePrefersReducedMotion();
  const [dpr, setDpr] = useState(1.5);

  return (
    <div className="pointer-events-none fixed inset-0 -z-10" aria-hidden="true">
      <Canvas
        dpr={dpr}
        frameloop={reduced ? "demand" : "always"}
        camera={{ position: [0, 1.5, 7], fov: 60 }}
        gl={{ antialias: false, powerPreference: "high-performance" }}
      >
        {/* Drop pixel ratio on weak devices, raise it on strong ones, to hold framerate. */}
        <PerformanceMonitor onIncline={() => setDpr(2)} onDecline={() => setDpr(1)} />
        <Galaxy reduced={reduced} />
      </Canvas>
    </div>
  );
}
