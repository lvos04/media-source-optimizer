"use client";

import { Ban, CircleSlash, Clock, Gauge, KeyRound, Repeat, Search, ShieldAlert, Tag, type LucideIcon } from "lucide-react";

import { Trace } from "@/components/mg/domain";
import { Card, StatusChip, type Tone } from "@/components/mg/primitives";
import { StateView } from "@/components/mg/states";
import type { MediaCategory, ParsedTitle, SourceReport, SourceStatus } from "@/lib/api/search";
import { useI18n, type MessageKey } from "@/lib/i18n";

export const MEDIA_CATEGORIES: MediaCategory[] = ["tv", "tv-anime", "movies", "movies-anime"];

export const SOURCE_STATUS: Record<SourceStatus, { tone: Tone; icon?: LucideIcon; label: MessageKey }> = {
  ok: { tone: "success", label: "zoeken.status.ok" },
  empty: { tone: "neutral", icon: Search, label: "zoeken.status.empty" },
  timeout: { tone: "warning", icon: Clock, label: "zoeken.status.timeout" },
  error: { tone: "danger", label: "zoeken.status.error" },
  category_error: { tone: "warning", icon: Tag, label: "zoeken.status.category_error" },
  skipped_cycle: { tone: "info", icon: Repeat, label: "zoeken.status.skipped_cycle" },
  skipped_scope: { tone: "neutral", icon: CircleSlash, label: "zoeken.status.skipped_scope" },
  rate_limited: { tone: "warning", icon: Gauge, label: "zoeken.status.rate_limited" },
  backoff: { tone: "warning", icon: Clock, label: "zoeken.status.backoff" },
  auth_error: { tone: "danger", icon: KeyRound, label: "zoeken.status.auth_error" },
};

export function SourceStatusChip({ status }: { status: SourceStatus }) {
  const { t } = useI18n();
  const s = SOURCE_STATUS[status] ?? SOURCE_STATUS.error;
  return <StatusChip tone={s.tone} icon={s.icon}>{t(s.label)}</StatusChip>;
}

export function categoryKey(c: MediaCategory): MessageKey {
  return `zoeken.cat.${c}` as MessageKey;
}

/** MediaGuard-style quality ribbon (resolution · source · codec · HDR/DV · audio languages). */
export function ReleaseRibbon({ p }: { p: ParsedTitle }) {
  const parts: Array<[string, string | undefined]> = [];
  if (p.resolution) parts.push([`${p.resolution}p`, undefined]);
  if (p.source) parts.push([p.source.toUpperCase() === "WEB" ? "WEB" : p.source.replace(/^\w/, (c) => c.toUpperCase()), undefined]);
  if (p.video_codec) parts.push([p.video_codec.toUpperCase(), p.video_codec === "av1" ? "q-av1" : undefined]);
  if (p.hdr.includes("dv")) parts.push([p.dv_profile ? `DV ${p.dv_profile}` : "DV", "q-dv"]);
  else if (p.hdr.length) parts.push([p.hdr[0].toUpperCase().replace("PLUS", "+"), "q-hdr"]);
  const langs = p.dual_audio ? "DUAL" : p.multi_audio ? "MULTI" : p.audio_languages.map((l) => l.toUpperCase()).join("·");
  if (langs) parts.push([langs, "q-lang"]);
  if (!parts.length) return null;
  const label = parts.map((x) => x[0]).join(", ");
  return <span className="qribbon" aria-label={label} title={label}>{parts.map(([text, cls]) => <span key={text} className={cls} aria-hidden="true">{text}</span>)}</span>;
}

export function FlagChips({ flags }: { flags: string[] }) {
  const { t } = useI18n();
  if (!flags.length) return null;
  return (
    <span className="row wrap" style={{ gap: 4 }}>
      {flags.map((f) => (
        <StatusChip key={f} tone={f === "geblokkeerd" ? "danger" : "warning"} icon={f === "geblokkeerd" ? Ban : ShieldAlert}>
          {t(`zoeken.flag.${f}` as MessageKey) || f}
        </StatusChip>
      ))}
    </span>
  );
}

export function numbering(p: ParsedTitle): string | null {
  if (p.season !== null && p.episodes.length) {
    const eps = p.episodes.length > 1 ? `E${pad(p.episodes[0])}–E${pad(p.episodes[p.episodes.length - 1])}` : `E${pad(p.episodes[0])}`;
    return `S${pad(p.season)}${eps}`;
  }
  if (p.absolute_episodes.length) {
    const a = p.absolute_episodes;
    return a.length > 1 ? `#${a[0]}–${a[a.length - 1]}` : `#${a[0]}`;
  }
  if (p.season !== null) return `S${pad(p.season)}`;
  return p.year ? String(p.year) : null;
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

/** Per-source status report of one search (honest: timeouts/category errors ≠ empty). */
export function SourceReportCard({ sources, trace, traceId, className }: { sources: SourceReport[]; trace: string[]; traceId: string; className?: string }) {
  const { t, num } = useI18n();
  return (
    <Card className={className} title={t("zoeken.report")} sub={t("zoeken.reportSub")} icon={Gauge} flush>
      {sources.length === 0 ? (
        <StateView kind="empty" compact title={t("zoeken.noSources.title")} body={t("zoeken.noSources.body")} />
      ) : (
        <div className="list">
          {sources.map((s) => (
            <div key={s.source_id} className="list-row" data-testid={`source-${s.source_id}`}>
              <div className="list-main">
                <div className="list-title">{s.label}</div>
                <div className="list-sub">
                  <span className="mono">{s.source_id}</span>
                  {s.message ? <> · {s.message}</> : null}
                </div>
              </div>
              <div className="list-aside row wrap" style={{ gap: 6, justifyContent: "flex-end" }}>
                <SourceStatusChip status={s.status} />
                {s.status === "ok" ? <span className="tag">{num(s.count)}</span> : null}
                {s.latency_ms !== null ? <span className="t-3 mono" style={{ fontSize: 12 }}>{num(s.latency_ms)} ms</span> : null}
              </div>
            </div>
          ))}
        </div>
      )}
      <div className="card-footer t-3" style={{ fontSize: 12 }}>
        <span className="row wrap" style={{ gap: 6 }}>
          {t("zoeken.trace")}: <Trace steps={trace.map((n, i) => ({ label: n, active: i === trace.length - 1 }))} />
          <span className="mono">· {traceId}</span>
        </span>
      </div>
    </Card>
  );
}
