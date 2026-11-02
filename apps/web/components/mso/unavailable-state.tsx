"use client";

import { Settings2 } from "lucide-react";
import type { ReactNode } from "react";

import { LinkButton } from "@/components/mg/primitives";
import { StateView } from "@/components/mg/states";
import { useI18n } from "@/lib/i18n";

export { isConnectorUnconfigured } from "@/components/mg/states";

/**
 * "This needs a connector that is not configured" — never show placeholder data instead.
 * Links to Instellingen → Verbindingen (anchored at the connector when `connectorId` is given).
 * `ErrorState`/`ResourceView` render this automatically for API errors with
 * `code: "connector_not_configured"`; use it directly when you already know from `GET /connectors`.
 *
 *   <UnavailableState connector="SABnzbd" connectorId="sabnzbd" />
 */
export function UnavailableState({ connector, connectorId, title, body, compact, action }: {
  connector?: string; connectorId?: string; title?: ReactNode; body?: ReactNode; compact?: boolean; action?: ReactNode;
}) {
  const { t } = useI18n();
  const href = `/instellingen?tab=verbindingen${connectorId ? `#connector-${encodeURIComponent(connectorId)}` : ""}`;
  return (
    <StateView
      kind="unavailable"
      compact={compact}
      title={title ?? (connector ? t("state.connectorTitle", { connector }) : t("state.connectorTitleGeneric"))}
      body={body ?? (connector ? t("state.connectorBody", { connector }) : t("state.connectorBodyGeneric"))}
      action={action ?? <LinkButton href={href} size="sm" variant="primary" icon={Settings2}>{t("state.connectorAction")}</LinkButton>}
    />
  );
}
