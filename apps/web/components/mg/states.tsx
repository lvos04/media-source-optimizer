// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/components/mg/states.tsx — internally versioned copy, see PROVENANCE.md
// (MSO: ErrorState renders the "connector not configured" unavailable state for code=connector_not_configured.)
"use client";

import { Ban, CircleSlash, Inbox, Link2Off, OctagonX, SearchX, Settings2, WifiOff, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Button, CopyField, LinkButton, Skeleton } from "@/components/mg/primitives";
import type { ApiError } from "@/lib/api/client";
import type { Resource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export type StateKind = "loading" | "empty" | "error" | "offline" | "unavailable" | "forbidden" | "not_found";

// Peil icon mapping (ONTWERPTAAL §7): Storing/fout octagon-x · niet beschikbaar circle-slash · offline wifi-off.
const ICONS: Record<Exclude<StateKind, "loading">, LucideIcon> = {
  empty: Inbox,
  error: OctagonX,
  offline: WifiOff,
  unavailable: CircleSlash,
  forbidden: Ban,
  not_found: SearchX,
};

export function StateView({ kind, title, body, action, meta, compact, icon }: {
  kind: StateKind; title?: ReactNode; body?: ReactNode; action?: ReactNode; meta?: ReactNode; compact?: boolean; icon?: LucideIcon;
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
  const Icon = icon ?? ICONS[kind];
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
      <StateView kind="unavailable" icon={Link2Off} compact={compact} title={t("state.connectorTitleGeneric")} body={error.message || t("state.connectorBodyGeneric")}
        action={<LinkButton href="/instellingen?tab=verbindingen" size="sm" variant="primary" icon={Settings2}>{t("state.connectorAction")}</LinkButton>} />
    );
  }
  const kind: StateKind = error.kind === "unavailable" ? "unavailable"
    : error.kind === "offline" ? "offline"
      : error.kind === "forbidden" ? "forbidden"
        : error.kind === "not_found" ? "not_found" : "error";
  // Peil Foutpaneel (C-14): what failed + why + what you can do, the code, and a copyable trace-ID.
  const meta = kind === "unavailable"
    ? <>{t("state.unavailableCode")} · {t("state.unavailableEndpoint", { endpoint: endpoint ?? `/api/v1${error.path}` })}</>
    : kind === "error" ? (
      <span className="stack-sm" style={{ alignItems: "center" }}>
        <span>{t("state.errorCode", { status: String(error.status), code: error.code ?? "error" })}</span>
        {error.correlationId ? <CopyField label={t("state.traceId")} value={error.correlationId} /> : null}
      </span>
    ) : error.correlationId ? <CopyField label={t("state.traceId")} value={error.correlationId} /> : undefined;
  return (
    <StateView
      kind={kind}
      compact={compact}
      meta={meta}
      body={kind === "forbidden" && error.message ? error.message : kind === "error" ? <>{error.message && error.message !== `HTTP ${error.status}` ? <>{error.message} </> : null}{t("state.errorBody")}</> : undefined}
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
  // Peil: verouderd is always visible — also when the last known list was empty ("Wachtrij is leeg" may be outdated).
  const staleMark = resource.stale ? (
    <div style={{ padding: "0 16px 8px" }}>
      <span className="stale-mark" role="status">{t("state.stale", { age: relative(resource.fetchedAt) })}</span>
    </div>
  ) : null;
  if (isEmpty?.(resource.data)) return <>{staleMark}{empty ?? <StateView kind="empty" compact={compact} />}</>;
  return (
    <>
      {staleMark}
      {children(resource.data)}
    </>
  );
}
