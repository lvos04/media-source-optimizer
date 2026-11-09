// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/app/layout.tsx — internally versioned copy, see PROVENANCE.md
import type { Metadata, Viewport } from "next";
import "./globals.css";

// Peil O-01: system font and system mono (ontwerptaal.css --font-sans/--font-mono); no web fonts, no font requests.
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
      <body>{children}</body>
    </html>
  );
}
