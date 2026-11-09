# MSO web (`apps/web`)

Next.js 16 (App Router) + React 19 + TypeScript + Tailwind 4 + shadcn/ui (new-york, Radix) + lucide. Dutch UI
(nl-NL). The look follows the platform design language **Peil 1.1.0** (`styles/peil/`, a pinned copy of
`design/ontwerptaal/`; plan and decisions in `docs/ontwerp/PEIL_MIGRATIE.md` and `docs/adr/ADR-0006/0007`).
**Reuse the components below and Peil utilities (`bg-card`, `text-muted-foreground`, `min-h-touch`, …) instead of
writing new CSS**; never hex colours, never text below `text-sm` (14 px), every control ≥ 44 px.

```
npm ci            # pinned versions, identical to MediaGuard
npm run dev       # next dev, proxies /api/v1, /newznab, /torznab → MSO_CORE_URL (default http://localhost:8200)
npm run typecheck # next typegen && tsc --noEmit
npm run lint
npm run check:peil # Peil copies unchanged (version + sha256)
npm run test:unit  # vitest: pure helpers + server-rendered component markup
npm run build     # standalone output; production entrypoint is `node server.mjs`
MSO_E2E_BASE_URL=http://localhost:3000 npm run e2e   # Playwright against a running stack
```

## Structure

```
app/
  layout.tsx              root html, theme bootstrap (localStorage "mso.theme", default Systeem; system font)
  login/ setup/           public pages (AuthFrame)
  (app)/layout.tsx        AppShell: session guard, sidebar/topbar/bottom nav, ModeProvider, ToastProvider
  (app)/<route>/page.tsx  one folder per nav route (see components/shell/nav.ts)
components/
  ui/                     shadcn/ui components with the Peil codemod (Button, Badge/RisicoBadge variants)
  mg/                     primitives (same API as before, now on components/ui + Radix), states, toast, domain, setting-field
  mso/charts/             ChartFrame: keyboard read-out + "Toon als tabel" twin for every chart
  shell/                  app-shell, auth-frame, nav
  mso/                    MSO shared pieces: ConnectorForm/ConnectorList, RiskChip, ChangePreview, ModeGate, UnavailableState
lib/
  api/client.ts           fetch wrapper (/api/v1, CSRF, ApiError)       api/hooks.ts  useResource, useMutation
  api/contracts.ts        ALL shared API types — add yours here           api/actions.ts useAction (toast feedback)
  i18n/                   t(), formatters; messages per area in i18n/messages/nl/<area>.ts
  session.tsx             useSession(): user, role, can("operator")
  mode.tsx                useMode(), useWriteGate()
  status.ts               status → tone/label helpers
styles/peil/              pinned Peil copies (ontwerptaal.css + MediaGuard/MSO alias layer) — never edit by hand
styles/mg.css, mso.css    legacy classes (cascade layer "legacy", below the utilities) — shrinking, do not add rules
styles/peil-mso.css       MSO corrections on Peil (legacy layer): focus ring, reflow, container-query table cards
```

## Writing a page

Routes owned by other agents: `/`, `/zoeken`, `/nzb-indexers`, `/torrent-indexers`, `/usenet-providers`,
`/torrentclient`, `/opslag`, `/downloads`, `/ontbrekend`, `/automatisering`, `/mediaguard`, `/kosten`.
Create `app/(app)/<route>/page.tsx` — the shell, auth, mode and toasts are already provided.

```tsx
"use client";
import { Gauge } from "lucide-react";
import { Card, PageHeader, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";

export default function IndexersPage() {
  const { t, num } = useI18n();
  const indexers = useResource<{ items: Indexer[] }>("/indexers", { kind: "nzb" }, 30_000); // path, query, poll ms
  return (
    <>
      <PageHeader title={t("nav.nzbIndexers")} sub="…" actions={…} />
      <div className="grid">
        <Card className="span-12" title="Indexers" icon={Gauge} flush>
          <ResourceView resource={indexers} endpoint="GET /api/v1/indexers" isEmpty={(d) => !d.items.length}
            empty={<StateView kind="empty" title="Nog geen indexers" />}>
            {(d) => <table className="table table--stack">…</table>}
          </ResourceView>
        </Card>
      </div>
    </>
  );
}
```

Rules (same as MediaGuard):
- **No dummy data, no dead buttons.** Every button calls a real endpoint, or is `disabled` with a `title` that says why.
- **States**: use `ResourceView` (loading skeleton / error / offline / unavailable / stale marker) or `StateView kind=…`
  directly. Errors with `code: "connector_not_configured"` automatically render the "Verbinding instellen" state;
  use `<UnavailableState connector="SABnzbd" connectorId="sabnzbd" />` when you already know.
- **Status = colour + icon + text**: `<StatusChip tone="success|warning|danger|info|accent|neutral">`.
- Layout classes: `grid` + `span-3/4/6/8/9/12`, `stack`, `row`, `row-between`, `list`/`list-row`, `kv`,
  `table table--stack` (cells need `data-label` so they stack on mobile), `toolbar`, `instance-grid`.
- Mobile: must not scroll horizontally at 390 px.

### Data

- `useResource<T>(path | null, query?, refreshMs?)` → `{ data, error, loading, stale, reload, setData }`. `null` path = disabled.
- Job-starting endpoints (`{job_id}`): `await waitForJob(id, { onUpdate })` from `lib/api/jobs.ts` polls `GET /jobs/{id}` until
  done/failed, then reload what changed (see the back-up card on /systeem). Job status labels: `jobLabel()` in `lib/status.ts`
  (`done` → "Geslaagd"); other backend enums: `enumLabel(t, prefix, value)`.
- `useMutation(fn)` → `{ run, pending, error }` (never throws), or `useAction()` → `run(key, "post", path, body, successToast)`
  with `pending === key` and automatic error toasts.
