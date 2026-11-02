"use client";

import { Copy, Eye, KeyRound, RefreshCw, Rss, Upload } from "lucide-react";
import { useState } from "react";

import { DangerDialog } from "@/components/mg/domain";
import { Button, Card, InlineState, Modal, StatusChip } from "@/components/mg/primitives";
import { ResourceView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { ChangePreview } from "@/components/mso/change-preview";
import { api, errorMessage } from "@/lib/api/client";
import type { ChangeSet } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import type { NewznabEndpoint, RegisterResult } from "@/lib/api/search";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

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
  const isAdmin = can("admin");

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
      const r = await api.post<RegisterResult>("/indexers/prowlarr/register", { kinds: ["newznab", "torznab"] });
      if (r.status === "already_registered") toast.push("info", t("idx.registerAlready"));
      else setChange(r.change);
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: errorMessage(e) }));
    } finally {
      setPending(null);
    }
  };

  return (
    <Card className={className} title={t("idx.endpoint")} sub={t("idx.endpointSub")} icon={Rss}
      actions={<Button size="sm" variant="primary" icon={Upload} pending={pending === "register"} disabled={!isAdmin || !prowlarrConfigured}
        title={!isAdmin ? t("idx.adminOnly") : !prowlarrConfigured ? t("idx.prowlarrNotConfigured") : undefined}
        onClick={() => void register()}>{t("idx.register")}</Button>}>
      <ResourceView resource={endpoint} endpoint="GET /api/v1/newznab/endpoint" compact>
        {(d) => (
          <div className="stack">
            {!d.public_url_configured ? <InlineState tone="warning">{t("idx.endpoint.noPublic")}</InlineState> : null}
            <CopyField id="nz-url" label={t("idx.endpoint.newznab")} value={d.newznab_url} />
            <CopyField id="tz-url" label={t("idx.endpoint.torznab")} value={d.torznab_url} />
            <dl className="kv">
              <dt>{t("idx.endpoint.apiPath")}</dt><dd className="mono">{d.api_path}</dd>
              <dt>{t("idx.endpoint.key")}</dt>
              <dd className="mono">{d.default_consumer.key_hint}{d.default_consumer.last_used_at ? <span className="t-3"> · {relative(d.default_consumer.last_used_at)}</span> : null}</dd>
              <dt>{t("idx.endpoint.backends")}</dt><dd>{d.backends.map((b) => <span key={b} className="tag" style={{ marginRight: 4 }}>{b}</span>)}</dd>
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
      <Modal open={Boolean(change)} onClose={() => setChange(null)} title={t("idx.registerTitle")} icon={Upload}>
        {change ? <ChangePreview change={change} card={false} onChanged={async (u) => { if (u) setChange(u); await endpoint.reload(); }} /> : null}
      </Modal>
    </Card>
  );
}
