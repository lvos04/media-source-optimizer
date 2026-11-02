"use client";

import { Coins } from "lucide-react";
import { useState } from "react";

import { Button, Field, Modal, Switch } from "@/components/mg/primitives";
import { api, errorMessage } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import type { Billing, Service, ServiceInput, ServiceKind, SourceOption } from "@/lib/api/insights";
import { useI18n, type MessageKey } from "@/lib/i18n";

export const KINDS: ServiceKind[] = ["usenet_provider", "block_account", "nzb_indexer", "torrent_indexer", "tracker", "vpn", "other"];
export const BILLINGS: Billing[] = ["monthly", "yearly", "block", "free"];
export const kindLabel = (k: string): MessageKey => `costs.kind.${k}` as MessageKey;
export const billingLabel = (b: string): MessageKey => `costs.billing.${b}` as MessageKey;

const EMPTY: ServiceInput = {
  label: "", kind: "usenet_provider", billing: "monthly", amount_cents: 0, currency: "EUR", block_size_gb: null,
  block_used_gb: null, renewal_date: null, notes: "", active: true, source_id: null, backbone: null,
};

function toInput(s: Service | null): ServiceInput {
  if (!s) return { ...EMPTY };
  return {
    label: s.label, kind: s.kind, billing: s.billing, amount_cents: s.amount_cents, currency: s.currency,
    block_size_gb: s.block_size_gb, block_used_gb: s.block_used_gb, renewal_date: s.renewal_date, notes: s.notes,
    active: s.active, source_id: s.source_id, backbone: s.backbone,
  };
}

/** Create/edit a service (POST /costs/services, PUT /costs/services/{id}); admin only. */
export function ServiceEditor({ open, service, onClose, onSaved }: {
  open: boolean; service: Service | null; onClose: () => void; onSaved: (s: Service) => void;
}) {
  const { t } = useI18n();
  const [form, setForm] = useState<ServiceInput>(() => toInput(service));
  const [amount, setAmount] = useState(() => (service ? (service.amount_cents / 100).toFixed(2) : ""));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sources = useResource<{ items: SourceOption[] }>(open ? "/costs/sources" : null);
  const set = <K extends keyof ServiceInput>(k: K, v: ServiceInput[K]) => setForm((f) => ({ ...f, [k]: v }));
  const num = (v: string) => (v.trim() === "" ? null : Number(v.replace(",", ".")));

  const save = async () => {
    setError(null);
    const cents = Math.round((num(amount) ?? 0) * 100);
    if (!form.label.trim()) { setError(t("costs.editor.label")); return; }
    if (!Number.isFinite(cents) || cents < 0) { setError(t("costs.editor.amount", { currency: form.currency })); return; }
    setPending(true);
    try {
      const body = { ...form, amount_cents: form.billing === "free" ? 0 : cents };
      const saved = service ? await api.put<Service>(`/costs/services/${service.id}`, body) : await api.post<Service>("/costs/services", body);
      onSaved(saved);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPending(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} icon={Coins} title={service ? t("costs.editor.edit") : t("costs.editor.new")}
      footer={<><Button onClick={onClose}>{t("action.cancel")}</Button><Button variant="primary" pending={pending} onClick={() => void save()}>{t("action.save")}</Button></>}>
      <div className="stack">
        <Field label={t("costs.editor.label")} htmlFor="svc-label">
          <input id="svc-label" className="input" value={form.label} maxLength={120} onChange={(e) => set("label", e.target.value)} data-autofocus />
        </Field>
        <div className="grid" style={{ gap: 12 }}>
          <div className="span-6">
            <Field label={t("costs.editor.kind")} htmlFor="svc-kind">
              <select id="svc-kind" className="select" value={form.kind} onChange={(e) => set("kind", e.target.value as ServiceKind)}>
                {KINDS.map((k) => <option key={k} value={k}>{t(kindLabel(k))}</option>)}
              </select>
            </Field>
          </div>
          <div className="span-6">
            <Field label={t("costs.editor.billing")} htmlFor="svc-billing">
              <select id="svc-billing" className="select" value={form.billing} onChange={(e) => set("billing", e.target.value as Billing)}>
                {BILLINGS.map((b) => <option key={b} value={b}>{t(billingLabel(b))}</option>)}
              </select>
            </Field>
          </div>
          {form.billing !== "free" ? (
            <>
              <div className="span-8">
                <Field label={t("costs.editor.amount", { currency: form.currency })} hint={t("costs.editor.amountHint")} htmlFor="svc-amount">
                  <input id="svc-amount" className="input num" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
                </Field>
              </div>
              <div className="span-4">
                <Field label={t("costs.editor.currency")} htmlFor="svc-cur">
                  <input id="svc-cur" className="input mono" maxLength={3} value={form.currency} onChange={(e) => set("currency", e.target.value.toUpperCase())} />
                </Field>
              </div>
            </>
          ) : null}
          {form.billing === "block" || form.kind === "block_account" ? (
            <>
              <div className="span-6">
                <Field label={t("costs.editor.blockSize")} htmlFor="svc-bsize">
                  <input id="svc-bsize" className="input num" inputMode="decimal" value={form.block_size_gb ?? ""} onChange={(e) => set("block_size_gb", num(e.target.value))} />
                </Field>
              </div>
              <div className="span-6">
                <Field label={t("costs.editor.blockUsed")} htmlFor="svc-bused">
                  <input id="svc-bused" className="input num" inputMode="decimal" value={form.block_used_gb ?? ""} onChange={(e) => set("block_used_gb", num(e.target.value))} />
                </Field>
              </div>
            </>
          ) : null}
          <div className="span-6">
            <Field label={t("costs.editor.renewal")} htmlFor="svc-renewal">
              <input id="svc-renewal" type="date" className="input" value={form.renewal_date ?? ""} onChange={(e) => set("renewal_date", e.target.value || null)} />
            </Field>
          </div>
          <div className="span-6">
            <Field label={t("costs.editor.backbone")} hint={t("costs.editor.backboneHint")} htmlFor="svc-backbone">
              <input id="svc-backbone" className="input" maxLength={120} value={form.backbone ?? ""} onChange={(e) => set("backbone", e.target.value || null)} />
            </Field>
          </div>
        </div>
        <Field label={t("costs.editor.source")} hint={t("costs.editor.sourceHint")} htmlFor="svc-source">
          <select id="svc-source" className="select" value={form.source_id ?? ""} onChange={(e) => set("source_id", e.target.value || null)} disabled={sources.loading}>
            <option value="">{t("costs.editor.sourceNone")}</option>
            {(sources.data?.items ?? []).map((s) => <option key={s.id} value={s.id}>{s.label} ({s.id})</option>)}
          </select>
        </Field>
        <Field label={t("costs.editor.notes")} htmlFor="svc-notes">
          <textarea id="svc-notes" className="textarea" maxLength={2000} value={form.notes} onChange={(e) => set("notes", e.target.value)} />
        </Field>
        <div className="row-between">
          <div><div className="label" style={{ marginBottom: 0 }}>{t("costs.editor.active")}</div><p className="hint" style={{ marginTop: 2 }}>{t("costs.editor.activeHint")}</p></div>
          <Switch checked={form.active} onChange={(v) => set("active", v)} label={t("costs.editor.active")} />
        </div>
        {error ? <div className="field-error" role="alert">{error}</div> : null}
      </div>
    </Modal>
  );
}
