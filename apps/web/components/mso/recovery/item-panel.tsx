"use client";

import { Check, Download, FileSearch, RotateCcw, SearchCheck, Send, SlidersHorizontal, Stethoscope, X } from "lucide-react";
import { useState } from "react";

import { DiffViewer, Timeline } from "@/components/mg/domain";
import { Button, Card, InlineState, StatusChip } from "@/components/mg/primitives";
import { ResourceView } from "@/components/mg/states";
import { QuotaMeter } from "@/components/mso/mediaguard/parts";
import { useCategoryLabel } from "@/components/mso/recovery/baseline-card";
import { RequirementsEditor, cleanRequirements } from "@/components/mso/recovery/requirements-editor";
import { useToast } from "@/components/mg/toast";
import { useAction } from "@/lib/api/actions";
import { useResource } from "@/lib/api/hooks";
import type { CandidateResult, MgQuotaOverview, RecoveryItem, RequirementsInput, RequirementsView, StoredCandidate } from "@/lib/api/recovery";
import { useI18n } from "@/lib/i18n";
import { useMode } from "@/lib/mode";
import { useSession } from "@/lib/session";
import { enumLabel } from "@/lib/status";

function RequirementChips({ r }: { r: RequirementsView }) {
  const { t } = useI18n();
  const chips: string[] = [];
  if (r.season != null && r.episodes.length) chips.push(`S${String(r.season).padStart(2, "0")}E${r.episodes.map((e) => String(e).padStart(2, "0")).join("/")}`);
  if (r.absolute_episodes.length) chips.push(t("rec.req.absoluteChip", { n: r.absolute_episodes.join(",") }));
  if (r.year) chips.push(String(r.year));
  if (r.min_resolution) chips.push(`≥ ${r.min_resolution}p`);
  if (r.max_resolution) chips.push(`≤ ${r.max_resolution}p`);
  r.require_hdr.forEach((h) => chips.push(t("rec.req.require", { what: h.toUpperCase() })));
  r.forbid_hdr.forEach((h) => chips.push(t("rec.req.forbid", { what: h.toUpperCase() })));
  if (r.required_audio_languages.length) chips.push(t("rec.req.audioChip", { langs: r.required_audio_languages.join(", ") }));
  if (r.require_dual_audio) chips.push(t("rec.req.dual"));
  if (r.required_subtitles.length) chips.push(t("rec.req.subsChip", { langs: r.required_subtitles.join(", ") }));
  if (r.forbid_hardsub) chips.push(t("rec.req.noHardsub"));
  if (r.max_size_gb) chips.push(`≤ ${r.max_size_gb} GB`);
  if (r.blocked_groups.length) chips.push(t("rec.req.blockedGroups", { n: r.blocked_groups.length }));
  return <div className="row wrap" style={{ gap: 6 }}>{chips.map((c) => <span key={c} className="tag">{c}</span>)}</div>;
}

