import { ProvenanceReceipt } from "@/components/provenance-receipt";

// /r/<manifestId> : a shareable, citable provenance-receipt permalink for one recovered manifest.
export default async function ReceiptPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ProvenanceReceipt manifestId={decodeURIComponent(id)} />;
}
