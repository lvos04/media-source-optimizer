"use client";

import { ArrowDown, ArrowUp, Check, Clock, Info, Lock, OctagonX, TriangleAlert, Unlock, type LucideIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Confidence } from "@/components/mg/domain";
import { Modal, StatusChip, type Tone } from "@/components/mg/primitives";
import { ChangePreview } from "@/components/mso/change-preview";
import type { ChangeSet } from "@/lib/api/contracts";
import type { Severity, TorrentFinding } from "@/lib/api/torrent";
import { useI18n, type MessageKey } from "@/lib/i18n";

export const SEVERITY_TONE: Record<Severity, Tone> = { critical: "danger", warning: "warning", info: "info" };
const SEVERITY_ICON = { critical: OctagonX, warning: TriangleAlert, info: Info } as const;

// Peil (PEIL_MIGRATIE §6.2): downloading/seeding are activities, not health → neutral with a direction arrow;
// only "stalled" (Beperkt) and "missing files/error" (Storing) carry a status colour.
const STATE_ICON: Record<string, LucideIcon> = {
  downloading: ArrowDown, forcedDL: ArrowDown, metaDL: ArrowDown, forcedMetaDL: ArrowDown,
  uploading: ArrowUp, forcedUP: ArrowUp, stalledUP: ArrowUp, queuedDL: Clock, queuedUP: Clock,
};
const STATE_GROUP: Record<string, { tone: Tone; key: MessageKey }> = {
  downloading: { tone: "neutral", key: "torrent.state.downloading" },
  forcedDL: { tone: "neutral", key: "torrent.state.downloading" },
  metaDL: { tone: "neutral", key: "torrent.state.metaDL" },
  forcedMetaDL: { tone: "neutral", key: "torrent.state.metaDL" },
  stalledDL: { tone: "warning", key: "torrent.state.stalledDL" },
  queuedDL: { tone: "neutral", key: "torrent.state.queuedDL" },
  pausedDL: { tone: "neutral", key: "torrent.state.pausedDL" },
  stoppedDL: { tone: "neutral", key: "torrent.state.pausedDL" },
  checkingDL: { tone: "info", key: "torrent.state.checking" },
  checkingUP: { tone: "info", key: "torrent.state.checking" },
  checkingResumeData: { tone: "info", key: "torrent.state.checking" },
  allocating: { tone: "info", key: "torrent.state.allocating" },
  moving: { tone: "info", key: "torrent.state.moving" },
  uploading: { tone: "neutral", key: "torrent.state.seeding" },
  forcedUP: { tone: "neutral", key: "torrent.state.seeding" },
  stalledUP: { tone: "neutral", key: "torrent.state.stalledUP" },
  queuedUP: { tone: "neutral", key: "torrent.state.queuedUP" },
  pausedUP: { tone: "neutral", key: "torrent.state.pausedUP" },
  stoppedUP: { tone: "neutral", key: "torrent.state.pausedUP" },
  error: { tone: "danger", key: "torrent.state.error" },
  missingFiles: { tone: "danger", key: "torrent.state.missingFiles" },
};

export function StateChip({ state }: { state: string | null }) {
  const { t } = useI18n();
  const s = STATE_GROUP[state ?? ""] ?? { tone: "unknown" as Tone, key: "torrent.state.unknown" as MessageKey };
  return <StatusChip tone={s.tone} icon={STATE_ICON[state ?? ""]} title={state ?? undefined}>{t(s.key)}</StatusChip>;
}

export function PrivateChip({ value }: { value: boolean | null }) {
  const { t } = useI18n();
  if (value === null) return <StatusChip tone="unknown">{t("torrent.privateUnknown")}</StatusChip>;
  return value
    ? <StatusChip tone="info" icon={Lock}>{t("torrent.private")}</StatusChip>
    : <StatusChip tone="neutral" icon={Unlock}>{t("torrent.public")}</StatusChip>;
}

