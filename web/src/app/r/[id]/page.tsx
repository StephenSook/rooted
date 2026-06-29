import { ProvenanceReceipt } from "@/components/provenance-receipt";

// /r/<manifestId> : a shareable, citable provenance-receipt permalink for one recovered manifest.
export default async function ReceiptPage({ params }: { params: Promise<{ id: string }> }) {
  // Next's App Router already URL-decodes dynamic segments, so `id` is the decoded manifestId
  // (urn:c2pa:...). A second decodeURIComponent would corrupt any id containing a literal %.
  const { id } = await params;
  return <ProvenanceReceipt manifestId={id} />;
}
