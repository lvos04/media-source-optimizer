// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/lib/i18n/index.tsx — internally versioned copy, see PROVENANCE.md
// (MSO is Dutch-only: the en-US dictionary and locale switch were dropped; the key mechanism and
// formatters are unchanged so a second locale can be added later.)
"use client";

import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";

import { nl, type MessageKey } from "./messages";

export type Locale = "nl-NL";
export const LOCALES: Locale[] = ["nl-NL"];
export type { MessageKey };

type Vars = Record<string, string | number>;

const dictionaries: Record<Locale, Record<MessageKey, string>> = { "nl-NL": nl };

function interpolate(template: string, vars?: Vars): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (_, name: string) => (name in vars ? String(vars[name]) : `{${name}}`));
}

export function translate(locale: Locale, key: MessageKey, vars?: Vars): string {
  return interpolate(dictionaries[locale][key] ?? nl[key] ?? key, vars);
}

export interface Formatters {
  locale: Locale;
  t: (key: MessageKey, vars?: Vars) => string;
  /** Plural helper: picks `${key}.one` / `${key}.other`. */
  tp: (key: string, count: number, vars?: Vars) => string;
  num: (value: number | null | undefined, digits?: number) => string;
  pct: (value: number | null | undefined, digits?: number) => string;
  bytes: (value: number | null | undefined) => string;
  date: (value: string | number | Date | null | undefined) => string;
  dateTime: (value: string | number | Date | null | undefined) => string;
  time: (value: string | number | Date | null | undefined) => string;
  relative: (value: string | number | Date | null | undefined) => string;
  duration: (seconds: number | null | undefined) => string;
  money: (value: number | null | undefined, currency?: string) => string;
}

const DASH = "—";

function toDate(value: string | number | Date): Date | null {
  const d = value instanceof Date ? value : new Date(value);
  return Number.isNaN(d.valueOf()) ? null : d;
}

export function makeFormatters(locale: Locale): Formatters {
  const t = (key: MessageKey, vars?: Vars) => translate(locale, key, vars);
  const num = (value: number | null | undefined, digits = 0) =>
    value === null || value === undefined || Number.isNaN(value)
      ? DASH
      : value.toLocaleString(locale, { maximumFractionDigits: digits, minimumFractionDigits: 0 });
  return {
    locale,
    t,
    tp: (key, count, vars) => {
      const k = `${key}.${count === 1 ? "one" : "other"}` as MessageKey;
      return translate(locale, k, { count: num(count), ...vars });
    },
    num,
    pct: (value, digits = 0) => (value === null || value === undefined ? DASH : `${num(value, digits)}%`),
    bytes: (value) => {
      if (value === null || value === undefined) return DASH;
      const units = ["B", "KB", "MB", "GB", "TB", "PB"];
      let v = Math.abs(value);
      let i = 0;
      while (v >= 1024 && i < units.length - 1) {
        v /= 1024;
        i += 1;
      }
      return `${value < 0 ? "−" : ""}${v.toLocaleString(locale, { maximumFractionDigits: v >= 100 ? 0 : 1 })} ${units[i]}`;
    },
    date: (value) => {
      if (value === null || value === undefined) return DASH;
      const d = toDate(value);
      return d ? d.toLocaleDateString(locale, { day: "numeric", month: "short", year: "numeric" }) : DASH;
    },
    dateTime: (value) => {
      if (value === null || value === undefined) return DASH;
      const d = toDate(value);
      return d ? d.toLocaleString(locale, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : DASH;
    },
    time: (value) => {
      if (value === null || value === undefined) return DASH;
      const d = toDate(value);
      return d ? d.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" }) : DASH;
    },
    relative: (value) => {
      if (value === null || value === undefined) return DASH;
      const d = toDate(value);
      if (!d) return DASH;
      const seconds = Math.round((d.valueOf() - Date.now()) / 1000);
      const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
      const abs = Math.abs(seconds);
      if (abs < 60) return rtf.format(seconds, "second");
      if (abs < 3600) return rtf.format(Math.round(seconds / 60), "minute");
      if (abs < 86400) return rtf.format(Math.round(seconds / 3600), "hour");
      return rtf.format(Math.round(seconds / 86400), "day");
    },
    duration: (seconds) => {
      if (seconds === null || seconds === undefined) return DASH;
      const d = Math.floor(seconds / 86400);
      const h = Math.floor((seconds % 86400) / 3600);
      const m = Math.floor((seconds % 3600) / 60);
      const s = Math.floor(seconds % 60);
      if (d > 0) return `${d}d ${h}u`;
      return h > 0 ? `${h}u ${m}m` : `${m}m ${s}s`;
    },
    money: (value, currency = "EUR") =>
      value === null || value === undefined ? DASH : value.toLocaleString(locale, { style: "currency", currency, maximumFractionDigits: 3 }),
  };
}

const I18nContext = createContext<Formatters>(makeFormatters("nl-NL"));

export function I18nProvider({ locale = "nl-NL", children }: { locale?: Locale; children: ReactNode }) {
  const value = useMemo(() => makeFormatters(locale), [locale]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): Formatters {
  return useContext(I18nContext);
}

export function useT() {
  const { t } = useContext(I18nContext);
  return useCallback((key: MessageKey, vars?: Vars) => t(key, vars), [t]);
}

export function isLocale(value: unknown): value is Locale {
  return value === "nl-NL";
}