function CandidateRow({ c, canSend, sendReason, onSend, pending }: {
  c: StoredCandidate; canSend: boolean; sendReason?: string; onSend: () => void; pending: boolean;
}) {
  const { t, num, bytes } = useI18n();
  const [open, setOpen] = useState(false);
  const blocked = !canSend || c.expired || !c.passed;
  const reason = !c.passed ? t("rec.cand.failsPolicy") : c.expired ? t("rec.cand.expired") : sendReason;
  return (
    <article className="evidence" style={{ marginBottom: 8 }}>
      <div className="evidence-head" style={{ flexWrap: "wrap" }}>
        {c.passed ? <StatusChip tone="success">{t("rec.cand.passes")}</StatusChip> : <StatusChip tone="neutral" icon={X}>{t("rec.cand.rejected")}</StatusChip>}
        <span className="tag">{c.protocol === "usenet" ? "Usenet" : "Torrent"}</span>
        <span className="mono" style={{ overflowWrap: "anywhere" }}>{c.title}</span>
        <span className="grow" />
        {c.score != null ? <span className="num" title={t("rec.cand.score")}>{num(c.score, 1)}</span> : null}
      </div>
      <div className="row wrap" style={{ gap: 10, padding: "0 12px 8px" }}>
        {c.size_bytes ? <span>{bytes(c.size_bytes)}</span> : null}
        {c.seeders != null ? <span>{t("rec.cand.seeders", { n: num(c.seeders) })}</span> : null}
        {c.source_id ? <span className="mono t-3">{c.source_id}</span> : null}
        {c.expired ? <StatusChip tone="warning">{t("rec.cand.expired")}</StatusChip> : null}
      </div>
      {!c.passed && c.reasons.length ? <p className="t-danger" style={{ padding: "0 12px" }}>{c.reasons.join(" · ")}</p> : null}
      <div className="row wrap" style={{ gap: 8, padding: "0 12px 10px" }}>
        <Button size="sm" icon={SearchCheck} aria-expanded={open} onClick={() => setOpen(!open)}>{open ? t("rec.cand.hideEvidence") : t("rec.cand.showEvidence")}</Button>
        <Button size="sm" variant={c.passed ? "primary" : "secondary"} icon={Send} pending={pending} disabled={blocked} title={blocked ? reason : undefined} onClick={onSend}>{t("rec.cand.send")}</Button>
      </div>
      {open ? (
        <div style={{ padding: "0 12px 12px" }}>
          <DiffViewer headers={[t("rec.cand.requirement"), t("rec.cand.required"), t("rec.cand.actual")]} keyWidth={140}
            rows={c.evidence.map((e) => ({
              key: e.key, label: e.label, before: e.required, change: e.ok ? "same" : "changed",
              after: <span className="row" style={{ gap: 6 }}>{e.ok ? <Check className="ico ico-sm" aria-label={t("rec.cand.ok")} /> : <X className="ico ico-sm" aria-label={t("rec.cand.notOk")} />}{e.actual}{e.reasons.length ? <span> — {e.reasons.join("; ")}</span> : null}</span>,
            }))} />
        </div>
      ) : null}
    </article>
  );
}

/**
 * Detail of one wanted item (requirements, MediaGuard diagnosis, actions, candidates, history).
 * `bare` renders without the surrounding Card (used inside the details dialog on /ontbrekend).
 */
