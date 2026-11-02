// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/components/mg/setting-field.tsx — internally versioned copy, see PROVENANCE.md
// (MSO: adapted to the flat settings contract of GET /api/v1/settings — no scopes/inheritance trace;
// adds risk chip, write-only secrets and `options` that may be strings or {value,label}.)
"use client";

import { Lock, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button, StatusChip, Switch } from "@/components/mg/primitives";
import { RiskChip } from "@/components/mso/risk-chip";
import type { SettingDef, SettingOption } from "@/lib/api/contracts";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export const NO_DRAFT = Symbol("no-draft");
export type Draft = unknown | typeof NO_DRAFT;

export function optionList(def: SettingDef): Array<{ value: string; label: string }> {
  return (def.options ?? []).map((o: SettingOption) => (typeof o === "string" ? { value: o, label: o } : { value: String(o.value), label: o.label ?? String(o.value) }));
}

/** Secrets are write-only: the backend returns a status ("ingesteld"/"leeg") or a truthy marker, never the value. */
export function secretIsSet(value: unknown): boolean {
  if (value === "leeg" || value === "" || value === null || value === undefined || value === false) return false;
  return true;
}

export function formatValue(def: SettingDef, value: unknown, yes: string, no: string): string {
  if (def.type === "secret") return secretIsSet(value) ? "ingesteld" : "leeg";
  if (value === null || value === undefined || value === "") return "—";
  if (def.type === "bool") return value ? yes : no;
  if (def.type === "enum") return optionList(def).find((o) => o.value === String(value))?.label ?? String(value);
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return `${String(value)}${def.unit ? ` ${def.unit}` : ""}`;
}

/**
 * One registered backend setting: name, key, help, risk, default, range/unit, restart/locked flags,
 * a type-appropriate control, server validation error, undo and "restore default" (as a draft).
 */
export function SettingField({ def, value, draft, error, onDraft, disabled }: {
  def: SettingDef; value: unknown; draft: Draft; error?: string | null; onDraft: (value: Draft) => void; disabled?: boolean;
}) {
  const { t } = useI18n();
  const current = draft === NO_DRAFT ? value ?? def.default : draft;
  const dirty = draft !== NO_DRAFT;
  const locked = def.locked;
  const inputId = `setting-${def.key.replace(/[^a-z0-9]/gi, "-")}`;
  const fmt = (v: unknown) => formatValue(def, v, t("settings.yes"), t("settings.no"));
  const isDefault = def.type === "secret" || JSON.stringify(value ?? def.default) === JSON.stringify(def.default);

  return (
    <div className={cn("setting", dirty && "setting--dirty", error && "setting--error", locked && "setting--locked")} data-setting={def.key}>
      <div>
        <div className="setting-name">
          <label htmlFor={inputId}>{def.label}</label>
          <RiskChip risk={def.risk} />
          {def.restart ? <StatusChip tone="warning" icon={RefreshCw}>{t("settingField.restart")}</StatusChip> : null}
          {locked ? <StatusChip tone="neutral" icon={Lock}>{t("settingField.locked")}</StatusChip> : null}
        </div>
        <div className="setting-key">{def.key}</div>
        {def.help ? <div className="setting-help">{def.help}</div> : null}
        {locked ? <div className="setting-help">{t("settingField.lockedHelp")}</div> : null}
        <div className="setting-meta">
          {def.type !== "secret" ? <span>{t("settingField.default", { value: fmt(def.default) })}</span> : <span>{t("settingField.secretStatus", { status: fmt(value) })}</span>}
          {(def.min !== null && def.min !== undefined) || (def.max !== null && def.max !== undefined) ? <span>· {t("settingField.range", { min: def.min ?? "−∞", max: def.max ?? "∞" })}</span> : null}
          {def.unit ? <span>· {t("settingField.unit", { unit: def.unit })}</span> : null}
          {!isDefault && !dirty ? <StatusChip tone="accent" icon={null}>{t("settingField.customized")}</StatusChip> : null}
          {dirty ? <span className="t-accent">{t("settingField.dirty")}</span> : null}
        </div>
      </div>
      <div className="setting-control">
        <Control def={def} id={inputId} value={current} saved={value} onChange={onDraft} disabled={disabled || locked} invalid={Boolean(error)} />
        {error ? <div className="field-error" id={`${inputId}-error`} role="alert">{error}</div> : null}
        <div className="row">
          {dirty ? <Button size="sm" variant="ghost" onClick={() => onDraft(NO_DRAFT)}>{t("action.undo")}</Button> : null}
          {!locked && def.type !== "secret" ? <Button size="sm" variant="ghost" disabled={isDefault || disabled} onClick={() => onDraft(def.default)}>{t("action.restoreDefault")}</Button> : null}
        </div>
      </div>
    </div>
  );
}

