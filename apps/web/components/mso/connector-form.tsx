"use client";

import { PlugZap, Save } from "lucide-react";
import { useState } from "react";

import { ConnectorLogo, ConnectorStatusChip } from "@/components/mg/domain";
import { Button, Field, InlineState, StatusChip, Switch } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { api, errorMessage } from "@/lib/api/client";
import { itemsOf, type Connector, type ConnectorTestResult } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";

const ORDER = ["prowlarr", "sabnzbd", "qbittorrent", "mediaguard"];
/** Connectors that are not required for MSO to work. */
export const OPTIONAL_CONNECTORS = new Set(["mediaguard"]);

/** Stable display order: Prowlarr, SABnzbd, qBittorrent, MediaGuard, then the rest by label. */
export function sortConnectors(rows: Connector[]): Connector[] {
  const rank = (c: Connector) => { const i = ORDER.indexOf(String(c.id)); return i === -1 ? ORDER.length : i; };
  return [...rows].sort((a, b) => rank(a) - rank(b) || a.label.localeCompare(b.label));
}

function capabilityList(value: ConnectorTestResult["capabilities"]): string[] {
  if (!value) return [];
  if (Array.isArray(value)) return value.map(String);
  return Object.entries(value).filter(([, v]) => v !== false && v !== null).map(([k]) => k);
}

/**
 * Edit one connector (Prowlarr, SABnzbd, qBittorrent, MediaGuard, …):
 * URL, enabled switch and the write-only credential fields from `secret_fields`.
 * - **Test** → `POST /api/v1/connectors/{id}/test {url, secrets}` (only filled-in secrets; the backend
 *   falls back to the stored ones).
 * - **Bewaar** → `PUT /api/v1/connectors/{id} {url, enabled, secrets?}`; afterwards the inputs are
 *   cleared and only "ingesteld" is shown — secrets are never echoed.
 * Pass `canEdit={false}` + `disabledReason` for users who may not change connectors.
 */
