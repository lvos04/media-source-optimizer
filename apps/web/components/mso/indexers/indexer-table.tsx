"use client";

import { CircleSlash, Pencil, PlugZap, Repeat, Trash2, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { DangerDialog } from "@/components/mg/domain";
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
  const { t, relative, num } = useI18n();
  if (s.is_mso) return <StatusChip tone="info" icon={Repeat}>{t("idx.isMso")}</StatusChip>;
  if (!s.enabled) return <StatusChip tone="neutral">{t("idx.disabled")}</StatusChip>;
  const chips = [];
  const notes: Array<{ text: string; danger?: boolean }> = [];
  const lastAt = s.stats?.last_search_at ?? null;
  const lastStatus = (s.stats?.last_status ?? "ok") as SourceStatus;
  // Broken per Prowlarr, yet MSO's last search *after* the breakage was first seen still got results:
  // "Resultaten" stays the primary status, Prowlarr's warning is secondary. Results from before the
  // breakage never make a broken indexer look healthy.
  const resultsDespiteBroken = Boolean(s.prowlarr_broken && lastAt && lastStatus === "ok" && s.prowlarr_broken_since
    && new Date(lastAt) >= new Date(s.prowlarr_broken_since));
  const brokenPrimary = Boolean(s.prowlarr_broken) && !resultsDespiteBroken;
  if (brokenPrimary) {
    chips.push(<StatusChip key="broken" tone="danger" icon={TriangleAlert} title={t("idx.brokenHelp")}>{t("idx.broken")}</StatusChip>);
    for (const h of s.prowlarr_health ?? []) if (h.broken) notes.push({ text: h.message, danger: true });
    if (lastAt && lastStatus === "ok") notes.push({ text: t("idx.brokenEarlierResults", { when: relative(lastAt) }) });
  }
  if (s.library_relevant === false) {
    chips.push(<StatusChip key="irr" tone="neutral" icon={CircleSlash} title={t("idx.irrelevantHelp")}>{t("idx.irrelevant")}</StatusChip>);
    if (s.relevance_reason) notes.push({ text: s.relevance_reason });
  }
  if (s.direct) {
    const st = DIRECT_STATUS[s.direct.status] ?? DIRECT_STATUS.unknown;
    chips.push(<StatusChip key="d" tone={st.tone}>{t(st.label)}</StatusChip>);
    if (s.direct.backoff_until && new Date(s.direct.backoff_until) > new Date()) {
      chips.push(<StatusChip key="b" tone="warning">{t("idx.backoff", { when: relative(s.direct.backoff_until) })}</StatusChip>);
    }
  } else if (s.prowlarr?.disabled_till && new Date(s.prowlarr.disabled_till) > new Date()) {
    chips.push(<StatusChip key="p" tone="warning">{t("idx.disabledTill", { when: relative(s.prowlarr.disabled_till) })}</StatusChip>);
  }
  if (!s.direct) {
    if (lastAt && !brokenPrimary) {
      // the status of the LAST observation (ok / empty / timeout / error …), not "any error in the window"
      const st = SOURCE_STATUS[lastStatus] ?? SOURCE_STATUS.error;
      chips.push(<StatusChip key="s" tone={st.tone} icon={st.icon}>{t(st.label)}</StatusChip>);
      if (resultsDespiteBroken) {
        chips.push(<StatusChip key="pw" tone="warning" icon={TriangleAlert} title={t("idx.brokenHelp")}>{t("idx.brokenSecondary")}</StatusChip>);
        for (const h of s.prowlarr_health ?? []) if (h.broken) notes.push({ text: h.message });
      }
    } else if (!lastAt && s.prowlarr?.queries) {
      // never asked by MSO yet, but Prowlarr has measurements: say so instead of "not queried"
      const failed = s.prowlarr.failed_queries ?? 0;
      chips.push(<StatusChip key="pm" tone={failed > s.prowlarr.queries / 2 ? "warning" : "neutral"}>{t("idx.prowlarrMeasured")}</StatusChip>);
      notes.push({ text: t("idx.prowlarrQueries", { n: num(s.prowlarr.queries), failed: num(failed) }) });
    }
  }
  return (
    <div className="stack-sm">
      <span className="row wrap" style={{ gap: 4 }}>{chips.length ? chips : <StatusChip tone="neutral">{t("idx.never")}</StatusChip>}</span>
      {lastAt && !brokenPrimary ? <span className="cell-sub">{t("idx.lastSearch", { when: relative(lastAt) })}</span> : null}
      {notes.map((n) => <span key={n.text} className={n.danger ? "cell-sub t-danger" : "cell-sub"} style={{ maxWidth: 360 }}>{n.text}</span>)}
      {s.direct?.last_error ? <span className="cell-sub t-danger">{s.direct.last_error}</span> : null}
    </div>
  );
}

