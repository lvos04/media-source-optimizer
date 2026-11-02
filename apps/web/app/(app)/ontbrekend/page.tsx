"use client";

import { Activity, ListTodo, Search, Trash2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Button, Card, InlineState, PageHeader, Segmented, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { MgModeChip } from "@/components/mso/mediaguard/parts";
import { BaselineCard, useCategoryLabel } from "@/components/mso/recovery/baseline-card";
import { BlacklistCard } from "@/components/mso/recovery/blacklist-card";
import { ItemPanel } from "@/components/mso/recovery/item-panel";
import { ManualWantedDialog } from "@/components/mso/recovery/requirements-editor";
import { useAction } from "@/lib/api/actions";
import { useResource } from "@/lib/api/hooks";
import type { MediaCategory, MgReasons, WantedList, WantedRow } from "@/lib/api/recovery";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

type Source = "all" | "mediaguard" | "manual";

function numbering(r: WantedRow): string {
  const parts: string[] = [];
  if (r.season_number != null && r.episode_number != null) parts.push(`S${String(r.season_number).padStart(2, "0")}E${String(r.episode_number).padStart(2, "0")}`);
  if (r.absolute_episode_number != null) parts.push(`#${r.absolute_episode_number}`);
  if (r.year) parts.push(String(r.year));
  return parts.join(" · ");
}

export default function MissingPage() {
  const { t, num, relative } = useI18n();
  const { can } = useSession();
  const categoryLabel = useCategoryLabel();
  const [source, setSource] = useState<Source>("all");
  const [cat, setCat] = useState<MediaCategory | "">("");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string | null>(null);
  const [bump, setBump] = useState(0);
  const wanted = useResource<WantedList>("/recovery/wanted", { source, media_category: cat || undefined, q: query || undefined, page, page_size: 50 }, 120_000);
  const reasons = useResource<{ data: MgReasons | null }>("/mediaguard/reasons");
  const { run, pending } = useAction();
  const isOperator = can("operator");

  useEffect(() => {
    const ref = new URLSearchParams(window.location.search).get("item");
    if (ref) setSelected(ref);
  }, []);
  const select = (ref: string | null) => {
    setSelected(ref);
    const url = new URL(window.location.href);
    if (ref) url.searchParams.set("item", ref); else url.searchParams.delete("item");
    window.history.replaceState(null, "", url.toString());
  };
  const reasonLabel = (code: string | null) => (code ? reasons.data?.data?.[code]?.label_nl ?? code : null);
  const analyze = async () => { if (await run("analyze", "post", "/recovery/analyze", undefined, t("rec.analyzeQueued"))) setBump((b) => b + 1); };
  const archive = async (ref: string) => {
    if (await run("arch:" + ref, "del", `/recovery/wanted/${ref.split(":")[1]}`, undefined, t("rec.manual.archived"))) {
      if (selected === ref) select(null);
      void wanted.reload();
    }
  };

  return (
    <>
      <PageHeader title={t("nav.missing")} sub={t("rec.sub")}
        actions={<>
          <MgModeChip mode={wanted.data?.mode} />
          <ManualWantedDialog disabled={!isOperator} disabledReason={t("mg.needOperator")} onCreated={(ref) => { void wanted.reload(); select(ref); }} />
          <Button variant="primary" icon={Activity} pending={pending === "analyze"} disabled={!isOperator} title={isOperator ? undefined : t("mg.needOperator")} onClick={() => void analyze()}>{t("rec.analyze")}</Button>
        </>} />

      {wanted.data && wanted.data.mode !== "connected" ? (
        <InlineState tone="info">
          <span>{wanted.data.mode === "standalone" ? t("rec.standaloneNote") : t("rec.degradedNote")} <Link href="/mediaguard">{t("rec.toMediaGuard")}</Link></span>
        </InlineState>
      ) : null}

      <div className="grid" style={{ marginTop: 16 }}>
        <BaselineCard />

        <Card className="span-12" title={t("rec.wanted.title")} icon={ListTodo} flush
          sub={wanted.data?.mediaguard_fetched_at ? t("rec.wanted.fetched", { when: relative(wanted.data.mediaguard_fetched_at) }) : undefined}
          actions={wanted.data?.mediaguard_stale ? <StatusChip tone="warning">{t("state.staleShort")}</StatusChip> : undefined}>
          <div className="toolbar" style={{ padding: "12px 16px" }}>
            <Segmented<Source> label={t("rec.wanted.source")} value={source} onChange={(v) => { setSource(v); setPage(1); }}
              options={[{ value: "all", label: t("rec.wanted.all") }, { value: "mediaguard", label: t("rec.origin.mg") }, { value: "manual", label: t("rec.origin.manual") }]} />
            <label className="sr-only" htmlFor="rec-cat">{t("rec.manual.category")}</label>
            <select id="rec-cat" className="select" style={{ width: "auto" }} value={cat} onChange={(e) => { setCat(e.target.value as MediaCategory | ""); setPage(1); }}>
              <option value="">{t("rec.wanted.allCategories")}</option>
              <option value="tv">{t("rec.cat.tv")}</option>
              <option value="tv-anime">{t("rec.cat.tvAnime")}</option>
              <option value="movies">{t("rec.cat.movies")}</option>
              <option value="movies-anime">{t("rec.cat.moviesAnime")}</option>
            </select>
            <form className="row" style={{ gap: 6 }} onSubmit={(e) => { e.preventDefault(); setQuery(q.trim()); setPage(1); }}>
              <label className="sr-only" htmlFor="rec-q">{t("rec.wanted.search")}</label>
              <input id="rec-q" className="input" style={{ width: 200 }} value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("rec.wanted.search")} />
              <Button type="submit" icon={Search} iconOnly>{t("rec.wanted.search")}</Button>
            </form>
          </div>
          <ResourceView resource={wanted} endpoint="GET /api/v1/recovery/wanted" isEmpty={(d) => d.items.length === 0}
            empty={<StateView kind="empty" title={t("rec.wanted.empty")} body={wanted.data?.mode === "connected" ? t("rec.wanted.emptyConnected") : t("rec.wanted.emptyStandalone")} />}>
            {(d) => (
              <>
                <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel (horizontaal scrollbaar)">
                  <table className="table table--stack">
                    <thead><tr><th>{t("rec.wanted.item")}</th><th>{t("rec.manual.category")}</th><th>{t("rec.wanted.cause")}</th><th>{t("rec.wanted.candidates")}</th><th><span className="sr-only">{t("rec.bl.actions")}</span></th></tr></thead>
                    <tbody>
                      {d.items.map((r) => (
                        <tr key={r.media_ref} aria-selected={selected === r.media_ref}>
                          <td data-label={t("rec.wanted.item")}>
                            <div className="row wrap" style={{ gap: 6 }}>
                              <strong>{r.title}</strong>
                              <StatusChip tone={r.origin === "mediaguard" ? "accent" : "info"} icon={null}>{r.origin === "mediaguard" ? t("rec.origin.mg") : t("rec.origin.manual")}</StatusChip>
                              {r.stale ? <StatusChip tone="warning">{t("state.staleShort")}</StatusChip> : null}
                            </div>
                            <div className="hint">{[numbering(r), r.instance_id, r.status].filter(Boolean).join(" · ")}</div>
                          </td>
                          <td data-label={t("rec.manual.category")}>{categoryLabel(r.media_category)}</td>
                          <td data-label={t("rec.wanted.cause")}>
                            {r.origin === "mediaguard" ? (reasonLabel(r.reason_code) ?? <span className="t-3">{t("rec.wanted.noDiagnosis")}</span>) : <span className="t-3">{t("rec.wanted.manualCause")}</span>}
                            {r.attempts ? <div className="hint">{t("rec.wanted.attempts", { n: num(r.attempts) })}</div> : null}
                          </td>
                          <td data-label={t("rec.wanted.candidates")}>
                            {r.candidates
                              ? <StatusChip tone={r.candidates.accepted ? "success" : "neutral"}>{t("rec.wanted.candCount", { ok: num(r.candidates.accepted), total: num(r.candidates.total) })}</StatusChip>
                              : <span className="t-3">{t("rec.wanted.notSearched")}</span>}
                          </td>
                          <td data-label={t("rec.bl.actions")}>
                            <div className="row" style={{ gap: 6 }}>
                              <Button size="sm" variant={selected === r.media_ref ? "primary" : "secondary"} onClick={() => select(selected === r.media_ref ? null : r.media_ref)}>{t("action.details")}</Button>
                              {r.origin === "manual" ? (
                                <Button size="sm" variant="ghost" icon={Trash2} iconOnly pending={pending === "arch:" + r.media_ref} disabled={!isOperator} title={isOperator ? t("rec.manual.archive") : t("mg.needOperator")} onClick={() => void archive(r.media_ref)}>{t("rec.manual.archive")}</Button>
                              ) : null}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {d.total > d.page_size ? (
                  <div className="row-between" style={{ padding: "10px 16px" }}>
                    <span className="hint" style={{ margin: 0 }}>{t("rec.wanted.page", { page: num(d.page), total: num(d.total) })}</span>
                    <div className="row" style={{ gap: 6 }}>
                      <Button size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>{t("action.previous")}</Button>
                      <Button size="sm" disabled={page * d.page_size >= d.total} onClick={() => setPage(page + 1)}>{t("action.next")}</Button>
                    </div>
                  </div>
                ) : null}
              </>
            )}
          </ResourceView>
        </Card>

        {selected ? <ItemPanel key={selected} mediaRef={selected} onChanged={() => { void wanted.reload(); setBump((b) => b + 1); }} /> : null}

        <BlacklistCard reloadKey={bump} />
      </div>
    </>
  );
}
