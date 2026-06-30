import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { Providers } from "@/lib/providers";
import { SceneBackdrop } from "@/components/three/scene-backdrop";
import { SITE_URL } from "@/lib/site";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  // Absolute base for file-based metadata (the Open Graph image, robots, sitemap) and any relative URL.
  metadataBase: new URL(SITE_URL),
  title: "Rooted: C2PA provenance recovery",
  description:
    "Vendor-neutral C2PA Soft Binding Resolution: recover stripped provenance for AI-generated media.",
  // The default OG image is supplied by the file convention (app/opengraph-image.tsx), so no images
  // are set here to avoid emitting two og:image tags.
  openGraph: {
    siteName: "Rooted",
    title: "Rooted: recover stripped C2PA provenance",
    description:
      "Vendor-neutral C2PA Soft Binding Resolution on Backblaze B2. Recover stripped provenance for AI-generated media, with a tamper-evident transparency-log proof.",
    type: "website",
    url: SITE_URL,
  },
  twitter: {
    card: "summary_large_image",
    title: "Rooted: recover stripped C2PA provenance",
    description:
      "Vendor-neutral C2PA Soft Binding Resolution on Backblaze B2. Recover stripped provenance for AI-generated media.",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        <SceneBackdrop />
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
