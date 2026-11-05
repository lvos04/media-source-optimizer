"use client";

import { RefreshCw, Settings2 } from "lucide-react";
import { useState } from "react";

import { Button, Card, PageHeader } from "@/components/mg/primitives";
import { ResourceView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { ConnectorList } from "@/components/mso/connector-form";
import { SuspiciousCleanup } from "@/components/mso/torrent/cleanup-card";
import { BottleneckCard, ClientCard, ConnectivityCard } from "@/components/mso/torrent/client-cards";
import { PolicyCards, PreferencesCard } from "@/components/mso/torrent/policy-cards";
import { PreventionCard } from "@/components/mso/torrent/prevention-card";
import { RulesEditor } from "@/components/mso/torrent/rules-editor";
import { TuningCard } from "@/components/mso/torrent/tuning-card";
import { UnavailableState } from "@/components/mso/unavailable-state";
import { api, errorMessage } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import type { JobRef, TorrentOverview } from "@/lib/api/torrent";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

export default function TorrentClientPage() {
  const { t } = useI18n();
  const toast = useToast();
  const { can } = useSession();
  const isOperator = can("operator");
  const isAdmin = can("admin");
  const overview = useResource<TorrentOverview>("/torrent/overview", undefined, 30_000);
  const [pending, setPending] = useState(false);
  const configured = overview.data?.connector.configured ?? false;

  const refresh = async () => {
    setPending(true);
    try {
      await api.post<JobRef>("/torrent/refresh");
      toast.push("success", t("torrent.refreshQueued"));
      window.setTimeout(() => void overview.reload(), 2500);
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: errorMessage(e) }));
    } finally {
      setPending(false);
    }
  };

  return (
    <>
      <PageHeader title={t("nav.torrentClient")} sub={t("torrent.page.sub")}
        actions={<Button icon={RefreshCw} pending={pending} disabled={!isOperator || !configured}
          title={!isOperator ? t("torrent.operatorOnly") : !configured ? t("torrent.notConfigured") : undefined} onClick={() => void refresh()}>{t("torrent.refresh")}</Button>} />
      <ResourceView resource={overview} endpoint="GET /api/v1/torrent/overview">
        {(d) => !d.connector.configured ? (
          <div className="grid">
            <Card className="span-12"><UnavailableState connector="qBittorrent" connectorId="qbittorrent" /></Card>
            <Card className="span-12" title={t("torrent.client.connection")} icon={Settings2}>
              <ConnectorList only={["qbittorrent"]} canEdit={isAdmin} disabledReason={isAdmin ? undefined : t("torrent.adminOnly")} />
            </Card>
          </div>
        ) : (
          <div className="grid">
            <SuspiciousCleanup onChanged={() => void overview.reload()} />
            <PreventionCard />
            <ClientCard data={d} />
            <ConnectivityCard data={d} />
            <BottleneckCard data={d} />
            <TuningCard />
            <PolicyCards />
            <PreferencesCard />
            <Card className="span-6" title={t("torrent.client.connection")} icon={Settings2}>
              <ConnectorList only={["qbittorrent"]} canEdit={isAdmin} disabledReason={isAdmin ? undefined : t("torrent.adminOnly")} />
            </Card>
            <RulesEditor />
          </div>
        )}
      </ResourceView>
    </>
  );
}
