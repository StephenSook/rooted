"use client";

import dynamic from "next/dynamic";
import { useMemo } from "react";

import { $api } from "@/lib/api/client";
import { usePrefersReducedMotion } from "@/lib/use-reduced-motion-pref";
import type { GLink, GNode } from "./merkle-graph";

// Client-only: r3f-forcegraph touches window at import, so it must not be server-rendered.
const MerkleGraph = dynamic(() => import("./merkle-graph"), { ssr: false });

type Entry = { leafIndex: number; manifestId: string; leafHash: string };

// Build the binary Merkle tree shape from the ordered leaves: real leaf hashes at the bottom,
// structural internal nodes pairing upward, and the real signed root at the top.
function buildTree(entries: Entry[], rootHash: string): { nodes: GNode[]; links: GLink[] } {
  const nodes: GNode[] = [];
  const links: GLink[] = [];
  let level = entries.map((e) => {
    const id = `leaf-${e.leafIndex}`;
    nodes.push({ id, kind: "leaf", hash: e.leafHash });
    return id;
  });
  let depth = 0;
  while (level.length > 1) {
    depth += 1;
    const next: string[] = [];
    for (let i = 0; i < level.length; i += 2) {
      const id = `n${depth}-${i / 2}`;
      nodes.push({ id, kind: "internal" });
      links.push({ source: level[i], target: id });
      if (level[i + 1]) links.push({ source: level[i + 1], target: id });
      next.push(id);
    }
    level = next;
  }
  const root = nodes.find((n) => n.id === level[0]);
  if (root) {
    root.kind = "root";
    root.hash = rootHash;
  }
  return { nodes, links };
}

export function MerkleExplorer() {
  const reduced = usePrefersReducedMotion();
  const { data, error, isPending } = $api.useQuery("get", "/transparency/log");
  const graph = useMemo(
    () => (data ? buildTree(data.entries ?? [], data.rootHash) : { nodes: [], links: [] }),
    [data],
  );

  return (
    <section className="rounded-xl border border-white/15 bg-white/[0.03] p-5 backdrop-blur-md">
      <h2 className="mb-1 text-xs uppercase tracking-widest text-white/50">
        Transparency log · Merkle tree
      </h2>

      {isPending && <p className="font-mono text-sm text-white/50">Loading the log…</p>}
      {error != null && <p className="font-mono text-sm text-amber-400">Backend unreachable.</p>}

      {data && graph.nodes.length > 0 && (
        <>
          <div
            className="h-80 w-full"
            role="img"
            aria-label="3D Merkle transparency tree; the tree size and root are listed below."
          >
            <MerkleGraph nodes={graph.nodes} links={graph.links} reduced={reduced} />
          </div>
          <dl className="mt-2 grid gap-1 font-mono text-xs text-white/60">
            <div className="flex gap-3">
              <dt className="w-20 text-white/55">leaves</dt>
              <dd className="text-white/80">{data.treeSize}</dd>
            </div>
            <div className="flex gap-3">
              <dt className="w-20 shrink-0 text-white/55">root</dt>
              <dd className="break-all text-amber-300/90">{data.rootHash}</dd>
            </div>
          </dl>
        </>
      )}
    </section>
  );
}
