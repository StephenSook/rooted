import { ProvenanceReceipt } from "@/components/provenance-receipt";

// /r/<manifestId> : a shareable, citable provenance-receipt permalink for one recovered manifest.
export default async function ReceiptPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  // On the live Vercel deploy the dynamic segment arrives URL-ENCODED (urn%3Ac2pa%3A...), so decode
  // it to the clean manifest id (urn:c2pa:...) before passing it down. The receipt then applies a
  // single encodeURIComponent for its fetch; without this decode that becomes a double-encode
  // (%3A -> %253A) and every colon-bearing id 404s. A clean id contains no literal %, so decoding it
  // is a no-op; a malformed % falls back to the raw value.
  let manifestId = id;
  try {
    manifestId = decodeURIComponent(id);
  } catch {
    manifestId = id;
  }
  return <ProvenanceReceipt manifestId={manifestId} />;
}
