// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/components/shell/auth-frame.tsx — internally versioned copy, see PROVENANCE.md
// (MSO: own brand mark/initials and selling points; Dutch-only, no locale switch.)
"use client";

import { Check, Gauge, SearchX } from "lucide-react";
import type { ReactNode } from "react";

import { I18nProvider, useI18n } from "@/lib/i18n";

/** Brand mark: MediaGuard's gradient tile, with MSO initials instead of the shield. */
export function BrandMark() {
  const { t } = useI18n();
  return <span className="brand-mark brand-mark--text" aria-hidden="true">{t("app.short")}</span>;
}

/** Split-screen frame for login and first-run setup (brand panel hidden on mobile). */
export function AuthFrame({ children, wide }: { children: ReactNode; wide?: boolean }) {
  return <I18nProvider locale="nl-NL"><Frame wide={wide}>{children}</Frame></I18nProvider>;
}

function Frame({ children, wide }: { children: ReactNode; wide?: boolean }) {
  const { t } = useI18n();
  return (
    <div className="auth">
      <aside className="auth-side">
        <div className="brand"><BrandMark /><span><div className="brand-name">{t("app.fullName")}</div><div className="brand-sub">{t("auth.sideTitle")}</div></span></div>
        <div className="auth-points">
          <div className="auth-point"><span className="list-icon list-icon--accent"><Gauge className="ico" aria-hidden="true" /></span><span><strong>{t("auth.point1Title")}</strong>{t("auth.point1Body")}</span></div>
          <div className="auth-point"><span className="list-icon list-icon--success"><SearchX className="ico" aria-hidden="true" /></span><span><strong>{t("auth.point2Title")}</strong>{t("auth.point2Body")}</span></div>
          <div className="auth-point"><span className="list-icon list-icon--info"><Check className="ico" aria-hidden="true" /></span><span><strong>{t("auth.point3Title")}</strong>{t("auth.point3Body")}</span></div>
        </div>
        <p className="t-3">{t("auth.footer")}</p>
      </aside>
      <main className="auth-main" id="main">
        <div className="auth-card" style={wide ? { width: "min(640px, 100%)" } : undefined}>{children}</div>
      </main>
    </div>
  );
}
