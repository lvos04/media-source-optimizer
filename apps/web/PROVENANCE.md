# Provenance — apps/web

Parts of this web app are an **explicit, internally versioned derivative** of the MediaGuard Ultimate
web app (same owner, no separate licence file in that repository). MSO deliberately looks like the
same product family, so the design system is copied rather than re-invented.

- Source repository: `lvos04/media-guard-ultimate`
- Source commit: `e92c2acc7f24cbf19289f0d5617175f6ff32c13e`
- Source app: `apps/web` (Next 16.3.3, React 19.2.8, Tailwind 4.3.3, lucide-react 1.66.0)
- Every copied source file starts with
  `// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/<path> — internally versioned copy, see PROVENANCE.md`
  (CSS: same text in a `/* … */` comment).

To pull in a later MediaGuard design change: diff the source path between `e92c2acc` and the new
commit, apply it here, and bump the commit in this file and in the header comments of the touched files.

## Copied files

| MSO file | Source (`apps/web/…`) | Changes |
| --- | --- | --- |
| `styles/mg.css` | `styles/mg.css` | **Verbatim** (tokens identical); only a header comment. MSO additions live in `styles/mso.css` (new). `styles/legacy-av1.css` was *not* copied. |
| `app/globals.css` | `app/globals.css` | Imports `mso.css` instead of `legacy-av1.css`. |
| `app/layout.tsx` | `app/layout.tsx` | Title/description → Media Source Optimizer / MSO; theme key `mg.theme` → `mso.theme`. |
| `app/fonts/InterVariable-latin.woff2`, `InterVariable-latin-ext.woff2`, `JetBrainsMonoVariable-latin.woff2` | `app/fonts/*` | Verbatim (binary). |
| `app/fonts/OFL-Inter.txt`, `app/fonts/OFL-JetBrainsMono.txt` | `app/fonts/*` | Verbatim licence texts. |
| `app/(app)/layout.tsx` | `app/(app)/layout.tsx` | Verbatim. |
| `app/login/page.tsx` | `app/login/page.tsx` | `/setup/status` (`setup_required`/`admin_exists`) instead of `/auth/setup-status`; stores the CSRF token from the login response; `next` sanitised. |
| `app/setup/page.tsx` | `app/setup/page.tsx` | Steps: beheerder → verbindingen (ConnectorForm) → bedrijfsmodus → samenvatting; endpoints `/setup/admin`, `/connectors`, `/setup/complete`. Language/theme step dropped. |
| `app/(app)/systeem/page.tsx` | `app/(app)/systeem/page.tsx` | Layout/idiom only; content rewritten for `/system/info`, `/backups`, `/jobs`. |
| `components/mg/primitives.tsx` | `components/mg/primitives.tsx` | Verbatim. |
| `components/mg/states.tsx` | `components/mg/states.tsx` | `ErrorState` renders a "verbinding niet ingesteld" state for `code: connector_not_configured`; exports `isConnectorUnconfigured`; forbidden shows backend message. |
| `components/mg/toast.tsx` | `components/mg/toast.tsx` | Verbatim. |
| `components/mg/domain.tsx` | `components/mg/domain.tsx` | Kept KpiCard, Sparkline, Confidence, EvidenceCard, Trace, DiffViewer (+ optional `label`), Stepper, Timeline, DangerDialog, PendingAction. InstanceCard/StatusChip/Logo → ConnectorCard/ConnectorStatusChip/ConnectorLogo. DistributionBar takes generic `segments`. Dropped Poster, MediaRow, QualityRibbon, SeasonHeatmap, JobLaneCard. |
| `components/mg/setting-field.tsx` | `components/mg/setting-field.tsx` | Adapted to the flat MSO settings contract (`GET /api/v1/settings`): no scopes/trace; risk chip; `locked`/`restart`; write-only secrets; `options` as strings or `{value,label}`; restore-default as a draft. |
| `components/shell/app-shell.tsx` | `components/shell/app-shell.tsx` | MSO nav; sidebar health from `/system/health`; topbar mode chip (`/system/mode`); "Automatisering pauzeren" (`/system/pause`/`resume`) replaces the emergency stop; theme in `localStorage["mso.theme"]` only; notifications, storage pill, SSE/events, locale switch removed. |
| `components/shell/auth-frame.tsx` | `components/shell/auth-frame.tsx` | MSO brand mark (initials "MSO" on the same gradient tile), MSO selling points; Dutch-only. Exports `BrandMark`. |
| `components/shell/nav.ts` | `components/shell/nav.ts` | MSO routes/groups/shortcuts; `BOTTOM_NAV_KEYS`. |
| `lib/api/client.ts` | `lib/api/client.ts` | Reads `code` and `correlation_id` from the error body; `upstream_unreachable` → offline; `errorMessage()` helper. |
| `lib/api/hooks.ts` | `lib/api/hooks.ts` | Verbatim. |
| `lib/api/actions.ts` | `lib/api/actions.ts` | Verbatim. |
| `lib/i18n/index.tsx` | `lib/i18n/index.tsx` | Dutch-only (`nl-NL`), same key mechanism and formatters; `duration` also shows days. |
| `lib/i18n/messages/index.ts` | `lib/i18n/messages/index.ts` | Merges per-area files from `messages/nl/`. |
| `lib/i18n/messages/nl/common.ts`, `components.ts`, `auth.ts` | `lib/i18n/messages/common.ts`, `components.ts`, `auth.ts` | Subset of keys kept, MSO texts added/rewritten. |
| `lib/session.tsx` | `lib/session.tsx` | `/setup/status` redirect, `{user, csrf_token}` shape, no per-user locale. |
| `lib/status.ts` | `lib/status.ts` | Library mappings replaced by health/job/change status helpers. |
| `lib/utils.ts` | `lib/utils.ts` | Verbatim. |
| `server.mjs` | `server.mjs` | Proxies `/api/v1/*`, `/newznab/*`, `/torznab/*` → `MSO_CORE_URL` (default `http://localhost:8200`); encoder proxy removed; env `MSO_WEB_HOST`. |
| `next.config.ts` | `next.config.ts` | Same security headers; dev rewrites for the three core prefixes. |
| `Dockerfile` | `Dockerfile` | Same build (standalone, non-root uid/gid 568, healthcheck on `/login`); user `mso`, env `MSO_CORE_URL`. |
| `playwright.config.ts` | `playwright.config.ts` | Runs against a running stack at `MSO_E2E_BASE_URL`; uses `/opt/pw-browsers` when present. |
| `eslint.config.mjs`, `postcss.config.mjs`, `tsconfig.json` | same | Verbatim (+ header comment). |
| `package.json`, `package-lock.json` | same | Same pinned versions; name `mso-web`; `typecheck` runs `next typegen` first. |
| `next-env.d.ts`, `.gitignore`, `.dockerignore` | same | Verbatim (`.gitignore` + `test-results/`). |

New MSO-only files (not derived): `styles/mso.css`, `public/favicon.svg` (own "MSO" mark),
`components/mso/*`, `lib/mode.tsx`, `lib/api/contracts.ts`, `lib/i18n/messages/nl/{settings,system,logs}.ts`,
`app/(app)/instellingen/page.tsx`, `app/(app)/logs/page.tsx`, `e2e/smoke.spec.ts`, `README.md`, this file.

## Font licences

The self-hosted fonts are licensed under the **SIL Open Font License 1.1** (OFL). The full licence
texts ship next to the font files and must stay with them:

- Inter — © 2016 The Inter Project Authors — `app/fonts/OFL-Inter.txt`
- JetBrains Mono — © 2020 The JetBrains Mono Project Authors — `app/fonts/OFL-JetBrainsMono.txt`

The fonts are bundled unmodified and are not sold on their own; no Reserved Font Name is used for a
modified version.
