import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { TamperPanel } from "@/components/tamper-panel";

const ORIG_SHA = "abc123";
const ORIG_MODEL = "seedream-5.0-lite";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

// Mock the two endpoints: /demo/signed-manifest serves the signed manifest; /verify returns valid
// only when the manifest's signed fields are unchanged (a faithful stand-in for the COSE check).
function mockApi(): void {
  globalThis.fetch = vi.fn(async (url: unknown, init?: { body?: unknown }) => {
    const u = String(url);
    if (u.includes("/demo/signed-manifest")) {
      return json({
        manifest: {
          manifestId: "urn:c2pa:demo",
          assetSha256: ORIG_SHA,
          createdAt: "2026-06-27T00:00:00Z",
          systemProvenance: { model: ORIG_MODEL },
        },
        signatureB64: "sig",
        publicKeyHex: "a".repeat(64),
      });
    }
    if (u.includes("/verify")) {
      const body = JSON.parse(String(init?.body ?? "{}"));
      const m = body.manifest ?? {};
      const valid = m.systemProvenance?.model === ORIG_MODEL && m.assetSha256 === ORIG_SHA;
      return json({ signatureValid: valid, publicKeyHex: "a".repeat(64), keySource: "configured" });
    }
    return json({}, 404);
  }) as unknown as typeof fetch;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("TamperPanel", () => {
  it("verifies the untouched manifest, then shows TAMPERED after a signed field is edited", async () => {
    mockApi();
    render(<TamperPanel />);

    // initial auto-verify: VALID
    expect(await screen.findByText(/SIGNATURE VALID/)).toBeTruthy();

    // edit the model (a signed field), then re-verify -> TAMPERED
    const modelInput = screen.getByDisplayValue(ORIG_MODEL);
    fireEvent.change(modelInput, { target: { value: "evil-model" } });
    fireEvent.click(screen.getByText("Re-verify"));

    expect(await screen.findByText(/TAMPERED/)).toBeTruthy();
  });
});
