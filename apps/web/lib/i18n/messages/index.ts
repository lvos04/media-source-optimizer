// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/lib/i18n/messages/index.ts — internally versioned copy, see PROVENANCE.md
//
// One file per feature area under ./nl/<area>.ts (export const messages = {...}). To add an area:
// create the file and add ONE import + ONE spread below. Keys are namespaced "<area>.<name>" so
// files never collide; a duplicate key is a type error in ./nl/<area>.ts reviews, not at runtime.
import { messages as auth } from "./nl/auth";
import { messages as common } from "./nl/common";
import { messages as insights } from "./nl/insights";
import { messages as components } from "./nl/components";
import { messages as goals } from "./nl/goals";
import { messages as logs } from "./nl/logs";
import { messages as maintenance } from "./nl/maintenance";
import { messages as settings } from "./nl/settings";
import { messages as system } from "./nl/system";
import { messages as recovery } from "./nl/recovery";
import { messages as safety } from "./nl/safety";
import { messages as torrent } from "./nl/torrent";
import { messages as usenet } from "./nl/usenet";
import { messages as usenetdiag } from "./nl/usenetdiag";
import { messages as usenetserver } from "./nl/usenetserver";
import { messages as zoeken } from "./nl/zoeken";

export const nl = {
  ...recovery,
  ...common,
  ...components,
  ...auth,
  ...settings,
  ...system,
  ...logs,
  ...insights,
  ...torrent,
  ...usenet,
  ...usenetdiag,
  ...usenetserver,
  ...zoeken,
  ...safety,
  ...maintenance,
  ...goals,
};
export type MessageKey = keyof typeof nl;
