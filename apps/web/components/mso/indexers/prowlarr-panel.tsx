"use client";

import { GitBranch, RefreshCw, Scale } from "lucide-react";

import { Trace } from "@/components/mg/domain";
import { Button, Card, StatusChip, type Tone } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { ConnectorList } from "@/components/mso/connector-form";
import { useAction } from "@/lib/api/actions";
import { useResource } from "@/lib/api/hooks";
import type { OriginGraph, ProwlarrConflict, ProwlarrView } from "@/lib/api/search";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";

const SEVERITY: Record<ProwlarrConflict["severity"], { tone: Tone; label: MessageKey }> = {
  error: { tone: "danger", label: "idx.severity.error" },
  warning: { tone: "warning", label: "idx.severity.warning" },
  info: { tone: "info", label: "idx.severity.info" },
};

function PreviewFacts({ preview }: { preview: Record<string, unknown> }) {
  const { t } = useI18n();
  const entries = Object.entries(preview).filter(([k]) => k !== "where");
  return (
    <dl className="kv" style={{ marginTop: 6 }}>
      {preview.where ? <><dt>{t("idx.conflicts.where")}</dt><dd>{String(preview.where)}</dd></> : null}
      {entries.map(([k, v]) => (
        <div key={k} style={{ display: "contents" }}>
          <dt className="mono">{k}</dt>
          <dd className="mono" style={{ fontSize: 12 }}>{Array.isArray(v) ? (v.length ? v.map((x) => (typeof x === "object" ? JSON.stringify(x) : String(x))).join(", ") : "—") : typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Prowlarr connector + sync, and the read-only reconcile view (MediaGuard owns tags/applications). */
export function ProwlarrPanel({ configured, onSynced }: { configured: boolean; onSynced: () => void | Promise<void> }) {
  const { t, num } = useI18n();
  const { can } = useSession();
  const { run, pending } = useAction();
  const view = useResource<ProwlarrView>(configured ? "/indexers/prowlarr/view" : null, undefined, 300_000);
  const isOperator = can("operator");

  const sync = async () => {
    const r = await run<{ indexers: number }>("sync", "post", "/indexers/prowlarr/sync");
    if (r) {
      await Promise.all([onSynced(), view.reload()]);
    }
  };

  return (
    <>
      <Card className="span-6" title={t("idx.prowlarr")} sub={t("idx.prowlarrSub")}
        actions={<Button size="sm" icon={RefreshCw} pending={pending === "sync"} disabled={!configured || !isOperator}
          title={!configured ? t("idx.prowlarrNotConfigured") : !isOperator ? t("zoeken.operatorOnly") : undefined}
          onClick={() => void sync()}>{t("idx.sync")}</Button>}>
        <ConnectorList only={["prowlarr"]} canEdit={can("admin")} disabledReason={t("idx.adminOnly")} />
      </Card>
      <Card className="span-6" title={t("idx.conflicts")} sub={t("idx.conflictsSub")} icon={Scale} flush>
        {!configured ? (
          <StateView kind="unavailable" compact title={t("idx.prowlarrNotConfigured")} />
        ) : (
          <ResourceView resource={view} endpoint="GET /api/v1/indexers/prowlarr/view" compact
            isEmpty={(d) => d.conflicts.length === 0} empty={<StateView kind="empty" compact title={t("idx.conflicts.none")} />}>
            {(d) => (
              <div className="list">
                {d.conflicts.map((c) => {
                  const sev = SEVERITY[c.severity];
                  return (
                    <div key={`${c.code}:${c.subject}`} className="list-row" style={{ alignItems: "flex-start" }}>
                      <div className="list-main">
                        <div className="list-title row wrap" style={{ gap: 6 }}><StatusChip tone={sev.tone}>{t(sev.label)}</StatusChip>{c.title}</div>
                        <div className="list-sub">{c.explanation}</div>
                        {Object.keys(c.preview).length ? (
                          <details style={{ marginTop: 4 }}>
                            <summary className="t-2" style={{ fontSize: 12, cursor: "pointer" }}>{t("idx.conflicts.preview")}</summary>
                            <PreviewFacts preview={c.preview} />
                          </details>
                        ) : null}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </ResourceView>
        )}
        {view.data ? <div className="card-footer t-3" style={{ fontSize: 12 }}>{view.data.policy} · {num(view.data.indexers.length)} indexers · {num(view.data.applications.length)} applicaties</div> : null}
      </Card>
    </>
  );
}

/** Anti-loop: the configured origin graph and how each cycle is broken per request. */
export function OriginCard({ className }: { className?: string }) {
  const { t } = useI18n();
  const graph = useResource<OriginGraph>("/search/origin-graph", undefined, 300_000);
  return (
    <Card className={className} title={t("idx.origin")} icon={GitBranch} flush
      sub={graph.data ? t("idx.originSub", { hops: graph.data.max_hops }) : undefined}>
      <ResourceView resource={graph} endpoint="GET /api/v1/search/origin-graph" compact
        isEmpty={(d) => d.cycles.length === 0} empty={<StateView kind="empty" compact title={t("idx.origin.none")} />}>
        {(d) => (
          <div className="list">
            {d.cycles.map((c) => (
              <div key={c.cycle.join(">")} className="list-row" style={{ alignItems: "flex-start" }}>
                <div className="list-main">
                  <div className="list-title"><Trace steps={c.cycle.map((n, i) => ({ label: n, active: i === 0 || i === c.cycle.length - 1 }))} /></div>
                  <div className="list-sub">{c.resolution}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </ResourceView>
    </Card>
  );
}
