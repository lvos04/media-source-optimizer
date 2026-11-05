"use client";

import { CalendarClock, CircleHelp, Coins, DownloadCloud, Layers, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { DangerDialog, KpiCard, Timeline } from "@/components/mg/domain";
import { Button, Card, InlineState, PageHeader, StatusChip, Switch } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { PriceInline } from "@/components/mso/costs/price-inline";
import { Scenarios } from "@/components/mso/costs/scenarios";
import { PeriodPicker, ServiceAnalysis, type Period } from "@/components/mso/costs/service-analysis";
import { billingLabel, kindLabel, ServiceEditor } from "@/components/mso/costs/service-editor";
import { SimulationPanel } from "@/components/mso/costs/simulation-panel";
import { api, errorMessage } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import type { CostSummary, Service, ServiceList, ServiceMetrics } from "@/lib/api/insights";
import { useI18n, type Formatters } from "@/lib/i18n";
import { useSession } from "@/lib/session";

/** Cost per successful GB / download (entered cost ÷ observed successes in the period). */
function RatioCell({ m, currency, f }: { m: ServiceMetrics | null | undefined; currency: string; f: Formatters }) {
  if (!m || !m.available) return <span className="t-3" title={m?.reason}>{m?.reason ?? "—"}</span>;
  return (
    <div className="stack-sm" style={{ gap: 2 }}>
      <div>{m.per_gb_cents != null ? f.t("costs.perGbValue", { value: f.money(m.per_gb_cents / 100, currency) }) : <span className="t-3">{f.t("costs.perGbNone")}</span>}</div>
      <div>{m.per_success_cents != null ? f.t("costs.perSuccessValue", { value: f.money(m.per_success_cents / 100, currency) }) : null}</div>
      <div className="t-3" style={{ fontSize: 12 }}>{f.t("costs.successesValue", { n: f.num(m.successful_downloads ?? 0), gb: f.num(m.successful_gb ?? 0, 1) })}</div>
      {m.cost_reason ? <div className="t-3" style={{ fontSize: 12 }}>{m.cost_reason}</div> : null}
    </div>
  );
}

function UniqueCell({ m, f }: { m: ServiceMetrics | null | undefined; f: Formatters }) {
  const u = m?.unique;
  if (!u) return <span className="t-3">—</span>;
  if (!u.available) return <span className="t-3" style={{ fontSize: 12 }}>{u.reason}</span>;
  return (
    <div title={u.note}>
      {f.t("costs.uniqueValue", { pct: f.pct((u.pct ?? 0) * 100, 1) })}
      <div className="t-3" style={{ fontSize: 12 }}>{f.t("costs.uniqueSample", { n: f.num(u.unique_articles ?? 0), total: f.num(u.compared_articles ?? 0) })}{u.confidence ? ` · ${u.confidence}` : ""}</div>
    </div>
  );
}

export default function CostsPage() {
  const f = useI18n();
  const { t, num, money, date } = f;
  const { can } = useSession();
  const toast = useToast();
  const isAdmin = can("admin");
  const [period, setPeriod] = useState<Period>("30");
  const services = useResource<ServiceList>("/costs/services", { period_days: Number(period) });
  // public torrent indexers are free and never ask for a price: hidden unless the user wants to see them
  const [showFree, setShowFree] = useState(false);
  const freeCount = services.data?.items.filter((x) => x.billing === "free").length ?? 0;
  const shown = (items: Service[]) => (showFree ? items : items.filter((x) => x.billing !== "free"));
  const summary = useResource<CostSummary>("/costs/summary");
  const [importing, setImporting] = useState(false);
  const runImport = async () => {
    setImporting(true);
    try {
      const r = await api.post<{ created: number; sab_providers_created: number }>("/costs/services/import");
      toast.push("success", t("costs.imported", { count: num(r.created) }));
      reload();
    } catch (e) {
      toast.push("danger", errorMessage(e));
    } finally {
      setImporting(false);
    }
  };
  const [selected, setSelected] = useState<Service | null>(null);
  const [editing, setEditing] = useState<{ service: Service | null } | null>(null);
  const [deleting, setDeleting] = useState<Service | null>(null);
  const [delPending, setDelPending] = useState(false);
  const [delError, setDelError] = useState<string | null>(null);
  const reload = () => { void services.reload(); void summary.reload(); };
  const s = summary.data;
  const next = s?.renewals.find((r) => r.days_left >= 0);

  const doDelete = async () => {
    if (!deleting) return;
    setDelPending(true);
    setDelError(null);
    try {
      await api.del(`/costs/services/${deleting.id}`);
      toast.push("success", t("costs.deleted"));
      if (selected?.id === deleting.id) setSelected(null);
      setDeleting(null);
      reload();
    } catch (e) {
      setDelError(errorMessage(e));
    } finally {
      setDelPending(false);
    }
  };

  return (
    <>
      <PageHeader title={t("nav.costs")} sub={t("costs.sub")}
        actions={<>
          <PeriodPicker value={period} onChange={setPeriod} />
          <Button icon={DownloadCloud} pending={importing} disabled={!isAdmin} title={isAdmin ? t("costs.importHint") : t("costs.adminOnly")} onClick={() => void runImport()}>{t("costs.import")}</Button>
          <Button variant="primary" icon={Plus} disabled={!isAdmin} title={isAdmin ? undefined : t("costs.adminOnly")} onClick={() => setEditing({ service: null })}>{t("costs.addService")}</Button>
        </>} />
      <InlineState tone="info" icon={CircleHelp}><span><strong>{t("costs.disclaimerTitle")}:</strong> {t("costs.disclaimer")}</span></InlineState>
      {services.data?.price_missing ? (
        <div style={{ marginTop: 12 }}><InlineState tone="warning">{t("costs.priceMissingBanner", { count: num(services.data.price_missing) })}</InlineState></div>
      ) : null}

      <section className="kpi-grid" aria-label={t("overview.kpis")} style={{ marginTop: 16 }}>
        <KpiCard label={t("costs.kpi.total")} icon={Coins} unavailable={!s || s.total.services === 0}
          value={s ? money(s.total.monthly_cents / 100, s.total.currency) : "—"} meta={s ? t("costs.kpi.totalMeta", { count: num(s.total.services) }) : null} />
        <KpiCard label={t("costs.kpi.unknown")} icon={CircleHelp} unavailable={!s} tone={s?.total.unknown.length ? "warning" : undefined}
          value={num(s?.total.unknown.length ?? 0)} meta={s?.total.unknown.length ? s.total.unknown.map((u) => u.label).join(", ") : t("costs.kpi.unknownMeta")} />
        <KpiCard label={t("costs.kpi.nextRenewal")} icon={CalendarClock} unavailable={!s}
          value={next ? date(next.date) : "—"} meta={next ? `${next.label} · ${t("costs.renewalIn", { days: num(next.days_left), amount: money(next.amount_cents / 100, next.currency) })}` : t("costs.kpi.noRenewal")} />
        <KpiCard label={t("costs.kpi.services")} icon={Layers} unavailable={!services.data}
          value={num((services.data?.items.length ?? 0) - freeCount)}
          meta={services.data ? t("costs.kpi.servicesMeta", { paid: num(services.data.items.length - freeCount), free: num(freeCount), inactive: num(services.data.items.filter((x) => !x.active).length) }) : null} />
      </section>

      <div className="grid" style={{ marginTop: 16 }}>
        <Card className="span-12" title={t("costs.services")} icon={Coins} flush
          actions={freeCount ? <label className="row" style={{ gap: 8, fontSize: 12.5 }}><Switch checked={showFree} label={t("costs.showFree")} onChange={setShowFree} /><span>{t("costs.showFree")} ({num(freeCount)})</span></label> : undefined}>
          {freeCount && !showFree ? <p className="hint" style={{ margin: 0, padding: "10px 16px 0" }}>{t("costs.freeHidden", { count: num(freeCount) })}</p> : null}
          <ResourceView resource={services} endpoint="GET /api/v1/costs/services" isEmpty={(d) => shown(d.items).length === 0}
            empty={services.data?.items.length ? <StateView kind="empty" title={t("costs.noPaidServices")} body={t("costs.noPaidServicesBody")} /> : <StateView kind="empty" title={t("costs.noServices")} body={t("costs.noServicesBody")}
              action={<Button variant="primary" icon={Plus} disabled={!isAdmin} title={isAdmin ? undefined : t("costs.adminOnly")} onClick={() => setEditing({ service: null })}>{t("costs.addService")}</Button>} />}>
            {(d) => (
              <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel (horizontaal scrollbaar)">
                <table className="table table--stack">
                  <thead><tr>
                    <th>{t("costs.col.service")}</th><th>{t("costs.col.amount")}</th><th>{t("costs.col.monthly")}</th>
                    <th>{t("costs.col.ratio")}</th><th>{t("costs.col.unique")}</th>
                    <th>{t("costs.col.renewal")}</th><th>{t("costs.col.source")}</th><th>{t("costs.col.backbone")}</th><th><span className="sr-only">{t("costs.col.actions")}</span></th>
                  </tr></thead>
                  <tbody>{shown(d.items).map((x) => (
                    <tr key={x.id} aria-selected={selected?.id === x.id}>
                      <td className="cell-primary">
                        <div>{x.label}</div>
                        <div className="t-3" style={{ fontSize: 12 }}>{t(kindLabel(x.kind))}{x.origin === "import" ? ` · ${t("costs.importedChip")}` : ""}{!x.active ? <> · <StatusChip tone="neutral">{t("costs.inactive")}</StatusChip></> : null}</div>
                      </td>
                      <td data-label={t("costs.col.amount")}>
                        {x.price_missing ? null : <div>{x.billing === "free" ? t("costs.billing.free") : `${money(x.amount_cents / 100, x.currency)} ${t(billingLabel(x.billing))}`}</div>}
                        <PriceInline key={`${x.id}-${x.amount_cents}-${x.billing}-${x.renewal_date ?? ""}`} service={x} canEdit={isAdmin} onSaved={() => { toast.push("success", t("costs.priceSaved")); reload(); }} />
                      </td>
                      <td data-label={t("costs.col.monthly")}>{x.monthly?.monthly_cents == null ? <span className="t-3" title={x.monthly?.reason}>—</span> : money(x.monthly.monthly_cents / 100, x.currency)}</td>
                      <td data-label={t("costs.col.ratio")}><RatioCell m={x.metrics} currency={x.currency} f={f} /></td>
                      <td data-label={t("costs.col.unique")}><UniqueCell m={x.metrics} f={f} /></td>
                      <td data-label={t("costs.col.renewal")}>{x.renewal_date ? date(x.renewal_date) : "—"}</td>
                      <td data-label={t("costs.col.source")}>{x.source_label ?? (x.source_id ? <span className="mono">{x.source_id}</span> : <span className="t-3">—</span>)}</td>
                      <td data-label={t("costs.col.backbone")}>{x.backbone ?? <span className="t-3">{t("costs.backboneUnknown")}</span>}</td>
                      <td data-label={t("costs.col.actions")}>
                        <div className="toolbar" style={{ justifyContent: "flex-end" }}>
                          <Button size="sm" variant={selected?.id === x.id ? "primary" : "secondary"} onClick={() => setSelected(x)}>{t("costs.analyse")}</Button>
                          <Button size="sm" icon={Pencil} iconOnly disabled={!isAdmin} title={isAdmin ? t("action.edit") : t("costs.adminOnly")} onClick={() => setEditing({ service: x })}>{t("action.edit")}</Button>
                          <Button size="sm" icon={Trash2} iconOnly variant="ghost" disabled={!isAdmin} title={isAdmin ? t("costs.delete") : t("costs.adminOnly")} onClick={() => setDeleting(x)}>{t("costs.delete")}</Button>
                        </div>
                      </td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </ResourceView>
        </Card>

        {selected ? (
          <>
            <ServiceAnalysis key={`a-${selected.id}-${period}`} serviceId={selected.id} label={selected.label} periodDays={Number(period)} />
            <SimulationPanel key={`s-${selected.id}-${period}`} serviceId={selected.id} periodDays={Number(period)} />
          </>
        ) : services.data?.items.length ? (
          <Card className="span-12"><StateView kind="empty" compact title={t("costs.analysisPick")} body={t("costs.analysisPickBody")} /></Card>
        ) : null}

        <Scenarios key={`sc-${period}-${services.data?.items.length ?? 0}`} periodDays={Number(period)} />

        <Card className="span-12" title={t("costs.renewals")} icon={CalendarClock}>
          <ResourceView resource={summary} endpoint="GET /api/v1/costs/summary" compact isEmpty={(d) => d.renewals.length === 0}
            empty={<StateView kind="empty" compact title={t("costs.renewalsNone")} />}>
            {(d) => (
              <Timeline items={d.renewals.map((r) => ({
                title: <>{r.label} · {date(r.date)}</>,
                meta: r.days_left >= 0 ? t("costs.renewalIn", { days: num(r.days_left), amount: money(r.amount_cents / 100, r.currency) }) : t("costs.renewalPast", { days: num(-r.days_left) }),
                tone: r.days_left < 0 ? "danger" : r.days_left <= d.reminder_days ? "warning" : "neutral",
                icon: CalendarClock,
              }))} />
            )}
          </ResourceView>
        </Card>
      </div>

      {editing ? (
        <ServiceEditor open service={editing.service} onClose={() => setEditing(null)}
          onSaved={(saved) => { setEditing(null); toast.push("success", t("costs.saved")); if (selected?.id === saved.id) setSelected(saved); reload(); }} />
      ) : null}
      <DangerDialog open={!!deleting} onClose={() => { setDeleting(null); setDelError(null); }} onConfirm={() => void doDelete()}
        title={t("costs.deleteTitle")} description={t("costs.deleteBody")} confirmLabel={t("costs.delete")} pending={delPending} error={delError} icon={Trash2}
        facts={deleting ? [{ label: t("costs.col.service"), value: deleting.label }] : undefined} />
    </>
  );
}
