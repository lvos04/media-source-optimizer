"use client";

import { ChevronLeft, ChevronRight, Clock, Flag, GitCompareArrows, RotateCw, Search, Send } from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";

import { Confidence } from "@/components/mg/domain";
import { Button, Card, Field, InlineState, PageHeader, Segmented, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { CompareDrawer } from "@/components/mso/search/compare-drawer";
import { FlagChips, MEDIA_CATEGORIES, ReleaseRibbon, SourceReportCard, categoryKey, confidenceShort, confidenceText, isFake, numbering } from "@/components/mso/search/parts";
import { SendDialog } from "@/components/mso/search/send-dialog";
import { api, errorMessage } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import type { MediaCategory, Protocol, SearchItem, SearchResponse } from "@/lib/api/search";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

type Cat = MediaCategory | "all";
type Proto = Protocol | "all";
type Sort = "relevance" | "age" | "size" | "seeders";
const PAGE = 50;

interface Query {
  q?: string; category?: MediaCategory; protocol?: Protocol; season?: number; episode?: number; absolute?: number;
  imdb?: string; tvdb?: number; tmdb?: number; sort: Sort; limit: number; offset: number;
  /** 1 = retry with the longer Prowlarr time-out ("Opnieuw proberen") */
  slow?: number;
}

function int(v: string): number | undefined {
  const n = Number.parseInt(v, 10);
  return Number.isFinite(n) && n >= 0 ? n : undefined;
}

export default function SearchPage() {
  const { t, num, bytes, relative } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const [q, setQ] = useState("");
  const [cat, setCat] = useState<Cat>("all");
  const [proto, setProto] = useState<Proto>("all");
  const [season, setSeason] = useState("");
  const [episode, setEpisode] = useState("");
  const [absolute, setAbsolute] = useState("");
  const [imdb, setImdb] = useState("");
  const [tvdb, setTvdb] = useState("");
  const [tmdb, setTmdb] = useState("");
  const [sort, setSort] = useState<Sort>("relevance");
  const [query, setQuery] = useState<Query | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [comparing, setComparing] = useState(false);
  const [sending, setSending] = useState<SearchItem | null>(null);
  const [flagging, setFlagging] = useState<string | null>(null);
  const isTv = cat === "tv" || cat === "tv-anime" || cat === "all";
  const isOperator = can("operator");

  const params = useMemo(() => (query ? Object.fromEntries(Object.entries(query).filter(([, v]) => v !== undefined && v !== "")) : undefined), [query]);
  const res = useResource<SearchResponse>(query ? "/search" : null, params as Record<string, string | number> | undefined);
  const prowlarrSlow = res.data?.sources.find((s) => s.source_id === "prowlarr" && s.status === "timeout") ?? null;
  // "Opnieuw proberen" keeps the old result visible until the new one arrives: track that explicitly.
  const [retryFrom, setRetryFrom] = useState<number | null>(null);
  const retrying = retryFrom !== null && res.fetchedAt === retryFrom && !res.error;

  // topbar search navigates to /zoeken?q=…
  useEffect(() => {
    const sp = new URLSearchParams(window.location.search);
    const initial = sp.get("q");
    const c = sp.get("category") as MediaCategory | null;
    if (c && MEDIA_CATEGORIES.includes(c)) setCat(c);
    if (initial) {
      setQ(initial);
      setQuery({ q: initial, category: c && MEDIA_CATEGORIES.includes(c) ? c : undefined, sort: "relevance", limit: PAGE, offset: 0 });
    }
  }, []);

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    const next: Query = {
      q: q.trim() || undefined, category: cat === "all" ? undefined : cat, protocol: proto === "all" ? undefined : proto,
      season: isTv ? int(season) : undefined, episode: isTv ? int(episode) : undefined, absolute: isTv ? int(absolute) : undefined,
      imdb: imdb.trim() || undefined, tvdb: int(tvdb), tmdb: int(tmdb), sort, limit: PAGE, offset: 0,
    };
    if (!next.q && !next.imdb && !next.tvdb && !next.tmdb && !next.category) { setFormError(t("zoeken.needInput")); return; }
    setFormError(null);
    setSelected([]);
    setQuery(next);
    const url = new URL(window.location.href);
    if (next.q) url.searchParams.set("q", next.q); else url.searchParams.delete("q");
    if (next.category) url.searchParams.set("category", next.category); else url.searchParams.delete("category");
    window.history.replaceState(null, "", url.toString());
  };

  const page = (dir: 1 | -1) => setQuery((cur) => (cur ? { ...cur, offset: Math.max(0, cur.offset + dir * PAGE) } : cur));
  const toggle = (token: string) => setSelected((s) => (s.includes(token) ? s.filter((x) => x !== token) : s.length >= 6 ? s : [...s, token]));

  const flag = async (item: SearchItem) => {
    setFlagging(item.token);
    try {
      await api.post("/search/flag", { token: item.token, reason: "verkeerde_inhoud" });
      toast.push("success", t("zoeken.flagged"), item.title);
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: errorMessage(e) }));
    } finally {
      setFlagging(null);
    }
  };

  return (
    <>
      <PageHeader title={t("nav.search")} sub={t("zoeken.sub")} />
      <div className="grid">
        <Card className="span-12" title={t("nav.search")} icon={Search}>
          <form className="stack" onSubmit={submit} aria-label={t("nav.search")}>
            <div className="row wrap" style={{ gap: 12, alignItems: "flex-end" }}>
              <div className="grow" style={{ minWidth: 220 }}>
                <Field label={t("zoeken.query")} htmlFor="q">
                  <input id="q" className="input" type="search" value={q} placeholder={t("zoeken.queryPlaceholder")} onChange={(e) => setQ(e.target.value)} autoComplete="off" />
                </Field>
              </div>
              <Button type="submit" variant="primary" icon={Search} pending={res.loading && Boolean(query)}>{t("zoeken.submit")}</Button>
            </div>
            <div className="row wrap" style={{ gap: 12 }}>
              <div className="stack-sm">
                <span className="label">{t("zoeken.category")}</span>
                <Segmented<Cat> label={t("zoeken.category")} value={cat} onChange={setCat}
                  options={[{ value: "all", label: t("zoeken.cat.all") }, ...MEDIA_CATEGORIES.map((c) => ({ value: c as Cat, label: t(categoryKey(c)) }))]} />
              </div>
              <div className="stack-sm">
                <span className="label">{t("zoeken.protocol")}</span>
                <Segmented<Proto> label={t("zoeken.protocol")} value={proto} onChange={setProto}
                  options={[{ value: "all", label: t("zoeken.protocol.all") }, { value: "usenet", label: t("zoeken.protocol.usenet") }, { value: "torrent", label: t("zoeken.protocol.torrent") }]} />
              </div>
            </div>
            <div className="row wrap" style={{ gap: 12 }}>
              {isTv ? (
                <>
                  <Field label={t("zoeken.season")} htmlFor="season"><input id="season" className="input" inputMode="numeric" style={{ width: 90 }} value={season} onChange={(e) => setSeason(e.target.value)} /></Field>
                  <Field label={t("zoeken.episode")} htmlFor="episode"><input id="episode" className="input" inputMode="numeric" style={{ width: 90 }} value={episode} onChange={(e) => setEpisode(e.target.value)} /></Field>
                  <Field label={t("zoeken.absolute")} hint={t("zoeken.absoluteHint")} htmlFor="absolute"><input id="absolute" className="input" inputMode="numeric" style={{ width: 120 }} value={absolute} onChange={(e) => setAbsolute(e.target.value)} /></Field>
                </>
              ) : null}
              <Field label={t("zoeken.sort")} htmlFor="sort">
                <select id="sort" className="select" value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
                  {(["relevance", "age", "size", "seeders"] as Sort[]).map((s) => <option key={s} value={s}>{t(`zoeken.sort.${s}`)}</option>)}
                </select>
              </Field>
            </div>
            <details>
              <summary className="t-2" style={{ cursor: "pointer" }}>{t("zoeken.ids")}</summary>
              <div className="row wrap" style={{ gap: 12, marginTop: 8 }}>
                <Field label="IMDb" htmlFor="imdb"><input id="imdb" className="input mono" placeholder="tt1234567" style={{ width: 140 }} value={imdb} onChange={(e) => setImdb(e.target.value)} /></Field>
                <Field label="TVDB" htmlFor="tvdb"><input id="tvdb" className="input mono" inputMode="numeric" style={{ width: 120 }} value={tvdb} onChange={(e) => setTvdb(e.target.value)} /></Field>
                <Field label="TMDB" htmlFor="tmdb"><input id="tmdb" className="input mono" inputMode="numeric" style={{ width: 120 }} value={tmdb} onChange={(e) => setTmdb(e.target.value)} /></Field>
              </div>
            </details>
            {formError ? <InlineState tone="warning" role="alert">{formError}</InlineState> : null}
          </form>
        </Card>

        {!query ? (
          <Card className="span-12"><StateView kind="empty" title={t("zoeken.idle.title")} body={t("zoeken.idle.body")} /></Card>
        ) : (
          <>
            {prowlarrSlow ? (
              <div className="span-12" data-testid="prowlarr-slow">
                <InlineState tone="warning" icon={Clock} role="status">
                  <span className="row wrap" style={{ gap: 8, justifyContent: "space-between", width: "100%" }}>
                    <span>
                      <strong>{t("zoeken.slow.title")}</strong>{" "}
                      {retrying ? t("zoeken.slow.retrying") : query.slow ? t("zoeken.slow.stillSlow") : t("zoeken.slow.body")}
                      {prowlarrSlow.message ? <span className="cell-sub" style={{ display: "block" }}>{prowlarrSlow.message}</span> : null}
                    </span>
                    {!query.slow || retrying ? (
                      <Button size="sm" icon={RotateCw} pending={retrying} onClick={() => { setRetryFrom(res.fetchedAt); setQuery((cur) => (cur ? { ...cur, slow: 1 } : cur)); }}>
                        {t("zoeken.slow.retry")}
                      </Button>
                    ) : null}
                  </span>
                </InlineState>
              </div>
            ) : null}
            <Card className="span-8" title={t("zoeken.results")} icon={Search} flush
              sub={res.data ? t("zoeken.resultsSub", { total: num(res.data.total) }) : undefined}
              actions={<Button size="sm" icon={GitCompareArrows} disabled={selected.length < 2} title={selected.length < 2 ? t("zoeken.compareHint") : undefined}
                onClick={() => setComparing(true)}>{t("zoeken.compare", { n: selected.length })}</Button>}>
              <ResourceView resource={res} endpoint="GET /api/v1/search" isEmpty={(d) => d.items.length === 0}
                empty={<StateView kind="empty" title={t("zoeken.empty.title")} body={t("zoeken.empty.body")} />}>
                {(d) => (
                  <>
                    <table className="table table--stack" data-testid="search-results">
                      <thead>
                        <tr>
                          <th scope="col" style={{ width: 32 }}><span className="sr-only">{t("zoeken.compareSelect")}</span></th>
                          <th scope="col">{t("zoeken.col.release")}</th>
                          <th scope="col">{t("zoeken.col.source")}</th>
                          <th scope="col">{t("zoeken.col.facts")}</th>
                          <th scope="col" title={t("zoeken.confidenceHelp")}>{t("zoeken.col.confidence")}</th>
                          <th scope="col"><span className="sr-only">{t("zoeken.col.actions")}</span></th>
                        </tr>
                      </thead>
                      <tbody>
                        {d.items.map((it) => {
                          const nr = numbering(it.parsed);
                          return (
                            <tr key={it.token}>
                              <td data-label={t("zoeken.compareSelect")}>
                                <input type="checkbox" className="check" aria-label={`${t("zoeken.compareSelect")}: ${it.title}`}
                                  checked={selected.includes(it.token)} onChange={() => toggle(it.token)} />
                              </td>
                              <td data-label={t("zoeken.col.release")}>
                                <div className="cell-title" style={{ wordBreak: "break-word" }}>{it.title}</div>
                                <div className="row wrap" style={{ gap: 6, marginTop: 4 }}>
                                  {it.media_category ? <span className="tag">{t(categoryKey(it.media_category))}</span> : null}
                                  {nr ? <span className="tag mono">{nr}</span> : null}
                                  <ReleaseRibbon p={it.parsed} />
                                  {it.private ? <StatusChip tone="warning" icon={null} outline>{t("zoeken.private")}</StatusChip> : null}
                                  {it.freeleech ? <StatusChip tone="success" icon={null} outline>{t("zoeken.freeleech")}</StatusChip> : null}
                                </div>
                                <div style={{ marginTop: 4 }}><FlagChips flags={it.false_positive_flags} details={Array.isArray(it.extra?.release_gate) ? (it.extra.release_gate as string[]) : undefined} /></div>
                                <div className="cell-sub mono" title={t("zoeken.col.identity")} style={{ wordBreak: "break-all" }}>{it.release_key}</div>
                              </td>
                              <td data-label={t("zoeken.col.source")}>
                                <div>{it.source_label}</div>
                                <div className="cell-sub">
                                  <StatusChip tone={it.protocol === "usenet" ? "info" : "accent"} icon={null} outline>{t(`zoeken.protocol.${it.protocol}`)}</StatusChip>
                                  {it.also_from.length ? <> · {t("zoeken.alsoFrom", { sources: it.also_from.join(", ") })}</> : null}
                                </div>
                              </td>
                              <td data-label={t("zoeken.col.facts")} className="mono">
                                <div>{bytes(it.size_bytes)}</div>
                                <div className="cell-sub">{it.publish_date ? relative(it.publish_date) : "—"}</div>
                                {it.protocol === "torrent" ? <div className="cell-sub">{t("zoeken.seeders", { n: num(it.seeders ?? 0) })}</div>
                                  : it.grabs !== null ? <div className="cell-sub">{t("zoeken.grabs", { n: num(it.grabs) })}</div> : null}
                              </td>
                              <td data-label={t("zoeken.col.confidence")}>
                                <span title={confidenceText(it, t)}>
                                  <Confidence value={it.confidence} tone={it.confidence < 0.4 ? "danger" : it.confidence < 0.6 ? "warning" : "success"} />
                                </span>
                                {it.extra?.confidence_basis ? <div className="cell-sub">{confidenceShort(it.extra.confidence_basis, t)}</div> : null}
                              </td>
                              <td data-label={t("zoeken.col.actions")}>
                                <div className="row wrap" style={{ gap: 6, justifyContent: "flex-end" }}>
                                  <Button size="sm" variant="primary" icon={Send} disabled={!isOperator || it.false_positive_flags.includes("geblokkeerd") || isFake(it)}
                                    title={!isOperator ? t("zoeken.operatorOnly") : isFake(it) ? t("releasegate.sendBlocked") : it.false_positive_flags.includes("geblokkeerd") ? t("zoeken.sendBlocked") : undefined}
                                    onClick={() => setSending(it)}>{t("zoeken.send")}</Button>
                                  <Button size="sm" variant="ghost" icon={Flag} iconOnly disabled={!isOperator} pending={flagging === it.token}
                                    title={!isOperator ? t("zoeken.operatorOnly") : t("zoeken.flag")} onClick={() => void flag(it)}>{t("zoeken.flag")}</Button>
                                </div>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                    <div className="card-footer row-between">
                      <span className="t-3">{t("zoeken.page", { from: num(d.offset + 1), to: num(d.offset + d.items.length), total: num(d.total) })}</span>
                      <span className="row" style={{ gap: 6 }}>
                        <Button size="sm" icon={ChevronLeft} disabled={d.offset === 0} onClick={() => page(-1)}>{t("zoeken.prev")}</Button>
                        <Button size="sm" icon={ChevronRight} disabled={d.offset + d.items.length >= d.total} onClick={() => page(1)}>{t("zoeken.next")}</Button>
                      </span>
                    </div>
                  </>
                )}
              </ResourceView>
            </Card>
            <div className="span-4 stack">
              {res.data ? <SourceReportCard sources={res.data.sources} trace={res.data.trace} traceId={res.data.trace_id} />
                : <Card title={t("zoeken.report")}><StateView kind="loading" compact /></Card>}
            </div>
          </>
        )}
      </div>
      {comparing ? <CompareDrawer tokens={selected} category={query?.category ?? null} onClose={() => setComparing(false)} /> : null}
      {sending ? <SendDialog item={sending} onClose={() => setSending(null)} /> : null}
    </>
  );
}
