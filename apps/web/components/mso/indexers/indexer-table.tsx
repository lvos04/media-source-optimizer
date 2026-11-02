"use client";

import { Pencil, PlugZap, Repeat, Trash2 } from "lucide-react";
import { useState } from "react";

import { Confidence, DangerDialog } from "@/components/mg/domain";
import { Button, StatusChip, type Tone } from "@/components/mg/primitives";
import { useToast } from "@/components/mg/toast";
import { DirectIndexerDialog } from "@/components/mso/indexers/direct-dialog";
import { SOURCE_STATUS } from "@/components/mso/search/parts";
import { api, errorMessage } from "@/lib/api/client";
import type { DirectIndexer, IndexerSource, IndexerTestResult, Protocol, SourceStatus } from "@/lib/api/search";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";

const DIRECT_STATUS: Record<string, { tone: Tone; label: MessageKey }> = {
  healthy: { tone: "success", label: "idx.status.healthy" },
  degraded: { tone: "warning", label: "idx.status.degraded" },
  offline: { tone: "danger", label: "idx.status.offline" },
  auth_failed: { tone: "danger", label: "idx.status.auth_failed" },
  unknown: { tone: "neutral", label: "idx.status.unknown" },
};

function StatusCell({ s }: { s: IndexerSource }) {
  const { t, relative } = useI18n();
  if (s.is_mso) return <StatusChip tone="info" icon={Repeat}>{t("idx.isMso")}</StatusChip>;
  if (!s.enabled) return <StatusChip tone="neutral">{t("idx.disabled")}</StatusChip>;
  const chips = [];
  if (s.direct) {
    const st = DIRECT_STATUS[s.direct.status] ?? DIRECT_STATUS.unknown;
    chips.push(<StatusChip key="d" tone={st.tone}>{t(st.label)}</StatusChip>);
    if (s.direct.backoff_until && new Date(s.direct.backoff_until) > new Date()) {
      chips.push(<StatusChip key="b" tone="warning">{t("idx.backoff", { when: relative(s.direct.backoff_until) })}</StatusChip>);
    }
  } else if (s.prowlarr?.disabled_till && new Date(s.prowlarr.disabled_till) > new Date()) {
    chips.push(<StatusChip key="p" tone="warning">{t("idx.disabledTill", { when: relative(s.prowlarr.disabled_till) })}</StatusChip>);
  }
  if (s.stats?.last_search_at) {
    const last = (s.stats.last_status ?? "ok") as SourceStatus;
    const recentError = s.stats.errors > 0 && s.stats.last_status;
    const st = recentError ? SOURCE_STATUS[last] ?? SOURCE_STATUS.error : SOURCE_STATUS.ok;
    if (!s.direct) chips.push(<StatusChip key="s" tone={st.tone} icon={st.icon}>{t(st.label)}</StatusChip>);
  }
  return (
    <div className="stack-sm">
      <span className="row wrap" style={{ gap: 4 }}>{chips.length ? chips : <StatusChip tone="neutral">{t("idx.never")}</StatusChip>}</span>
      {s.stats?.last_search_at ? <span className="cell-sub">{t("idx.lastSearch", { when: relative(s.stats.last_search_at) })}</span> : null}
      {s.direct?.last_error ? <span className="cell-sub t-danger">{s.direct.last_error}</span> : null}
    </div>
  );
}

