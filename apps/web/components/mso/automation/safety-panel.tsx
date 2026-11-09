"use client";

import { Hourglass, OctagonPause, PlayCircle, RotateCcw, ShieldCheck, TimerReset, Undo2 } from "lucide-react";
import { useEffect, useState } from "react";

import { DangerDialog } from "@/components/mg/domain";
import { Button, Card, InlineState, Segmented, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { formatOpValue } from "@/components/mso/change-preview";
import { useAction } from "@/lib/api/actions";
import { api, errorMessage } from "@/lib/api/client";
import { useResource, type Resource } from "@/lib/api/hooks";
import { HOLD_CHANGED_EVENT, notifyHoldChanged, type CooldownItem, type SafetyStatus, type UndoPreview, type UndoResult, type UndoWindow } from "@/lib/api/safety";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useWriteGate } from "@/lib/mode";
import { useSession } from "@/lib/session";

const WINDOWS: UndoWindow[] = ["24h", "7d"];

/** Automatisering → Veiligheid: noodrem, Autopilot terugdraaien, afkoeling na mislukte pogingen, onderbroken
 * wijzigingen (GET /automation/safety, GET/POST /automation/undo). */
export function SafetyPanel({ onOpenChange }: { onOpenChange: (id: number) => void }) {
  const safety = useResource<SafetyStatus>("/automation/safety", undefined, 30_000);
  const { reload } = safety;
  // "Hervatten" in the app-wide banner (or here) → refresh, so no stale hold with a working "Hervatten" stays behind
  useEffect(() => {
    const onChange = () => void reload();
    window.addEventListener(HOLD_CHANGED_EVENT, onChange);
    return () => window.removeEventListener(HOLD_CHANGED_EVENT, onChange);
  }, [reload]);
  return (
    <>
      <BrakeCard safety={safety} />
      <UndoCard onDone={() => void safety.reload()} onOpenChange={onOpenChange} />
      <CooldownCard safety={safety} onOpenChange={onOpenChange} />
      {safety.data?.applying.length ? <ApplyingCard safety={safety.data} onOpenChange={onOpenChange} /> : null}
    </>
  );
}

function BrakeCard({ safety }: { safety: Resource<SafetyStatus> }) {
  const { t, num, relative, dateTime } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const [pending, setPending] = useState(false);
  const resume = async () => {
    setPending(true);
    try {
      const next = await api.post<SafetyStatus>("/automation/hold/resume");
      safety.setData(() => next);
      notifyHoldChanged();
      toast.push("success", t("safety.hold.resumed"));
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: errorMessage(e) }));
      void safety.reload();
    } finally {
      setPending(false);
    }
  };
  return (
    <Card className="span-5" title={t("safety.brake.title")} icon={OctagonPause} sub={t("safety.brake.sub")}>
      <ResourceView resource={safety} endpoint="GET /api/v1/automation/safety" compact>
        {(s) => (
          <div className="stack">
            {s.hold?.active ? (
              <InlineState tone="danger" icon={OctagonPause} role="status">
                <strong>{t("safety.brake.active")}</strong> ({relative(s.hold.since)}). {s.hold.reason}.
              </InlineState>
            ) : <InlineState tone="success" icon={ShieldCheck}>{t("safety.brake.inactive")}</InlineState>}
            <dl className="kv">
              <dt>{t("safety.brake.rollbacks")}</dt>
              <dd><StatusChip tone={s.brake.rollbacks >= s.brake.max_rollbacks ? "danger" : s.brake.rollbacks ? "warning" : "success"}>{t("safety.brake.of", { count: num(s.brake.rollbacks), max: num(s.brake.max_rollbacks) })}</StatusChip></dd>
              <dt>{t("safety.brake.failures")}</dt>
              <dd><StatusChip tone={s.brake.failures >= s.brake.max_failures ? "danger" : s.brake.failures ? "warning" : "success"}>{t("safety.brake.of", { count: num(s.brake.failures), max: num(s.brake.max_failures) })}</StatusChip></dd>
            </dl>
            <p className="hint" style={{ margin: 0 }}>{t("safety.brake.window", { since: dateTime(s.brake.since) })}. {t("safety.brake.explain")}</p>
            {s.hold?.active ? (
              <div className="row wrap">
                <span title={can("operator") ? undefined : t("safety.hold.operatorOnly")}>
                  <Button variant="primary" size="sm" icon={PlayCircle} pending={pending} disabled={!can("operator")} onClick={() => void resume()}>{t("safety.hold.resume")}</Button>
                </span>
              </div>
            ) : null}
          </div>
        )}
      </ResourceView>
    </Card>
  );
}

