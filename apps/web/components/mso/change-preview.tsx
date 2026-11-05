"use client";

import { Check, GitCompareArrows, Play, Undo2 } from "lucide-react";
import { useState, type ReactNode } from "react";

import { DangerDialog, DiffViewer } from "@/components/mg/domain";
import { Button, Card, InlineState, StatusChip } from "@/components/mg/primitives";
import { useToast } from "@/components/mg/toast";
import { RiskChip } from "@/components/mso/risk-chip";
import { api, errorMessage } from "@/lib/api/client";
import type { ChangeSet } from "@/lib/api/contracts";
import { useI18n } from "@/lib/i18n";
import { useWriteGate } from "@/lib/mode";
import { CHANGE_STATUS } from "@/lib/status";

/** Compact, human-readable rendering of an op value (before/after). */
export function formatOpValue(value: unknown): ReactNode {
  if (value === null || value === undefined || value === "") return <span className="t-3">—</span>;
  if (typeof value === "boolean") return value ? "aan" : "uit";
  if (typeof value === "string" || typeof value === "number") return <span className="mono pre-wrap">{String(value)}</span>;
  if (Array.isArray(value) && value.every((v) => typeof v !== "object")) return <span className="mono pre-wrap">{value.join(", ")}</span>;
  return <pre className="mono pre-wrap" style={{ margin: 0, fontSize: 11.5 }}>{JSON.stringify(value, null, 2)}</pre>;
}

/** Dutch labels for impact keys the backend may send (unknown keys are shown humanised). */
const IMPACT_LABELS: Record<string, string> = {
  kind: "Soort", metric: "Maatstaf", baseline: "Basislijn", eval_h: "Evaluatie na (uur)", evaluate_after_h: "Evaluatie na (uur)",
  area: "Onderdeel", servers: "Servers", reasons: "Redenen", tracker: "Tracker", save_path: "Opslagpad", files: "Bestanden",
  payloads: "Te registreren", already: "Al aanwezig", tags_used: "Gebruikte tags", n: "Aantal metingen", median: "Mediaan",
  mean: "Gemiddelde", from: "Vanaf", to: "Tot", count: "Aantal", items: "Onderdelen", hysteresis: "Hysterese",
  param: "Instelling", before: "Huidig", after: "Voorgesteld", reason: "Reden", name: "Naam", label: "Naam", status: "Status",
  category: "Categorie", categories: "Categorieën", indexers: "Indexers", priority: "Prioriteit", expected: "Verwacht",
  risk: "Risico", note: "Opmerking", message: "Toelichting", window_h: "Meetperiode (uur)", samples: "Metingen",
};
const IMPACT_VALUES: Record<string, string> = {
  guardrail: "beleidsgrens", tuning: "tuning", disk_io: "schijf-I/O", advies: "advies", automatisch_veilig: "automatisch veilig",
  toestemming_nodig: "toestemming nodig", true: "ja", false: "nee",
};
const ISO_DATE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;

