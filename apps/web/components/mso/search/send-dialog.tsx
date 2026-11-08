"use client";

import { Send } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button, Field, InlineState, Modal } from "@/components/mg/primitives";
import { StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { isFake } from "@/components/mso/search/parts";
import { api, errorMessage } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import type { SearchItem, SendResult, SendTargets } from "@/lib/api/search";
import { useI18n } from "@/lib/i18n";

/** "Verstuur naar SABnzbd/qBittorrent": a direct, audited, idempotent action (no change set). */
export function SendDialog({ item, onClose, onSent }: { item: SearchItem; onClose: () => void; onSent?: (r: SendResult) => void }) {
  const { t, relative } = useI18n();
  const toast = useToast();
  const targets = useResource<SendTargets>("/search/send-targets");
  const target = targets.data?.items.find((x) => x.protocol === item.protocol) ?? null;
  const options = useMemo<string[]>(() => (target?.categories.length ? target.categories : (targets.data?.media_categories ?? [])), [target, targets.data]);
  const [category, setCategory] = useState<string>("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (category || !options.length) return;
    const preferred = item.media_category && options.includes(item.media_category) ? item.media_category : options[0];
    setCategory(preferred);
  }, [category, item.media_category, options]);

  const send = async () => {
    if (!target) return;
    setPending(true);
    setError(null);
    try {
      const r = await api.post<SendResult>("/search/send", { token: item.token, client: target.client, category });
      if (r.status === "already_sent") toast.push("info", t("zoeken.alreadySent", { when: relative(r.send.updated_at) }), item.title);
      else toast.push("success", t("zoeken.sent", { client: target.label }), item.title);
      onSent?.(r);
      onClose();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPending(false);
    }
  };

  return (
    <Modal open onClose={onClose} icon={Send} title={t("zoeken.sendTitle")} description={item.title}
      footer={<>
        <Button onClick={onClose}>{t("action.cancel")}</Button>
        <Button variant="primary" icon={Send} pending={pending} disabled={!target?.available || !category || isFake(item)}
          title={!target?.available ? target?.reason ?? undefined : undefined} onClick={() => void send()} data-testid="send-confirm">
          {target ? t("zoeken.sendTo", { client: target.label }) : t("zoeken.send")}
        </Button>
      </>}>
      {targets.loading ? <StateView kind="loading" compact /> : null}
      {target && !target.available ? (
        <InlineState tone="warning" role="alert">{t("zoeken.sendUnavailable", { client: target.label, reason: target.reason ?? "—" })}</InlineState>
      ) : null}
      {target?.available ? (
        <Field label={t("zoeken.sendCategory", { client: target.label })} htmlFor="send-category">
          <select id="send-category" className="select" value={category} onChange={(e) => setCategory(e.target.value)}>
            {options.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </Field>
      ) : null}
      <p className="hint" style={{ marginTop: 10 }}>{t("zoeken.sendHint")}</p>
      {item.false_positive_flags.includes("geblokkeerd") ? <InlineState tone="danger">{t("zoeken.sendBlocked")}</InlineState> : null}
      {isFake(item) ? (
        <InlineState tone="danger" role="alert"><strong>{t("releasegate.sendBlocked")}</strong> {t("releasegate.sendBlockedBody")}</InlineState>
      ) : null}
      {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
    </Modal>
  );
}