export function ConnectorForm({ connector, onSaved, canEdit = true, disabledReason, optional }: {
  connector: Connector; onSaved?: () => void | Promise<void>; canEdit?: boolean; disabledReason?: string; optional?: boolean;
}) {
  const { t, num, relative } = useI18n();
  const [url, setUrl] = useState(connector.url ?? "");
  const [enabled, setEnabled] = useState(connector.enabled);
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [result, setResult] = useState<ConnectorTestResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [urlError, setUrlError] = useState<string | null>(null);
  const [pending, setPending] = useState<"test" | "save" | null>(null);
  const [saved, setSaved] = useState(false);

  const filled = Object.fromEntries(Object.entries(secrets).filter(([, v]) => v !== ""));
  const dirty = url !== (connector.url ?? "") || enabled !== connector.enabled || Object.keys(filled).length > 0;
  const idBase = `connector-${connector.id}`;

  const validUrl = () => {
    if (!url.trim()) { setUrlError(t("connector.urlRequired")); return false; }
    setUrlError(null);
    return true;
  };

  const test = async () => {
    if (!validUrl()) return;
    setPending("test");
    setTestError(null);
    setResult(null);
    try {
      setResult(await api.post<ConnectorTestResult>(`/connectors/${encodeURIComponent(connector.id)}/test`, { url: url.trim(), secrets: filled }));
    } catch (e) {
      setTestError(errorMessage(e));
    } finally {
      setPending(null);
    }
  };

  const save = async () => {
    if (enabled && !validUrl()) return;
    setPending("save");
    setSaveError(null);
    try {
      await api.put(`/connectors/${encodeURIComponent(connector.id)}`, { url: url.trim(), enabled, ...(Object.keys(filled).length ? { secrets: filled } : {}) });
      setSecrets({});
      setSaved(true);
      await onSaved?.();
    } catch (e) {
      setSaveError(errorMessage(e));
    } finally {
      setPending(null);
    }
  };

  const caps = capabilityList(result?.capabilities ?? null);
  const disabled = !canEdit;

  return (
    <article className="instance" id={idBase} data-testid={idBase} style={{ scrollMarginTop: 80 }} aria-labelledby={`${idBase}-title`}>
      <div className="instance-head">
        <ConnectorLogo connector={connector} />
        <div className="grow">
          <div className="instance-name" id={`${idBase}-title`}>{connector.label}</div>
          <div className="instance-id">{connector.id}{connector.version ? ` · ${connector.version}` : ""}{connector.last_checked_at ? ` · ${t("connector.checked")} ${relative(connector.last_checked_at)}` : ""}</div>
        </div>
        {optional ? <StatusChip tone="neutral" icon={null} outline>{t("connector.optional")}</StatusChip> : null}
        <ConnectorStatusChip connector={connector} />
      </div>
      {connector.last_error ? <InlineState tone="warning">{connector.last_error}</InlineState> : null}
      {disabled && disabledReason ? <InlineState tone="neutral">{disabledReason}</InlineState> : null}

      <Field label={t("connector.url")} htmlFor={`${idBase}-url`} hint={t("connector.urlHint")} error={urlError}>
        <input id={`${idBase}-url`} className="input mono" value={url} disabled={disabled} inputMode="url" autoComplete="off" spellCheck={false}
          aria-invalid={Boolean(urlError)} onChange={(e) => { setUrl(e.target.value); setSaved(false); }} />
      </Field>
      {connector.secret_fields.map((f) => (
        <Field key={f.name} label={<>{f.label} <span className="t-3" style={{ fontWeight: 400 }}>· {f.status === "ingesteld" ? t("status.set") : t("status.empty")}</span></>} htmlFor={`${idBase}-${f.name}`}>
          <input id={`${idBase}-${f.name}`} type="password" className="input" autoComplete="new-password" disabled={disabled}
            placeholder={f.status === "ingesteld" ? t("connector.secretSet") : t("connector.secretEmpty")}
            value={secrets[f.name] ?? ""} onChange={(e) => { setSecrets((s) => ({ ...s, [f.name]: e.target.value })); setSaved(false); }} />
        </Field>
      ))}
      <label className="row t-2" style={{ fontSize: 13 }}>
        <Switch checked={enabled} onChange={(v) => { setEnabled(v); setSaved(false); }} label={t("connector.enabled")} disabled={disabled} />{t("connector.enabled")}
      </label>

      {result ? (
        <InlineState tone={result.ok ? "success" : "danger"} role="status">
          <strong>{result.ok ? t("connector.testOk") : t("connector.testFailed")}</strong>
          {result.message ? <div>{result.message}</div> : null}
          <div className="t-2" style={{ fontSize: 12, marginTop: 4 }}>
            {result.version ? <>{t("connector.version")} <span className="mono">{result.version}</span> · </> : null}
            {result.latency_ms !== null && result.latency_ms !== undefined ? <>{t("connector.latency")} {num(result.latency_ms)} ms</> : null}
          </div>
          {caps.length ? <div className="row wrap" style={{ gap: 4, marginTop: 6 }}>{caps.map((c) => <span key={c} className="tag">{c}</span>)}</div> : null}
        </InlineState>
      ) : null}
      {testError ? <InlineState tone="danger" role="alert">{t("connector.testFailed")}: {testError}</InlineState> : null}
      {saveError ? <InlineState tone="danger" role="alert">{t("toast.failed", { message: saveError })}</InlineState> : null}
      {saved && !dirty ? <InlineState tone="success" role="status">{t("connector.saved", { label: connector.label })}</InlineState> : null}

      <div className="row wrap">
        <Button size="sm" icon={PlugZap} pending={pending === "test"} disabled={disabled || pending === "save"} title={disabled ? disabledReason : undefined} onClick={() => void test()}>{t("action.test")}</Button>
        <Button size="sm" variant="primary" icon={Save} pending={pending === "save"} disabled={disabled || !dirty || pending === "test"} title={disabled ? disabledReason : undefined} onClick={() => void save()}>{t("action.store")}</Button>
      </div>
    </article>
  );
}

/**
 * All connectors from `GET /api/v1/connectors` as editable ConnectorForms (Instellingen → Verbindingen).
 * `only` limits the list (e.g. `only={["mediaguard"]}` on the MediaGuard page).
 */
export function ConnectorList({ only, canEdit = true, disabledReason }: { only?: string[]; canEdit?: boolean; disabledReason?: string }) {
  const { t } = useI18n();
  const connectors = useResource<Connector[] | { items: Connector[] }>("/connectors");
  return (
    <ResourceView resource={connectors} endpoint="GET /api/v1/connectors" isEmpty={(d) => itemsOf(d).filter((c) => !only || only.includes(String(c.id))).length === 0}
      empty={<StateView kind="empty" title={t("connector.empty")} />}>
      {(data) => (
        <div className="instance-grid">
          {sortConnectors(itemsOf(data)).filter((c) => !only || only.includes(String(c.id))).map((c) => (
            <ConnectorForm key={c.id} connector={c} optional={OPTIONAL_CONNECTORS.has(String(c.id))} canEdit={canEdit} disabledReason={disabledReason} onSaved={() => connectors.reload()} />
          ))}
        </div>
      )}
    </ResourceView>
  );
}
