"use client";

import { useEffect, useState } from "react";

import { PageHeader, Tabs } from "@/components/mg/primitives";
import { TorrentDownloadsTab } from "@/components/mso/downloads/torrent-tab";
import { UsenetDownloadsTab } from "@/components/mso/downloads/usenet-tab";
import { useI18n } from "@/lib/i18n";

const TABS = ["usenet", "torrent"] as const;
type Tab = (typeof TABS)[number];

/** Page shell; the tab contents are owned by the Usenet (B) and Torrent (C) slices. */
export default function DownloadsPage() {
  const { t } = useI18n();
  const [tab, setTab] = useState<Tab>("usenet");
  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get("tab");
    if (q && (TABS as readonly string[]).includes(q)) setTab(q as Tab);
  }, []);
  const select = (v: Tab) => {
    setTab(v);
    const url = new URL(window.location.href);
    url.searchParams.set("tab", v);
    window.history.replaceState(null, "", url.toString());
  };
  return (
    <>
      <PageHeader title={t("nav.downloads")} sub={t("downloadsPage.sub")} />
      <Tabs label={t("downloadsPage.tabs")} value={tab} onSelect={select} items={[
        { value: "usenet", label: t("downloadsPage.usenet") },
        { value: "torrent", label: t("downloadsPage.torrent") },
      ]} />
      <div style={{ marginTop: 16 }}>
        {tab === "usenet" ? <UsenetDownloadsTab /> : <TorrentDownloadsTab />}
      </div>
    </>
  );
}
