import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

const COUNT = 9000;
const ARMS = 3;
const RADIUS = 5;
const SPIN = 1.1;
const RANDOMNESS = 0.35;
const INNER = new THREE.Color("#ff8a3d"); // warm core
const OUTER = new THREE.Color("#3b6dff"); // cool rim (Backblaze-ish blue)

// A spiral galaxy of additive points. Positions and colors are computed once; the only per-frame
// work is a slow Y rotation, which is skipped entirely under reduced motion.
function buildGalaxy() {
  const positions = new Float32Array(COUNT * 3);
  const colors = new Float32Array(COUNT * 3);
  for (let i = 0; i < COUNT; i++) {
    const i3 = i * 3;
    const radius = Math.pow(Math.random(), 1.5) * RADIUS;
    const branch = ((i % ARMS) / ARMS) * Math.PI * 2;
    const spin = radius * SPIN;
    const spread = () =>
      Math.pow(Math.random(), 3) * (Math.random() < 0.5 ? 1 : -1) * RANDOMNESS * radius;

    positions[i3] = Math.cos(branch + spin) * radius + spread();
    positions[i3 + 1] = spread() * 0.5;
    positions[i3 + 2] = Math.sin(branch + spin) * radius + spread();

    const mixed = INNER.clone().lerp(OUTER, radius / RADIUS);
    colors[i3] = mixed.r;
    colors[i3 + 1] = mixed.g;
    colors[i3 + 2] = mixed.b;
  }
  return { positions, colors };
}

export function Galaxy({ reduced }: { reduced: boolean }) {
  const ref = useRef<THREE.Points>(null);
  const { positions, colors } = useMemo(buildGalaxy, []);

  useFrame((_, delta) => {
    if (!reduced && ref.current) ref.current.rotation.y += delta * 0.04;
  });

  return (
    <points ref={ref} rotation={[0.5, 0, 0]} frustumCulled={false}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
        <bufferAttribute attach="attributes-color" args={[colors, 3]} />
      </bufferGeometry>
      <pointsMaterial
        size={0.025}
        sizeAttenuation
        vertexColors
        transparent
        opacity={0.9}
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </points>
  );
}
