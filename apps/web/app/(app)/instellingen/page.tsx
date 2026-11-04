"use client";

import { Info, KeyRound, Plus, Search, Settings, UserCheck, UserX } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";

import { Button, Card, Field, InlineState, Modal, PageHeader, StatusChip, Tabs } from "@/components/mg/primitives";
import { NO_DRAFT, SettingField, type Draft } from "@/components/mg/setting-field";
import { ErrorState, ResourceView, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { ConnectorList } from "@/components/mso/connector-form";
import { ModeSettingsCard } from "@/components/mso/mode-settings";
import { api, ApiError, errorMessage } from "@/lib/api/client";
import { itemsOf, ROLES, type Role, type SettingDef, type SettingsPayload, type UserRow } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

const CONNECTIONS = "verbindingen";
const USERS = "gebruikers";
/** Settings category that also hosts the operating-mode section (deep link `/instellingen?tab=systeem#modus`). */
const SYSTEM = "systeem";

export default function SettingsPage() {
  return <Suspense><SettingsView /></Suspense>;
}

function SettingsView() {
  const { t, tp } = useI18n();
  const router = useRouter();
  const params = useSearchParams();
  const settings = useResource<SettingsPayload>("/settings");
  const [query, setQuery] = useState(params.get("q") ?? "");

  const categories = useMemo(() => {
    const map = new Map<string, number>();
    map.set(SYSTEM, 0); // always present: the Bedrijfsmodus section lives here
    (settings.data?.definitions ?? []).forEach((d) => map.set(d.category, (map.get(d.category) ?? 0) + 1));
    return [...map.entries()];
  }, [settings.data]);
  const requested = params.get("tab");
  const tab = requested && (requested === CONNECTIONS || requested === USERS || categories.some(([c]) => c === requested))
    ? requested : SYSTEM;
  const select = (value: string) => {
    const qs = new URLSearchParams(params.toString());
    qs.set("tab", value);
    router.replace(`/instellingen?${qs.toString()}`, { scroll: false });
    setQuery("");
  };

  const items = [
    ...categories.map(([c, n]) => ({ value: c, label: c, count: n })),
    { value: CONNECTIONS, label: t("settings.tab.connections") },
    { value: USERS, label: t("settings.tab.users") },
  ];

  return (
    <>
      <PageHeader title={t("settings.title")} sub={t("settings.sub")} />
      <div style={{ marginBottom: 16, overflowX: "auto" }}><Tabs label={t("settings.tabs")} value={tab} onSelect={select} items={items} /></div>
      {tab === SYSTEM ? <div style={{ marginBottom: 16 }}><ModeSettingsCard /></div> : null}
      {tab === CONNECTIONS ? <ConnectionsTab />
        : tab === USERS ? <UsersTab />
          : settings.loading ? <StateView kind="loading" />
            : settings.error && !settings.data ? <ErrorState error={settings.error} onRetry={() => void settings.reload()} endpoint="GET /api/v1/settings" />
              : settings.data ? (
                <SchemaSettings payload={settings.data} category={tab} query={query} onQuery={setQuery} reload={settings.reload} countLabel={(n) => tp("settings.count", n)} />
              ) : null}
    </>
  );
}

function ConnectionsTab() {
  const { t } = useI18n();
  const { can } = useSession();
  return (
    <div className="stack">
      <p className="t-2" style={{ fontSize: 13, maxWidth: 760, margin: 0 }}>{t("settings.connectionsSub")}</p>
      <ConnectorList canEdit={can("admin")} disabledReason={t("connector.adminOnly")} />
    </div>
  );
}

/** Map a PUT /settings validation error onto the offending keys (pydantic `loc` or key mentioned in the message). */
function fieldErrors(error: unknown, keys: string[]): { byKey: Record<string, string>; rest: string | null } {
  const byKey: Record<string, string> = {};
  if (error instanceof ApiError && Array.isArray(error.detail)) {
    for (const item of error.detail as Array<{ loc?: unknown[]; msg?: string }>) {
      const key = item.loc?.map(String).find((p) => keys.includes(p));
      if (key && item.msg) byKey[key] = item.msg;
    }
  } else if (error instanceof ApiError && error.detail && typeof error.detail === "object" && !Array.isArray(error.detail)) {
    const errors = (error.detail as { errors?: Record<string, string> }).errors;
    if (errors) for (const [k, v] of Object.entries(errors)) if (keys.includes(k)) byKey[k] = String(v);
  }
  if (!Object.keys(byKey).length) {
    const message = errorMessage(error);
    const key = keys.find((k) => message.includes(k));
    if (key) byKey[key] = message;
    else return { byKey, rest: message };
  }
  return { byKey, rest: null };
}

function SchemaSettings({ payload, category, query, onQuery, reload, countLabel }: {
  payload: SettingsPayload; category: string; query: string; onQuery: (q: string) => void; reload: () => Promise<void>; countLabel: (n: number) => string;
}) {
  const { t, tp } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const canEdit = can("operator");

  const defs = payload.definitions;
  const byKey = useMemo(() => Object.fromEntries(defs.map((d) => [d.key, d])), [defs]);
  const q = query.trim().toLowerCase();
  const shown: SettingDef[] = q
    ? defs.filter((d) => [d.key, d.label, d.help ?? "", d.category].join(" ").toLowerCase().includes(q))
    : defs.filter((d) => d.category === category);
  const dirtyKeys = Object.keys(drafts).filter((k) => drafts[k] !== NO_DRAFT);

  const save = async () => {
    setSaving(true);
    setErrors({});
    setGlobalError(null);
    try {
      await api.put("/settings", { values: Object.fromEntries(dirtyKeys.map((k) => [k, drafts[k]])) });
      const restart = dirtyKeys.some((k) => byKey[k]?.restart);
      toast.push("success", t("settings.saved"), restart ? t("settings.savedRestart") : undefined);
      setDrafts({});
      await reload();
    } catch (error) {
      const { byKey: fe, rest } = fieldErrors(error, dirtyKeys);
      setErrors(fe);
      setGlobalError(rest);
      toast.push("danger", t("settings.saveFailed"), errorMessage(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="stack">
      {!canEdit ? <InlineState tone="neutral">{t("settings.readOnlyRole")}</InlineState> : null}
      <div className="search-field">
        <Search className="ico" aria-hidden="true" />
        <input className="input" type="search" placeholder={t("settings.search")} aria-label={t("settings.searchLabel")} value={query} onChange={(e) => onQuery(e.target.value)} data-testid="settings-search" />
      </div>
      <section className="card" aria-label={q ? t("settings.searchLabel") : category}>
        <div className="card-header" style={{ borderBottom: "1px solid var(--mg-border-soft)" }}>
          <h2 className="card-title"><Settings className="ico" aria-hidden="true" />{q ? t("settings.searchLabel") : category}</h2>
          <span className="card-sub">{countLabel(shown.length)}</span>
        </div>
        {shown.length === 0 ? <StateView kind="empty" compact title={q ? t("settings.noResults", { q: query }) : t("settings.noneInCategory")} />
          : shown.map((def) => (
            <SettingField
              key={def.key}
              def={def}
              value={payload.values[def.key]}
              draft={def.key in drafts ? drafts[def.key] : NO_DRAFT}
              error={errors[def.key]}
              disabled={!canEdit}
              onDraft={(value) => { setDrafts((d) => ({ ...d, [def.key]: value })); setErrors((e) => { const n = { ...e }; delete n[def.key]; return n; }); }}
            />
          ))}
      </section>
      {dirtyKeys.length || Object.keys(errors).length || globalError ? (
        <div className="savebar" role="region" aria-label={tp("settings.saveBar", dirtyKeys.length)}>
          <Info className="ico" aria-hidden="true" style={{ color: "var(--mg-accent)" }} />
          <span className="grow" style={{ fontSize: 13 }}>
            <strong>{tp("settings.saveBar", dirtyKeys.length)}</strong>
            {Object.keys(errors).length ? <> · <span className="t-danger">{t("settings.saveBarErrors", { count: Object.keys(errors).length })}</span></> : null}
            {globalError ? <> · <span className="t-danger" role="alert">{globalError}</span></> : null}
          </span>
          <Button size="sm" variant="ghost" onClick={() => { setDrafts({}); setErrors({}); setGlobalError(null); }}>{t("action.discard")}</Button>
          <Button size="sm" variant="primary" pending={saving} disabled={!dirtyKeys.length || !canEdit} title={!canEdit ? t("settings.readOnlyRole") : undefined} onClick={() => void save()} data-testid="settings-save">{t("action.save")}</Button>
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- users
function UsersTab() {
  const { t, dateTime } = useI18n();
  const { can, user: me } = useSession();
  const toast = useToast();
  const users = useResource<UserRow[] | { items: UserRow[] }>(can("admin") ? "/users" : null);
  const [adding, setAdding] = useState(false);
  const [pwdFor, setPwdFor] = useState<UserRow | null>(null);
  const [form, setForm] = useState({ username: "", password: "", role: "viewer" as Role });
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);

  if (!can("admin")) return <InlineState tone="warning">{t("users.adminOnly")}</InlineState>;

  const patch = async (user: UserRow, body: Record<string, unknown>) => {
    setPending(`patch-${user.id}`);
    try {
      await api.patch(`/users/${encodeURIComponent(String(user.id))}`, body);
      toast.push("success", t("users.updated"));
      await users.reload();
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: errorMessage(e) }));
    } finally {
      setPending(null);
    }
  };

  const create = async () => {
    setPending("create");
    setError(null);
    try {
      await api.post("/users", { username: form.username.trim(), password: form.password, role: form.role });
      toast.push("success", t("users.createdToast", { name: form.username.trim() }));
      setAdding(false);
      setForm({ username: "", password: "", role: "viewer" });
      await users.reload();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPending(null);
    }
  };

  const setPassword = async () => {
    if (!pwdFor) return;
    setPending("password");
    setError(null);
    try {
      await api.patch(`/users/${encodeURIComponent(String(pwdFor.id))}`, { password: form.password });
      toast.push("success", t("users.updated"));
      setPwdFor(null);
      setForm((f) => ({ ...f, password: "" }));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPending(null);
    }
  };

  const createValid = form.username.trim().length >= 2 && form.password.length >= 12;

  return (
    <Card title={t("users.title")} sub={t("users.sub")} flush actions={<Button variant="primary" size="sm" icon={Plus} onClick={() => { setError(null); setForm({ username: "", password: "", role: "viewer" }); setAdding(true); }}>{t("users.add")}</Button>}>
      <ResourceView resource={users} endpoint="GET /api/v1/users" isEmpty={(d) => itemsOf(d).length === 0} empty={<StateView kind="empty" title={t("users.empty")} />}>
        {(data) => (
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel (horizontaal scrollbaar)">
            <table className="table table--stack">
              <thead><tr><th>{t("users.username")}</th><th>{t("users.role")}</th><th>{t("users.lastLogin")}</th><th>{t("users.status")}</th><th><span className="sr-only">{t("action.details")}</span></th></tr></thead>
              <tbody>
                {itemsOf(data).map((u) => {
                  const self = u.username === me?.username;
                  return (
                    <tr key={u.id}>
                      <td className="cell-primary"><div className="cell-title">{u.username}</div>{u.created_at ? <div className="cell-sub">{t("users.created")} {dateTime(u.created_at)}</div> : null}</td>
                      <td data-label={t("users.role")}>
                        <select className="select input--sm" style={{ width: 150 }} aria-label={t("users.role")} value={u.role} disabled={self || pending !== null} title={self ? t("users.self") : undefined} onChange={(e) => void patch(u, { role: e.target.value })}>
                          {ROLES.map((r) => <option key={r} value={r}>{t(`role.${r}`)}</option>)}
                        </select>
                      </td>
                      <td data-label={t("users.lastLogin")}>{u.last_login_at ? dateTime(u.last_login_at) : t("users.never")}</td>
                      <td data-label={t("users.status")}>{u.disabled ? <StatusChip tone="danger">{t("users.disabled")}</StatusChip> : <StatusChip tone="success">{t("users.active")}</StatusChip>}</td>
                      <td>
                        <div className="row wrap" style={{ justifyContent: "flex-end" }}>
                          <Button size="sm" icon={KeyRound} onClick={() => { setError(null); setForm((f) => ({ ...f, password: "" })); setPwdFor(u); }}>{t("users.resetPassword")}</Button>
                          {u.disabled
                            ? <Button size="sm" icon={UserCheck} disabled={self} title={self ? t("users.self") : undefined} pending={pending === `patch-${u.id}`} onClick={() => void patch(u, { disabled: false })}>{t("users.enable")}</Button>
                            : <Button size="sm" variant="danger" icon={UserX} disabled={self} title={self ? t("users.self") : undefined} pending={pending === `patch-${u.id}`} onClick={() => void patch(u, { disabled: true })}>{t("users.disable")}</Button>}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </ResourceView>

      <Modal open={adding} onClose={() => setAdding(false)} title={t("users.add")} icon={Plus}
        footer={<><Button onClick={() => setAdding(false)}>{t("action.cancel")}</Button><Button variant="primary" pending={pending === "create"} disabled={!createValid} title={!createValid ? t("auth.passwordHint") : undefined} onClick={() => void create()}>{t("action.add")}</Button></>}>
        <div className="stack">
          {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
          <Field label={t("users.username")} htmlFor="new-user"><input id="new-user" className="input" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} autoComplete="off" /></Field>
          <Field label={t("auth.password")} htmlFor="new-pass" hint={t("auth.passwordHint")}><input id="new-pass" type="password" className="input" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} autoComplete="new-password" /></Field>
          <Field label={t("users.role")} htmlFor="new-role">
            <select id="new-role" className="select" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>{ROLES.map((r) => <option key={r} value={r}>{t(`role.${r}`)}</option>)}</select>
          </Field>
        </div>
      </Modal>

      <Modal open={Boolean(pwdFor)} onClose={() => setPwdFor(null)} title={`${t("users.resetPassword")} · ${pwdFor?.username ?? ""}`} icon={KeyRound}
        footer={<><Button onClick={() => setPwdFor(null)}>{t("action.cancel")}</Button><Button variant="primary" pending={pending === "password"} disabled={form.password.length < 12} title={form.password.length < 12 ? t("auth.passwordHint") : undefined} onClick={() => void setPassword()}>{t("action.save")}</Button></>}>
        <div className="stack">
          {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
          <Field label={t("users.newPassword")} htmlFor="reset-pass" hint={t("auth.passwordHint")}><input id="reset-pass" type="password" className="input" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} autoComplete="new-password" /></Field>
        </div>
      </Modal>
    </Card>
  );
}
