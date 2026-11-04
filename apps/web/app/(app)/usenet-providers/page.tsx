"use client";

import { Download, RefreshCw, Server } from "lucide-react";
import { useState } from "react";

import { Button, Card, PageHeader } from "@/components/mg/primitives";
import { ResourceView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { ArticleTestsCard, AvailabilityCard } from "@/components/mso/usenet/availability";
import { CategoryMapCard } from "@/components/mso/usenet/categories";
import { HeaderIndexCard } from "@/components/mso/usenet/header-index";
import { PriorityCard } from "@/components/mso/usenet/priority";
import { AddProviderButton, ProvidersSection } from "@/components/mso/usenet/providers";
import { api, errorMessage } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import type { ProviderImportResult, UsenetProvider } from "@/lib/api/usenet";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

export default function UsenetProvidersPage() {
  const { t, num } = useI18n();
  const toast = useToast();
  const { can } = useSession();
  const providers = useResource<{ items: UsenetProvider[] }>("/usenet/providers", undefined, 60_000);
  const [busy, setBusy] = useState<"import" | "refresh" | null>(null);

  const importSab = async () => {
    setBusy("import");
    try {
      const res = await api.post<ProviderImportResult>("/usenet/providers/import-sab");
      if (res.items.length) toast.push("success", t("usenet.providers.imported", { count: num(res.items.length) }), res.note ?? undefined);
      else toast.push("info", t("usenet.providers.importedNone"));
      await providers.reload();
    } catch (e) {
      toast.push("danger", errorMessage(e));
    } finally {
      setBusy(null);
    }
  };
  const refresh = async () => {
    setBusy("refresh");
    try {
      await api.post("/usenet/sab/refresh", {});
      toast.push("success", t("usenet.dl.refreshed"));
      await providers.reload();
    } catch (e) {
      toast.push("danger", errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <PageHeader
        title={t("nav.usenetProviders")}
        sub={t("usenet.page.sub")}
        actions={
          <>
            <Button icon={RefreshCw} onClick={() => void refresh()} pending={busy === "refresh"} disabled={!can("operator")}
              title={can("operator") ? undefined : t("usenet.operatorOnly")}>{t("usenet.refresh")}</Button>
            <Button icon={Download} onClick={() => void importSab()} pending={busy === "import"} disabled={!can("admin")}
              title={can("admin") ? undefined : t("usenet.adminOnly")}>{t("usenet.providers.import")}</Button>
            <AddProviderButton onSaved={() => void providers.reload()} />
          </>
        }
      />
      <div className="grid">
        <Card title={t("usenet.providers.title")} icon={Server} className="span-12">
          <ResourceView resource={providers} endpoint="GET /api/v1/usenet/providers">
            {(d) => <ProvidersSection providers={d.items} reload={() => void providers.reload()} />}
          </ResourceView>
        </Card>
        <AvailabilityCard />
        <ArticleTestsCard />
        <PriorityCard />
        <CategoryMapCard />
        <HeaderIndexCard providers={providers.data?.items ?? []} />
      </div>
    </>
  );
}