function humanKey(key: string): string {
  if (IMPACT_LABELS[key]) return IMPACT_LABELS[key];
  if (/[A-Z ]/.test(key)) return key; // already a human label ("Basislijn", "Status in qBittorrent")
  const words = key.replace(/[._-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Readable value: primitives inline, lists joined, objects as nested key/value rows — never a raw JSON dump. */
function ImpactValue({ value, depth = 0 }: { value: unknown; depth?: number }) {
  const { dateTime, num } = useI18n();
  if (value === null || value === undefined || value === "") return <span className="t-3">—</span>;
  if (typeof value === "boolean") return <>{value ? "ja" : "nee"}</>;
  if (typeof value === "number") return <>{num(value, Number.isInteger(value) ? 0 : 2)}</>;
  if (typeof value === "string") {
    if (ISO_DATE.test(value) && !Number.isNaN(Date.parse(value))) return <>{dateTime(value)}</>;
    return <span className="pre-wrap">{IMPACT_VALUES[value] ?? value}</span>;
  }
  if (Array.isArray(value)) {
    if (!value.length) return <span className="t-3">—</span>;
    if (value.every((v) => v === null || typeof v !== "object")) return <span className="pre-wrap">{value.map((v) => (typeof v === "string" ? IMPACT_VALUES[v] ?? v : String(v))).join(", ")}</span>;
    if (depth >= 2) return <>{value.length} onderdelen</>;
    return (
      <ul style={{ margin: 0, paddingLeft: 18 }}>
        {value.slice(0, 20).map((v, i) => <li key={i}><ImpactValue value={v} depth={depth + 1} /></li>)}
        {value.length > 20 ? <li className="t-3">… en nog {value.length - 20}</li> : null}
      </ul>
    );
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (!entries.length) return <span className="t-3">—</span>;
    if (depth >= 2) return <>{entries.map(([k, v]) => `${humanKey(k)}: ${v !== null && typeof v === "object" ? "…" : String(v)}`).join(" · ")}</>;
    return <ImpactRows entries={entries} depth={depth + 1} />;
  }
  return <>{String(value)}</>;
}

function ImpactRows({ entries, depth }: { entries: Array<[string, unknown]>; depth: number }) {
  return (
    <dl className="kv" data-testid="change-impact">
      {entries.map(([k, v]) => <div key={k} style={{ display: "contents" }}><dt>{humanKey(k)}</dt><dd><ImpactValue value={v} depth={depth} /></dd></div>)}
    </dl>
  );
}

function Impact({ impact }: { impact: unknown }) {
  if (impact === null || impact === undefined || impact === "") return null;
  if (typeof impact === "string" || typeof impact === "number") return <p style={{ margin: 0, fontSize: 13 }}>{String(impact)}</p>;
  if (Array.isArray(impact)) return <div style={{ fontSize: 13 }}><ImpactValue value={impact} /></div>;
  if (typeof impact === "object") return <ImpactRows entries={Object.entries(impact as Record<string, unknown>)} depth={1} />;
  return null;
}

/**
 * Renders a change set (preview → approved → applied → rolled_back | failed) with a DiffViewer of its
 * ops and the lifecycle buttons:
 *   Goedkeuren  → POST /api/v1/changes/{id}/approve   (status preview)
 *   Toepassen   → POST /api/v1/changes/{id}/apply     (status approved; or preview when risk ≠ toestemming_nodig)
 *   Terugdraaien→ POST /api/v1/changes/{id}/rollback  (status applied; typed confirmation)
 * Every button is disabled with a reason in read-only mode or for roles below operator.
 * `onChanged` receives the backend response (the updated change set when it returns one).
 */
export function ChangePreview({ change, onChanged, actions = true, card = true }: {
  change: ChangeSet; onChanged?: (updated: ChangeSet | null) => void | Promise<void>; actions?: boolean; card?: boolean;
}) {
  const { t } = useI18n();
  const gate = useWriteGate("operator");
  const toast = useToast();
  // Action state is keyed by change id: a parent that reuses this component for another change set (e.g. a
  // list + detail panel) must never show the error, pending state or rollback dialog of the previous one —
  // also not when that change's request only finishes after the switch.
  const changeKey = String(change.id);
  const [pendingState, setPendingState] = useState<{ id: string; action: "approve" | "apply" | "rollback" } | null>(null);
  const [errorState, setErrorState] = useState<{ id: string; message: string } | null>(null);
  const [rollbackFor, setRollbackFor] = useState<string | null>(null);
  const pending = pendingState?.id === changeKey ? pendingState.action : null;
  const error = errorState?.id === changeKey ? errorState.message : null;
  const confirmRollback = rollbackFor === changeKey;
  const setConfirmRollback = (open: boolean) => setRollbackFor(open ? changeKey : null);
  const status = CHANGE_STATUS[change.status] ?? CHANGE_STATUS.preview;
  const id = encodeURIComponent(changeKey);

  const run = async (action: "approve" | "apply" | "rollback") => {
    const forId = changeKey;
    setPendingState({ id: forId, action });
    setErrorState(null);
    try {
      const result = await api.post<ChangeSet | null>(`/changes/${id}/${action}`);
      toast.push("success", t(action === "approve" ? "change.approved" : action === "apply" ? "change.applied" : "change.rolledBack"), change.title);
      setRollbackFor((cur) => (cur === forId ? null : cur));
      await onChanged?.(result && typeof result === "object" && "id" in result ? result : null);
    } catch (e) {
      setErrorState({ id: forId, message: errorMessage(e) });
    } finally {
      setPendingState((cur) => (cur?.id === forId ? null : cur));
    }
  };

  const canApprove = change.status === "preview";
  const canApply = change.status === "approved" || (change.status === "preview" && change.risk !== "toestemming_nodig");
  const canRollback = change.status === "applied";
  const final = change.status === "rolled_back" || change.status === "failed";
  const reason = (ok: boolean, why: string) => (!gate.allowed ? gate.reason : ok ? undefined : why);

  const body = (
    <div className="stack">
      <div className="row wrap" style={{ gap: 8 }}>
        <RiskChip risk={change.risk} />
        <StatusChip tone={status.tone}>{t(status.label)}</StatusChip>
        <span className="t-2" style={{ fontSize: 12.5 }}>{t("change.target")}: <span className="mono">{change.target}</span></span>
      </div>
      {change.explanation ? <div><span className="eyebrow">{t("change.why")}</span><p style={{ margin: "4px 0 0", fontSize: 13 }}>{change.explanation}</p></div> : null}
      {change.ops.length ? (
        <div style={{ overflowX: "auto" }}>
          <DiffViewer
            headers={[t("change.setting"), t("change.before"), t("change.after")]}
            keyWidth={180}
            rows={change.ops.map((op, i) => ({
              key: `${op.path}-${i}`,
              label: <>{op.label}<span className="change-ops-path">{op.path}</span></>,
              before: formatOpValue(op.before),
              after: formatOpValue(op.after),
              change: JSON.stringify(op.before) === JSON.stringify(op.after) ? "same" : op.before === null || op.before === undefined ? "added" : op.after === null || op.after === undefined ? "removed" : "changed",
            }))}
          />
        </div>
      ) : <InlineState tone="neutral">{t("change.noOps")}</InlineState>}
      {change.impact !== null && change.impact !== undefined && change.impact !== "" ? <div><span className="eyebrow">{t("change.impact")}</span><div style={{ marginTop: 4 }}><Impact impact={change.impact} /></div></div> : null}
      {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
      {actions && !final ? (
        <div className="row wrap">
          {change.status === "preview" ? (
            <Button size="sm" icon={Check} pending={pending === "approve"} disabled={!gate.allowed || !canApprove || pending !== null} title={reason(canApprove, t("change.alreadyFinal"))} onClick={() => void run("approve")}>{t("action.approve")}</Button>
          ) : null}
          {change.status !== "applied" ? (
            <Button size="sm" variant="primary" icon={Play} pending={pending === "apply"} disabled={!gate.allowed || !canApply || pending !== null} title={reason(canApply, t("change.needsApproval"))} onClick={() => void run("apply")}>{t("action.apply")}</Button>
          ) : null}
          {change.status === "applied" ? (
            <Button size="sm" variant="danger" icon={Undo2} pending={pending === "rollback"} disabled={!gate.allowed || !canRollback || pending !== null} title={reason(canRollback, t("change.notApplied"))} onClick={() => setConfirmRollback(true)}>{t("action.rollback")}</Button>
          ) : null}
          {!gate.allowed && gate.reason ? <span className="hint" style={{ margin: 0 }}>{gate.reason}</span> : null}
        </div>
      ) : null}
      <DangerDialog open={confirmRollback} onClose={() => setConfirmRollback(false)} onConfirm={() => void run("rollback")} icon={Undo2}
        title={t("change.rollbackTitle")} description={t("change.rollbackBody", { target: change.target })} confirmLabel={t("action.rollback")}
        pending={pending === "rollback"} error={error} />
    </div>
  );

  if (!card) return body;
  return <Card title={change.title} icon={GitCompareArrows}>{body}</Card>;
}