export function DiagnosisChip({ finding }: { finding: TorrentFinding | null }) {
  const { t } = useI18n();
  if (!finding) return <StatusChip tone="success" icon={Check}>{t("torrent.diag.ok")}</StatusChip>;
  return <StatusChip tone={SEVERITY_TONE[finding.severity]} icon={SEVERITY_ICON[finding.severity]} title={finding.explanation}>{finding.title}</StatusChip>;
}

function evidenceValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/** Finding with explanation, confidence, suggestion and raw evidence (never colour alone). */
export function FindingItem({ finding }: { finding: TorrentFinding }) {
  const { t } = useI18n();
  const entries = Object.entries(finding.evidence ?? {}).filter(([k]) => k !== "rules");
  return (
    <article className="evidence">
      <div className="evidence-head">
        <DiagnosisChip finding={finding} />
        <span className="grow" />
        <span className="t-3" style={{ fontSize: 12 }}>{t("torrent.confidence")}</span>
        <Confidence value={finding.confidence} tone={finding.severity === "critical" ? "danger" : finding.severity === "warning" ? "warning" : "success"} />
      </div>
      <dl className="evidence-body">
        <dt>{t("torrent.explanation")}</dt><dd>{finding.explanation}</dd>
        {finding.suggestion ? <><dt>{t("torrent.suggestion")}</dt><dd>{finding.suggestion}</dd></> : null}
      </dl>
      {entries.length ? (
        <details className="raw">
          <summary>{t("torrent.evidence")}</summary>
          <dl className="kv" style={{ fontSize: 12 }}>
            {entries.map(([k, v]) => <div key={k} style={{ display: "contents" }}><dt className="mono">{k}</dt><dd className="mono" style={{ overflowWrap: "anywhere" }}>{evidenceValue(v)}</dd></div>)}
          </dl>
        </details>
      ) : null}
    </article>
  );
}

export function FindingList({ findings, empty }: { findings: TorrentFinding[]; empty?: string }) {
  const { t } = useI18n();
  if (!findings.length) return <p className="t-2" style={{ margin: 0, fontSize: 13 }}><Check className="ico ico-sm" aria-hidden="true" /> {empty ?? t("torrent.noFindings")}</p>;
  return <div className="stack-sm">{findings.map((f) => <FindingItem key={`${f.code}-${f.title}`} finding={f} />)}</div>;
}

/** bytes/s → "x MB/s" */
export function useRate() {
  const { bytes } = useI18n();
  return (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${bytes(v)}/s`);
}

const TORRENT_CHANGED = "mso:torrent-changed";

/** Tell every torrent block that a qBittorrent change set was approved/applied/rolled back (so read-only blocks
 *  such as "Veilige snelheidslimieten" and "Instellingen relevant voor tuning" reload at once). */
export function notifyTorrentChanged() {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(TORRENT_CHANGED));
}

/** Calls `reload` whenever `notifyTorrentChanged()` fires (and once more shortly after, when qBittorrent has
 *  settled and MSO's snapshot is refreshed). */
export function useTorrentChanged(reload: () => unknown) {
  const ref = useRef(reload);
  useEffect(() => { ref.current = reload; });
  useEffect(() => {
    const timers: number[] = [];
    const on = () => {
      void ref.current();
      timers.push(window.setTimeout(() => void ref.current(), 3000));
    };
    window.addEventListener(TORRENT_CHANGED, on);
    return () => { window.removeEventListener(TORRENT_CHANGED, on); timers.forEach((x) => window.clearTimeout(x)); };
  }, []);
}

/** Change set returned by a preview endpoint, shown in a dialog with the standard ChangePreview. */
export function ChangeDialog({ change, onClose, onChanged }: { change: ChangeSet | null; onClose: () => void; onChanged?: () => void }) {
  const { t } = useI18n();
  const [current, setCurrent] = useState<ChangeSet | null>(change);
  if (change && current?.id !== change.id) setCurrent(change);
  if (!change || !current) return null;
  return (
    <Modal open onClose={onClose} title={current.title} description={t("torrent.changeDialogHint")}>
      <ChangePreview change={current} card={false} onChanged={(updated) => { if (updated) setCurrent(updated); onChanged?.(); notifyTorrentChanged(); }} />
    </Modal>
  );
}
