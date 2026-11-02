// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/components/mg/states.tsx — internally versioned copy, see PROVENANCE.md
// (MSO: ErrorState renders the "connector not configured" unavailable state for code=connector_not_configured.)
"use client";

import { Ban, CircleX, Inbox, PlugZap, SearchX, Settings2, WifiOff, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Button, LinkButton, Skeleton } from "@/components/mg/primitives";
import type { ApiError } from "@/lib/api/client";
import type { Resource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export type StateKind = "loading" | "empty" | "error" | "offline" | "unavailable" | "forbidden" | "not_found";

const ICONS: Record<Exclude<StateKind, "loading">, LucideIcon> = {
  empty: Inbox,
  error: CircleX,
  offline: WifiOff,
  unavailable: PlugZap,
  forbidden: Ban,
  not_found: SearchX,
};

export function StateView({ kind, title, body, action, meta, compact }: {
  kind: StateKind; title?: ReactNode; body?: ReactNode; action?: ReactNode; meta?: ReactNode; compact?: boolean;
}) {
  const { t } = useI18n();
  if (kind === "loading") {
    return (
      <div className={cn("state", compact && "state--compact")} role="status" aria-live="polite" style={{ alignItems: "stretch", textAlign: "left" }}>
        <span className="sr-only">{t("state.loading")}</span>
        <Skeleton lines={compact ? 2 : 4} />
      </div>
    );
  }
  const Icon = ICONS[kind];
  const defaults: Record<Exclude<StateKind, "loading">, [string, string]> = {
    empty: [t("state.emptyTitle"), ""],
    error: [t("state.errorTitle"), t("state.errorBody")],
    offline: [t("state.offlineTitle"), t("state.offlineBody")],
    unavailable: [t("state.unavailableTitle"), t("state.unavailableBody")],
    forbidden: [t("state.forbiddenTitle"), t("state.forbiddenBody")],
    not_found: [t("state.notFoundTitle"), t("state.notFoundBody")],
  };
  return (
    <div className={cn("state", `state--${kind}`, compact && "state--compact")} role={kind === "error" ? "alert" : "status"} data-state={kind}>
      <span className="state-icon"><Icon className="ico" aria-hidden="true" /></span>
      <div className="state-title">{title ?? defaults[kind][0]}</div>
      {(body ?? defaults[kind][1]) ? <div className="state-text">{body ?? defaults[kind][1]}</div> : null}
      {meta ? <div className="state-meta">{meta}</div> : null}
      {action}
    </div>
  );
}

/** Backend error codes that mean "the external system this needs is not configured". */
export const CONNECTOR_UNCONFIGURED_CODES = ["connector_not_configured", "connector_disabled"];
export function isConnectorUnconfigured(error: ApiError | null | undefined): boolean {
  return Boolean(error?.code && CONNECTOR_UNCONFIGURED_CODES.includes(error.code));
}

export function ErrorState({ error, onRetry, endpoint, compact }: { error: ApiError; onRetry?: () => void; endpoint?: string; compact?: boolean }) {
  const { t } = useI18n();
  if (isConnectorUnconfigured(error)) {
    return (
      <StateView kind="unavailable" compact={compact} title={t("state.connectorTitleGeneric")} body={error.message || t("state.connectorBodyGeneric")}
        action={<LinkButton href="/instellingen?tab=verbindingen" size="sm" variant="primary" icon={Settings2}>{t("state.connectorAction")}</LinkButton>} />
    );
  }
  const kind: StateKind = error.kind === "unavailable" ? "unavailable"
    : error.kind === "offline" ? "offline"
      : error.kind === "forbidden" ? "forbidden"
        : error.kind === "not_found" ? "not_found" : "error";
  const meta = kind === "unavailable"
    ? t("state.unavailableEndpoint", { endpoint: endpoint ?? `/api/v1${error.path}` })
    : error.correlationId ? t("state.correlation", { id: error.correlationId }) : kind === "error" ? `HTTP ${error.status} · ${error.message}` : undefined;
  return (
    <StateView
      kind={kind}
      compact={compact}
      meta={meta}
      body={kind === "forbidden" && error.message ? error.message : undefined}
      action={onRetry && kind !== "forbidden" ? <Button size="sm" onClick={onRetry}>{t("action.retry")}</Button> : undefined}
    />
  );
}

/**
 * Render a Resource: loading → skeleton, error without data → ErrorState, otherwise children(data).
 * When data exists but the latest refresh failed, a stale marker is rendered above the content.
 */
export function ResourceView<T>({ resource, children, endpoint, compact, empty, isEmpty }: {
  resource: Resource<T>; children: (data: T) => ReactNode; endpoint?: string; compact?: boolean;
  empty?: ReactNode; isEmpty?: (data: T) => boolean;
}) {
  const { t, relative } = useI18n();
  if (resource.loading) return <StateView kind="loading" compact={compact} />;
  if (resource.error && resource.data === null) return <ErrorState error={resource.error} onRetry={() => void resource.reload()} endpoint={endpoint} compact={compact} />;
  if (resource.data === null) return <StateView kind="loading" compact={compact} />;
  if (isEmpty?.(resource.data)) return <>{empty ?? <StateView kind="empty" compact={compact} />}</>;
  return (
    <>
      {resource.stale ? (
        <div className="stale-mark" role="status" style={{ padding: "0 16px 8px" }}>
          {t("state.stale", { age: relative(resource.fetchedAt) })}
        </div>
      ) : null}
      {children(resource.data)}
    </>
  );
}
