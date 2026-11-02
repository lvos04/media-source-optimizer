// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/app/layout.tsx — internally versioned copy, see PROVENANCE.md
import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";

import "./globals.css";

// Self-hosted, SIL Open Font License 1.1 (see app/fonts/OFL-*.txt). No external font requests.
const inter = localFont({
  src: [
    { path: "./fonts/InterVariable-latin.woff2", weight: "100 900", style: "normal" },
    { path: "./fonts/InterVariable-latin-ext.woff2", weight: "100 900", style: "normal" },
  ],
  variable: "--font-inter",
  display: "swap",
});
const mono = localFont({
  src: [{ path: "./fonts/JetBrainsMonoVariable-latin.woff2", weight: "100 800", style: "normal" }],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: { default: "Media Source Optimizer", template: "%s · MSO" },
  description: "Bronnen, indexers en downloads optimaliseren — companion van MediaGuard Ultimate",
  icons: { icon: "/favicon.svg" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#0C111B",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="nl" data-theme="dark" suppressHydrationWarning>
      <head>
        {/* apply the per-browser theme before first paint (no flash); default dark */}
        <script dangerouslySetInnerHTML={{ __html: "try{var c=localStorage.getItem('mso.theme');if(c){var d=c==='dark'||(c==='system'&&matchMedia('(prefers-color-scheme: dark)').matches);document.documentElement.dataset.theme=d?'dark':'light';document.documentElement.dataset.themeChoice=c}}catch(e){}" }} />
      </head>
      <body className={`${inter.variable} ${mono.variable}`}>{children}</body>
    </html>
  );
}
