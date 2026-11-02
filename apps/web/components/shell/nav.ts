// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/components/shell/nav.ts — internally versioned copy, see PROVENANCE.md
// (MSO routes and groups.)
import {
  Bot, Coins, DatabaseBackup, Download, FileClock, HardDrive, LayoutDashboard, Magnet, Newspaper, Rss, Search, SearchX, Server,
  Settings, ShieldCheck, type LucideIcon,
} from "lucide-react";

import type { MessageKey } from "@/lib/i18n";

export interface NavItem { key: string; href: string; label: MessageKey; icon: LucideIcon; shortcut?: string }
export interface NavGroup { label: MessageKey | null; items: NavItem[] }

export const NAV: NavGroup[] = [
  { label: null, items: [{ key: "overview", href: "/", label: "nav.overview", icon: LayoutDashboard, shortcut: "g o" }] },
  {
    label: "nav.group.search",
    items: [
      { key: "search", href: "/zoeken", label: "nav.search", icon: Search, shortcut: "g z" },
      { key: "nzb-indexers", href: "/nzb-indexers", label: "nav.nzbIndexers", icon: Newspaper, shortcut: "g n" },
      { key: "torrent-indexers", href: "/torrent-indexers", label: "nav.torrentIndexers", icon: Rss, shortcut: "g t" },
    ],
  },
  {
    label: "nav.group.usenet",
    items: [
      { key: "usenet-providers", href: "/usenet-providers", label: "nav.usenetProviders", icon: Server, shortcut: "g u" },
    ],
  },
  {
    label: "nav.group.torrents",
    items: [
      { key: "torrent-client", href: "/torrentclient", label: "nav.torrentClient", icon: Magnet, shortcut: "g q" },
      { key: "storage", href: "/opslag", label: "nav.storage", icon: HardDrive },
    ],
  },
  {
    label: "nav.group.operations",
    items: [
      { key: "downloads", href: "/downloads", label: "nav.downloads", icon: Download, shortcut: "g d" },
      { key: "missing", href: "/ontbrekend", label: "nav.missing", icon: SearchX, shortcut: "g m" },
      { key: "automation", href: "/automatisering", label: "nav.automation", icon: Bot, shortcut: "g a" },
    ],
  },
  {
    label: "nav.group.integrations",
    items: [
      { key: "mediaguard", href: "/mediaguard", label: "nav.mediaguard", icon: ShieldCheck },
      { key: "costs", href: "/kosten", label: "nav.costs", icon: Coins, shortcut: "g k" },
    ],
  },
  {
    label: "nav.group.system",
    items: [
      { key: "settings", href: "/instellingen", label: "nav.settings", icon: Settings, shortcut: "g s" },
      { key: "logs", href: "/logs", label: "nav.logs", icon: FileClock, shortcut: "g l" },
      { key: "system", href: "/systeem", label: "nav.system", icon: DatabaseBackup, shortcut: "g b" },
    ],
  },
];

export const ALL_NAV_ITEMS = NAV.flatMap((g) => g.items);

/** Mobile bottom nav: Overzicht · Zoeken · Downloads · Ontbrekend (+ "Meer" opens the drawer). */
export const BOTTOM_NAV_KEYS = ["overview", "search", "downloads", "missing"];

export function isActive(href: string, pathname: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}