export function ItemPanel({ mediaRef, onChanged, bare = false }: { mediaRef: string; onChanged: () => void; bare?: boolean }) {
  const { t, relative, num } = useI18n();
  const { can } = useSession();
  const { mode: liveMode } = useMode();
  const categoryLabel = useCategoryLabel();
  const item = useResource<RecoveryItem>(`/recovery/items/${encodeURIComponent(mediaRef)}`);
  const quota = useResource<MgQuotaOverview>(mediaRef.startsWith("mg:") ? "/mediaguard/quota" : null, undefined, 60_000);
  const { run, pending } = useAction();
  const [extraOpen, setExtraOpen] = useState(false);
  const [extra, setExtra] = useState<RequirementsInput>({});
  const [last, setLast] = useState<CandidateResult | null>(null);
  const isOperator = can("operator");

  const findCandidates = async () => {
    const body = Object.keys(cleanRequirements(extra)).length ? { extra: cleanRequirements(extra) } : {};
    const res = await run<CandidateResult>("cands", "post", `/recovery/items/${encodeURIComponent(mediaRef)}/candidates`, body,
      t("rec.cand.found"));
    if (res) { setLast(res); void item.reload(); onChanged(); }
  };
  const toast = useToast();
  const requestSearch = async () => {
    const res = await run<{ duplicate: boolean; message: string }>("req", "post", `/recovery/items/${encodeURIComponent(mediaRef)}/request-search`);
    if (res) {
      toast.push(res.duplicate ? "info" : "success", res.duplicate ? res.message : t("rec.item.requested"), res.duplicate ? undefined : res.message);
      // MSO limit + MediaGuard quota changed: reload item (history), quota meter and the list
      await Promise.all([item.reload(), quota.reload()]);
      onChanged();
    }
    return res;
  };
  const send = async (releaseKey: string) => {
    const res = await run("send:" + releaseKey, "post", `/recovery/items/${encodeURIComponent(mediaRef)}/send`, { release_key: releaseKey }, t("rec.cand.sent"));
    if (res) { void item.reload(); onChanged(); }
  };

  const reloadButton = <Button size="sm" icon={RotateCcw} onClick={() => { void item.reload(); void quota.reload(); }}>{t("action.reload")}</Button>;
  const content = (
      <ResourceView resource={item} endpoint={`GET /api/v1/recovery/items/${mediaRef}`}>
        {(d) => {
          const tgt = d.target;
          const sendOk = isOperator && d.actions.manual_send.available && d.backoff.allowed;
          const sendReason = !isOperator ? t("mg.needOperator") : !d.actions.manual_send.available ? (d.actions.manual_send.reason ?? "") : !d.backoff.allowed ? t("rec.item.backoff", { when: d.backoff.next_allowed_at ? relative(d.backoff.next_allowed_at) : "—" }) : undefined;
          const accepted = d.candidates.filter((c) => c.passed);
          const rejected = d.candidates.filter((c) => !c.passed);
          return (
            <div className="stack">
              <div className="row wrap" style={{ gap: 8 }}>
                <strong style={{ fontSize: 15 }}>{tgt.title}</strong>
                <span className="tag">{categoryLabel(tgt.media_category)}</span>
                <StatusChip tone={tgt.origin === "mediaguard" ? "accent" : "info"} icon={null}>{tgt.origin === "mediaguard" ? t("rec.origin.mg") : t("rec.origin.manual")}</StatusChip>
                <span className="mono t-3">{tgt.media_ref}</span>
              </div>

              <section>
                <h3 className="card-title" style={{ marginBottom: 6 }}>{t("rec.item.requirements")}</h3>
                <RequirementChips r={d.requirements} />
                <p className="hint">{t("rec.item.reqSources", { list: d.requirements.sources.join(" + ") })}</p>
                {!d.requirements.policy_verified ? <InlineState tone="warning">{t("rec.item.policyUnverified")}</InlineState> : null}
                {d.requirements.informational.map((i) => <p key={i} className="hint">{i}</p>)}
              </section>

              {tgt.origin === "mediaguard" ? (
                <section>
                  <h3 className="card-title" style={{ marginBottom: 6 }}><Stethoscope className="ico" aria-hidden="true" />{t("rec.item.diagnosis")}</h3>
                  {d.diagnosis ? (
                    <Timeline items={d.diagnosis.reasons.map((r) => ({
                      title: <span><strong>{r.label_nl}</strong> — {r.message_nl}</span>,
                      meta: <span>{r.suggested_fix_nl} · {relative(r.ts)}</span>,
                      tone: r.severity === "info" ? "accent" : r.severity === "warning" ? "warning" : "danger",
                    }))} />
                  ) : <p className="hint">{d.diagnosis_error ?? t("rec.item.noDiagnosis")}</p>}
                </section>
              ) : null}

              <section className="stack" style={{ gap: 10 }}>
                <h3 className="card-title">{t("rec.item.actions")}</h3>
                <div className="row wrap" style={{ gap: 8 }}>
                  <Button variant="primary" icon={FileSearch} pending={pending === "cands"} disabled={!isOperator} title={isOperator ? undefined : t("mg.needOperator")} onClick={() => void findCandidates()}>{t("rec.item.findCandidates")}</Button>
                  <Button icon={SlidersHorizontal} aria-expanded={extraOpen} onClick={() => setExtraOpen(!extraOpen)}>{t("rec.item.extra")}</Button>
                  {tgt.origin === "mediaguard" ? (
                    <Button icon={RotateCcw} pending={pending === "req"} disabled={!isOperator || !d.actions.request_search.available}
                      title={!isOperator ? t("mg.needOperator") : d.actions.request_search.reason ?? undefined}
                      onClick={() => void requestSearch()}>{t("rec.item.askMediaGuard")}</Button>
                  ) : null}
                </div>
                {extraOpen ? (
                  <div className="card" style={{ padding: 12 }}>
                    <p className="hint" style={{ marginTop: 0 }}>{t("rec.item.extraHint")}</p>
                    <RequirementsEditor value={extra} onChange={setExtra} floor={{ min_resolution: d.requirements.min_resolution }} />
                  </div>
                ) : null}
                {tgt.origin === "mediaguard" ? (
                  <div>
                    <p className="hint" style={{ marginTop: 0 }}>{t("rec.item.askHint")}</p>
                    {liveMode === "read_only" ? <p className="hint" style={{ marginTop: 0 }}>{t("rec.item.askReadOnly")}</p> : null}
                    <QuotaMeter quota={quota.data} instanceId={tgt.instance_id} compact />
                  </div>
                ) : null}
                {!d.backoff.allowed ? <InlineState tone="warning">{t("rec.item.backoff", { when: d.backoff.next_allowed_at ? relative(d.backoff.next_allowed_at) : "—" })}</InlineState> : null}
                <p className="hint" style={{ margin: 0 }}>{t("rec.item.category", { cat: d.actions.manual_send.category })}</p>
              </section>

              {last ? (
                <InlineState tone={last.counts.accepted ? "success" : "info"}>
                  {t("rec.cand.summary", { accepted: num(last.counts.accepted), total: num(last.counts.total), blacklisted: num(last.counts.blacklisted) })}
                  {last.search.sources.length ? ` · ${last.search.sources.map((s) => `${s.label ?? s.source_id}: ${enumLabel(t, "rec.src.status", s.status)}`).join(", ")}` : ""}
                </InlineState>
              ) : null}

              <section>
                <h3 className="card-title" style={{ marginBottom: 6 }}><Download className="ico" aria-hidden="true" />{t("rec.cand.title", { n: num(accepted.length) })}</h3>
                {d.candidates.length === 0 ? <p className="hint">{t("rec.cand.none")}</p> : null}
                {accepted.map((c) => <CandidateRow key={c.release_key} c={c} canSend={sendOk} sendReason={sendReason} pending={pending === "send:" + c.release_key} onSend={() => void send(c.release_key)} />)}
                {rejected.length ? (
                  <details>
                    <summary style={{ cursor: "pointer" }}>{t("rec.cand.rejectedList", { n: num(rejected.length) })}</summary>
                    <div style={{ marginTop: 8 }}>
                      {rejected.map((c) => <CandidateRow key={c.release_key} c={c} canSend={false} pending={false} onSend={() => undefined} />)}
                    </div>
                  </details>
                ) : null}
              </section>

              {d.sends.length || d.search_requests.length ? (
                <section>
                  <h3 className="card-title" style={{ marginBottom: 6 }}>{t("rec.item.history")}</h3>
                  <Timeline items={[
                    ...d.search_requests.map((r) => ({ title: t("rec.item.histRequest", { status: enumLabel(t, "rec.req.status", r.status) }), meta: `${relative(r.requested_at)} · ${r.actor}${r.error ? ` · ${r.error}` : ""}`, tone: (r.status === "sent" ? "success" : r.status === "failed" ? "danger" : "warning") as "success" | "danger" | "warning" })),
                    ...d.sends.map((s) => ({ title: t("rec.item.histSend", { client: s.client === "sabnzbd" ? "SABnzbd" : s.client === "qbittorrent" ? "qBittorrent" : s.client, status: enumLabel(t, "rec.send.status", s.status) }), meta: `${relative(s.created_at)} · ${s.title}${s.error ? ` · ${s.error}` : ""}`, tone: (s.status === "sent" || s.status === "download_ok" ? "success" : "danger") as "success" | "danger" })),
                  ]} />
                </section>
              ) : null}
            </div>
          );
        }}
      </ResourceView>
  );
  if (bare) return <div className="stack"><div className="row" style={{ justifyContent: "flex-end" }}>{reloadButton}</div>{content}</div>;
  return <Card className="span-12" title={t("rec.item.title")} icon={FileSearch} actions={reloadButton}>{content}</Card>;
}
