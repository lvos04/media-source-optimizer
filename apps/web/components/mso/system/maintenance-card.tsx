"use client";

import { Broom, Database } from "lucide-react";
import Link from "next/link";

import { Button, Card, InlineState, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { useAction } from "@/lib/api/actions";
import { type Resource, useResource } from "@/lib/api/hooks";
import type { MaintenanceStatus } from "@/lib/api/maintenance";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

const ENDPOINT = "GET /api/v1/system/maintenance";

/** Systeem → "Onderhoud" (daily retention: what was cleaned, the periods) and "Database" (size, largest tables). */
export function MaintenanceCards() {
  const res = useResource<MaintenanceStatus>("/system/maintenance", undefined, 60_000);
  return (
    <>
      <RetentionCard res={res} />
      <DatabaseCard res={res} />
    </>
  );
}

type Res = Resource<MaintenanceStatus>;

function RetentionCard({ res }: { res: Res }) {
  const { t, num, dateTime, relative, duration } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const { run, pending } = useAction();
  const admin = can("admin");

  const start = async () => {
    const r = await run<{ job_id: number | string }>("retention", "post", "/system/maintenance/run");
    if (r === undefined) return;
    toast.push("info", t("maintenance.runStarted"), t("maintenance.runStartedBody"));
    window.dispatchEvent(new CustomEvent("mso:jobs-changed"));
    window.setTimeout(() => void res.reload(), 4000);
  };

  return (
    <Card id="onderhoud" className="span-6" title={t("maintenance.title")} sub={t("maintenance.sub")} icon={Broom}
      actions={<Button size="sm" disabled={!admin} title={!admin ? t("maintenance.adminOnly") : undefined} pending={pending === "retention"}
        onClick={() => void start()} data-testid="maintenance-run">{t("maintenance.run")}</Button>}>
      <ResourceView resource={res} endpoint={ENDPOINT} compact>
        {(m) => {
          const r = m.retention;
          const last = r.last;
          const d = last?.deleted ?? {};
          const s = r.settings;
          const extendedBy = r.raw.extended ? [...r.raw.readers].sort((a, b) => b.days - a.days)[0] : null;
          return (
            <div className="stack-sm">
              {!r.enabled ? <InlineState tone="warning">{t("maintenance.disabled")}</InlineState> : null}
              <dl className="kv" data-testid="maintenance-last">
                <dt>{t("maintenance.lastRun")}</dt>
                <dd>{last ? <span title={dateTime(last.at)}>{t("maintenance.lastRunValue", { when: relative(last.at), duration: duration(last.duration_s) })}</span>
                  : <span className="t-3">{t("maintenance.never")}</span>}</dd>
                {last ? (
                  <>
                    <dt>{t("maintenance.removed")}</dt>
                    <dd>{t("maintenance.removedValue", {
                      jobs: num((d.jobs_done ?? 0) + (d.jobs_failed ?? 0)),
                      raw: num(d.observations_raw ?? 0),
                      logs: num((d.automation_log ?? 0) + (d.login_attempts ?? 0)),
                    })}</dd>
                    <dt>{t("maintenance.vacuum")}</dt>
                    <dd>{last.vacuumed.length ? <StatusChip tone="success">{t("maintenance.vacuumValue", { count: num(last.vacuumed.length) })}</StatusChip> : "—"}</dd>
                  </>
                ) : null}
                <dt>{t("maintenance.periods")}</dt>
                <dd>{t("maintenance.periodsValue", {
                  done: num(s["retention.jobs_done_days"]), failed: num(s["retention.jobs_failed_days"]),
                  raw: num(r.raw.effective_days), hourly: num(s["retention.observations_hourly_days"]),
                  auto: num(s["retention.automation_log_days"]), login: num(s["retention.login_attempts_days"]),
                })}</dd>
              </dl>
              {last && !last.complete ? <InlineState tone="info">{t("maintenance.incomplete")}</InlineState> : null}
              {extendedBy ? (
                <p className="hint" style={{ margin: 0 }}>{t("maintenance.rawExtended", {
                  days: num(r.raw.effective_days), configured: num(r.raw.configured_days), reader: extendedBy.reader.toLowerCase(),
                })}</p>
              ) : null}
              <Link className="link-more" href="/instellingen?tab=systeem">{t("maintenance.settingsLink")}</Link>
            </div>
          );
        }}
      </ResourceView>
    </Card>
  );
}

function DatabaseCard({ res }: { res: Res }) {
  const { t, num, bytes } = useI18n();
  return (
    <Card className="span-6" title={t("maintenance.database")} icon={Database} flush>
      <ResourceView resource={res} endpoint={ENDPOINT} compact isEmpty={(m) => !m.database.tables.length}
        empty={<StateView kind="empty" compact />}>
        {(m) => (
          <>
            <dl className="kv" style={{ padding: "12px 16px" }}>
              <dt>{t("maintenance.dbSize")}</dt><dd className="num" data-testid="db-size">{bytes(m.database.size_bytes)}</dd>
            </dl>
            <div className="table-wrap" tabIndex={0} role="region" aria-label={t("maintenance.topTables")}>
              <table className="table table--compact table--stack">
                <thead><tr><th>{t("maintenance.table")}</th><th className="num">{t("maintenance.tableRows")}</th><th className="num">{t("maintenance.tableSize")}</th></tr></thead>
                <tbody>{m.database.tables.map((tb) => (
                  <tr key={tb.name}>
                    <td className="cell-primary mono">{tb.name}</td>
                    <td data-label={t("maintenance.tableRows")} className="num">{num(tb.rows)}</td>
                    <td data-label={t("maintenance.tableSize")} className="num">{bytes(tb.bytes)}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </>
        )}
      </ResourceView>
    </Card>
  );
}

/** One line under the back-up card: free space vs. what a safe dump needs (+ what is left out and pruning). */
export function BackupSpaceNote() {
  const { t, bytes, num } = useI18n();
  const res = useResource<MaintenanceStatus>("/system/maintenance", undefined, 60_000);
  const b = res.data?.backups;
  if (!b) return null;
  const vars = { free: bytes(b.space.free_bytes), needed: bytes(b.space.needed_bytes), path: b.space.path };
  return (
    <div className="stack-sm" data-testid="backup-space">
      {b.space.ok ? <p className="hint" style={{ margin: 0 }}>{t("maintenance.backupSpace", vars)}</p>
        : <InlineState tone="warning" role="alert">{t("maintenance.backupSpaceLow", vars)}</InlineState>}
      <p className="hint" style={{ margin: 0 }}>{t("maintenance.backupExcluded")}</p>
      <p className="hint" style={{ margin: 0 }}>{t("maintenance.backupSafety", {
        days: num(b.safety.pre_restore_days), keep: num(b.safety.pre_restore_keep), upgrade: num(b.safety.pre_upgrade_keep),
      })}</p>
    </div>
  );
}
