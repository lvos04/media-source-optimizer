"use client";

import { Check, X } from "lucide-react";
import { useState } from "react";

import { Button, InlineState, StatusChip } from "@/components/mg/primitives";
import { billingLabel, BILLINGS } from "@/components/mso/costs/service-editor";
import { api, errorMessage } from "@/lib/api/client";
import type { Billing, PriceInput, Service } from "@/lib/api/insights";
import { useI18n } from "@/lib/i18n";

/** "Prijs ontbreekt" marker + inline edit of price, period (billing) and renewal date only
 *  (PATCH /costs/services/{id}/price, admin). */
export function PriceInline({ service, canEdit, onSaved }: { service: Service; canEdit: boolean; onSaved: () => void }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [billing, setBilling] = useState<Billing>(service.billing === "free" && service.price_missing ? "monthly" : service.billing);
  const [amount, setAmount] = useState(service.price_missing ? "" : (service.amount_cents / 100).toFixed(2));
  const [renewal, setRenewal] = useState(service.renewal_date ?? "");
  const [blockSize, setBlockSize] = useState(service.block_size_gb ? String(service.block_size_gb) : "");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) {
    return (
      <div className="stack-sm" style={{ alignItems: "flex-start" }}>
        {service.price_missing ? <StatusChip tone="warning">{t("costs.priceMissing")}</StatusChip> : null}
        <Button size="sm" variant={service.price_missing ? "primary" : "ghost"} disabled={!canEdit} title={canEdit ? undefined : t("costs.adminOnly")}
          onClick={() => setOpen(true)}>{service.price_missing ? t("costs.fillPrice") : t("costs.editPrice")}</Button>
      </div>
    );
  }

  const save = async () => {
    setError(null);
    const value = amount.trim() === "" ? (billing === "free" ? 0 : NaN) : Number(amount.replace(",", "."));
    if (!Number.isFinite(value) || value < 0) { setError(t("costs.priceInvalid")); return; }
    const size = blockSize.trim() === "" ? null : Number(blockSize.replace(",", "."));
    if (billing === "block" && (!size || size <= 0)) { setError(t("costs.priceBlockSize")); return; }
    const body: PriceInput = {
      billing, amount_cents: billing === "free" ? 0 : Math.round(value * 100), renewal_date: renewal || null,
      ...(billing === "block" ? { block_size_gb: size } : {}),
    };
    setPending(true);
    try {
      await api.patch(`/costs/services/${service.id}/price`, body);
      setOpen(false);
      onSaved();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPending(false);
    }
  };

  const id = `price-${service.id}`;
  return (
    <form className="stack-sm" style={{ minWidth: 220 }} onSubmit={(e) => { e.preventDefault(); void save(); }} aria-label={t("costs.fillPrice")}>
      <div className="row wrap" style={{ gap: 6 }}>
        <label className="sr-only" htmlFor={`${id}-amount`}>{t("costs.editor.amount", { currency: service.currency })}</label>
        <input id={`${id}-amount`} className="input" inputMode="decimal" style={{ width: 96 }} placeholder="0,00" value={amount}
          disabled={billing === "free"} onChange={(e) => setAmount(e.target.value)} autoFocus />
        <label className="sr-only" htmlFor={`${id}-billing`}>{t("costs.editor.billing")}</label>
        <select id={`${id}-billing`} className="select" style={{ width: "auto" }} value={billing} onChange={(e) => setBilling(e.target.value as Billing)}>
          {BILLINGS.map((b) => <option key={b} value={b}>{t(billingLabel(b))}</option>)}
        </select>
      </div>
      {billing === "block" ? (
        <div className="row wrap" style={{ gap: 6 }}>
          <label className="t-2" htmlFor={`${id}-size`} style={{ fontSize: 12 }}>{t("costs.editor.blockSize")}</label>
          <input id={`${id}-size`} className="input" inputMode="decimal" style={{ width: 96 }} value={blockSize} onChange={(e) => setBlockSize(e.target.value)} />
        </div>
      ) : null}
      <div className="row wrap" style={{ gap: 6 }}>
        <label className="t-2" htmlFor={`${id}-renewal`} style={{ fontSize: 12 }}>{t("costs.editor.renewal")}</label>
        <input id={`${id}-renewal`} className="input" type="date" style={{ width: "auto" }} value={renewal} onChange={(e) => setRenewal(e.target.value)} />
      </div>
      {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
      <div className="row" style={{ gap: 6 }}>
        <Button size="sm" variant="primary" type="submit" icon={Check} pending={pending}>{t("action.save")}</Button>
        <Button size="sm" variant="ghost" icon={X} onClick={() => { setOpen(false); setError(null); }}>{t("action.cancel")}</Button>
      </div>
    </form>
  );
}
