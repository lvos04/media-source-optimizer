"use client";

import { Copy, Eye, KeyRound, Plus, RefreshCw, Rss, Server, Upload } from "lucide-react";
import { useState } from "react";

import { DangerDialog } from "@/components/mg/domain";
import { Button, Card, InlineState, LinkButton, Modal, StatusChip } from "@/components/mg/primitives";
import { ResourceView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { ChangePreview } from "@/components/mso/change-preview";
import { DirectIndexerDialog } from "@/components/mso/indexers/direct-dialog";
import { api, errorMessage } from "@/lib/api/client";
import type { ChangeSet } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import type { NewznabEndpoint, RegisterResult, RegistrationKind } from "@/lib/api/search";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";

const BACKEND_LABEL: Record<string, MessageKey> = {
  direct: "idx.backend.direct", spotweb: "idx.backend.spotweb", headerindex: "idx.backend.headerindex", prowlarr: "idx.backend.prowlarr",
};

function CopyField({ id, label, value, secret }: { id: string; label: string; value: string; secret?: boolean }) {
  const { t } = useI18n();
  const toast = useToast();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      toast.push("success", t("idx.copied"), secret ? undefined : value);
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: errorMessage(e) }));
    }
  };
  return (
    <div className="stack-sm">
      <label className="label" htmlFor={id}>{label}</label>
      <div className="row" style={{ gap: 6 }}>
        <input id={id} className="input mono grow" readOnly value={value} onFocus={(e) => e.currentTarget.select()} style={{ minWidth: 0 }} />
        <Button size="sm" icon={Copy} iconOnly onClick={() => void copy()}>{t("idx.copy")}</Button>
      </div>
    </div>
  );
}

/** MSO's own Newznab/Torznab endpoint: URLs to configure in Prowlarr, key reveal/rotate (admin, audited) and
 * "Registreer in Prowlarr" → change preview (toestemming nodig). */
