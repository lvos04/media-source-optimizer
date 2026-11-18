"use client";

import { KeyRound, Plus } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { DangerDialog } from "@/components/mg/domain";
import { Button, Card, CopyField, Field, InlineState, Modal, Skeleton, StatusChip, Switch } from "@/components/mg/primitives";
import { ErrorState, StateView, StaleMark } from "@/components/mg/states";
import { api, ApiError, coreRequest, errorMessage } from "@/lib/api/client";
import { useI18n } from "@/lib/i18n";

type Scope = "read" | "changes:approve";
interface MachineKey {
  id: string; name: string; scopes: Scope[] | null; last_four: string;
  created_at: string; last_used_at: string | null; revoked_at: string | null;
}
interface Created { item: MachineKey; key: string }

function requestId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
  const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** Alleen gemount voor een vastgestelde menselijke beheerder. Geheimen blijven buiten lijst/cache. */
export function MachineKeysCard() {
  const { t, date, dateTime, time, locale } = useI18n();
  const [items, setItems] = useState<MachineKey[] | null>(null);
  const [fetchError, setFetchError] = useState<ApiError | null>(null);
  const [fetching, setFetching] = useState(true);
  const [fetchedAt, setFetchedAt] = useState<number | null>(null);
  const [longLoading, setLongLoading] = useState(false);
  const [offline, setOffline] = useState(false);
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const [dialog, setDialog] = useState(false);
  const [created, setCreated] = useState<Created | null>(null);
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<Scope[]>([]);
  const [revoking, setRevoking] = useState<MachineKey | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [errorField, setErrorField] = useState<"name" | "scopes" | null>(null);
  const [notice, setNotice] = useState("");
  const [retryAt, setRetryAt] = useState<number | null>(null);
  const alive = useRef(false);
  const generation = useRef(0);
  const busy = useRef(false);
  const loadGeneration = useRef(0);
  const errorFocus = useRef<HTMLDivElement>(null);
  const createdFocus = useRef<HTMLSpanElement>(null);
  const heading = useRef<HTMLSpanElement>(null);
  const anchorShown = useRef(false);

  const close = useCallback(() => {
    generation.current += 1;
    setCreated(null); setDialog(false); setRevoking(null); setError(null); setErrorField(null);
  }, []);
  const load = useCallback(async () => {
    const attempt = ++loadGeneration.current;
    setFetching(true);
    try {
      const response = await api.get<{ items: MachineKey[] }>("/machine-keys");
      if (!alive.current || attempt !== loadGeneration.current) return;
      setItems(response.items); setFetchError(null); setFetchedAt(Date.now()); setNeedsRefresh(false);
    } catch (failure) {
      if (!alive.current || attempt !== loadGeneration.current) return;
      const failureError = failure as ApiError;
      setFetchError(failureError);
      if ([401, 403].includes(failureError.status)) { setItems(null); close(); }
    } finally {
      if (alive.current && attempt === loadGeneration.current) setFetching(false);
    }
  }, [close]);
  useEffect(() => {
    alive.current = true;
    void load();
    const lost = () => { setOffline(true); setNeedsRefresh(true); close(); };
    const restored = () => setOffline(false);
    const hidden = () => close();
    setOffline(!navigator.onLine);
    window.addEventListener("offline", lost); window.addEventListener("online", restored);
    window.addEventListener("pagehide", hidden);
    return () => {
      alive.current = false; generation.current += 1; loadGeneration.current += 1;
      window.removeEventListener("offline", lost); window.removeEventListener("online", restored);
      window.removeEventListener("pagehide", hidden);
    };
  }, [load, close]);
  useEffect(() => {
    if (!fetching) return;
    const timer = window.setTimeout(() => setLongLoading(true), 10_000);
    return () => { clearTimeout(timer); setLongLoading(false); };
  }, [fetching]);
  useEffect(() => { if (error) errorFocus.current?.focus(); }, [error]);
  useEffect(() => { if (created) createdFocus.current?.focus(); }, [created]);
  useEffect(() => {
    if (fetchedAt && !anchorShown.current && location.hash === "#machine-sleutels") {
      anchorShown.current = true;
      document.getElementById("machine-sleutels")?.scrollIntoView();
    }
  }, [fetchedAt]);
  useEffect(() => {
    if (!retryAt) return;
    const timer = window.setTimeout(() => setRetryAt(null), Math.max(0, retryAt - Date.now()));
    return () => clearTimeout(timer);
  }, [retryAt]);

  const stale = offline || needsRefresh || Boolean(fetchError);
  const usable = items !== null && !stale && !fetching && !pending && !retryAt;
  const refreshDisabled = fetching || offline || pending || Boolean(retryAt);
  const retryTime = retryAt ? new Date(Math.ceil(retryAt / 1000) * 1000).toLocaleTimeString(locale, {
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  }) : null;
  const retryNotice = retryTime ? t("machineKeys.error.retryAt", { time: retryTime }) : null;
  const mutationError = typeof error === "string" ? error
    : error instanceof ApiError && error.code === "machine_key_restore_pending" ? t("machineKeys.error.restorePending")
      : error instanceof ApiError && error.kind !== "offline" ? errorMessage(error) : t("machineKeys.error.uncertain");
  const fail = (failure: unknown) => {
    setError(failure);
    setErrorField(failure instanceof ApiError && ["invalid_name", "duplicate_name"].includes(failure.code ?? "") ? "name"
      : failure instanceof ApiError && ["scope_required", "unknown_scope"].includes(failure.code ?? "") ? "scopes" : null);
    if (failure instanceof ApiError && failure.status === 429 && failure.retryAfterSeconds !== null) {
      setRetryAt(Date.now() + failure.retryAfterSeconds * 1000);
    }
    if (!(failure instanceof ApiError) || failure.kind === "offline" || failure.status >= 500) setNeedsRefresh(true);
    if (failure instanceof ApiError && [401, 403].includes(failure.status)) {
      setItems(null); setFetchError(failure); close();
    }
  };
  const create = async () => {
    if (busy.current || !usable) return;
    if (!name.trim() || name.trim().length > 80 || /[\p{Cc}\p{Cf}\p{Cs}]/u.test(name)) {
      setErrorField("name"); setError(t("machineKeys.error.name")); return;
    }
    if (!scopes.length) { setErrorField("scopes"); setError(t("machineKeys.error.scopeRequired")); return; }
    busy.current = true; setPending(true); setError(null);
    const attempt = generation.current;
    try {
      const response = await api.post<Created>("/machine-keys", { name: name.trim(), scopes });
      if (!alive.current) return;
      if (attempt === generation.current) setCreated(response);
      await load();
    } catch (failure) {
      if (alive.current && attempt === generation.current) fail(failure);
      else if (alive.current) setNeedsRefresh(true);
    } finally {
      busy.current = false;
      if (alive.current) setPending(false);
    }
  };
  const revoke = async () => {
    if (!revoking || busy.current || !usable) return;
    const selected = revoking;
    const attempt = generation.current;
    busy.current = true; setPending(true); setError(null);
    try {
      const response = await coreRequest<{ item: MachineKey; already_revoked: boolean }>(`/machine-keys/${encodeURIComponent(selected.id)}/revoke`,
        { method: "POST", body: {}, idempotencyKey: requestId() });
      if (!alive.current) return;
      setItems((old) => old?.map((item) => item.id === selected.id ? response.item : item) ?? null);
      setNotice(t(response.already_revoked ? "machineKeys.alreadyRevoked" : "machineKeys.revoked"));
      if (attempt === generation.current) { close(); requestAnimationFrame(() => heading.current?.focus()); }
    } catch (failure) {
      if (alive.current && attempt === generation.current) fail(failure);
      else if (alive.current) setNeedsRefresh(true);
    } finally { busy.current = false; if (alive.current) setPending(false); }
  };
  const showDate = (value: string | null | undefined, never = false) => value === null && never
    ? t("machineKeys.list.never") : value && Number.isFinite(Date.parse(value))
      ? <time dateTime={value}>{date(value)}, {time(value)}</time> : t("machineKeys.list.unknown");
  const rights = (value: Scope[] | null) => {
    if (!Array.isArray(value) || value.some((s) => s !== "read" && s !== "changes:approve")) {
      return <><StatusChip tone="unknown">{t("machineKeys.list.unknownScopes")}</StatusChip><p>{t("machineKeys.list.unknownHint")}</p></>;
    }
    return value.length ? <div className="row wrap">{value.map((scope) => <StatusChip tone="neutral" key={scope}><span className="min-w-0">{t(scope === "read" ? "machineKeys.scope.read" : "machineKeys.scope.approve")} <code className="whitespace-nowrap">{scope}</code></span></StatusChip>)}</div> : t("machineKeys.list.noScopes");
  };
  if (fetchError && [401, 403].includes(fetchError.status)) return <InlineState tone="neutral" role="status">{t("machineKeys.error.forbidden")}</InlineState>;

  return <section id="machine-sleutels" className="machine-keys mt-4" aria-label={t("machineKeys.title")}>
    <Card title={<span ref={heading} tabIndex={-1}>{t("machineKeys.title")}</span>} sub={t("machineKeys.intro")} actions={<Button icon={Plus} disabled={!usable} onClick={() => { close(); setName(""); setScopes([]); setDialog(true); }}>{t("machineKeys.add")}</Button>}>
      <div className="row wrap mb-4"><Button aria-disabled={refreshDisabled || undefined} onClick={() => { if (!refreshDisabled) void load(); }}>{t("action.reload")}</Button>
        {fetchedAt ? <span>{t("machineKeys.list.updated", { time: dateTime(fetchedAt) })}</span> : null}
        <span>{t("machineKeys.list.timeZone", { zone: Intl.DateTimeFormat().resolvedOptions().timeZone })}</span>
      </div>
      {notice ? <p role="status">{notice}</p> : null}
      {!dialog && !revoking && retryNotice ? <p role="status">{retryNotice}</p> : null}
      {stale && items !== null ? (
        <>
          <StaleMark>{t("machineKeys.stale", { time: fetchedAt ? dateTime(fetchedAt) : t("machineKeys.list.unknown") })}</StaleMark>
          <p>{t(fetchError?.code === "machine_key_restore_pending" ? "machineKeys.error.restorePending" : "machineKeys.unavailable")}</p>
        </>
      ) : null}
      {fetching ? <p role="status">{t(longLoading ? "machineKeys.loadingLong" : items ? "machineKeys.list.refreshing" : "machineKeys.loading")}</p> : null}
      {fetching && items === null ? <div aria-busy="true"><Skeleton lines={3} /></div> : null}
      {fetchError && !items ? <ErrorState error={fetchError} onRetry={() => void load()} /> : null}
      {items?.length === 0 && !stale ? <StateView kind="empty" title={t("machineKeys.empty.title")} body={t("machineKeys.empty.body")} /> : null}
      {items?.length ? <table className="table table--stack" role="table" aria-busy={fetching}>
        <caption className="sr-only">{t("machineKeys.list.caption")}</caption>
        <thead role="rowgroup"><tr role="row">{(["name", "scopes", "dates", "status"] as const).map((key) => <th key={key} role="columnheader" scope="col">{t(`machineKeys.list.${key}`)}</th>)}</tr></thead>
        <tbody role="rowgroup">{items.map((item) => <tr role="row" key={item.id}>
          <td role="cell"><span className="machine-key-column-label" aria-hidden="true">{t("machineKeys.list.name")}</span><strong>{item.name}</strong><div>{t("machineKeys.list.lastFour")}: <code>{item.last_four}</code></div></td>
          <td role="cell"><span className="machine-key-column-label" aria-hidden="true">{t("machineKeys.list.scopes")}</span>{rights(item.scopes)}</td>
          <td role="cell"><span className="machine-key-column-label" aria-hidden="true">{t("machineKeys.list.dates")}</span><dl><dt>{t("machineKeys.list.created")}</dt><dd>{showDate(item.created_at)}</dd><dt>{t("machineKeys.list.lastUsed")}</dt><dd>{showDate(item.last_used_at, true)}</dd>{item.revoked_at ? <><dt>{t("machineKeys.list.revokedAt")}</dt><dd>{showDate(item.revoked_at)}</dd></> : null}</dl></td>
          <td role="cell"><span className="machine-key-column-label" aria-hidden="true">{t("machineKeys.list.status")}</span><div className="row wrap"><StatusChip tone={item.revoked_at === undefined ? "unknown" : "neutral"}>{t(item.revoked_at === null ? "machineKeys.list.active" : item.revoked_at ? "machineKeys.list.revoked" : "machineKeys.list.unknown")}</StatusChip>
            {!item.revoked_at ? <Button variant="danger" disabled={!usable} aria-label={t("machineKeys.revokeName", { name: item.name })} onClick={() => { close(); setRevoking(item); }}>{t("machineKeys.revoke")}</Button> : null}</div></td>
        </tr>)}</tbody>
      </table> : null}
    </Card>
    <Modal open={dialog} onClose={close} icon={KeyRound} title={<span ref={createdFocus} tabIndex={-1}>{t(created ? "machineKeys.created.title" : "machineKeys.form.title")}</span>}
      footer={created ? <Button onClick={close}>{t("machineKeys.created.close")}</Button> : <><Button onClick={close}>{t("action.cancel")}</Button><Button variant="primary" type="submit" form="machine-key-form" pending={pending} disabled={!usable}>{t("machineKeys.form.create")}</Button></>}>
      {created ? <div className="stack machine-key-dialog"><strong>{created.item.name}</strong>{rights(created.item.scopes)}<p>{t("machineKeys.created.warning")}</p><CopyField label={t("machineKeys.created.value")} value={created.key} /></div>
        : <form id="machine-key-form" className="stack machine-key-dialog" noValidate onSubmit={(event) => { event.preventDefault(); void create(); }}>
          {error ? (
            <div className="stack-sm">
              <div id="machine-key-error" ref={errorFocus} tabIndex={-1} role="alert">
                <InlineState tone="danger">
                  <p>{mutationError}</p>
                  {retryNotice ? <p className="mt-2">{retryNotice}</p> : null}
                </InlineState>
              </div>
              {errorField ? <Button className="self-start" onClick={() => document.getElementById(`machine-key-${errorField}`)?.focus()}>{t(errorField === "name" ? "machineKeys.form.name" : "machineKeys.form.scopes")}</Button> : null}
            </div>
          ) : null}
          <Field label={t("machineKeys.form.name")} htmlFor="machine-key-name"><input id="machine-key-name" data-autofocus className="input" required maxLength={80} autoComplete="off" aria-invalid={errorField === "name" || undefined} aria-describedby={`machine-key-name-hint${errorField === "name" ? " machine-key-error" : ""}`} disabled={pending} value={name} onChange={(event) => setName(event.target.value)} /></Field>
          <p id="machine-key-name-hint">{t("machineKeys.form.nameHint")}</p>
          <fieldset id="machine-key-scopes" tabIndex={-1} className="stack" aria-busy={pending} aria-describedby={errorField === "scopes" ? "machine-key-error" : undefined}><legend>{t("machineKeys.form.scopes")}</legend><p>{t("machineKeys.form.scopesHint")}</p>
            {(["read", "changes:approve"] as Scope[]).map((scope, index) => <div key={scope} className="flex items-start justify-between gap-4"><div className="min-w-0"><label htmlFor={`machine-key-scope-${index}`}>{t(index ? "machineKeys.scope.approve" : "machineKeys.scope.read")}</label><code className="block [overflow-wrap:anywhere]">{scope}</code><p id={`machine-key-scope-hint-${index}`}>{t(index ? "machineKeys.scope.approveHint" : "machineKeys.scope.readHint")}</p></div><Switch id={`machine-key-scope-${index}`} describedBy={`machine-key-scope-hint-${index}`} label={t(index ? "machineKeys.scope.approve" : "machineKeys.scope.read")} disabled={pending} checked={scopes.includes(scope)} onChange={(checked) => setScopes((old) => checked ? [...old, scope] : old.filter((s) => s !== scope))} /></div>)}
          </fieldset><p>{t("machineKeys.form.limits")}</p>
          {pending ? <p role="status">{t("machineKeys.form.creating")}</p> : null}
        </form>}
    </Modal>
    <DangerDialog open={Boolean(revoking)} onClose={close} onConfirm={() => void revoke()} title={t("machineKeys.revokeTitle")} description={t("machineKeys.revokeBody")} confirmLabel={t("machineKeys.revokeConfirm")} confirmDisabled={!usable} pending={pending}
      facts={revoking ? [{ label: t("machineKeys.list.name"), value: revoking.name }, { label: t("machineKeys.list.scopes"), value: rights(revoking.scopes) }, { label: t("machineKeys.list.lastFour"), value: revoking.last_four }] : []}>
      <div className="machine-key-dialog stack-sm">
        {error ? (
          <div ref={errorFocus} tabIndex={-1} role="alert">
            <InlineState tone="danger">
              <p>{mutationError}</p>
              {retryNotice ? <p className="mt-2">{retryNotice}</p> : null}
            </InlineState>
          </div>
        ) : null}
        {pending ? <p role="status">{t("machineKeys.revoking")}</p> : null}
      </div>
    </DangerDialog>
  </section>;
}
