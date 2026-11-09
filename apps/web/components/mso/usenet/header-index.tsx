"use client";

import { Database, Eraser, Play, Power, Save } from "lucide-react";
import { useState } from "react";

import { DangerDialog } from "@/components/mg/domain";
import { Button, Card, Field, InlineState, Progress, StatusChip, type Tone } from "@/components/mg/primitives";
import { ResourceView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { useRatio } from "@/components/mso/usenet/shared";
import { api, errorMessage } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import type { HeaderIndexStatus, UsenetProvider } from "@/lib/api/usenet";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";

type Cfg = HeaderIndexStatus["config"];
const RUN_TONE: Record<HeaderIndexStatus["runs"][number]["status"], Tone> = {
  done: "success", running: "accent", budget_reached: "warning", failed: "danger", skipped: "neutral",
};
const NUMS: Array<[keyof Cfg, MessageKey, number, number]> = [
  ["retention_days", "usenet.hi.retention", 1, 30],
  ["max_storage_mb", "usenet.hi.storage", 16, 4096],
  ["max_headers_per_run", "usenet.hi.headersPerRun", 500, 200000],
  ["max_runs_per_day", "usenet.hi.runsPerDay", 1, 24],
  ["max_download_mb_per_run", "usenet.hi.downloadPerRun", 1, 1000],
  ["max_run_seconds", "usenet.hi.secondsPerRun", 10, 1800],
];

function ConfigForm({ status, providers, onSaved }: { status: HeaderIndexStatus; providers: UsenetProvider[]; onSaved: () => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const { can } = useSession();
  const admin = can("admin");
  const [cfg, setCfg] = useState<Cfg>(status.config);
  const [groups, setGroups] = useState(status.config.groups.join("\n"));
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState<"save" | "enable" | "disable" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const enabled = status.config.enabled;

  const submit = async (kind: "save" | "enable" | "disable") => {
    setBusy(kind);
    setError(null);
    const groupList = groups.split(/\s+/).map((g) => g.trim()).filter(Boolean);
    const body: Record<string, unknown> = { ...cfg, groups: groupList };
    if (kind === "enable") Object.assign(body, { enabled: true, acknowledge_limits: ack });
    else if (kind === "disable") Object.assign(body, { enabled: false });
    else delete body.enabled;
    try {
      await api.put("/usenet/headerindex/config", body);
      toast.push("success", t("usenet.hi.saved"));
      onSaved();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  const reason = admin ? undefined : t("usenet.adminOnly");
  return (
    <div className="stack">
      <div className="grid" style={{ gap: 12 }}>
        <div className="span-6">
          <Field label={t("usenet.hi.provider")} htmlFor="hi-provider">
            <select id="hi-provider" className="input" value={cfg.provider} disabled={!admin} onChange={(e) => setCfg({ ...cfg, provider: e.target.value })}>
              <option value="">{t("usenet.hi.providerNone")}</option>
              {providers.filter((p) => p.enabled).map((p) => <option key={p.slug} value={p.slug}>{p.label}</option>)}
            </select>
          </Field>
        </div>
        <div className="span-6">
          <Field label={t("usenet.hi.groups")} hint={t("usenet.hi.groupsHint")} htmlFor="hi-groups">
            <textarea id="hi-groups" className="input mono" rows={3} value={groups} disabled={!admin} onChange={(e) => setGroups(e.target.value)} />
          </Field>
        </div>
        {NUMS.map(([key, label, min, max]) => (
          <div key={key} className="span-4">
            <Field label={t(label)} htmlFor={`hi-${key}`}>
              <input id={`hi-${key}`} className="input" type="number" min={min} max={max} disabled={!admin}
                value={cfg[key] as number} onChange={(e) => setCfg({ ...cfg, [key]: Number(e.target.value) })} />
            </Field>
          </div>
        ))}
      </div>
      {!enabled ? (
        <label className="row" style={{ alignItems: "flex-start", gap: 8 }}>
          <input type="checkbox" checked={ack} disabled={!admin} onChange={(e) => setAck(e.target.checked)} style={{ marginTop: 3 }} />
          <span>{t("usenet.hi.acknowledge")}</span>
        </label>
      ) : null}
      {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
      <div className="row wrap">
        {enabled ? (
          <>
            <Button icon={Save} onClick={() => void submit("save")} pending={busy === "save"} disabled={!admin} title={reason}>{t("usenet.hi.save")}</Button>
            <Button variant="ghost" icon={Power} onClick={() => void submit("disable")} pending={busy === "disable"} disabled={!admin} title={reason}>{t("usenet.hi.disable")}</Button>
          </>
        ) : (
          <Button variant="primary" icon={Power} onClick={() => void submit("enable")} pending={busy === "enable"}
            disabled={!admin || !ack || !cfg.provider || !groups.trim()} title={reason}>{t("usenet.hi.enable")}</Button>
        )}
      </div>
    </div>
  );
}

/** Optional, opt-in, bounded own header index: wizard, budgets, usage meter, obfuscation ratio, purge. */
export function HeaderIndexCard({ providers }: { providers: UsenetProvider[] }) {
  const { t, num, bytes, relative } = useI18n();
  const ratio = useRatio();
  const toast = useToast();
  const { can } = useSession();
  const status = useResource<HeaderIndexStatus>("/usenet/headerindex", undefined, 30_000);
  const [busy, setBusy] = useState<"run" | "expired" | "all" | null>(null);
  const [confirmAll, setConfirmAll] = useState(false);

  const run = async () => {
    setBusy("run");
    try {
      await api.post("/usenet/headerindex/run");
      toast.push("success", t("usenet.hi.runQueued"));
    } catch (e) {
      toast.push("danger", errorMessage(e));
    } finally {
      setBusy(null);
    }
  };
  const purge = async (scope: "expired" | "all") => {
    setBusy(scope);
    try {
      const res = await api.post<{ deleted: number }>("/usenet/headerindex/purge", { scope });
      toast.push("success", t("usenet.hi.purged", { count: num(res.deleted) }));
      setConfirmAll(false);
      await status.reload();
    } catch (e) {
      toast.push("danger", errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card title={t("usenet.hi.title")} icon={Database} className="span-12"
      actions={status.data ? <StatusChip tone={status.data.config.enabled ? "success" : "neutral"}>{t(status.data.config.enabled ? "usenet.hi.on" : "usenet.hi.off")}</StatusChip> : undefined}>
      <ResourceView resource={status} endpoint="GET /api/v1/usenet/headerindex">
        {(s) => (
          <div className="stack">
            <InlineState tone="warning">{s.disclaimer}</InlineState>
            <p className="t-2" style={{ margin: 0 }}>{t(s.config.enabled ? "usenet.hi.endpointOn" : "usenet.hi.endpointOff")}</p>
            {s.config.enabled || s.usage.headers ? (
              <div className="grid" style={{ gap: 16 }}>
                <div className="span-6 stack-sm">
                  <div className="meter-row">
                    <span>{t("usenet.hi.usage")}</span>
                    <span className="t-2">{t("usenet.hi.usageValue", { used: bytes(s.usage.estimated_bytes), budget: bytes(s.usage.budget_bytes) })}</span>
                    <Progress value={(s.usage.ratio ?? 0) * 100} tone={s.usage.budget_reached ? "danger" : (s.usage.ratio ?? 0) > 0.8 ? "warning" : undefined} label={t("usenet.hi.usage")} />
                  </div>
                  <span className="t-3">{t("usenet.hi.physical", { size: bytes(s.usage.physical_bytes) })}</span>
                  {s.usage.budget_reached ? <InlineState tone="danger" role="alert">{t("usenet.hi.budgetReached")}</InlineState> : null}
                </div>
                <div className="span-6">
                  <dl className="kv">
                    <dt>{t("usenet.hi.headers")}</dt><dd>{num(s.metrics.headers)}</dd>
                    <dt>{t("usenet.hi.releases")}</dt><dd>{num(s.metrics.releases)}</dd>
                    <dt title={t("usenet.hi.obfuscatedHint")}>{t("usenet.hi.obfuscated")}</dt>
                    <dd>{ratio(s.metrics.obfuscated_ratio)}</dd>
                    <dt title={t("usenet.hi.usableNewHint")}>{t("usenet.hi.usableNew")}</dt>
                    <dd>{ratio(s.metrics.usable_ratio_new)} · {num(s.metrics.new_releases_48h)}</dd>
                  </dl>
                </div>
              </div>
            ) : null}
            {s.config.enabled || s.usage.headers ? (
              <div className="row wrap">
                <Button icon={Play} onClick={() => void run()} pending={busy === "run"}
                  disabled={!s.config.enabled || !can("operator") || s.usage.budget_reached}
                  title={!can("operator") ? t("usenet.operatorOnly") : s.usage.budget_reached ? t("usenet.hi.budgetReached") : !s.config.enabled ? t("usenet.hi.off") : undefined}>
                  {t("usenet.hi.run")}
                </Button>
                <Button icon={Eraser} onClick={() => void purge("expired")} pending={busy === "expired"} disabled={!can("admin")} title={can("admin") ? undefined : t("usenet.adminOnly")}>
                  {t("usenet.hi.purgeExpired")}
                </Button>
                <Button variant="danger" icon={Eraser} onClick={() => setConfirmAll(true)} disabled={!can("admin") || !s.usage.headers} title={can("admin") ? undefined : t("usenet.adminOnly")}>
                  {t("usenet.hi.purgeAll")}
                </Button>
              </div>
            ) : null}
            <div>
              <span className="eyebrow">{s.config.enabled ? t("usenet.hi.save") : t("usenet.hi.wizard")}</span>
              <ConfigForm key={JSON.stringify(s.config)} status={s} providers={providers} onSaved={() => void status.reload()} />
            </div>
            {s.runs.length ? (
              <div>
                <span className="eyebrow">{t("usenet.hi.runs")} · {t("usenet.hi.runsToday", { count: num(s.runs_today) })}</span>
                <ul className="list" style={{ marginTop: 4 }}>
                  {s.runs.slice(0, 8).map((r) => (
                    <li key={r.id} className="list-row">
                      <div className="list-main">
                        <div className="list-title">{relative(r.started_at)}</div>
                        <div className="list-sub">{t("usenet.hi.stored", { stored: num(r.headers_stored), bytes: bytes(r.bytes_received) })}{r.message ? ` · ${r.message}` : ""}</div>
                      </div>
                      <StatusChip tone={RUN_TONE[r.status]} pending={r.status === "running"}>{t(`usenet.hi.run.${r.status}` as MessageKey)}</StatusChip>
                    </li>
                  ))}
                </ul>
              </div>
            ) : s.config.enabled ? <p className="t-3" style={{ margin: 0 }}>{t("usenet.hi.noRuns")}</p> : null}
          </div>
        )}
      </ResourceView>
      <DangerDialog open={confirmAll} onClose={() => setConfirmAll(false)} onConfirm={() => void purge("all")} pending={busy === "all"}
        title={t("usenet.hi.purgeTitle")} description={t("usenet.hi.purgeBody")} confirmLabel={t("usenet.hi.purgeAll")} icon={Eraser} />
    </Card>
  );
}