export function EndpointPanel({ className, prowlarrConfigured }: { className?: string; prowlarrConfigured: boolean }) {
  const { t, relative } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const endpoint = useResource<NewznabEndpoint>("/newznab/endpoint");
  const [dialog, setDialog] = useState<"reveal" | "rotate" | null>(null);
  const [key, setKey] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [change, setChange] = useState<ChangeSet | null>(null);
  const [payloads, setPayloads] = useState<Partial<Record<RegistrationKind, Record<string, unknown>>> | null>(null);
  const [adding, setAdding] = useState<"spotweb" | "newznab" | null>(null);
  const isAdmin = can("admin");
  // Per protocol: only register where MSO has own sources (Prowlarr's add-indexer test needs real results).
  const readiness = endpoint.data?.registration;
  const readyKinds = (["newznab", "torznab"] as const).filter((k) => readiness?.[k]?.ready);
  const notReadyReason = readiness && !readyKinds.length
    ? (!readiness.torznab.reason || readiness.torznab.reason.slice(0, 60) === readiness.newznab.reason?.slice(0, 60)
      ? readiness.newznab.reason
      : `${readiness.newznab.reason ?? ""} ${t("idx.endpoint.torznabPrefix")} ${readiness.torznab.reason}`)
    : null;

  const keyAction = async (action: "reveal" | "rotate", id: number) => {
    setPending(action);
    setError(null);
    try {
      const r = await api.post<{ apikey: string }>(`/newznab/consumers/${id}/${action}`);
      setKey(r.apikey);
      setDialog(null);
      if (action === "rotate") { toast.push("success", t("idx.rotated")); await endpoint.reload(); }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPending(null);
    }
  };

  const register = async () => {
    setPending("register");
    try {
      const r = await api.post<RegisterResult>("/indexers/prowlarr/register", { kinds: readyKinds.length ? readyKinds : ["newznab", "torznab"] });
      if (r.status === "already_registered") toast.push("info", t("idx.registerAlready"));
      else { setChange(r.change); setPayloads(r.payloads ?? null); }
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: errorMessage(e) }));
    } finally {
      setPending(null);
    }
  };

  return (
    <Card className={className} title={t("idx.endpoint")} sub={t("idx.endpointSub")} icon={Rss}
      actions={<Button size="sm" variant="primary" icon={Upload} pending={pending === "register"} disabled={!isAdmin || !prowlarrConfigured || Boolean(notReadyReason)}
        title={!isAdmin ? t("idx.adminOnly") : !prowlarrConfigured ? t("idx.prowlarrNotConfigured") : notReadyReason ?? undefined}
        onClick={() => void register()}>{t("idx.register")}</Button>}>
      <ResourceView resource={endpoint} endpoint="GET /api/v1/newznab/endpoint" compact>
        {(d) => (
          <div className="stack">
            {!d.public_url_configured ? <InlineState tone="warning">{t("idx.endpoint.noPublic")}</InlineState> : null}
            {notReadyReason ? <InlineState tone="info">{notReadyReason}</InlineState> : null}
            {readiness && !readiness.newznab.ready ? (
              <div className="stack-sm" data-testid="endpoint-own-sources">
                <span className="t-2" style={{ fontSize: 12.5 }}>{t("idx.endpoint.addOwnHelp")}</span>
                <div className="row wrap" style={{ gap: 6 }}>
                  <Button size="sm" variant="primary" icon={Plus} disabled={!isAdmin} title={!isAdmin ? t("idx.adminOnly") : undefined}
                    onClick={() => setAdding("spotweb")}>{t("idx.endpoint.addSpotweb")}</Button>
                  <Button size="sm" icon={Plus} disabled={!isAdmin} title={!isAdmin ? t("idx.adminOnly") : undefined}
                    onClick={() => setAdding("newznab")}>{t("idx.endpoint.addDirect")}</Button>
                  <LinkButton href="/usenet-providers" size="sm" icon={Server}>{t("idx.endpoint.headerIndexLink")}</LinkButton>
                </div>
              </div>
            ) : null}
            <CopyField id="nz-url" label={t("idx.endpoint.newznab")} value={d.newznab_url} />
            <CopyField id="tz-url" label={t("idx.endpoint.torznab")} value={d.torznab_url} />
            <dl className="kv">
              <dt>{t("idx.endpoint.apiPath")}</dt><dd className="mono">{d.api_path}</dd>
              <dt>{t("idx.endpoint.key")}</dt>
              <dd className="mono">{d.default_consumer.key_hint}{d.default_consumer.last_used_at ? <span className="t-3"> · {relative(d.default_consumer.last_used_at)}</span> : null}</dd>
              <dt>{t("idx.endpoint.backends")}</dt><dd>{d.backends.map((b) => <span key={b} className="tag" style={{ marginRight: 4 }}>{BACKEND_LABEL[b] ? t(BACKEND_LABEL[b]) : b}</span>)}</dd>
              {(["newznab", "torznab"] as const).map((k) => {
                const r = d.registration?.[k];
                if (!r) return null;
                return (
                  <div key={k} style={{ display: "contents" }}>
                    <dt>{t(k === "newznab" ? "idx.endpoint.readyNewznab" : "idx.endpoint.readyTorznab")}</dt>
                    <dd>{r.ready
                      ? <StatusChip tone="success">{t("idx.endpoint.ready", { sources: r.sources.filter((x) => x.rss).map((x) => x.label).join(", ") })}</StatusChip>
                      : <><StatusChip tone="neutral">{t("idx.endpoint.notReady")}</StatusChip>{readyKinds.length ? <div className="cell-sub">{r.reason}</div> : null}</>}</dd>
                  </div>
                );
              })}
              <dt>Prowlarr</dt>
              <dd>{d.prowlarr_registrations.length
                ? <StatusChip tone="success">{t("idx.endpoint.registered")} ({d.prowlarr_registrations.map((r) => r.source_id).join(", ")})</StatusChip>
                : <StatusChip tone="neutral">{t("idx.endpoint.notRegistered")}</StatusChip>}</dd>
            </dl>
            {key ? <CopyField id="nz-key" label={t("idx.endpoint.key")} value={key} secret /> : null}
            <div className="row wrap" style={{ gap: 6 }}>
              <Button size="sm" icon={Eye} disabled={!isAdmin} title={!isAdmin ? t("idx.adminOnly") : undefined} onClick={() => { setError(null); setDialog("reveal"); }}>{t("idx.reveal")}</Button>
              <Button size="sm" variant="danger" icon={RefreshCw} disabled={!isAdmin} title={!isAdmin ? t("idx.adminOnly") : undefined} onClick={() => { setError(null); setDialog("rotate"); }}>{t("idx.rotate")}</Button>
            </div>
            <DangerDialog open={dialog === "reveal"} onClose={() => setDialog(null)} icon={KeyRound} title={t("idx.revealTitle")} description={t("idx.revealBody")}
              confirmLabel={t("idx.reveal")} pending={pending === "reveal"} error={error} onConfirm={() => void keyAction("reveal", d.default_consumer.id)} />
            <DangerDialog open={dialog === "rotate"} onClose={() => setDialog(null)} icon={RefreshCw} title={t("idx.rotateTitle")} description={t("idx.rotateBody")}
              confirmLabel={t("idx.rotate")} confirmWord="VERNIEUW" pending={pending === "rotate"} error={error} onConfirm={() => void keyAction("rotate", d.default_consumer.id)} />
          </div>
        )}
      </ResourceView>
      {adding ? (
        <DirectIndexerDialog protocol="usenet" initialKind={adding} onClose={() => setAdding(null)}
          onSaved={async () => { toast.push("success", t("idx.saved")); await endpoint.reload(); }} />
      ) : null}
      <Modal open={Boolean(change)} onClose={() => { setChange(null); setPayloads(null); }} title={t("idx.registerTitle")} icon={Upload}>
        {change ? (
          <div className="stack">
            <ChangePreview change={change} card={false} onChanged={async (u) => { if (u) setChange(u); await endpoint.reload(); }} />
            {payloads && Object.keys(payloads).length ? (
              <details>
                <summary className="t-2" style={{ fontSize: 12.5, cursor: "pointer" }}>{t("idx.registerTechnical")}</summary>
                <p className="hint" style={{ margin: "6px 0" }}>{t("idx.registerTechnicalHelp")}</p>
                {Object.entries(payloads).map(([k, p]) => (
                  <div key={k} className="stack-sm">
                    <span className="eyebrow">{k === "newznab" ? "Newznab" : "Torznab"}</span>
                    <pre className="mono pre-wrap" style={{ margin: 0, fontSize: 11.5, maxHeight: 260, overflow: "auto" }}>{JSON.stringify(p, null, 2)}</pre>
                  </div>
                ))}
              </details>
            ) : null}
          </div>
        ) : null}
      </Modal>
    </Card>
  );
}