- `api.get/post/put/patch/del` send cookies + `X-CSRF-Token`; failures are `ApiError { kind, status, message, code, correlationId, detail }`.
  A 401 anywhere redirects to `/login` (or `/setup` on first run).
- Lists may be `T[]` or `{items: T[]}`: `itemsOf(data)`.

### Permissions and operating mode

- `useSession().can("viewer" | "operator" | "admin")`.
- `useMode()` → `{ mode: "read_only" | "manage" | null, readOnly, paused, pause(), resume(), setMode(mode), reload }`.
  This is the single live source for mode/pause: derive every "mag automatisering draaien?" display from it (not from a
  page's own polled copy). The mode is switched in Instellingen → Systeem → Bedrijfsmodus (`/instellingen?tab=systeem#modus`,
  also the topbar chip's link); `setMode` does `PUT /system/mode` (admin) and reloads all consumers.
- Anything that changes an **external** system (Prowlarr, SABnzbd, qBittorrent, MediaGuard) must be gated:
  ```tsx
  const gate = useWriteGate("operator");            // role + manage mode
  <Button disabled={!gate.allowed} title={gate.reason}>Toepassen</Button>
  // or: <ModeGate>{(gate) => …}</ModeGate>   /   <ModeGate>…only when allowed…</ModeGate>
  ```
  MSO-internal actions (settings, users, jobs, backups) are gated on role only.

### Shared MSO components (`components/mso/`)

| Component | Props | Notes |
| --- | --- | --- |
| `RiskChip` | `risk: "advies" \| "automatisch_veilig" \| "toestemming_nodig" \| null`, `size?: "lg"` | info "Advies" · success "Automatisch veilig" · warning "Toestemming nodig". `RISK` map exported. |
| `ChangePreview` | `change: ChangeSet`, `onChanged?(updated \| null)`, `actions? = true`, `card? = true` | DiffViewer of `ops`, explanation, impact; Goedkeuren/Toepassen/Terugdraaien → `POST /changes/{id}/approve\|apply\|rollback`; disabled with reason in read-only mode. Apply from `preview` is allowed unless risk is `toestemming_nodig`. |
| `ConnectorForm` | `connector: Connector`, `onSaved?`, `canEdit? = true`, `disabledReason?`, `optional?` | URL, enabled, write-only secrets; Test → `POST /connectors/{id}/test`, Bewaar → `PUT /connectors/{id}`. |
| `ConnectorList` | `only?: string[]`, `canEdit?`, `disabledReason?` | Fetches `GET /connectors` and renders ConnectorForms (e.g. `only={["mediaguard"]}`). Also exports `sortConnectors`, `OPTIONAL_CONNECTORS`. |
| `ModeGate` | `children: ReactNode \| (gate) => ReactNode`, `minRole? = "operator"`, `requireManage? = true`, `fallback?` | Re-exports `useMode`, `useWriteGate`. |
| `UnavailableState` | `connector?`, `connectorId?`, `title?`, `body?`, `compact?`, `action?` | Links to `/instellingen?tab=verbindingen#connector-<id>`. |

From `components/mg/domain.tsx`: `KpiCard`, `Sparkline`, `ConnectorCard` (read-only connector summary),
`ConnectorStatusChip`, `ConnectorLogo`, `EvidenceCard`, `Confidence`, `Trace`, `DiffViewer`,
`DistributionBar({segments})`, `Stepper`, `Timeline`, `DangerDialog` (typed confirm via `confirmWord`), `PendingAction`.
From `components/mg/primitives.tsx`: `Card`, `PageHeader`, `Button`, `LinkButton`, `StatusChip`, `Dot`, `Switch`,
`Segmented`, `Tabs`, `Field`, `InlineState`, `Progress`, `Skeleton`, `Kbd`, `Modal` (portal on `<body>`, always fully visible with
internal scroll, `wide` for detail views; focuses once on open and never steals focus on re-render). Toasts: `useToast().push(tone, title, body?)`.
Shell data: `useShell()` → `{ health, info, reloadHealth }`.

### Texts (i18n)

Dutch strings live in `lib/i18n/messages/nl/<area>.ts`:

```ts
// lib/i18n/messages/nl/indexers.ts
export const messages = {
  "indexers.title": "NZB-indexers",
  "indexers.hits.one": "{count} treffer",     // tp("indexers.hits", n)
  "indexers.hits.other": "{count} treffers",
};
```

Then add one import + one spread in `lib/i18n/messages/index.ts`. Prefix every key with your area so files never
collide. Use `const { t, tp, num, pct, bytes, money, date, dateTime, relative, duration } = useI18n()`.
Shared keys (nav, actions, states, statuses, roles) are in `nl/common.ts` — reuse, don't duplicate.

### API contracts

Add request/response types to `lib/api/contracts.ts` (or `lib/api/<area>.ts` if large). Existing: `Me`, `SetupStatus`,
`SystemMode`, `SystemHealth`, `SystemInfo`, `Connector`, `ConnectorTestResult`, `SettingDef`, `SettingsPayload`,
`Risk`, `ChangeSet`, `ChangeOp`, `AuditEntry`, `AuditPage`, `Backup`, `Job`, `UserRow`.

### Keyboard shortcuts

`/` focuses search (Enter → `/zoeken?q=…`), `?` shows help, `g` + key navigates (see `shortcut` in `nav.ts`):
`g o` overzicht, `g z` zoeken, `g n` NZB-indexers, `g t` torrent-indexers, `g u` usenet, `g q` torrentclient,
`g d` downloads, `g m` ontbrekend, `g a` automatisering, `g k` kosten, `g s` instellingen, `g l` logs, `g b` back-up.
