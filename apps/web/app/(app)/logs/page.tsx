"use client";

import { FileClock, History, Search } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { Timeline } from "@/components/mg/domain";
import { Button, Card, Modal, PageHeader } from "@/components/mg/primitives";
import { ErrorState, StateView } from "@/components/mg/states";
import { api, ApiError } from "@/lib/api/client";
import type { AuditEntry, AuditPage } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";

const LIMITS = [50, 100, 200];

export default function LogsPage() {
  return <Suspense><AuditView /></Suspense>;
}

function DetailValue({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "") return <span className="t-3">—</span>;
  if (typeof value === "string") return <p className="pre-wrap" style={{ margin: 0, fontSize: 13 }}>{value}</p>;
  return <pre className="mono pre-wrap" style={{ margin: 0, fontSize: 11.5, maxHeight: 320, overflow: "auto" }}>{JSON.stringify(value, null, 2)}</pre>;
}

function AuditView() {
  const { t, tp, dateTime, relative } = useI18n();
  const router = useRouter();
  const params = useSearchParams();
  const [action, setAction] = useState(params.get("action") ?? "");
  const [applied, setApplied] = useState(params.get("action") ?? "");
  const [limit, setLimit] = useState(100);
  const first = useResource<AuditPage>("/audit", { limit, action: applied || undefined });
  const [more, setMore] = useState<AuditEntry[]>([]);
  const [cursor, setCursor] = useState<AuditPage["next_before_id"]>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<ApiError | null>(null);
  const [selected, setSelected] = useState<AuditEntry | null>(null);

  // a fresh first page resets pagination
  useEffect(() => {
    setMore([]);
    setCursor(first.data?.next_before_id ?? null);
  }, [first.data]);

  const rows = [...(first.data?.items ?? []), ...more];

  const apply = (e: React.FormEvent) => {
    e.preventDefault();
    const value = action.trim();
    setApplied(value);
    const qs = new URLSearchParams(params.toString());
    if (value) qs.set("action", value); else qs.delete("action");
    router.replace(`/logs${qs.toString() ? `?${qs.toString()}` : ""}`, { scroll: false });
  };

  const loadMore = async () => {
    if (cursor === null || cursor === undefined) return;
    setLoadingMore(true);
    setMoreError(null);
    try {
      const page = await api.get<AuditPage>("/audit", { limit, action: applied || undefined, before_id: cursor });
      setMore((m) => [...m, ...page.items]);
      setCursor(page.next_before_id);
    } catch (e) {
      setMoreError(e instanceof ApiError ? e : new ApiError("server", 0, String(e), "/audit"));
    } finally {
      setLoadingMore(false);
    }
  };

  const related = selected ? rows.filter((r) => r.id !== selected.id && ((selected.change_id !== null && selected.change_id !== undefined && r.change_id === selected.change_id) || (selected.target && r.target === selected.target))) : [];

  return (
    <>
      <PageHeader title={t("nav.logs")} sub={t("logs.sub")} />
      <Card title={t("logs.audit")} icon={FileClock} flush sub={first.data ? tp("logs.shown", rows.length) : undefined}
        actions={<Button size="sm" onClick={() => void first.reload()}>{t("action.reload")}</Button>}>
        <form className="toolbar" style={{ padding: "12px 16px" }} onSubmit={apply} role="search">
          <div className="search-field" style={{ flex: "1 1 260px", maxWidth: 420 }}>
            <Search className="ico" aria-hidden="true" />
            <input className="input" type="search" aria-label={t("logs.filterAction")} placeholder={t("logs.filterAction")} value={action} onChange={(e) => setAction(e.target.value)} data-testid="audit-filter" />
          </div>
          <select className="select" style={{ width: "auto" }} aria-label={t("logs.limit")} value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
            {LIMITS.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
          <Button type="submit" size="sm" variant="primary">{t("action.filter")}</Button>
          {applied ? <Button size="sm" variant="ghost" onClick={() => { setAction(""); setApplied(""); router.replace("/logs", { scroll: false }); }}>{t("action.clear")}</Button> : null}
        </form>
        {first.loading ? <StateView kind="loading" />
          : first.error && !first.data ? <ErrorState error={first.error} onRetry={() => void first.reload()} endpoint="GET /api/v1/audit" />
            : rows.length === 0 ? <StateView kind="empty" title={applied ? t("logs.emptyFiltered", { action: applied }) : t("logs.empty")} />
              : (
                <>
                  {first.stale ? <div className="stale-mark" role="status" style={{ padding: "0 16px 8px" }}>{t("state.stale", { age: relative(first.fetchedAt) })}</div> : null}
                  <div className="table-wrap">
                    <table className="table table--compact table--stack">
                      <thead><tr><th>{t("logs.when")}</th><th>{t("logs.who")}</th><th>{t("logs.what")}</th><th>{t("logs.target")}</th><th>{t("logs.summary")}</th><th><span className="sr-only">{t("action.details")}</span></th></tr></thead>
                      <tbody>
                        {rows.map((r) => (
                          <tr key={r.id}>
                            <td className="cell-primary num" style={{ whiteSpace: "nowrap" }}>{dateTime(r.ts)}</td>
                            <td data-label={t("logs.who")} className="mono">{r.actor ?? t("logs.system")}</td>
                            <td data-label={t("logs.what")}><span className="tag">{r.action}</span></td>
                            <td data-label={t("logs.target")} className="mono" style={{ maxWidth: 240, overflowWrap: "anywhere" }}>{r.target ?? "—"}{r.change_id !== null && r.change_id !== undefined ? <div className="cell-sub">{t("logs.change")} #{String(r.change_id)}</div> : null}</td>
                            <td data-label={t("logs.summary")}>{r.summary ?? "—"}</td>
                            <td><Button size="sm" variant="ghost" onClick={() => setSelected(r)}>{t("action.details")}</Button></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {moreError ? <ErrorState error={moreError} compact onRetry={() => void loadMore()} endpoint="GET /api/v1/audit" /> : null}
                  {cursor !== null && cursor !== undefined ? (
                    <div className="card-footer"><span /><Button size="sm" pending={loadingMore} onClick={() => void loadMore()}>{t("action.loadMore")}</Button></div>
                  ) : null}
                </>
              )}
      </Card>

      <Modal open={Boolean(selected)} onClose={() => setSelected(null)} icon={History} title={selected ? t("logs.detailTitle", { id: String(selected.id) }) : ""}
        description={selected?.summary ?? undefined} footer={<Button onClick={() => setSelected(null)}>{t("action.close")}</Button>}>
        {selected ? (
          <div className="stack">
            <Timeline items={[
              { title: <><span className="tag">{selected.action}</span> {selected.target ? <span className="mono">{selected.target}</span> : null}</>, meta: `${dateTime(selected.ts)} · ${selected.actor ?? t("logs.system")}`, tone: "accent" },
              ...related.slice(0, 8).map((r) => ({ title: <><span className="tag">{r.action}</span> {r.summary ?? ""}</>, meta: `${dateTime(r.ts)} · ${r.actor ?? t("logs.system")}`, tone: "neutral" as const })),
            ]} />
            {related.length ? <p className="hint" style={{ margin: 0 }}>{t("logs.related")} — {t("logs.relatedHint")}</p> : null}
            <div><span className="eyebrow">{t("logs.detail")}</span><div style={{ marginTop: 6 }}><DetailValue value={selected.detail} /></div></div>
          </div>
        ) : null}
      </Modal>
    </>
  );
}
