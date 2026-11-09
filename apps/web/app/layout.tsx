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

// Peil: themeColor follows the resolved colour scheme (background token of ontwerptaal.css, light/dark).
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#EEF1F5" },
    { media: "(prefers-color-scheme: dark)", color: "#0A0D12" },
  ],
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="nl" suppressHydrationWarning>
      <head>
        {/* Apply the per-browser theme before first paint (no flash). Default "system" (Peil O-02): the script ALWAYS
            sets the resolved data-theme="dark|light" — without the attribute Peil is light. Key mso.theme unchanged. */}
        <script dangerouslySetInnerHTML={{ __html: "(function(){var r=document.documentElement,c='system';try{c=localStorage.getItem('mso.theme')||'system'}catch(e){}if(c!=='dark'&&c!=='light')c='system';var d=c==='dark'||(c==='system'&&window.matchMedia&&matchMedia('(prefers-color-scheme: dark)').matches);r.dataset.theme=d?'dark':'light';r.dataset.themeChoice=c})()" }} />
      </head>
      <body className={`${inter.variable} ${mono.variable}`}>{children}</body>
    </html>
  );
}
