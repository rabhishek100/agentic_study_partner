import type { Metadata, Viewport } from "next";
import { Geist, Source_Serif_4 } from "next/font/google";

import { Providers } from "./providers";
import "./globals.css";

// Interface chrome and long-form answer prose are deliberately different
// typefaces: the serif carries the reading, the sans carries the controls.
const geistSans = Geist({
  subsets: ["latin"],
  variable: "--font-geist-sans",
  display: "swap",
});

const readingSerif = Source_Serif_4({
  subsets: ["latin"],
  variable: "--font-reading-serif",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Mugensei",
  description: "Master difficult material. A grounded study companion.",
  icons: { icon: "/favicon.svg" },
};

export const viewport: Viewport = {
  // The canvas roles. Kept in sync with `--canvas` in globals.css.
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f4f0e6" },
    { media: "(prefers-color-scheme: dark)", color: "#070b0a" },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${geistSans.variable} ${readingSerif.variable}`}
    >
      <body className="min-h-dvh">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
