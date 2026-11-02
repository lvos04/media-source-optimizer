// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/lib/i18n/messages/index.ts — internally versioned copy, see PROVENANCE.md
//
// One file per feature area under ./nl/<area>.ts (export const messages = {...}). To add an area:
// create the file and add ONE import + ONE spread below. Keys are namespaced "<area>.<name>" so
// files never collide; a duplicate key is a type error in ./nl/<area>.ts reviews, not at runtime.
import { messages as auth } from "./nl/auth";
import { messages as common } from "./nl/common";
import { messages as components } from "./nl/components";
import { messages as logs } from "./nl/logs";
import { messages as settings } from "./nl/settings";
import { messages as system } from "./nl/system";

export const nl = {
  ...common,
  ...components,
  ...auth,
  ...settings,
  ...system,
  ...logs,
};
export type MessageKey = keyof typeof nl;
