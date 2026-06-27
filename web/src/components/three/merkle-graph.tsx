"use client";

// The WebGL force-graph, isolated so it can be loaded client-only (next/dynamic ssr:false from the
// explorer): r3f-forcegraph touches `window` at import, which breaks server prerendering otherwise.
import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import R3fForceGraph from "r3f-forcegraph";
import { useMemo, useRef } from "react";

export type Kind = "leaf" | "internal" | "root";
export type GNode = { id: string; kind: Kind; hash?: string };
export type GLink = { source: string; target: string };

const COLOR: Record<Kind, string> = {
  leaf: "#34d399", // emerald: a real manifest leaf
  internal: "#64748b", // slate: a structural Merkle node
  root: "#f59e0b", // amber: the signed checkpoint root
};

function Graph({ nodes, links }: { nodes: GNode[]; links: GLink[] }) {
  const fgRef = useRef<{ tickFrame: () => void } | null>(null);
  const data = useMemo(() => ({ nodes, links }), [nodes, links]);
  useFrame(() => fgRef.current?.tickFrame());
  return (
    <R3fForceGraph
      ref={fgRef}
      graphData={data}
      nodeColor={(n: GNode) => COLOR[n.kind]}
      nodeRelSize={4}
      nodeOpacity={0.95}
      linkColor={() => "rgba(255,255,255,0.18)"}
      linkWidth={0.5}
    />
  );
}

export default function MerkleGraph({
  nodes,
  links,
  reduced,
}: {
  nodes: GNode[];
  links: GLink[];
  reduced: boolean;
}) {
  return (
    <Canvas
      frameloop="always"
      camera={{ position: [0, 0, 220], far: 2000 }}
      dpr={[1, 1.5]}
      gl={{ antialias: false }}
    >
      <ambientLight intensity={Math.PI} />
      <Graph nodes={nodes} links={links} />
      <OrbitControls enablePan={false} enableZoom autoRotate={!reduced} autoRotateSpeed={0.5} />
    </Canvas>
  );
}
