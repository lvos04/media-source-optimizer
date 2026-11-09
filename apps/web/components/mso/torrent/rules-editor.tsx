"use client";

import { Pencil, Plus, Scale, Trash2 } from "lucide-react";
import { useId, useState } from "react";

import { DangerDialog } from "@/components/mg/domain";
import { Button, Card, Field, InlineState, Modal, Segmented, StatusChip, Switch } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { api, errorMessage } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import type { TrackerRule, TrackerRuleList } from "@/lib/api/torrent";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

interface Draft {
  tracker_host: string; category: string; label: string; private: boolean; min_ratio: string; min_seed_time_h: string;
  mode: "either" | "both"; allow_cross_seed: boolean; notes: string;
}
const EMPTY: Draft = { tracker_host: "", category: "", label: "", private: true, min_ratio: "", min_seed_time_h: "", mode: "either", allow_cross_seed: false, notes: "" };

function toDraft(r: TrackerRule): Draft {
  return {
    tracker_host: r.tracker_host ?? "", category: r.category ?? "", label: r.label, private: r.private,
    min_ratio: r.min_ratio === null ? "" : String(r.min_ratio), min_seed_time_h: r.min_seed_time_h === null ? "" : String(r.min_seed_time_h),
    mode: r.mode, allow_cross_seed: r.allow_cross_seed, notes: r.notes,
  };
}

