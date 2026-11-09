"use client";

import { PlugZap, Save } from "lucide-react";
import { useState } from "react";

import { Button, Field, InlineState, Modal, Switch } from "@/components/mg/primitives";
import { api, errorMessage } from "@/lib/api/client";
import type { DirectIndexer, IndexerTestResult, Protocol } from "@/lib/api/search";
import { useI18n } from "@/lib/i18n";

type Kind = DirectIndexer["kind"];
type Scope = "normal" | "anime";

/** Add/edit a direct Newznab/Torznab/Spotweb indexer (admin). Test uses the unsaved values; the API key is
 * write-only (never shown again). */
export function DirectIndexerDialog({ protocol, existing, initialKind, onClose, onSaved }: {
  protocol: Protocol; existing?: DirectIndexer | null; initialKind?: Kind; onClose: () => void; onSaved: () => void | Promise<void>;
}) {
  const { t, num } = useI18n();
  const kinds: Kind[] = protocol === "torrent" ? ["torznab"] : ["newznab", "spotweb"];
  const startKind: Kind = existing?.kind ?? (initialKind && kinds.includes(initialKind) ? initialKind : kinds[0]);
  const [kind, setKind] = useState<Kind>(startKind);
  const [label, setLabel] = useState(existing?.label ?? (startKind === "spotweb" && !existing ? "Spotweb" : ""));
  const [url, setUrl] = useState(existing?.url ?? "");
  const [apiPath, setApiPath] = useState(existing?.api_path ?? (startKind === "spotweb" ? "/index.php?page=newznabapi" : "/api"));
  const [apiKey, setApiKey] = useState("");
  const [scopes, setScopes] = useState<Scope[]>(existing?.scopes ?? ["normal", "anime"]);
  const [priv, setPriv] = useState(existing?.private ?? protocol === "usenet");
  const [enabled, setEnabled] = useState(existing?.enabled ?? true);
  const [rate, setRate] = useState(String(existing?.rate_per_min ?? 30));
  const [quota, setQuota] = useState(String(existing?.daily_quota ?? 0));
  const [timeout, setTimeoutS] = useState(String(existing?.timeout_s ?? 15));
  const [pending, setPending] = useState<"test" | "save" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<IndexerTestResult | null>(null);

  const toggleScope = (s: Scope) => setScopes((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]));
  const changeKind = (k: Kind) => {
    setKind(k);
    if (k === "spotweb" && apiPath === "/api") setApiPath("/index.php?page=newznabapi");
    if (k !== "spotweb" && apiPath === "/index.php?page=newznabapi") setApiPath("/api");
  };

  const test = async () => {
    setPending("test");
    setError(null);
    setResult(null);
    try {
      setResult(await api.post<IndexerTestResult>("/indexers/direct/test", {
        kind, url: url.trim(), api_path: apiPath, api_key: apiKey || undefined, slug: existing?.slug,
      }));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPending(null);
    }
  };

  const save = async () => {
    if (!scopes.length) { setError(t("idx.form.scopeRequired")); return; }
    setPending("save");
    setError(null);
    const body = {
      label: label.trim() || url.trim(), url: url.trim(), api_path: apiPath, scopes, private: priv, enabled,
      rate_per_min: Number(rate) || 30, daily_quota: Number(quota) || 0, timeout_s: Number(timeout) || 15,
      ...(apiKey ? { api_key: apiKey } : {}),
    };
    try {
      if (existing) await api.patch(`/indexers/direct/${encodeURIComponent(existing.slug)}`, body);
      else await api.post("/indexers/direct", { kind, ...body });
      await onSaved();
      onClose();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPending(null);
    }
  };

  return (
    <Modal open onClose={onClose} title={existing ? t("idx.edit") : t("idx.add")}
      footer={<>
        <Button onClick={onClose}>{t("action.cancel")}</Button>
        <Button icon={PlugZap} pending={pending === "test"} disabled={!url.trim() || pending === "save"} onClick={() => void test()}>{t("action.test")}</Button>
        <Button variant="primary" icon={Save} pending={pending === "save"} disabled={!url.trim() || pending === "test"} onClick={() => void save()}>{t("action.store")}</Button>
      </>}>
      <div className="stack">
        {!existing && kinds.length > 1 ? (
          <Field label={t("idx.form.kind")} htmlFor="ix-kind">
            <select id="ix-kind" className="select" value={kind} onChange={(e) => changeKind(e.target.value as Kind)}>
              {kinds.map((k) => <option key={k} value={k}>{t(`idx.form.kind.${k}`)}</option>)}
            </select>
          </Field>
        ) : null}
        {kind === "spotweb" && !existing ? <InlineState tone="info">{t("idx.form.spotwebHint")}</InlineState> : null}
        <Field label={t("idx.form.label")} htmlFor="ix-label">
          <input id="ix-label" className="input" value={label} maxLength={80} onChange={(e) => setLabel(e.target.value)} />
        </Field>
        <Field label={t("idx.form.url")} hint={t("idx.form.urlHint")} htmlFor="ix-url">
          <input id="ix-url" className="input mono" value={url} inputMode="url" autoComplete="off" spellCheck={false} onChange={(e) => setUrl(e.target.value)} />
        </Field>
        <Field label={t("idx.form.apiPath")} hint={t("idx.form.apiPathHint")} htmlFor="ix-path">
          <input id="ix-path" className="input mono" value={apiPath} onChange={(e) => setApiPath(e.target.value)} />
        </Field>
        <Field label={t("idx.form.apiKey")} htmlFor="ix-key">
          <input id="ix-key" type="password" className="input" autoComplete="new-password" value={apiKey}
            placeholder={existing?.api_key === "ingesteld" ? t("idx.form.apiKeySet") : t("idx.form.apiKeyEmpty")} onChange={(e) => setApiKey(e.target.value)} />
        </Field>
        <fieldset className="stack-sm" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="label">{t("idx.form.scopes")}</legend>
          <div className="row wrap" style={{ gap: 14 }}>
            {(["normal", "anime"] as Scope[]).map((s) => (
              <label key={s} className="row" style={{ gap: 6 }}>
                <input type="checkbox" className="check" checked={scopes.includes(s)} onChange={() => toggleScope(s)} />{t(`idx.scope.${s}`)}
              </label>
            ))}
          </div>
          <p className="hint">{t("idx.form.scopesHint")}</p>
        </fieldset>
        <div className="row wrap" style={{ gap: 12 }}>
          <Field label={t("idx.form.rate")} htmlFor="ix-rate"><input id="ix-rate" className="input" inputMode="numeric" style={{ width: 100 }} value={rate} onChange={(e) => setRate(e.target.value)} /></Field>
          <Field label={t("idx.form.quota")} htmlFor="ix-quota"><input id="ix-quota" className="input" inputMode="numeric" style={{ width: 120 }} value={quota} onChange={(e) => setQuota(e.target.value)} /></Field>
          <Field label={t("idx.form.timeout")} htmlFor="ix-timeout"><input id="ix-timeout" className="input" inputMode="numeric" style={{ width: 90 }} value={timeout} onChange={(e) => setTimeoutS(e.target.value)} /></Field>
        </div>
        <label className="row t-2"><Switch checked={priv} onChange={setPriv} label={t("idx.form.private")} />{t("idx.form.private")}</label>
        <label className="row t-2"><Switch checked={enabled} onChange={setEnabled} label={t("idx.form.enabled")} />{t("idx.form.enabled")}</label>
        {result ? (
          <InlineState tone={result.ok ? "success" : "danger"} role="status">
            <strong>{result.ok ? t("connector.testOk") : t("connector.testFailed")}</strong>
            <div>{result.message}</div>
            {result.latency_ms !== undefined && result.latency_ms !== null ? <div className="t-2">{t("connector.latency")} {num(result.latency_ms)} ms</div> : null}
          </InlineState>
        ) : null}
        {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
      </div>
    </Modal>
  );
}
