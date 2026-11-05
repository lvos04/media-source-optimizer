// Root Dutch 404 (outside the app shell, e.g. when no route group matches), in house style.
"use client";

import { BrandMark } from "@/components/shell/auth-frame";
import { NotFoundView } from "@/components/shell/not-found-view";
import { I18nProvider } from "@/lib/i18n";

export default function NotFound() {
  return (
    <I18nProvider locale="nl-NL">
      <main id="main" style={{ minHeight: "100dvh", display: "grid", placeItems: "center", padding: 16, background: "var(--mg-bg)" }}>
        <div className="card" style={{ width: "min(560px, 100%)" }}>
          <div className="row" style={{ gap: 10, padding: "16px 16px 0" }}><BrandMark /><strong>Media Source Optimizer</strong></div>
          <NotFoundView />
        </div>
      </main>
    </I18nProvider>
  );
}