function UndoCard({ onDone, onOpenChange }: { onDone: () => void; onOpenChange: (id: number) => void }) {
  const { t, num, relative } = useI18n();
  const gate = useWriteGate("operator");
  const toast = useToast();
  const [win, setWin] = useState<UndoWindow>("24h");
  const preview = useResource<UndoPreview>("/automation/undo", { window: win }, 60_000);
  const [confirm, setConfirm] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<UndoResult | null>(null);
  const items = preview.data?.items ?? [];
  const switchesOff = items.some((i) => i.ops.some((o) => o.switches_off));

  const submit = async () => {
    setPending(true);
    setError(null);
    try {
      const res = await api.post<UndoResult>("/automation/undo", { window: win, change_ids: items.map((i) => i.id) });
      setResult(res);
      setConfirm(false);
      toast.push(res.counts.mislukt ? "danger" : "success", t("safety.undo.title"),
        t("safety.undo.done", { ok: num(res.counts.teruggedraaid), skipped: num(res.counts.overgeslagen), failed: num(res.counts.mislukt) }));
      void preview.reload();
      onDone();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPending(false);
    }
  };

  return (
    <Card className="span-7" title={t("safety.undo.title")} icon={Undo2} sub={t("safety.undo.sub")} flush
      actions={<Segmented label={t("safety.undo.window")} value={win} onChange={(v) => { setWin(v); setResult(null); }}
        options={WINDOWS.map((w) => ({ value: w, label: t(`safety.undo.window.${w}` as MessageKey) }))} />}>
      {result ? (
        <div style={{ padding: "12px 16px 0" }}>
          <InlineState tone={result.counts.mislukt ? "warning" : "success"} role="status">
            {t("safety.undo.done", { ok: num(result.counts.teruggedraaid), skipped: num(result.counts.overgeslagen), failed: num(result.counts.mislukt) })}
          </InlineState>
          {result.results.length ? (
            <ul className="t-2" style={{ margin: "8px 0 0", paddingLeft: 18, fontSize: 12.5 }}>
              {result.results.map((r) => (
                <li key={r.id}>{r.title}: {t(`safety.undo.result.${r.result}` as MessageKey)}{r.detail ? ` — ${r.detail}` : ""}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      <ResourceView resource={preview} endpoint="GET /api/v1/automation/undo" compact
        isEmpty={(d) => d.items.length === 0 && d.not_undoable.length === 0}
        empty={<StateView kind="empty" compact title={t("safety.undo.empty")} body={t("safety.undo.emptyBody")} />}>
        {(d) => (
          <>
            {d.items.length ? (
              <div className="list">
                {d.items.map((i) => (
                  <div className="list-row" key={i.id} style={{ alignItems: "flex-start" }}>
                    <div className="list-main" style={{ minWidth: 0 }}>
                      <div className="list-title" style={{ whiteSpace: "normal" }}>{i.title}</div>
                      <div className="list-sub mono">#{i.id} · {i.target} · {relative(i.applied_at)}</div>
                      <ul style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: 12.5 }}>
                        {i.ops.map((o, idx) => (
                          <li key={`${o.path}-${idx}`}>
                            {o.label ?? o.path}: {formatOpValue(o.now)} → {formatOpValue(o.back_to)}
                            {o.switches_on ? <> <StatusChip tone="info">{t("safety.undo.switchesOn")}</StatusChip></> : null}
                            {o.switches_off ? <> <StatusChip tone="warning">{t("safety.undo.switchesOff")}</StatusChip></> : null}
                          </li>
                        ))}
                      </ul>
                    </div>
                    <div className="list-aside"><Button size="sm" onClick={() => onOpenChange(i.id)}>{t("safety.cooldown.open")}</Button></div>
                  </div>
                ))}
              </div>
            ) : <StateView kind="empty" compact title={t("safety.undo.empty")} body={t("safety.undo.emptyBody")} />}
            {d.not_undoable.length ? (
              <details style={{ padding: "8px 16px" }}>
                <summary className="t-2" style={{ cursor: "pointer", fontSize: 12.5 }}>{t("safety.undo.notUndoable")} ({num(d.not_undoable.length)})</summary>
                <ul className="t-2" style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: 12.5 }}>
                  {d.not_undoable.map((i) => <li key={i.id}>{i.title} — {i.reason}</li>)}
                </ul>
              </details>
            ) : null}
            <div className="row wrap" style={{ padding: "12px 16px" }}>
              <Button variant="danger" size="sm" icon={RotateCcw} disabled={!gate.allowed || !d.items.length || pending}
                title={!gate.allowed ? gate.reason : undefined} onClick={() => setConfirm(true)}>
                {t("safety.undo.button", { count: num(d.items.length) })}
              </Button>
              {!gate.allowed && gate.reason ? <span className="hint" style={{ margin: 0 }}>{gate.reason}</span> : null}
            </div>
            <DangerDialog open={confirm} onClose={() => { setConfirm(false); setError(null); }} onConfirm={() => void submit()} icon={Undo2}
              title={t("safety.undo.dialogTitle")} confirmLabel={t("safety.undo.confirm")} pending={pending} error={error}
              description={t("safety.undo.dialogBody", { count: num(d.items.length), window: t(`safety.undo.window.${win}` as MessageKey).toLowerCase(), days: num(d.override_days) })
                + (switchesOff ? ` ${t("safety.undo.dialogSwitchesOff")}` : "")}
              impact={[{ label: t("safety.undo.window"), value: t(`safety.undo.window.${win}` as MessageKey) }, { label: t("safety.undo.title"), value: num(d.items.length) }]} />
          </>
        )}
      </ResourceView>
    </Card>
  );
}

function CooldownCard({ safety, onOpenChange }: { safety: Resource<SafetyStatus>; onOpenChange: (id: number) => void }) {
  const { t, num, relative, dateTime } = useI18n();
  const { can } = useSession();
  const { run, pending } = useAction();
  const retry = async (c: CooldownItem) => {
    const next = await run<SafetyStatus>(`reset-${c.key}`, "post", "/automation/failures/reset", { key: c.key }, t("safety.cooldown.retried"));
    if (next) safety.setData(() => next);
  };
  return (
    <Card className="span-12" title={t("safety.cooldown.title")} icon={TimerReset} sub={t("safety.cooldown.sub")} flush>
      <ResourceView resource={safety} endpoint="GET /api/v1/automation/safety" compact isEmpty={(s) => s.cooldowns.length === 0}
        empty={<StateView kind="empty" compact title={t("safety.cooldown.empty")} body={t("safety.cooldown.emptyBody")} />}>
        {(s) => (
          <div className="list">
            {s.cooldowns.map((c) => (
              <div className="list-row list-row--wrap" key={c.key} style={{ alignItems: "flex-start" }}>
                <div className="list-main" style={{ minWidth: 0 }}>
                  <div className="list-title" style={{ whiteSpace: "normal" }}>{c.title}</div>
                  <div className="list-sub">
                    {c.target} · {t("safety.cooldown.failures", { count: num(c.failures) })} · <span title={dateTime(c.next_attempt_at)}>{t("safety.cooldown.next", { when: relative(c.next_attempt_at) })}</span>
                  </div>
                  {c.last_error ? <p className="t-2 pre-wrap" style={{ margin: "6px 0 0", fontSize: 12.5 }}>{t("safety.cooldown.lastError")}: {c.last_error}</p> : null}
                </div>
                <div className="list-aside" style={{ gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>
                  <StatusChip tone={c.parked ? "danger" : "warning"} icon={Hourglass}>{c.parked ? t("safety.cooldown.parked") : t("safety.cooldown.cooling")}</StatusChip>
                  {c.change_id ? <Button size="sm" onClick={() => onOpenChange(Number(c.change_id))}>{t("safety.cooldown.open")}</Button> : null}
                  <span title={can("operator") ? undefined : t("safety.hold.operatorOnly")}>
                    <Button size="sm" icon={RotateCcw} disabled={!can("operator") || pending === `reset-${c.key}`} pending={pending === `reset-${c.key}`} onClick={() => void retry(c)}>{t("safety.cooldown.retry")}</Button>
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </ResourceView>
    </Card>
  );
}

function ApplyingCard({ safety, onOpenChange }: { safety: SafetyStatus; onOpenChange: (id: number) => void }) {
  const { t, relative } = useI18n();
  return (
    <Card className="span-12" title={t("safety.applying.title")} icon={Hourglass} tone="warning" flush>
      <p className="hint" style={{ margin: 0, padding: "12px 16px 4px" }}>{t("safety.applying.body")}</p>
      <div className="list">
        {safety.applying.map((a) => (
          <div className="list-row" key={a.id}>
            <div className="list-main">
              <div className="list-title" style={{ whiteSpace: "normal" }}>{a.title}</div>
              <div className="list-sub mono">#{a.id} · {a.target}{a.apply_started_at ? ` · ${t("safety.applying.started", { when: relative(a.apply_started_at) })}` : ""}</div>
            </div>
            <div className="list-aside"><Button size="sm" onClick={() => onOpenChange(a.id)}>{t("safety.cooldown.open")}</Button></div>
          </div>
        ))}
      </div>
    </Card>
  );
}
