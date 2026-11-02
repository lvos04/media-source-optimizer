// Layout derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/app/(app)/systeem/page.tsx — internally versioned copy, see PROVENANCE.md
"use client";

import { Ban, DatabaseBackup, HeartPulse, ListChecks, RotateCcw, Stethoscope } from "lucide-react";
import { useEffect, useState } from "react";

import { DangerDialog } from "@/components/mg/domain";
import { Button, Card, InlineState, PageHeader, Progress, Segmented, StatusChip, Switch } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useShell } from "@/components/shell/app-shell";
import { useAction } from "@/lib/api/actions";
import type { Backup, BackupList, Job, JobList, SystemInfo } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { healthLabel, healthTone, jobLabel, jobTone } from "@/lib/status";

const JOB_FILTERS = ["", "queued", "running", "failed", "succeeded", "cancelled"] as const;
type JobFilter = (typeof JOB_FILTERS)[number];

export default function SystemPage() {
  const { t, dateTime, duration, num } = useI18n();
  const { health, healthError, reloadHealth } = useShell();
  const info = useResource<SystemInfo>("/system/info");

  return (
    <>
      <PageHeader title={t("nav.system")} sub={t("system.sub")} />
      <div className="grid">
        <Card className="span-4" title={t("system.core")} icon={HeartPulse}>
          <ResourceView resource={info} endpoint="GET /api/v1/system/info" compact>
            {(v) => (
              <dl className="kv">
                <dt>{t("system.version")}</dt><dd className="mono">{v.version}</dd>
                <dt>{t("system.schema")}</dt><dd className="mono">{String(v.schema_version)}</dd>
                <dt>{t("system.uptime")}</dt><dd>{duration(v.uptime_s)}</dd>
              </dl>
            )}
          </ResourceView>
        </Card>
        <Card className="span-8" title={t("system.health")} icon={Stethoscope} flush actions={<Button size="sm" onClick={() => void reloadHealth()}>{t("action.reload")}</Button>}>
          {health ? (
            health.components.length ? (
              <div className="list">{health.components.map((c) => (
                <div className="list-row" key={c.name}>
                  <div className="list-main"><div className="list-title mono">{c.name}</div>{c.message ? <div className="list-sub" style={{ whiteSpace: "normal" }}>{c.message}</div> : null}</div>
                  <StatusChip tone={healthTone(c.status)}>{t(healthLabel(c.status))}</StatusChip>
                </div>
              ))}</div>
            ) : <StateView kind="empty" compact />
          ) : healthError ? <StateView kind="offline" compact action={<Button size="sm" onClick={() => void reloadHealth()}>{t("action.retry")}</Button>} /> : <StateView kind="loading" compact />}
        </Card>

        <Card className="span-12" title={t("system.migrations")} icon={ListChecks} flush>
          <ResourceView resource={info} endpoint="GET /api/v1/system/info" compact isEmpty={(v) => !v.migrations?.length} empty={<StateView kind="empty" compact title={t("system.noMigrations")} />}>
            {(v) => (
              <div className="table-wrap" style={{ maxHeight: 320 }}>
                <table className="table table--compact table--stack">
                  <thead><tr><th>{t("system.migrationVersion")}</th><th>{t("system.migrationName")}</th><th>{t("system.migrationApplied")}</th></tr></thead>
                  <tbody>{[...v.migrations].reverse().map((m) => (
                    <tr key={String(m.version)}>
                      <td className="cell-primary mono">{String(m.version)}</td>
                      <td data-label={t("system.migrationName")}>{m.name}</td>
                      <td data-label={t("system.migrationApplied")}>{m.applied_at ? dateTime(m.applied_at) : "—"}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </ResourceView>
        </Card>

        <BackupsCard />
        <JobsCard num={num} />
      </div>
    </>
  );
}

function BackupsCard() {
  const { t, dateTime, bytes } = useI18n();
  const { can } = useSession();
  const backups = useResource<BackupList>("/backups");
  const { run, pending } = useAction();
  const [includeSecrets, setIncludeSecrets] = useState(false);
  const [restore, setRestore] = useState<Backup | null>(null);
  const admin = can("admin");

  const create = async () => {
    const result = await run("backup", "post", "/backups", { include_secrets: includeSecrets }, t("system.backupStarted"));
    if (result !== undefined) {
      window.dispatchEvent(new CustomEvent("mso:jobs-changed"));
      await backups.reload();
    }
  };

  return (
    <Card className="span-6" title={t("system.backups")} icon={DatabaseBackup} flush
      actions={<Button size="sm" variant="primary" disabled={!admin} title={!admin ? t("system.restoreAdminOnly") : undefined} pending={pending === "backup"} onClick={() => void create()} data-testid="backup-create">{t("system.backupNow")}</Button>}>
      <div className="card-body stack-sm" style={{ borderBottom: "1px solid var(--mg-border-soft)" }}>
        <label className="row t-2" style={{ fontSize: 13 }}>
          <Switch checked={includeSecrets} onChange={setIncludeSecrets} label={t("system.backupIncludeSecrets")} disabled={!admin} />{t("system.backupIncludeSecrets")}
        </label>
        {includeSecrets ? <InlineState tone="warning">{t("system.backupIncludeSecretsHelp")}</InlineState> : <p className="hint" style={{ margin: 0 }}>{t("system.backupIncludeSecretsHelp")}</p>}
      </div>
      <ResourceView resource={backups} endpoint="GET /api/v1/backups" compact isEmpty={(r) => !r.items?.length} empty={<StateView kind="empty" compact title={t("system.noBackups")} />}>
        {(r) => (
          <div className="list" style={{ maxHeight: 420, overflow: "auto" }}>{r.items.map((b) => (
            <div className="list-row" key={String(b.id)}>
              <div className="list-main">
                <div className="list-title mono" title={b.file}>{b.file}</div>
                <div className="list-sub">{dateTime(b.created_at)} · {bytes(b.size_bytes)} · {b.kind}</div>
              </div>
              <StatusChip tone={b.includes_secrets ? "warning" : "neutral"} icon={null} outline={!b.includes_secrets}>{b.includes_secrets ? t("system.backupWithSecrets") : t("system.backupWithoutSecrets")}</StatusChip>
              <Button size="sm" variant="danger" icon={RotateCcw} disabled={!admin} title={!admin ? t("system.restoreAdminOnly") : undefined} onClick={() => setRestore(b)}>{t("system.restore")}</Button>
            </div>
          ))}</div>
        )}
      </ResourceView>
      <DangerDialog open={Boolean(restore)} onClose={() => setRestore(null)} icon={DatabaseBackup} pending={pending === "restore"}
        title={t("system.restoreTitle", { file: restore?.file ?? "" })} description={t("system.restoreBody")}
        facts={restore ? [
          { label: t("system.created"), value: dateTime(restore.created_at) },
          { label: t("system.size"), value: bytes(restore.size_bytes) },
          { label: t("system.kind"), value: restore.kind },
          { label: t("system.backupIncludeSecrets"), value: restore.includes_secrets ? t("settings.yes") : t("settings.no") },
        ] : undefined}
        confirmWord={restore?.file} confirmLabel={t("system.restore")}
        onConfirm={async () => {
          if (!restore) return;
          const r = await run("restore", "post", `/backups/${encodeURIComponent(String(restore.id))}/restore`, { confirm: restore.file }, t("system.restoreStarted"));
          if (r !== undefined) { setRestore(null); window.dispatchEvent(new CustomEvent("mso:jobs-changed")); }
        }} />
    </Card>
  );
}

function JobsCard({ num }: { num: (v: number | null | undefined, d?: number) => string }) {
  const { t, dateTime, relative } = useI18n();
  const { can } = useSession();
  const [status, setStatus] = useState<JobFilter>("");
  const jobs = useResource<JobList>("/jobs", { status: status || undefined }, 10_000);
  const { run, pending } = useAction();
  const operator = can("operator");
  const { reload } = jobs;
  // backups/restores enqueue jobs: refresh when the backups card signals it
  useEffect(() => {
    const listener = () => void reload();
    window.addEventListener("mso:jobs-changed", listener);
    return () => window.removeEventListener("mso:jobs-changed", listener);
  }, [reload]);

  const act = async (job: Job, action: "cancel" | "retry") => {
    const r = await run(`${action}-${job.id}`, "post", `/jobs/${encodeURIComponent(String(job.id))}/${action}`, {}, action === "cancel" ? t("jobs.cancelled") : t("jobs.retried"));
    if (r !== undefined) await reload();
  };

  return (
    <Card className="span-6" title={t("jobs.title")} icon={ListChecks} flush>
      <div className="card-body" style={{ paddingBottom: 8, overflowX: "auto" }}>
        <Segmented label={t("jobs.status")} value={status} onChange={setStatus}
          options={JOB_FILTERS.map((s) => ({ value: s, label: s ? t(jobLabel(s)) : t("jobs.all") }))} />
      </div>
      <ResourceView resource={jobs} endpoint="GET /api/v1/jobs" compact isEmpty={(r) => !r.items?.length} empty={<StateView kind="empty" compact title={t("jobs.empty")} />}>
        {(r) => (
          <div className="list" style={{ maxHeight: 520, overflow: "auto" }}>{r.items.map((job) => {
            const cancellable = job.status === "queued" || job.status === "running";
            const retryable = job.status === "failed" || job.status === "cancelled";
            return (
              <div className="list-row" key={String(job.id)} style={{ alignItems: "flex-start", flexWrap: "wrap" }}>
                <div className="list-main" style={{ minWidth: 200 }}>
                  <div className="list-title"><span className="mono">{job.kind}</span> <span className="t-3">#{String(job.id)}</span></div>
                  <div className="list-sub" style={{ whiteSpace: "normal" }}>
                    {job.summary ? `${job.summary} · ` : ""}{t("jobs.attempts")} {num(job.attempts)}/{num(job.max_attempts)} · {t("jobs.created")} {dateTime(job.created_at)}{job.updated_at ? ` · ${t("jobs.updated")} ${relative(job.updated_at)}` : ""}
                  </div>
                  {job.status === "running" && job.progress !== null && job.progress !== undefined ? (
                    <div style={{ marginTop: 6 }}><Progress value={job.progress <= 1 ? job.progress * 100 : job.progress} thin label={t("jobs.progress")} /></div>
                  ) : null}
                  {job.error ? <div className="t-danger pre-wrap" style={{ fontSize: 12, marginTop: 4 }}>{job.error}</div> : null}
                </div>
                <StatusChip tone={jobTone(job.status)} pending={job.status === "running"}>{t(jobLabel(job.status))}</StatusChip>
                <div className="row">
                  {cancellable ? <Button size="sm" icon={Ban} disabled={!operator} title={!operator ? t("jobs.operatorOnly") : undefined} pending={pending === `cancel-${job.id}`} onClick={() => void act(job, "cancel")}>{t("action.cancelJob")}</Button> : null}
                  {retryable ? <Button size="sm" icon={RotateCcw} disabled={!operator} title={!operator ? t("jobs.operatorOnly") : undefined} pending={pending === `retry-${job.id}`} onClick={() => void act(job, "retry")}>{t("action.retryJob")}</Button> : null}
                </div>
              </div>
            );
          })}</div>
        )}
      </ResourceView>
    </Card>
  );
}