function Control({ def, id, value, saved, onChange, disabled, invalid }: {
  def: SettingDef; id: string; value: unknown; saved: unknown; onChange: (v: Draft) => void; disabled?: boolean; invalid?: boolean;
}) {
  const { t } = useI18n();
  const describedBy = invalid ? `${id}-error` : undefined;
  // text-ish inputs keep raw text locally so partial input ("1.", "") is possible
  const [raw, setRawState] = useState<string>(() => toRaw(def, value));
  const typed = useRef(false);
  const setRaw = (next: string) => { typed.current = true; setRawState(next); };
  useEffect(() => {
    // external value change (undo, reload, reset) → resync; own typing keeps the raw text
    if (typed.current) { typed.current = false; return; }
    setRawState(toRaw(def, value));
  }, [def, value]);

  switch (def.type) {
    case "bool":
      return <div className="row" style={{ justifyContent: "flex-end" }}><Switch id={id} checked={Boolean(value)} onChange={(v) => onChange(v)} label={def.label} disabled={disabled} /></div>;
    case "enum":
      return (
        <select id={id} className="select" value={String(value ?? "")} disabled={disabled} aria-invalid={invalid} aria-describedby={describedBy} onChange={(e) => onChange(e.target.value)}>
          {optionList(def).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      );
    case "int":
    case "float":
    case "duration":
      if (def.type === "duration" && typeof def.default === "string") {
        return <input id={id} className="input mono" value={raw} disabled={disabled} aria-invalid={invalid} aria-describedby={describedBy} onChange={(e) => { setRaw(e.target.value); onChange(e.target.value); }} />;
      }
      return (
        <div className="row" style={{ gap: 6 }}>
          <input
            id={id} className="input num" inputMode={def.type === "float" ? "decimal" : "numeric"} value={raw} disabled={disabled}
            aria-invalid={invalid} aria-describedby={describedBy}
            onChange={(e) => {
              setRaw(e.target.value);
              const normalized = e.target.value.replace(",", ".").trim();
              const n = def.type === "float" ? Number.parseFloat(normalized) : Number(normalized);
              onChange(normalized === "" || Number.isNaN(n) ? normalized : n);
            }}
          />
          {def.unit ? <span className="t-3" style={{ fontSize: 12 }}>{def.unit}</span> : null}
        </div>
      );
    case "list":
      if (def.options?.length) {
        const options = optionList(def);
        const selected = new Set(Array.isArray(value) ? (value as unknown[]).map(String) : []);
        return (
          <div className="row wrap" role="group" aria-label={def.label} id={id}>
            {options.map((o) => (
              <button key={o.value} type="button" className="filter-chip" aria-pressed={selected.has(o.value)} disabled={disabled} onClick={() => {
                const next = new Set(selected);
                if (next.has(o.value)) next.delete(o.value); else next.add(o.value);
                onChange(options.map((x) => x.value).filter((x) => next.has(x)));
              }}>{o.label}</button>
            ))}
          </div>
        );
      }
      return (
        <>
          <input id={id} className="input" value={raw} disabled={disabled} aria-invalid={invalid} aria-describedby={describedBy}
            onChange={(e) => { setRaw(e.target.value); onChange(e.target.value.split(",").map((s) => s.trim()).filter(Boolean)); }} />
          <span className="hint">{t("settingField.listHint")}</span>
        </>
      );
    case "secret":
      // write-only: never pre-filled; an empty draft means "keep the stored secret"
      return (
        <input id={id} type="password" className="input" autoComplete="new-password" value={typeof value === "string" && value !== saved ? value : ""}
          placeholder={secretIsSet(saved) ? t("settingField.secretSet") : t("settingField.secretEmpty")} disabled={disabled}
          aria-invalid={invalid} aria-describedby={describedBy}
          onChange={(e) => onChange(e.target.value === "" ? NO_DRAFT : e.target.value)} />
      );
    default:
      return <input id={id} className="input" value={raw} disabled={disabled} aria-invalid={invalid} aria-describedby={describedBy} onChange={(e) => { setRaw(e.target.value); onChange(e.target.value); }} />;
  }
}

function toRaw(def: SettingDef, value: unknown): string {
  if (value === null || value === undefined) return "";
  if (def.type === "secret") return "";
  if (def.type === "list" && Array.isArray(value)) return value.join(", ");
  return String(value);
}
