"use client";

import { Activity, Gauge, Lock, Plus, RefreshCw, ShieldCheck } from "lucide-react";
import { useState } from "react";

import { KpiCard } from "@/components/mg/domain";
import { Button, Card, PageHeader } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { DirectIndexerDialog } from "@/components/mso/indexers/direct-dialog";
import { EndpointPanel } from "@/components/mso/indexers/endpoint-panel";
import { IndexerTable } from "@/components/mso/indexers/indexer-table";
import { OriginCard, ProwlarrPanel } from "@/components/mso/indexers/prowlarr-panel";
import { useAction } from "@/lib/api/actions";
import { useResource } from "@/lib/api/hooks";
import type { IndexerList, Protocol } from "@/lib/api/search";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

/** Shared body of /nzb-indexers (usenet) and /torrent-indexers (torrent). */
export function IndexersPage({ protocol }: { protocol: Protocol }) {
  const { t, num, pct } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const { run, pending } = useAction();
  const list = useResource<IndexerList>("/indexers", { protocol }, 60_000);
  const [adding, setAdding] = useState(false);
  const isAdmin = can("admin");
  const items = (list.data?.items ?? []).filter((i) => !i.is_mso);
  const measured = items.filter((i) => i.reliability !== null);
  const avgRel = measured.length ? measured.reduce((a, b) => a + (b.reliability ?? 0), 0) / measured.length : null;
  const okLast = items.filter((i) => i.stats?.last_search_at && !(i.stats.errors > 0 && i.stats.last_status)).length;
  const ready = list.data !== null;

  const recompute = async () => {
    if (await run("rel", "post", "/indexers/reliability/recompute", undefined, t("idx.recomputed"))) await list.reload();
  };

  return (
    <>
      <PageHeader title={t(protocol === "usenet" ? "nav.nzbIndexers" : "nav.torrentIndexers")} sub={t(protocol === "usenet" ? "idx.nzb.sub" : "idx.torrent.sub")}
        actions={<>
          <Button icon={RefreshCw} pending={pending === "rel"} disabled={!can("operator")} title={!can("operator") ? t("zoeken.operatorOnly") : undefined} onClick={() => void recompute()}>{t("idx.recompute")}</Button>
          <Button variant="primary" icon={Plus} disabled={!isAdmin} title={!isAdmin ? t("idx.adminOnly") : undefined} onClick={() => setAdding(true)}>{t("idx.add")}</Button>
        </>} />
      <div className="grid">
        <div className="span-12 kpi-grid">
          <KpiCard label={t("idx.kpi.sources")} icon={Activity} value={ready ? num(items.length) : "—"} unavailable={!ready} />
          <KpiCard label={t("idx.kpi.healthy")} icon={ShieldCheck} value={ready ? num(okLast) : "—"} unavailable={!ready} />
          <KpiCard label={t("idx.kpi.reliability")} icon={Gauge} value={avgRel !== null ? pct(avgRel) : "—"}
            meta={ready ? t("idx.kpi.coverage", { n: num(measured.length) }) : undefined} unavailable={!ready} />
          <KpiCard label={t("idx.kpi.private")} icon={Lock} value={ready ? num(items.filter((i) => i.private).length) : "—"} unavailable={!ready} />
        </div>
        <Card className="span-12" title={t("idx.list")} sub={t("idx.listSub")} flush>
          <ResourceView resource={list} endpoint="GET /api/v1/indexers" isEmpty={(d) => d.items.filter((i) => !i.is_mso).length === 0}
            empty={<StateView kind="empty" title={t("idx.empty.title")} body={t("idx.empty.body")}
              action={isAdmin ? <Button variant="primary" icon={Plus} onClick={() => setAdding(true)}>{t("idx.add")}</Button> : undefined} />}>
            {(d) => <IndexerTable items={d.items.filter((i) => !i.is_mso)} protocol={protocol} reload={list.reload} />}
          </ResourceView>
        </Card>
        <ProwlarrPanel configured={Boolean(list.data?.prowlarr_configured)} onSynced={list.reload} />
        <EndpointPanel className="span-6" prowlarrConfigured={Boolean(list.data?.prowlarr_configured)} />
        <OriginCard className="span-6" />
      </div>
      {adding ? <DirectIndexerDialog protocol={protocol} onClose={() => setAdding(false)} onSaved={async () => { toast.push("success", t("idx.saved")); await list.reload(); }} /> : null}
    </>
  );
}