const SCOPE_SOURCE: Record<string, { label: MessageKey; help: MessageKey }> = {
  tag: { label: "idx.scopeSource.tag", help: "idx.scopeSource.tagHelp" },
  derived: { label: "idx.scopeSource.derived", help: "idx.scopeSource.derivedHelp" },
  default: { label: "idx.scopeSource.default", help: "idx.scopeSource.defaultHelp" },
};

/** Reliability is stored as 0..1; shown as a percentage with a bar (colour always with the number). */
function Reliability({ value }: { value: number }) {
  const { pct } = useI18n();
  const color = value < 0.6 ? "var(--mg-danger)" : value < 0.85 ? "var(--mg-warning)" : "var(--mg-success)";
  return <span className="confidence">{pct(value * 100)}<span className="bar"><span style={{ width: `${Math.round(value * 100)}%`, background: color }} /></span></span>;
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
            const msoLatency = s.stats?.median_latency_ms ?? null;
            const latency = msoLatency ?? s.prowlarr?.avg_response_ms ?? null;
            const scopeSrc = s.origin === "prowlarr" ? SCOPE_SOURCE[s.scope_source ?? (s.untagged ? "default" : "tag")] : undefined;
            const inactive = s.library_relevant === false || Boolean(s.prowlarr_broken);
            return (
              <tr key={s.id} data-inactive={inactive || undefined}>
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
                    {s.library_relevant === false ? <span className="t-3" style={{ fontSize: 12 }}>—</span>
                      : s.scopes.map((sc) => <span key={sc} className="tag">{t(`idx.scope.${sc}`)}</span>)}
                    {scopeSrc && s.library_relevant !== false ? (
                      <span className="t-3" style={{ fontSize: 11 }} title={t(scopeSrc.help)}>
                        {t(scopeSrc.label)}<span className="sr-only"> — {t(scopeSrc.help)}</span>
                      </span>
                    ) : null}
                    {s.untagged && s.library_relevant !== false ? <StatusChip tone="warning" title={t("idx.untaggedHelp")}>{t("idx.untagged")}</StatusChip> : null}
                  </span>
                </td>
                <td data-label={t("idx.col.status")}><StatusCell s={s} /></td>
                <td data-label={t("idx.col.reliability")}>
                  {s.reliability !== null ? <Reliability value={s.reliability} />
                    : <span className="t-3" style={{ fontSize: 12 }}>{t("idx.notEnoughData")}</span>}
                  {rel ? <div className="cell-sub">{t("idx.samples", { n: num(rel.samples), fp: num(rel.false_positives) })}</div> : null}
                </td>
                <td data-label={t("idx.col.latency")} className="mono" style={{ fontSize: 12 }}>
                  {latency !== null ? `${num(latency)} ms` : "—"}
                  {latency !== null ? <div className="cell-sub" style={{ fontFamily: "inherit" }}>{t(msoLatency !== null ? "idx.latency.mso" : "idx.latency.prowlarr")}</div> : null}
                </td>
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
