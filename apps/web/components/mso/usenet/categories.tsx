"use client";

import { FolderPlus, RefreshCw, RotateCcw, Save, Tags, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { Button, Card, InlineState, LinkButton, StatusChip, type Tone } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { ChangePreview } from "@/components/mso/change-preview";
import { RiskChip } from "@/components/mso/risk-chip";
import { api, errorMessage } from "@/lib/api/client";
import type { ChangeSet } from "@/lib/api/contracts";
import { useResource, type Resource } from "@/lib/api/hooks";
import type { MediaCategory, SabCategories, SabCategory } from "@/lib/api/usenet";
import type { CategoryDiagnosis, CategoryInstance, ClientCategoryStatus, StarDay } from "@/lib/api/usenetcat";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";

const SOURCE_TONE = { handmatig: "info", automatisch: "success", geen: "neutral" } as const;

function CategoryTable({ data, onSaved }: { data: SabCategories; onSaved: () => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const { can } = useSession();
  const [draft, setDraft] = useState<Record<string, MediaCategory | null>>(
    () => Object.fromEntries(data.items.map((c) => [c.name, c.media_category])),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirty = data.items.some((c) => draft[c.name] !== c.media_category);
  const mediaLabel = (m: MediaCategory | null) => (m ? t(`usenet.cat.media.${m}` as MessageKey) : t("usenet.cat.none"));

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.put<SabCategories>("/usenet/sab/category-map", {
        items: data.items.filter((c) => c.name !== "*").map((c) => ({ name: c.name, media_category: draft[c.name] ?? null })),
      });
      toast.push("success", t("usenet.cat.saved"));
      onSaved();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  const row = (c: SabCategory) => {
    const id = `usenet-cat-${c.name.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
    const isDefault = c.name === "*";
    return (
      <tr key={c.name}>
        <td data-label={t("usenet.cat.sab")}>
          <label htmlFor={id} className="mono">{isDefault ? t("usenet.dl.defaultCategory") : c.name}</label>
        </td>
        <td data-label={t("usenet.cat.media")}>
          <select id={id} className="input" value={draft[c.name] ?? ""} disabled={isDefault || !can("admin")}
            title={isDefault ? t("usenet.cat.help") : !can("admin") ? t("usenet.adminOnly") : undefined}
            onChange={(e) => setDraft((d) => ({ ...d, [c.name]: (e.target.value || null) as MediaCategory | null }))}>
            <option value="">{t("usenet.cat.none")}</option>
            {data.media_categories.map((m) => <option key={m} value={m}>{mediaLabel(m)}</option>)}
          </select>
        </td>
        <td data-label={t("usenet.cat.origin")}>
          <span className="row wrap" style={{ gap: 6 }}>
            <StatusChip tone={SOURCE_TONE[c.source]}>{t(`usenet.cat.source.${c.source}` as MessageKey)}</StatusChip>
            {!isDefault && c.source === "handmatig" ? (
              <Button size="sm" variant="ghost" icon={RotateCcw} disabled={!can("admin")}
                title={can("admin") ? `${t("usenet.cat.reset")}: ${mediaLabel(c.auto)}` : t("usenet.adminOnly")}
                onClick={() => setDraft((d) => ({ ...d, [c.name]: c.auto }))}>
                {t("usenet.cat.reset")}
              </Button>
            ) : null}
          </span>
        </td>
      </tr>
    );
  };

  return (
    <>
      <table className="table table--stack">
        <thead>
          <tr><th>{t("usenet.cat.sab")}</th><th>{t("usenet.cat.media")}</th><th>{t("usenet.cat.origin")}</th></tr>
        </thead>
        <tbody>{data.items.map(row)}</tbody>
      </table>
      <div className="card-body stack-sm">
        <p className="t-2" style={{ margin: 0, fontSize: 12.5 }}>{t("usenet.cat.help")}</p>
        {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
        <div>
          <Button variant="primary" icon={Save} onClick={() => void save()} pending={saving} disabled={!dirty || !can("admin")}
            title={!can("admin") ? t("usenet.adminOnly") : undefined}>
            {t("usenet.cat.save")}
          </Button>
        </div>
      </div>
    </>
  );
}

/** SABnzbd categories → library categories (automatic name heuristic, editable; stored in `usenet.category_map`),
 * preceded by the 1.6.0 diagnosis of why downloads land in '*'. */
export function CategoryMapCard() {
  const { t } = useI18n();
  const cats = useResource<SabCategories>("/usenet/sab/categories");
  return (
    <Card title={t("usenet.cat.title")} icon={Tags} sub={t("usenet.cat.sub")} className="span-12" flush id="sab-categorieen">
      <CategoryDiagnosisSection onChanged={() => void cats.reload()} />
      <ResourceView resource={cats} endpoint="GET /api/v1/usenet/sab/categories" isEmpty={(d) => !d.items.length}
        empty={<StateView kind="empty" compact />}>
        {(d) => <CategoryTable key={JSON.stringify(d.items)} data={d} onSaved={() => void cats.reload()} />}
      </ResourceView>
    </Card>
  );
}

// ------------------------------------------------------------------ 1.6.0: why downloads land in '*'
const DIAG = "/usenet/categories/diagnosis";
const STATUS_TONE: Record<ClientCategoryStatus, Tone> = {
  ok: "success", ontbreekt_in_sab: "danger", geen_categorie: "warning", uit: "neutral", onbekend: "neutral",
};

function quoted(names: string[]) {
  return names.map((n) => `'${n}'`).join(", ");
}

/** 14 days of SABnzbd downloads: all (muted) and those in '*' (warning), with a text summary for screen readers. */
function StarDaysChart({ days, recentDays, recentStar, recentTotal }: { days: StarDay[]; recentDays: number; recentStar: number; recentTotal: number }) {
  const { t, num, date } = useI18n();
  const max = Math.max(1, ...days.map((d) => d.total));
  const slot = 20;
  const h = 56;
  const summary = t("usenetcat.days.summary", { days: num(recentDays), star: num(recentStar), total: num(recentTotal) });
  if (!days.some((d) => d.total)) return <p className="t-3" style={{ margin: 0, fontSize: 12.5 }}>{t("usenetcat.days.none", { days: num(days.length) })}</p>;
  return (
    <div className="stack-sm" style={{ gap: 4 }}>
      <svg viewBox={`0 0 ${days.length * slot} ${h}`} width="100%" height={h} preserveAspectRatio="none" role="img" aria-label={summary}
        style={{ maxWidth: 520, display: "block" }}>
        {days.map((d, i) => {
          const th = (d.total / max) * (h - 4);
          const sh = (d.star / max) * (h - 4);
          return (
            <g key={d.day}>
              <title>{t("usenetcat.days.bar", { day: date(d.day), star: num(d.star), total: num(d.total) })}</title>
              <rect x={i * slot + 3} y={h - th} width={slot - 6} height={th} rx={2} fill="var(--mg-neutral-tint)" />
              <rect x={i * slot + 3} y={h - sh} width={slot - 6} height={sh} rx={2} fill="var(--mg-warning)" />
            </g>
          );
        })}
      </svg>
      <div className="row wrap t-3" style={{ gap: 12, fontSize: 12 }}>
        <span>{summary}</span>
        <span className="row" style={{ gap: 4 }}><span aria-hidden="true" style={{ width: 10, height: 10, borderRadius: 2, background: "var(--mg-warning)", display: "inline-block" }} />{t("usenetcat.days.legendStar")}</span>
        <span className="row" style={{ gap: 4 }}><span aria-hidden="true" style={{ width: 10, height: 10, borderRadius: 2, background: "var(--mg-neutral-tint)", display: "inline-block" }} />{t("usenetcat.days.legendTotal")}</span>
      </div>
    </div>
  );
}

function InstanceRows({ items }: { items: CategoryInstance[] }) {
  const { t } = useI18n();
  const reason = (r: string | null) => (r ? t(`usenetcat.mgReason.${r}` as MessageKey) : "");
  return (
    <table className="table table--stack">
      <thead>
        <tr><th>{t("usenetcat.inst.instance")}</th><th>{t("usenetcat.inst.sends")}</th><th>{t("usenetcat.inst.inSab")}</th><th>{t("usenetcat.inst.managed")}</th><th>{t("usenetcat.inst.health")}</th></tr>
      </thead>
      <tbody>
        {items.map((inst) => {
          const app = inst.app === "radarr" ? "Radarr" : "Sonarr";
          const c = inst.clients[0];
          return (
            <tr key={inst.id}>
              <td data-label={t("usenetcat.inst.instance")}>
                <strong>{inst.name}</strong> <span className="t-3" style={{ fontSize: 12 }}>{app}</span>
                {inst.clients_reason ? <div className="t-3" style={{ fontSize: 12, whiteSpace: "normal" }}>{t("usenetcat.inst.unreadable", { reason: reason(inst.clients_reason) })}</div> : null}
              </td>
              <td data-label={t("usenetcat.inst.sends")}>
                {inst.no_sab_client ? <span className="t-3">{t("usenetcat.inst.noSab")}</span>
                  : inst.clients.map((cl, i) => (
                    <div key={`${cl.name ?? "x"}-${i}`}>
                      <span className="mono">{cl.category ?? "—"}</span>
                      {cl.certainty === "waarschijnlijk" ? <span className="t-3" style={{ fontSize: 12 }}> · {t("usenetcat.inst.probable")}</span> : null}
                      {cl.name ? <div className="t-3" style={{ fontSize: 12 }}>{cl.name}</div> : null}
                    </div>
                  ))}
              </td>
              <td data-label={t("usenetcat.inst.inSab")}>
                {inst.clients.length ? inst.clients.map((cl, i) => (
                  <div key={`${cl.name ?? "x"}-${i}`}><StatusChip tone={STATUS_TONE[cl.status]}>{t(`usenetcat.status.${cl.status}` as MessageKey)}</StatusChip></div>
                )) : <span className="t-3">—</span>}
              </td>
              <td data-label={t("usenetcat.inst.managed")}>{c ? t(`usenetcat.managed.${c.fix_in}` as MessageKey) : "—"}</td>
              <td data-label={t("usenetcat.inst.health")} style={{ whiteSpace: "normal", maxWidth: 360 }}>
                {inst.health.length ? (
                  <ul style={{ margin: 0, paddingLeft: 16 }}>
                    {inst.health.map((h, i) => {
                      const key = `usenetcat.health.${h.source}`;
                      const label = t(key as MessageKey);
                      return (
                        <li key={`${h.source}-${i}`} style={{ fontSize: 12.5 }}>
                          <StatusChip tone={h.type === "error" ? "danger" : "warning"}>{label === key ? h.source || "—" : label}</StatusChip>{" "}
                          <span className="t-2">{h.message}</span>
                          <div className="t-3" style={{ fontSize: 11.5 }}>{t(inst.health_source === "live" ? "usenetcat.health.source" : "usenetcat.health.cached", { app })}</div>
                        </li>
                      );
                    })}
                  </ul>
                ) : <span className="t-3" style={{ fontSize: 12.5 }}>{t("usenetcat.health.none")}</span>}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function ProposalBlock({ d, diag, onChanged }: { d: CategoryDiagnosis; diag: Resource<CategoryDiagnosis>; onChanged: () => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const { can } = useSession();
  const [pending, setPending] = useState(false);
  const change = useResource<ChangeSet>(d.proposal ? `/changes/${d.proposal.id}` : null);
  const names = d.missing.map((m) => m.category);

  const refreshAll = async () => {
    try {
      const fresh = await api.get<CategoryDiagnosis>(DIAG, { refresh: true });
      diag.setData(() => fresh);
    } catch {
      await diag.reload();
    }
    onChanged();
  };
  const propose = async () => {
    setPending(true);
    try {
      await api.post<ChangeSet>("/usenet/categories/propose", { names });
      toast.push("success", t("usenetcat.a.proposed"));
      await diag.reload();
    } catch (e) {
      toast.push("danger", errorMessage(e));
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="stack-sm" style={{ border: "1px solid var(--mg-border-soft)", borderRadius: "var(--mg-r)", padding: 12 }}>
      <div className="row-between wrap">
        <span className="eyebrow">{t("usenetcat.a.fixTitle")}</span>
        <RiskChip risk="toestemming_nodig" />
      </div>
      <p className="t-2" style={{ margin: 0, fontSize: 13 }}>{t("usenetcat.a.fixBody")}</p>
      <InlineState tone="warning" icon={TriangleAlert}>{t("usenetcat.a.warning")}</InlineState>
      <p className="t-3" style={{ margin: 0, fontSize: 12.5 }}>{t("usenetcat.a.alternative")}</p>
      {d.proposal ? (
        <ResourceView resource={change} compact endpoint={`GET /api/v1/changes/${d.proposal.id}`}>
          {(ch) => <ChangePreview change={ch} card={false} onChanged={async () => { await change.reload(); await refreshAll(); }} />}
        </ResourceView>
      ) : (
        <div>
          <Button variant="primary" icon={FolderPlus} onClick={() => void propose()} pending={pending}
            disabled={!can("admin") || !d.sab.available} title={!can("admin") ? t("usenet.adminOnly") : undefined}>
            {t("usenetcat.a.propose", { cats: quoted(names) })}
          </Button>
        </div>
      )}
    </div>
  );
}

function Verdict({ d }: { d: CategoryDiagnosis }) {
  const { t, num } = useI18n();
  const days = num(d.star.recent_days);
  const mgReason = d.mediaguard.reason ? t(`usenetcat.mgReason.${d.mediaguard.reason}` as MessageKey)
    : d.mediaguard.available ? t("usenetcat.mgReason.geen_instanties") : t("usenetcat.mgReason.route_ontbreekt");
  if (d.verdict === "a") {
    return (
      <div className="stack-sm">
        <InlineState tone="danger" role="status">{t("usenetcat.verdict.a", { cats: quoted(d.missing.map((m) => m.category)) })}</InlineState>
        <ul className="list" style={{ margin: 0 }}>
          {d.missing.map((m) => (
            <li key={m.category} className="list-row list-row--wrap">
              <span className="mono"><strong>{m.category}</strong></span>
              <div className="list-main">
                <div className="list-sub" style={{ whiteSpace: "normal" }}>{t("usenetcat.a.item", { instances: m.instances.join(", "), folder: d.folders[m.category] ?? m.category })}</div>
                {m.certainty === "waarschijnlijk" ? <div className="list-sub t-3" style={{ whiteSpace: "normal" }}>{t("usenetcat.a.probable")}</div> : null}
              </div>
            </li>
          ))}
        </ul>
      </div>
    );
  }
  if (d.verdict === "b") {
    return (
      <div className="stack-sm">
        <InlineState tone="warning" role="status">{t("usenetcat.verdict.b", { count: num(d.no_category.length) })}</InlineState>
        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
          {d.no_category.map((b, i) => (
            <li key={`${b.instance}-${i}`}>
              {t(b.fix_in === "mediaguard" ? "usenetcat.b.mediaguard" : "usenetcat.b.arr", {
                instance: b.instance, client: b.client ?? t("usenetcat.b.unnamed"), expected: b.expected ? `'${b.expected}'` : t("usenetcat.b.noExample"),
              })}
            </li>
          ))}
        </ul>
      </div>
    );
  }
  if (d.verdict === "c" && d.other) {
    return (
      <InlineState tone="info" role="status">
        {t("usenetcat.verdict.c", { days, n: num(d.other.recent_star) })}{" "}
        {d.other.from_mso ? `${t("usenetcat.c.mso", { n: num(d.other.from_mso) })} ` : ""}
        {d.other.unexplained ? `${t("usenetcat.c.other", { n: num(d.other.unexplained) })} ` : ""}
        {d.other.queue_star ? t("usenetcat.c.queue", { n: num(d.other.queue_star) }) : ""}
      </InlineState>
    );
  }
  if (d.verdict === "ok") return <InlineState tone="success" role="status">{t("usenetcat.verdict.ok", { days })}</InlineState>;
  if (d.verdict === "geen_ster") return <InlineState tone="success" role="status">{t("usenetcat.verdict.noStar", { days, reason: mgReason })}</InlineState>;
  if (!d.sab.configured) return <InlineState tone="neutral" role="status">{t("usenetcat.verdict.noSab")}</InlineState>;
  if (!d.sab.available) return <InlineState tone="warning" role="status">{t("usenetcat.verdict.unknownSab", { error: d.sab.error ?? "—" })}</InlineState>;
  return <InlineState tone="warning" role="status">{t("usenetcat.verdict.unknownMg", { reason: mgReason })}</InlineState>;
}

/** The diagnosis: verdict + fix location, the one proposal for case (a), MSO's own sends, '*' per day and per
 * Sonarr/Radarr instance what it sends and what Sonarr/Radarr report themselves. */
export function CategoryDiagnosisSection({ onChanged }: { onChanged?: () => void }) {
  const { t, relative, dateTime } = useI18n();
  const toast = useToast();
  const diag = useResource<CategoryDiagnosis>(DIAG, undefined, 300_000);
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    setBusy(true);
    try {
      const fresh = await api.get<CategoryDiagnosis>(DIAG, { refresh: true });
      diag.setData(() => fresh);
      toast.push("success", t("usenetcat.refreshed"));
    } catch (e) {
      toast.push("danger", errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card-body stack" style={{ borderBottom: "1px solid var(--mg-border-soft)" }} data-testid="category-diagnosis">
      <div className="row-between wrap">
        <span className="eyebrow">{t("usenetcat.title")}</span>
        <span className="row wrap" style={{ gap: 8 }}>
          {diag.data ? <span className="t-3" style={{ fontSize: 12 }}>{t("usenetcat.checked", { when: relative(diag.data.checked_at) })}</span> : null}
          <Button size="sm" icon={RefreshCw} onClick={() => void refresh()} pending={busy}>{t("usenetcat.refresh")}</Button>
        </span>
      </div>
      <p className="t-2" style={{ margin: 0, fontSize: 12.5 }}>{t("usenetcat.why")}</p>
      <ResourceView resource={diag} compact endpoint={`GET /api/v1${DIAG}`}>
        {(d) => (
          <div className="stack">
            <Verdict d={d} />
            {d.verdict === "a" ? <ProposalBlock d={d} diag={diag} onChanged={() => onChanged?.()} /> : null}
            {d.last_applied && !d.proposal ? (
              <p className="t-3 row wrap" style={{ margin: 0, fontSize: 12.5, gap: 8 }}>
                <span>
                  {d.last_applied.status === "rolled_back"
                    ? t("usenetcat.rolledBack", { when: dateTime(d.last_applied.rolled_back_at), cats: quoted(d.last_applied.categories) })
                    : t("usenetcat.applied", { when: dateTime(d.last_applied.applied_at), cats: quoted(d.last_applied.categories) })}
                </span>
                <LinkButton size="sm" href={`/automatisering?tab=wijzigingen&change=${d.last_applied.id}`}>{t("usenetcat.openChanges")}</LinkButton>
              </p>
            ) : null}
            {d.mso.missing.length ? <InlineState tone="neutral">{t("usenetcat.mso.body", { cats: quoted(d.mso.missing) })}</InlineState> : null}
            <div className="stack-sm">
              <span className="eyebrow">{t("usenetcat.days.title")}</span>
              <StarDaysChart days={d.star.days} recentDays={d.star.recent_days} recentStar={d.star.recent_star} recentTotal={d.star.recent_total} />
            </div>
            {d.mediaguard.available ? (
              <div className="stack-sm">
                <span className="eyebrow">{t("usenetcat.inst.title")}</span>
                {d.instances.length ? <InstanceRows items={d.instances} /> : <p className="t-3" style={{ margin: 0, fontSize: 12.5 }}>{t("usenetcat.inst.none")}</p>}
              </div>
            ) : null}
          </div>
        )}
      </ResourceView>
    </div>
  );
}

/** Downloads → Usenet: the diagnosed cause next to the "'*' in the list below" count. */
export function StarCategoryNote({ count, total }: { count: number; total: number }) {
  const { t, num } = useI18n();
  const diag = useResource<CategoryDiagnosis>(DIAG);
  const d = diag.data;
  let cause = diag.error ? t("usenet.dl.defaultCatFix") : t("usenetcat.note.unknown");
  if (d?.verdict === "a") cause = t("usenetcat.note.a", { cats: quoted(d.missing.map((m) => m.category)) });
  else if (d?.verdict === "b") cause = t("usenetcat.note.b", { count: num(d.no_category.length) });
  else if (d?.verdict === "c") cause = t("usenetcat.note.c");
  else if (d?.verdict === "ok") cause = t("usenetcat.note.ok");
  else if (d?.verdict === "geen_ster") cause = t("usenetcat.note.noStar");
  return (
    <div style={{ padding: "12px 16px 0" }}>
      <InlineState tone={d?.verdict === "a" || d?.verdict === "b" ? "warning" : "info"}>
        <strong>{t("usenet.dl.defaultCatTitle")}</strong> {t("usenet.dl.defaultCatCount", { n: num(count), total: num(total) })}{" "}
        {t("usenet.dl.defaultCatBody")} {diag.loading ? "" : cause}{" "}
        <LinkButton size="sm" href="/usenet-providers#sab-categorieen">{t("usenetcat.note.link")}</LinkButton>
      </InlineState>
    </div>
  );
}
