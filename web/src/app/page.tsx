import { AgentPanel } from "@/components/agent-panel";
import { AudioPanel } from "@/components/audio-panel";
import { B2EventsPanel } from "@/components/b2-events-panel";
import { CheckpointHistoryPanel } from "@/components/checkpoint-history-panel";
import { CheckpointLockPanel } from "@/components/checkpoint-lock-panel";
import { ComparisonPanel } from "@/components/comparison-panel";
import { ConsistencyPanel } from "@/components/consistency-panel";
import { ContentCredentials } from "@/components/content-credentials";
import { FederationPanel } from "@/components/federation-panel";
import { GenblazePanel } from "@/components/genblaze-panel";
import { GeneratePanel } from "@/components/generate-panel";
import { Hero } from "@/components/hero";
import { LineagePanel } from "@/components/lineage-panel";
import { ProvidersPanel } from "@/components/providers-panel";
import { RebuildPanel } from "@/components/rebuild-panel";
import { RecoverPanel } from "@/components/recover-panel";
import { RobustnessPanel } from "@/components/robustness-panel";
import { StatusPanel } from "@/components/status-panel";
import { StoragePanel } from "@/components/storage-panel";
import { SupportedAlgorithms } from "@/components/supported-algorithms";
import { TamperPanel } from "@/components/tamper-panel";
import { TranscriptPanel } from "@/components/transcript-panel";
import { VideoPanel } from "@/components/video-panel";
import { MerkleExplorer } from "@/components/three/merkle-explorer";

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col justify-center gap-8 px-6 py-16">
      <Hero />

      <GeneratePanel />

      <RecoverPanel />

      <ComparisonPanel />

      <RobustnessPanel />

      <ProvidersPanel />

      <AudioPanel />

      <VideoPanel />

      <ContentCredentials />

      <TamperPanel />

      <StoragePanel />

      <B2EventsPanel />

      <RebuildPanel />

      <GenblazePanel />

      <TranscriptPanel />

      <LineagePanel />

      <MerkleExplorer />

      <CheckpointLockPanel />

      <ConsistencyPanel />

      <CheckpointHistoryPanel />

      <SupportedAlgorithms />

      <FederationPanel />

      <StatusPanel />

      <AgentPanel />

      <p className="font-mono text-xs text-white/50">
        Next 15, React 19, Tailwind v4, R3F, typed against the FastAPI SBR API.
      </p>
    </main>
  );
}
