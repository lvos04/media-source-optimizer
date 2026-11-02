"use client";

import { CalendarClock, CircleHelp, Coins, Layers, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { DangerDialog, KpiCard, Timeline } from "@/components/mg/domain";
import { Button, Card, InlineState, PageHeader, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { Scenarios } from "@/components/mso/costs/scenarios";
import { PeriodPicker, ServiceAnalysis, type Period } from "@/components/mso/costs/service-analysis";
import { billingLabel, kindLabel, ServiceEditor } from "@/components/mso/costs/service-editor";
import { SimulationPanel } from "@/components/mso/costs/simulation-panel";
import { api, errorMessage } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import type { CostSummary, Service } from "@/lib/api/insights";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

export default function CostsPage() {
  const { t, num, money, date } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const isAdmin = can("admin");
  const services = useResource<{ items: Service[] }>("/costs/services");
  const summary = useResource<CostSummary>("/costs/summary");
  const [period, setPeriod] = useState<Period>("30");
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
          <Button variant="primary" icon={Plus} disabled={!isAdmin} title={isAdmin ? undefined : t("costs.adminOnly")} onClick={() => setEditing({ service: null })}>{t("costs.addService")}</Button>
        </>} />
      <InlineState tone="info" icon={CircleHelp}><span><strong>{t("costs.disclaimerTitle")}:</strong> {t("costs.disclaimer")}</span></InlineState>

      <section className="kpi-grid" aria-label={t("overview.kpis")} style={{ marginTop: 16 }}>
        <KpiCard label={t("costs.kpi.total")} icon={Coins} unavailable={!s || s.total.services === 0}
          value={s ? money(s.total.monthly_cents / 100, s.total.currency) : "—"} meta={s ? t("costs.kpi.totalMeta", { count: num(s.total.services) }) : null} />
        <KpiCard label={t("costs.kpi.unknown")} icon={CircleHelp} unavailable={!s} tone={s?.total.unknown.length ? "warning" : undefined}
          value={num(s?.total.unknown.length ?? 0)} meta={s?.total.unknown.length ? s.total.unknown.map((u) => u.label).join(", ") : t("costs.kpi.unknownMeta")} />
        <KpiCard label={t("costs.kpi.nextRenewal")} icon={CalendarClock} unavailable={!s}
          value={next ? date(next.date) : "—"} meta={next ? `${next.label} · ${t("costs.renewalIn", { days: num(next.days_left), amount: money(next.amount_cents / 100, next.currency) })}` : t("costs.kpi.noRenewal")} />
        <KpiCard label={t("costs.kpi.services")} icon={Layers} unavailable={!services.data}
          value={num(services.data?.items.length ?? 0)} meta={services.data ? `${num(services.data.items.filter((x) => !x.active).length)} × ${t("costs.inactive").toLowerCase()}` : null} />
      </section>

      <div className="grid" style={{ marginTop: 16 }}>
        <Card className="span-12" title={t("costs.services")} icon={Coins} flush>
          <ResourceView resource={services} endpoint="GET /api/v1/costs/services" isEmpty={(d) => d.items.length === 0}
            empty={<StateView kind="empty" title={t("costs.noServices")} body={t("costs.noServicesBody")}
              action={<Button variant="primary" icon={Plus} disabled={!isAdmin} title={isAdmin ? undefined : t("costs.adminOnly")} onClick={() => setEditing({ service: null })}>{t("costs.addService")}</Button>} />}>
            {(d) => (
              <div className="table-wrap">
                <table className="table table--stack">
                  <thead><tr>
                    <th>{t("costs.col.service")}</th><th>{t("costs.col.amount")}</th><th>{t("costs.col.monthly")}</th>
                    <th>{t("costs.col.renewal")}</th><th>{t("costs.col.source")}</th><th>{t("costs.col.backbone")}</th><th><span className="sr-only">{t("costs.col.actions")}</span></th>
                  </tr></thead>
                  <tbody>{d.items.map((x) => (
                    <tr key={x.id} aria-selected={selected?.id === x.id}>
                      <td className="cell-primary">
                        <div>{x.label}</div>
                        <div className="t-3" style={{ fontSize: 12 }}>{t(kindLabel(x.kind))}{!x.active ? <> · <StatusChip tone="neutral">{t("costs.inactive")}</StatusChip></> : null}</div>
                      </td>
                      <td data-label={t("costs.col.amount")}>{x.billing === "free" ? t("costs.billing.free") : `${money(x.amount_cents / 100, x.currency)} ${t(billingLabel(x.billing))}`}</td>
                      <td data-label={t("costs.col.monthly")}>{x.monthly?.monthly_cents == null ? <span className="t-3" title={x.monthly?.reason}>—</span> : money(x.monthly.monthly_cents / 100, x.currency)}</td>
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