function RuleForm({ initial, id, onClose, onSaved }: { initial: Draft; id: number | null; onClose: () => void; onSaved: () => void }) {
  const { t } = useI18n();
  const uid = useId();
  const [d, setD] = useState<Draft>(initial);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setD((o) => ({ ...o, [k]: v }));
  const save = async () => {
    setPending(true);
    setError(null);
    const body = {
      tracker_host: d.tracker_host.trim() || null, category: d.category.trim() || null, label: d.label.trim(),
      private: d.private, min_ratio: d.min_ratio.trim() === "" ? null : Number(d.min_ratio.replace(",", ".")),
      min_seed_time_h: d.min_seed_time_h.trim() === "" ? null : Number(d.min_seed_time_h.replace(",", ".")),
      mode: d.mode, allow_cross_seed: d.allow_cross_seed, notes: d.notes,
    };
    try {
      if (id === null) await api.post("/torrent/tracker-rules", body);
      else await api.put(`/torrent/tracker-rules/${id}`, body);
      onSaved();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPending(false);
    }
  };
  return (
    <Modal open onClose={onClose} title={id === null ? t("torrent.rules.add") : t("torrent.rules.edit")} icon={Scale}
      footer={<><Button onClick={onClose}>{t("action.cancel")}</Button><Button variant="primary" pending={pending} onClick={() => void save()}>{t("action.save")}</Button></>}>
      <div className="stack">
        <Field label={t("torrent.rules.host")} hint={t("torrent.rules.hostHint")} htmlFor={`${uid}-h`}>
          <input id={`${uid}-h`} className="input" value={d.tracker_host} onChange={(e) => set("tracker_host", e.target.value)} placeholder="tracker.voorbeeld.org" autoComplete="off" />
        </Field>
        <Field label={t("torrent.rules.category")} hint={t("torrent.rules.categoryHint")} htmlFor={`${uid}-c`}>
          <input id={`${uid}-c`} className="input" value={d.category} onChange={(e) => set("category", e.target.value)} />
        </Field>
        <Field label={t("torrent.rules.label")} htmlFor={`${uid}-l`}>
          <input id={`${uid}-l`} className="input" value={d.label} onChange={(e) => set("label", e.target.value)} />
        </Field>
        <div className="row wrap" style={{ gap: 12 }}>
          <Field label={t("torrent.rules.minRatio")} htmlFor={`${uid}-r`}>
            <input id={`${uid}-r`} className="input" inputMode="decimal" value={d.min_ratio} onChange={(e) => set("min_ratio", e.target.value)} />
          </Field>
          <Field label={t("torrent.rules.minHours")} htmlFor={`${uid}-t`}>
            <input id={`${uid}-t`} className="input" inputMode="decimal" value={d.min_seed_time_h} onChange={(e) => set("min_seed_time_h", e.target.value)} />
          </Field>
        </div>
        <Segmented label={t("torrent.rules.mode")} value={d.mode} onChange={(v) => set("mode", v)} options={[
          { value: "either", label: t("torrent.rules.modeEither") }, { value: "both", label: t("torrent.rules.modeBoth") },
        ]} />
        <div className="row-between"><span>{t("torrent.rules.private")}</span><Switch checked={d.private} onChange={(v) => set("private", v)} label={t("torrent.rules.private")} /></div>
        <div className="row-between">
          <span>{t("torrent.rules.crossSeed")}<span className="hint" style={{ display: "block", margin: 0 }}>{t("torrent.rules.crossSeedHint")}</span></span>
          <Switch checked={d.allow_cross_seed} onChange={(v) => set("allow_cross_seed", v)} label={t("torrent.rules.crossSeed")} />
        </div>
        <Field label={t("torrent.rules.notes")} htmlFor={`${uid}-n`}>
          <textarea id={`${uid}-n`} className="textarea" rows={2} value={d.notes} onChange={(e) => set("notes", e.target.value)} />
        </Field>
        {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
      </div>
    </Modal>
  );
}

export function RulesEditor() {
  const { t, num } = useI18n();
  const toast = useToast();
  const { can } = useSession();
  const isAdmin = can("admin");
  const rules = useResource<TrackerRuleList>("/torrent/tracker-rules");
  const [editing, setEditing] = useState<{ id: number | null; draft: Draft } | null>(null);
  const [deleting, setDeleting] = useState<TrackerRule | null>(null);
  const [delPending, setDelPending] = useState(false);
  const [delError, setDelError] = useState<string | null>(null);
  const why = isAdmin ? undefined : t("torrent.adminOnly");

  const remove = async () => {
    if (!deleting) return;
    setDelPending(true);
    setDelError(null);
    try {
      await api.del(`/torrent/tracker-rules/${deleting.id}`);
      toast.push("success", t("torrent.rules.deleted"));
      setDeleting(null);
      void rules.reload();
    } catch (e) {
      setDelError(errorMessage(e));
    } finally {
      setDelPending(false);
    }
  };

  return (
    <Card className="span-12" title={t("torrent.rules.title")} icon={Scale} flush
      actions={<Button size="sm" variant="primary" icon={Plus} disabled={!isAdmin} title={why} onClick={() => setEditing({ id: null, draft: EMPTY })}>{t("torrent.rules.add")}</Button>}>
      <p className="t-2" style={{ margin: "0 16px 12px" }}>{t("torrent.rules.explain")}</p>
      <ResourceView resource={rules} endpoint="GET /api/v1/torrent/tracker-rules" compact>
        {(d) => {
          const ruled = new Set(d.items.map((r) => r.tracker_host).filter(Boolean));
          const missing = d.known_trackers.filter((k) => !ruled.has(k.host));
          return (
            <div className="stack" style={{ paddingBottom: 12 }}>
              {d.items.length ? (
                <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel (horizontaal scrollbaar)">
                  <table className="table table--stack">
                    <thead><tr>
                      <th>{t("torrent.rules.scope")}</th><th>{t("torrent.rules.obligation")}</th><th>{t("torrent.rules.crossSeedShort")}</th>
                      <th><span className="sr-only">{t("torrent.col.actions")}</span></th>
                    </tr></thead>
                    <tbody>{d.items.map((r) => (
                      <tr key={r.id}>
                        <td className="cell-primary">
                          <div>{r.label || r.tracker_host || r.category}</div>
                          <div className="t-3 mono">{r.tracker_host ?? t("torrent.rules.anyTracker")} · {r.category ?? t("torrent.rules.anyCategory")}</div>
                          {r.private ? <StatusChip tone="info">{t("torrent.private")}</StatusChip> : null}
                        </td>
                        <td data-label={t("torrent.rules.obligation")}>
                          {r.min_ratio === null && r.min_seed_time_h === null ? <span className="t-3">—</span>
                            : t(r.mode === "both" ? "torrent.rules.summaryBoth" : "torrent.rules.summaryEither", {
                              ratio: r.min_ratio === null ? "—" : num(r.min_ratio, 2), hours: r.min_seed_time_h === null ? "—" : num(r.min_seed_time_h, 1),
                            })}
                        </td>
                        <td data-label={t("torrent.rules.crossSeedShort")}>
                          {r.allow_cross_seed ? <StatusChip tone="success">{t("torrent.rules.allowed")}</StatusChip> : <StatusChip tone="neutral">{t("torrent.rules.notAllowed")}</StatusChip>}
                        </td>
                        <td data-label={t("torrent.col.actions")}>
                          <div className="toolbar" style={{ justifyContent: "flex-end" }}>
                            <Button size="sm" icon={Pencil} iconOnly disabled={!isAdmin} title={why ?? t("action.edit")} onClick={() => setEditing({ id: r.id, draft: toDraft(r) })}>{t("action.edit")}</Button>
                            <Button size="sm" icon={Trash2} iconOnly variant="ghost" disabled={!isAdmin} title={why ?? t("torrent.rules.delete")} onClick={() => setDeleting(r)}>{t("torrent.rules.delete")}</Button>
                          </div>
                        </td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              ) : <StateView kind="empty" compact title={t("torrent.rules.none")} body={t("torrent.rules.noneBody")} />}
              {missing.length ? (
                <div style={{ padding: "0 16px" }}>
                  <span className="eyebrow">{t("torrent.rules.unruled")}</span>
                  <div className="row wrap" style={{ gap: 6, marginTop: 6 }}>
                    {missing.map((k) => (
                      <Button key={k.host} size="sm" icon={Plus} disabled={!isAdmin} title={why}
                        onClick={() => setEditing({ id: null, draft: { ...EMPTY, tracker_host: k.host, private: k.private } })}>
                        {k.host}{k.private ? ` (${t("torrent.private").toLowerCase()})` : ""}
                      </Button>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          );
        }}
      </ResourceView>
      {editing ? <RuleForm initial={editing.draft} id={editing.id} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); toast.push("success", t("torrent.rules.saved")); void rules.reload(); }} /> : null}
      <DangerDialog open={!!deleting} onClose={() => { setDeleting(null); setDelError(null); }} onConfirm={() => void remove()} icon={Trash2}
        title={t("torrent.rules.deleteTitle")} description={t("torrent.rules.deleteBody")} confirmLabel={t("torrent.rules.delete")}
        pending={delPending} error={delError} facts={deleting ? [{ label: t("torrent.rules.scope"), value: deleting.tracker_host ?? deleting.category ?? "" }] : undefined} />
    </Card>
  );
}
