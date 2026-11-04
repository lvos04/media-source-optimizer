"use client";

import { Pencil, PlugZap, Server, Trash2 } from "lucide-react";
import { useState } from "react";

import { DangerDialog } from "@/components/mg/domain";
import { Button, Card, Field, InlineState, Modal, Progress, Segmented, StatusChip, Switch } from "@/components/mg/primitives";
import { StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { ProviderStatusChip, SabStatusCell, useRatio } from "@/components/mso/usenet/shared";
import { api, errorMessage } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import type { ProviderInput, ProviderPreset, ProviderTestResult, SabServerList, UsenetProvider } from "@/lib/api/usenet";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";

function ProviderCard({ p, onEdit, onChanged }: { p: UsenetProvider; onEdit: () => void; onChanged: () => void }) {
  const { t, num, bytes, relative } = useI18n();
  const ratio = useRatio();
  const toast = useToast();
  const { can } = useSession();
  const [testing, setTesting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const test = async () => {
    setTesting(true);
    setMessage(null);
    try {
      const res = await api.post<ProviderTestResult>(`/usenet/providers/${encodeURIComponent(p.slug)}/test`);
      setMessage(res.status === "ok" ? t("usenet.providers.testOk") : res.message);
      const tone = res.status === "ok" ? "success" : !res.tested ? "info" : "danger";
      toast.push(tone, `${p.label}: ${t(`usenet.status.${res.status}` as MessageKey)}`, res.status === "ok" ? undefined : res.message);
      onChanged();
    } catch (e) {
      toast.push("danger", errorMessage(e));
    } finally {
      setTesting(false);
    }
  };

  return (
    <Card
      title={p.label}
      icon={Server}
      sub={t(p.kind === "block" ? "usenet.providers.kind.block" : "usenet.providers.kind.unlimited")}
      actions={
        <span className="row" style={{ gap: 6 }}>
          <ProviderStatusChip status={p.health.status} detail={p.health.detail} />
          <span className="t-3" style={{ fontSize: 11.5 }}>{t(`usenet.providers.source.${p.health.source}` as MessageKey)}</span>
        </span>
      }
      footer={
        <div className="row wrap">
          <Button size="sm" icon={PlugZap} onClick={() => void test()} pending={testing}
            disabled={!can("operator") || !p.enabled || !p.credentials_complete}
            title={!can("operator") ? t("usenet.operatorOnly") : !p.enabled ? t("usenet.providers.disabled") : !p.credentials_complete ? t("usenet.providers.needsCredentials") : undefined}>
            {t("usenet.providers.test")}
          </Button>
          <Button size="sm" variant="ghost" icon={Pencil} onClick={onEdit} disabled={!can("admin")} title={can("admin") ? undefined : t("usenet.adminOnly")}>
            {t("usenet.providers.edit")}
          </Button>
        </div>
      }
    >
      <div className="stack-sm">
        {!p.enabled ? <StatusChip tone="neutral">{t("usenet.providers.disabled")}</StatusChip> : null}
        {!p.credentials_complete ? <InlineState tone="warning">{t("usenet.providers.credentialsHint")}</InlineState> : null}
        {p.credentials_complete && p.status !== "ok" && p.status !== "unknown" && p.status !== "missing_credentials" && p.status_detail ? (
          <InlineState tone={p.status === "rate_limited" || p.status === "timeout" ? "warning" : "danger"}>{p.status_detail}</InlineState>
        ) : null}
        <dl className="kv">
          <dt>{t("usenet.providers.sabStatus")}</dt>
          <dd>{p.sab_server ? <SabStatusCell st={p.sab_status} /> : <span className="t-3">{t("usenet.providers.sabStatusNone")}</span>}</dd>
          <dt>{t("usenet.providers.msoStatus")}</dt>
          <dd><ProviderStatusChip status={p.status} detail={p.status_detail} /></dd>
          <dt>{t("usenet.providers.lastCheck")}</dt><dd>{relative(p.last_check_at)}</dd>
          <dt>{t("usenet.providers.lastOk")}</dt><dd>{relative(p.last_ok_at)}</dd>
          <dt>{t("usenet.providers.connections")}</dt>
          <dd>{t("usenet.providers.connectionsValue", { account: num(p.connections), mso: num(p.mso_connections) })}</dd>
          <dt>{t("usenet.providers.retention")}</dt>
          <dd>{p.retention_days ? t("usenet.providers.retentionValue", { days: num(p.retention_days) }) : "—"}</dd>
          <dt>{t("usenet.providers.sabServer")}</dt><dd className="mono">{p.sab_server ?? t("usenet.providers.notLinked")}</dd>
          <dt>{t("usenet.providers.credentials")}</dt>
          <dd>
            <StatusChip tone={p.credentials_complete ? "success" : "warning"}>
              {t(p.credentials_complete ? "usenet.providers.credentialsSet" : "usenet.providers.credentialsMissing")}
            </StatusChip>
          </dd>
        </dl>
        {p.kind === "block" ? (
          <div className="meter-row" title={t("usenet.providers.blockUsageHint")}>
            <span>{t("usenet.providers.blockUsage")}</span>
            <span className="t-2">
              {p.block_used_bytes === null ? t("usenet.providers.blockUnknown")
                : p.block_size_bytes ? `${bytes(p.block_used_bytes)} / ${bytes(p.block_size_bytes)} (${ratio(p.block_used_ratio)})` : bytes(p.block_used_bytes)}
            </span>
            {p.block_used_ratio !== null ? (
              <Progress value={p.block_used_ratio * 100} tone={p.block_used_ratio > 0.9 ? "danger" : p.block_used_ratio > 0.7 ? "warning" : undefined}
                label={t("usenet.providers.blockUsage")} thin />
            ) : null}
          </div>
        ) : null}
        {message ? <p className="t-2" style={{ margin: 0, fontSize: 12.5 }} role="status">{message}</p> : null}
      </div>
    </Card>
  );
}

const EMPTY: ProviderInput = { slug: "", label: "", kind: "unlimited", host: "", port: 563, ssl: true, connections: 10, enabled: true, notes: "" };

function ProviderForm({ open, onClose, existing, onSaved }: {
  open: boolean; onClose: () => void; existing: UsenetProvider | null; onSaved: () => void;
}) {
  const { t } = useI18n();
  const toast = useToast();
  const presets = useResource<{ items: ProviderPreset[] }>(open && !existing ? "/usenet/providers/presets" : null);
  const servers = useResource<SabServerList>(open ? "/usenet/sab/servers" : null);
  const [form, setForm] = useState<ProviderInput>(() => existing ? {
    label: existing.label, kind: existing.kind, host: existing.host, port: existing.port, ssl: existing.ssl,
    connections: existing.connections, retention_days: existing.retention_days, block_size_gb: existing.block_size_gb,
    sab_server: existing.sab_server, enabled: existing.enabled, notes: existing.notes,
  } : { ...EMPTY });
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (patch: Partial<ProviderInput>) => setForm((f) => ({ ...f, ...patch }));
  const id = (s: string) => `usenet-provider-${s}`;

  const save = async () => {
    setSaving(true);
    setError(null);
    const body: ProviderInput = { ...form };
    if (username) body.username = username;
    if (password) body.password = password;
    try {
      if (existing) await api.put(`/usenet/providers/${encodeURIComponent(existing.slug)}`, body);
      else await api.post("/usenet/providers", body);
      toast.push("success", t("usenet.providers.saved"), form.label);
      onSaved();
      onClose();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!existing) return;
    setDeleting(true);
    try {
      await api.del(`/usenet/providers/${encodeURIComponent(existing.slug)}`);
      toast.push("success", t("usenet.providers.deleted"), existing.label);
      setConfirmDelete(false);
      onSaved();
      onClose();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setDeleting(false);
    }
  };

  const serverItems = servers.data?.items ?? [];
  const secretPlaceholder = (s: "username" | "password") => existing?.credentials[s] === "ingesteld" ? t("usenet.form.secretSet") : "";

  return (
    <Modal open={open} onClose={onClose} icon={Server} title={t(existing ? "usenet.form.titleEdit" : "usenet.form.titleNew")}
      footer={
        <>
          {existing ? <Button variant="danger" icon={Trash2} onClick={() => setConfirmDelete(true)}>{t("usenet.providers.delete")}</Button> : null}
          <Button onClick={onClose}>{t("action.cancel")}</Button>
          <Button variant="primary" onClick={() => void save()} pending={saving}
            disabled={!form.label || !form.host || (!existing && !form.slug)}>{t("usenet.form.save")}</Button>
        </>
      }>
      <div className="stack">
        {!existing && presets.data?.items.length ? (
          <div className="row wrap" role="group" aria-label={t("usenet.form.presets")}>
            <span className="t-3" style={{ fontSize: 12.5 }}>{t("usenet.form.presets")}:</span>
            {presets.data.items.map((p) => (
              <Button key={p.slug} size="sm" variant="ghost" onClick={() => set({ slug: p.slug, label: p.label, kind: p.kind, host: p.host, port: p.port, ssl: p.ssl })}>
                {p.label}
              </Button>
            ))}
          </div>
        ) : null}
        {!existing ? (
          <Field label={t("usenet.form.slug")} hint={t("usenet.form.slugHint")} htmlFor={id("slug")}>
            <input id={id("slug")} className="input mono" value={form.slug ?? ""} maxLength={40} autoComplete="off"
              onChange={(e) => set({ slug: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "") })} />
          </Field>
        ) : null}
        <Field label={t("usenet.form.label")} htmlFor={id("label")}>
          <input id={id("label")} className="input" value={form.label ?? ""} maxLength={80} onChange={(e) => set({ label: e.target.value })} />
        </Field>
        <div>
          <span className="label">{t("usenet.form.kind")}</span>
          <Segmented label={t("usenet.form.kind")} value={form.kind ?? "unlimited"} onChange={(v) => set({ kind: v })}
            options={[{ value: "unlimited", label: t("usenet.providers.kind.unlimited") }, { value: "block", label: t("usenet.providers.kind.block") }]} />
        </div>
        <div className="grid" style={{ gap: 12 }}>
          <div className="span-8">
            <Field label={t("usenet.form.host")} htmlFor={id("host")}>
              <input id={id("host")} className="input mono" value={form.host ?? ""} autoComplete="off" onChange={(e) => set({ host: e.target.value.trim() })} />
            </Field>
          </div>
          <div className="span-4">
            <Field label={t("usenet.form.port")} htmlFor={id("port")}>
              <input id={id("port")} className="input" type="number" min={1} max={65535} value={form.port ?? 563} onChange={(e) => set({ port: Number(e.target.value) })} />
            </Field>
          </div>
        </div>
        <div className="row" style={{ gap: 10 }}>
          <Switch id={id("ssl")} checked={form.ssl ?? true} onChange={(v) => set({ ssl: v })} label={t("usenet.form.ssl")} />
          <label htmlFor={id("ssl")}>{t("usenet.form.ssl")}</label>
          <Switch id={id("enabled")} checked={form.enabled ?? true} onChange={(v) => set({ enabled: v })} label={t("usenet.form.enabled")} />
          <label htmlFor={id("enabled")}>{t("usenet.form.enabled")}</label>
        </div>
        <Field label={t("usenet.form.connections")} hint={t("usenet.form.connectionsHint")} htmlFor={id("conn")}>
          <input id={id("conn")} className="input" type="number" min={1} max={200} value={form.connections ?? 10} onChange={(e) => set({ connections: Number(e.target.value) })} />
        </Field>
        <Field label={t("usenet.form.retention")} htmlFor={id("ret")}>
          <input id={id("ret")} className="input" type="number" min={0} value={form.retention_days ?? ""}
            onChange={(e) => set({ retention_days: e.target.value === "" ? null : Number(e.target.value) })} />
        </Field>
        {form.kind === "block" ? (
          <Field label={t("usenet.form.blockSize")} htmlFor={id("block")}>
            <input id={id("block")} className="input" type="number" min={1} value={form.block_size_gb ?? ""}
              onChange={(e) => set({ block_size_gb: e.target.value === "" ? null : Number(e.target.value) })} />
          </Field>
        ) : null}
        <Field label={t("usenet.form.sabServer")} hint={servers.error ? t("usenet.form.sabServerUnavailable") : undefined} htmlFor={id("sab")}>
          {servers.error || !serverItems.length ? (
            <input id={id("sab")} className="input mono" value={form.sab_server ?? ""} onChange={(e) => set({ sab_server: e.target.value || null })} />
          ) : (
            <select id={id("sab")} className="input" value={form.sab_server ?? ""} onChange={(e) => set({ sab_server: e.target.value || null })}>
              <option value="">{t("usenet.form.sabServerNone")}</option>
              {serverItems.map((s) => <option key={s.name} value={s.name}>{s.displayname} ({s.name})</option>)}
            </select>
          )}
        </Field>
        <div className="grid" style={{ gap: 12 }}>
          <div className="span-6">
            <Field label={t("usenet.form.username")} hint={t("usenet.form.secretHint")} htmlFor={id("user")}>
              <input id={id("user")} className="input" autoComplete="off" value={username} placeholder={secretPlaceholder("username")} onChange={(e) => setUsername(e.target.value)} />
            </Field>
          </div>
          <div className="span-6">
            <Field label={t("usenet.form.password")} htmlFor={id("pass")}>
              <input id={id("pass")} className="input" type="password" autoComplete="new-password" value={password} placeholder={secretPlaceholder("password")} onChange={(e) => setPassword(e.target.value)} />
            </Field>
          </div>
        </div>
        <Field label={t("usenet.form.notes")} htmlFor={id("notes")}>
          <textarea id={id("notes")} className="input" rows={2} maxLength={1000} value={form.notes ?? ""} onChange={(e) => set({ notes: e.target.value })} />
        </Field>
        {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
      </div>
      <DangerDialog open={confirmDelete} onClose={() => setConfirmDelete(false)} onConfirm={() => void remove()} pending={deleting}
        title={t("usenet.providers.deleteTitle")} description={t("usenet.providers.deleteBody")} confirmLabel={t("usenet.providers.delete")}
        confirmWord={existing?.slug} icon={Trash2} />
    </Modal>
  );
}

/** Provider cards with status, test and add/edit (credentials write-only). */
export function ProvidersSection({ providers, reload }: { providers: UsenetProvider[]; reload: () => void }) {
  const { t } = useI18n();
  const [editing, setEditing] = useState<UsenetProvider | null>(null);
  if (!providers.length) {
    return <StateView kind="empty" title={t("usenet.providers.empty")} body={t("usenet.providers.emptyBody")} />;
  }
  return (
    <>
      <div className="instance-grid">
        {providers.map((p) => <ProviderCard key={p.slug} p={p} onEdit={() => setEditing(p)} onChanged={reload} />)}
      </div>
      {editing ? <ProviderForm key={editing.slug} open onClose={() => setEditing(null)} existing={editing} onSaved={reload} /> : null}
    </>
  );
}

/** Add-button + modal (used in the page header). */
export function AddProviderButton({ onSaved }: { onSaved: () => void }) {
  const { t } = useI18n();
  const { can } = useSession();
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant="primary" icon={Server} onClick={() => setOpen(true)} disabled={!can("admin")} title={can("admin") ? undefined : t("usenet.adminOnly")}>
        {t("usenet.providers.add")}
      </Button>
      {open ? <ProviderForm open onClose={() => setOpen(false)} existing={null} onSaved={onSaved} /> : null}
    </>
  );
}
