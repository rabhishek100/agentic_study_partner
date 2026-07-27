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
  title: "Agentic Study Partner",
  description: "A grounded study companion for technical books",
  icons: { icon: "/favicon.svg" },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f2f0e8" },
    { media: "(prefers-color-scheme: dark)", color: "#0f1612" },
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
