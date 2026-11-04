"use client";

import { CircleCheck, CircleHelp, CircleSlash, CircleX, Link2, Link2Off, Unplug } from "lucide-react";

import { Progress, StatusChip, type Tone } from "@/components/mg/primitives";
import type { CompatMatrix, MgMode, MgQuotaOverview } from "@/lib/api/recovery";
import { useI18n } from "@/lib/i18n";

const MODE_TONE: Record<MgMode, Tone> = { connected: "success", degraded: "warning", standalone: "neutral" };
const MODE_ICON = { connected: Link2, degraded: Unplug, standalone: Link2Off };
const MODE_KEY = { connected: "mg.mode.connected", degraded: "mg.mode.degraded", standalone: "mg.mode.standalone" } as const;

/** connected / degraded / standalone — colour + icon + text. */
export function MgModeChip({ mode, size }: { mode: MgMode | null | undefined; size?: "lg" }) {
  const { t } = useI18n();
  if (!mode) return <StatusChip tone="neutral" icon={CircleHelp} size={size}>{t("mg.mode.unknown")}</StatusChip>;
  return <StatusChip tone={MODE_TONE[mode]} icon={MODE_ICON[mode]} size={size}>{t(MODE_KEY[mode])}</StatusChip>;
}

/** MSO's own request quota + MediaGuard's backfill quota per instance (MSO always stays below it). */
export function QuotaMeter({ quota, instanceId, compact }: { quota: MgQuotaOverview | null | undefined; instanceId?: string | null; compact?: boolean }) {
  const { t, num, relative } = useI18n();
  if (!quota) return null;
  const m = quota.mso;
  const mg = quota.mediaguard ?? null;
  const rows = mg ? Object.entries(mg).filter(([id]) => !instanceId || id === instanceId) : [];
  const tone = (left: number) => (left <= 0 ? "danger" : left <= 2 ? "warning" : "success");
  return (
    <div className="stack" style={{ gap: 10 }}>
      <div>
        <div className="row-between" style={{ fontSize: 12.5 }}>
          <span>{t("mg.quota.mso")}</span>
          <span className="num">{t("mg.quota.used", { used: num(m.hour_used), max: num(m.hour_max) })} · {t("mg.quota.day", { used: num(m.day_used), max: num(m.day_max) })}</span>
        </div>
        <Progress value={m.hour_max ? (m.hour_used / m.hour_max) * 100 : 100} tone={tone(m.remaining)} thin label={t("mg.quota.mso")} />
      </div>
      {rows.length ? rows.map(([id, q]) => {
        const usable = Math.max(0, q.remaining - quota.reserve);
        return (
          <div key={id}>
            <div className="row-between" style={{ fontSize: 12.5 }}>
              <span>{t("mg.quota.instance", { id })}</span>
              <span className="num">{t("mg.quota.remaining", { left: num(q.remaining), usable: num(usable) })}</span>
            </div>
            <Progress value={q.hour_max ? (q.hour_used / q.hour_max) * 100 : 100} tone={tone(usable)} thin label={t("mg.quota.instance", { id })} />
          </div>
        );
      }) : <p className="hint">{t("mg.quota.unknown")}</p>}
      {!compact ? (
        <p className="hint">
          {t("mg.quota.reserveHint", { reserve: num(quota.reserve) })}
          {quota.mediaguard_fetched_at ? ` · ${t("mg.quota.fetched", { when: relative(quota.mediaguard_fetched_at) })}` : ""}
        </p>
      ) : null}
    </div>
  );
}

function LiveChip({ live }: { live: "aanwezig" | "ontbreekt" | "onbekend" }) {
  const { t } = useI18n();
  if (live === "aanwezig") return <StatusChip tone="success" icon={CircleCheck}>{t("mg.compat.present")}</StatusChip>;
  if (live === "ontbreekt") return <StatusChip tone="danger" icon={CircleX}>{t("mg.compat.missing")}</StatusChip>;
  return <StatusChip tone="neutral" icon={CircleHelp}>{t("mg.compat.unknown")}</StatusChip>;
}

export function CompatTable({ compat }: { compat: CompatMatrix }) {
  const { t, relative } = useI18n();
  return (
    <div className="stack" style={{ gap: 12 }}>
      <p className="hint" style={{ padding: "0 16px" }}>
        {t("mg.compat.reference", { repo: compat.reference.repo, commit: compat.reference.commit })}
        {" · "}
        {compat.live_source ? t(compat.live_source === "openapi" ? "mg.compat.sourceOpenapi" : "mg.compat.sourceProbe") : t("mg.compat.sourceNone")}
        {compat.checked_at ? ` · ${relative(compat.checked_at)}` : ""}
      </p>
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel (horizontaal scrollbaar)">
        <table className="table table--stack">
          <thead>
            <tr><th>{t("mg.compat.route")}</th><th>{t("mg.compat.role")}</th><th>{t("mg.compat.purpose")}</th><th>{t("mg.compat.verified")}</th><th>{t("mg.compat.live")}</th></tr>
          </thead>
          <tbody>
            {compat.routes.map((r) => (
              <tr key={r.id}>
                <td data-label={t("mg.compat.route")}>
                  <div className="row wrap" style={{ gap: 6 }}>
                    <span className="tag">{r.method}</span>
                    <span className="mono" style={{ overflowWrap: "anywhere" }}>{r.path}</span>
                  </div>
                  <div className="hint">{r.label}{r.required ? ` · ${t("mg.compat.required")}` : ""}{r.access === "write" ? ` · ${t("mg.compat.onlyWrite")}` : ""}</div>
                </td>
                <td data-label={t("mg.compat.role")}>{r.role === "none" ? t("mg.compat.noLogin") : r.role}</td>
                <td data-label={t("mg.compat.purpose")} style={{ maxWidth: 380 }}>{r.purpose}<div className="hint mono">{r.ref}</div></td>
                <td data-label={t("mg.compat.verified")}>
                  {r.verified ? <StatusChip tone="success">{t("mg.compat.verifiedYes")}</StatusChip> : <StatusChip tone="warning">{t("mg.compat.verifiedNo")}</StatusChip>}
                </td>
                <td data-label={t("mg.compat.live")}><LiveChip live={r.live} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ padding: "0 16px 16px" }}>
        <h3 className="card-title" style={{ fontSize: 13, marginBottom: 8 }}>{t("mg.compat.neverUsed")}</h3>
        <ul className="stack" style={{ gap: 6, margin: 0, paddingLeft: 0, listStyle: "none" }}>
          {compat.never_used.map((n) => (
            <li key={n.path} className="row wrap" style={{ gap: 6, fontSize: 12.5 }}>
              <StatusChip tone="neutral" icon={CircleSlash}>{n.method}</StatusChip>
              <span className="mono" style={{ overflowWrap: "anywhere" }}>{n.path}</span>
              <span className="t-3">— {n.why}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
