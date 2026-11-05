"use client";

import { ArrowLeft, Home, SearchX } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";

import { Button, LinkButton } from "@/components/mg/primitives";
import { useI18n } from "@/lib/i18n";

/** Dutch 404 in house style: used inside the app shell and on the bare root not-found page. */
export function NotFoundView() {
  const { t } = useI18n();
  const path = usePathname();
  const router = useRouter();
  return (
    <div className="state state--not_found" role="status" data-state="not_found" data-testid="not-found" style={{ padding: "48px 16px" }}>
      <span className="state-icon"><SearchX className="ico" aria-hidden="true" /></span>
      <span className="eyebrow">{t("notFound.code")}</span>
      <h1 className="state-title" style={{ fontSize: 20, margin: 0 }}>{t("notFound.title")}</h1>
      <div className="state-text" style={{ maxWidth: 460 }}>{t("notFound.body")}</div>
      {path ? <div className="state-meta"><span>{t("notFound.path")}: </span><code className="mono" style={{ wordBreak: "break-all" }}>{path}</code></div> : null}
      <div className="row wrap" style={{ gap: 8, justifyContent: "center", marginTop: 8 }}>
        <LinkButton href="/" variant="primary" icon={Home}>{t("notFound.home")}</LinkButton>
        <Button icon={ArrowLeft} onClick={() => router.back()}>{t("notFound.back")}</Button>
      </div>
    </div>
  );
}