export function IndexerTable({ items, protocol, reload }: { items: IndexerSource[]; protocol: Protocol; reload: () => void | Promise<void> }) {
  const { t, num } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const [editing, setEditing] = useState<DirectIndexer | null>(null);
  const [deleting, setDeleting] = useState<DirectIndexer | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const isAdmin = can("admin");
  const isOperator = can("operator");
  const sorted = [...items].sort((a, b) => (b.reliability ?? -1) - (a.reliability ?? -1) || a.label.localeCompare(b.label));

  const test = async (d: DirectIndexer) => {
    setPending(`test:${d.slug}`);
    try {
      const r = await api.post<IndexerTestResult>(`/indexers/direct/${encodeURIComponent(d.slug)}/test`);
      toast.push(r.ok ? "success" : "danger", t("idx.tested", { message: r.message }), d.label);
      await reload();
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: errorMessage(e) }));
    } finally {
      setPending(null);
    }
  };

  const remove = async () => {
    if (!deleting) return;
    setPending(`del:${deleting.slug}`);
    setDeleteError(null);
    try {
      await api.del(`/indexers/direct/${encodeURIComponent(deleting.slug)}`);
      toast.push("success", t("idx.deleted"), deleting.label);
      setDeleting(null);
      await reload();
    } catch (e) {
      setDeleteError(errorMessage(e));
    } finally {
      setPending(null);
    }
  };

  return (
    <>
      <table className="table table--stack" data-testid={`indexers-${protocol}`}>
        <thead>
          <tr>
            <th scope="col">{t("idx.col.indexer")}</th>
            <th scope="col">{t("idx.col.scope")}</th>
            <th scope="col">{t("idx.col.status")}</th>
            <th scope="col">{t("idx.col.reliability")}</th>
            <th scope="col">{t("idx.col.latency")}</th>
            <th scope="col"><span className="sr-only">{t("idx.col.actions")}</span></th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((s) => {
            const originKey = (s.kind === "spotweb" ? "idx.origin.spotweb" : `idx.origin.${s.origin}`) as MessageKey;
            const rel = s.reliability_detail;
            const latency = s.stats?.median_latency_ms ?? s.prowlarr?.avg_response_ms ?? null;
            return (
              <tr key={s.id}>
                <td data-label={t("idx.col.indexer")}>
                  <div className="cell-title">{s.label}</div>
                  <div className="cell-sub row wrap" style={{ gap: 4 }}>
                    <StatusChip tone="neutral" icon={null} outline>{t(originKey)}</StatusChip>
                    {s.private ? <StatusChip tone="warning" icon={null} outline>{t("idx.private")}</StatusChip> : null}
                    <span className="mono" style={{ fontSize: 11 }}>{s.id}</span>
                  </div>
                </td>
                <td data-label={t("idx.col.scope")}>
                  <span className="row wrap" style={{ gap: 4 }}>
                    {s.scopes.map((sc) => <span key={sc} className="tag">{t(`idx.scope.${sc}`)}</span>)}
                    {s.untagged ? <StatusChip tone="warning" title={t("idx.untaggedHelp")}>{t("idx.untagged")}</StatusChip> : null}
                  </span>
                </td>
                <td data-label={t("idx.col.status")}><StatusCell s={s} /></td>
                <td data-label={t("idx.col.reliability")}>
                  {s.reliability !== null ? <Confidence value={s.reliability} tone={s.reliability < 0.6 ? "danger" : s.reliability < 0.85 ? "warning" : "success"} />
                    : <span className="t-3" style={{ fontSize: 12 }}>{t("idx.notEnoughData")}</span>}
                  {rel ? <div className="cell-sub">{t("idx.samples", { n: num(rel.samples), fp: num(rel.false_positives) })}</div> : null}
                </td>
                <td data-label={t("idx.col.latency")} className="mono" style={{ fontSize: 12 }}>{latency !== null ? `${num(latency)} ms` : "—"}</td>
                <td data-label={t("idx.col.actions")}>
                  {s.direct ? (
                    <div className="row wrap" style={{ gap: 6, justifyContent: "flex-end" }}>
                      <Button size="sm" icon={PlugZap} pending={pending === `test:${s.direct.slug}`} disabled={!isOperator}
                        title={!isOperator ? t("zoeken.operatorOnly") : undefined} onClick={() => void test(s.direct!)}>{t("action.test")}</Button>
                      <Button size="sm" icon={Pencil} iconOnly disabled={!isAdmin} title={!isAdmin ? t("idx.adminOnly") : t("idx.edit")}
                        onClick={() => setEditing(s.direct!)}>{t("idx.edit")}</Button>
                      <Button size="sm" variant="danger" icon={Trash2} iconOnly disabled={!isAdmin} title={!isAdmin ? t("idx.adminOnly") : t("idx.delete")}
                        onClick={() => { setDeleteError(null); setDeleting(s.direct!); }}>{t("idx.delete")}</Button>
                    </div>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {editing ? <DirectIndexerDialog protocol={protocol} existing={editing} onClose={() => setEditing(null)} onSaved={async () => { toast.push("success", t("idx.saved")); await reload(); }} /> : null}
      <DangerDialog open={Boolean(deleting)} onClose={() => setDeleting(null)} onConfirm={() => void remove()} icon={Trash2}
        title={t("idx.deleteTitle", { label: deleting?.label ?? "" })} description={t("idx.deleteBody")}
        confirmLabel={t("idx.delete")} pending={pending?.startsWith("del:")} error={deleteError} />
    </>
  );
}
