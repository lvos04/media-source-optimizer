# Media Source Optimizer — engineering brief (shared by all contributors)

Product plan: `docs/dev/PLAN.md` (the original Dutch plan; read sections relevant to your slice).
MediaGuard reference (READ-ONLY, never modify, never run git write commands there): `/home/user/media-guard-ultimate`.
AV1 reference (read-only): `/home/user/lvos04/av1`.

## Hard rules
1. Never modify anything under `/home/user/media-guard-ultimate` or `/home/user/lvos04/av1`.
2. Do NOT run `git commit`/`git push`/`git checkout`/`git stash` in this repo — the coordinator commits.
3. Do not edit files owned by another slice (see ownership below). Shared foundation files
   (`mso/app.py`, `mso/db.py`, `mso/auth.py`, `mso/changes.py`, `mso/jobs.py`, `mso/domain/*`,
   `mso/connectors/base.py`, `tests/conftest.py`, migrations 0001/0002) are owned by the coordinator: if you
   need a change there, describe it in your final report (small additive helpers in your own module are fine).
4. No dummy data in the product, no dead buttons. Mocks exist only under `apps/mocks/` for tests.
5. Never log or return secrets (API keys, passwords, passkeys, cookies). Use `mso.security.redact`.
6. Everything that writes to an external system goes through a change set (`mso.changes`): preview first,
   risk class `advies` | `automatisch_veilig` | `toestemming_nodig`, apply only in beheermodus, rollback.
   Exceptions: user-initiated "send this NZB/torrent to the client" and "request search in MediaGuard"
   — these are direct actions but still audited, rate limited and idempotent.
7. Honesty: never claim unique retention from total-downloaded bytes, never promise savings, never suggest a
   self-built indexer replaces a provider/commercial indexer. Report confidence/observation coverage.
8. Never: delete/rename/move media or seed files, add public trackers/DHT to private torrents, stop seeding
   that violates a tracker's ratio/seed-time policy, bypass captchas/paywalls/private-tracker rules,
   auto-cancel subscriptions, scrape private trackers.

## Runtime and tooling
- Python venv: `/home/user/media-source-optimizer/.venv` (use `.venv/bin/python`, `.venv/bin/pip install …`;
  if you add a dependency, pin it and tell the coordinator so it lands in `apps/core/pyproject.toml`).
- Tests: `cd apps/core && ../../.venv/bin/python -m pytest -q -p no:warnings tests/<your files>`.
  A local PostgreSQL 16 test server runs on 127.0.0.1:55432 (user `mso`, trust auth); each pytest session
  creates its own database, tables are truncated between tests. If it is down, start it with:
  `su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D /var/lib/msotest-pg/data -o '-p 55432 -k /tmp -c listen_addresses=127.0.0.1' -l /var/lib/msotest-pg/log start"`.
- Backup/restore tests call `pg_dump`/`pg_restore`. With several PostgreSQL clients installed side by side (e.g.
  16 and 18 behind pg_wrapper) set `MSO_PG_BIN=/usr/lib/postgresql/16/bin`, as `.github/workflows/ci.yaml` and
  `release.yaml` do; otherwise dump and check can run with different versions ("unsupported version (1.16)").
- Run only your feature modules while others are mid-edit: `MSO_FEATURES=prowlarr,search pytest …`
  (comma list of module-name prefixes under `mso.connectors.*` / `mso.features.*`).
- Fixtures (`tests/conftest.py`): `app`, `db`, `client` (anonymous), `admin`/`operator`/`viewer` (logged in,
  CSRF handled), `manage_mode`, `worker` (`worker.drain()` runs queued jobs), `serve(asgi_app) -> base_url`
  (runs a mock service on a real local port — use this for connector tests, real HTTP).
- Docker daemon is available (`docker`), but other contributors use it too; don't prune or stop others' containers.
- Web: `apps/web` (Next.js 16 + React 19 + Tailwind 4, MediaGuard design system copied in). NEVER run
  `next build`/`npm run build`/`npm run dev` (concurrent builds corrupt `.next`); use
  `cd apps/web && npx tsc --noEmit -p . 2>&1 | grep -E '<your paths>'` and `npx eslint <your files>`.

## Backend structure
```
apps/core/mso/
  app.py               FastAPI factory; auto-discovers mso.connectors.* and mso.features.* (imports them,
                       mounts any module-level `router` / `routers`). Never edit to register things.
  config.py db.py auth.py security.py vault.py audit.py jobs.py changes.py state.py settings_registry.py backups.py
  domain/categories.py  4 MediaGuard categories tv|tv-anime|movies|movies-anime, Newznab mapping, scopes normal|anime
  domain/release.py     NormalizedRelease, parse_title, release_key (identity), dedupe, QualityRequirements, check_quality
  domain/outcomes.py    download_outcomes upsert + FAILURE_REASONS, observe(), upsert_source(), recommend()
  connectors/base.py    ConnectorSpec registry, ConnectorConfig, TestResult, load_config(), http_client() (SSRF-checked)
  connectors/<name>.py  one per external system (prowlarr, sabnzbd, qbittorrent, mediaguard)
  features/<area>/      engines + API routers (package with __init__.py)
  migrations/NNNN_*.sql with `-- migrate:up` / `-- migrate:down` (down must fully revert up)
apps/mocks/msomocks/<name>.py   fake services: expose `create_app(**faults) -> FastAPI` + module docstring.
                                `python -m msomocks <name> --port N` must start it (add your entry to
                                apps/mocks/msomocks/__main__.py registry dict — append only, one line).
```

### Migration number ranges (avoid collisions)
| Range | Owner |
|---|---|
| 0001–0099 | coordinator (foundation, shared domain) |
| 0100–0199 | Slice A: indexers, search, Newznab/Torznab |
| 0200–0299 | Slice B: Usenet engine, SABnzbd, NNTP |
| 0300–0399 | Slice C: torrent engine, qBittorrent |
| 0400–0499 | Slice D: MediaGuard, recovery |
| 0500–0599 | Slice E: costs, AI advisor, overview, automation |

### Key APIs available
```python
from mso.db import get_db, jsonb          # db.fetchall/fetchone/scalar/execute(sql, params); db.tx() context
from mso.auth import require, Principal   # Depends(require("viewer"|"operator"|"admin"))
from mso.audit import audit               # audit(actor, "area.action", "Nederlandse samenvatting", target=, detail=)
from mso import jobs                      # @jobs.handler("kind"); jobs.enqueue(kind, payload, idempotency_key=); jobs.every(kind, seconds)
from mso import changes                   # changes.register_applier(target, factory->Applier(read, write, validate))
                                          # changes.create(target, title, ops=[{path,label,before,after}], risk=, explanation=, impact=, dedupe_key=, evaluate_after_h=)
from mso import state                     # state.mode() 'read_only'|'manage'; state.paused(); state.automation_allowed()
from mso import settings_registry as sr   # sr.define(key,label,type,default, category=, help=, min=, max=, options=, risk=, unit=); sr.get(key)
from mso.connectors import base as cb     # cb.register(ConnectorSpec(...)); cb.load_config(id) -> ConnectorConfig(url, secrets, options); cb.http_client(url)
from mso.domain import outcomes, release, categories
from mso.security import check_url, redact, limiter, UnsafeTarget
from mso.vault import get_vault           # vault.set(name, value) / get(name) / status(name) — name e.g. 'provider.eweka.password'
```
API conventions: prefix `/api/v1/<area>`; errors `{detail, code, correlation_id}` (raise HTTPException with Dutch
`detail`); GET = viewer, operational actions = operator, configuration/credentials/approvals = admin.
Times as ISO 8601 UTC. Lists as `{items:[…]}`. Settings keys `<area>.<name>`.

Shared tables (migration 0002): `download_outcomes`, `sources`, `services`, `observations`, `recommendations`
— see the SQL file for columns. `sources.id` conventions: `prowlarr:<indexerId>`, `direct:<slug>`,
`spotweb:<slug>`, `headerindex:<slug>`, `provider:<slug>`, `block:<slug>`, `tracker:<host>`.

## Frontend structure
- Pages: `apps/web/app/(app)/<route>/page.tsx` ("use client" pages using hooks), shared MSO components in
  `apps/web/components/mso/`, feature components in `apps/web/components/mso/<area>/`.
- Read `apps/web/README.md` (written by the web-foundation contributor) for component APIs, `useResource`,
  `useMutation`, i18n, `ChangePreview`, `RiskChip`, `useMode`.
- Look at MediaGuard pages for layout patterns (`/home/user/media-guard-ultimate/apps/web/app/(app)/*`),
  and screenshots `docs/design/screenshots-app/*.png` there. Use classes from `styles/mg.css` (`grid`, `span-N`,
  `card`, `kpi`, `table`, `table--stack`, `chip--*`, `dl`, `evidence`, …). Dutch copy. Every state:
  loading/empty/error/offline/unavailable/stale/uncertain/pending/success; colour always with icon + text.
- Every button calls a real endpoint or is disabled with a visible reason (e.g. "Alleen in beheermodus").

## Slice ownership
| Slice | Backend | UI routes |
|---|---|---|
| A Indexers & search | connectors/prowlarr.py, features/search/*, features/indexers/*, features/newznab_server/*, mocks prowlarr + newznab/torznab indexer + spotweb | /zoeken, /nzb-indexers, /torrent-indexers |
| B Usenet | connectors/sabnzbd.py, features/usenet/*, mocks sabnzbd + nntp | /usenet-providers, components/mso/downloads/usenet-tab.tsx |
| C Torrents | connectors/qbittorrent.py, features/torrent/*, mocks qbittorrent | /torrentclient, /opslag, components/mso/downloads/torrent-tab.tsx |
| D MediaGuard & recovery | connectors/mediaguard.py, features/mediaguard/*, features/recovery/*, mocks mediaguard | /mediaguard, /ontbrekend |
| E Costs, overview, automation, AI | features/costs/*, features/overview/*, features/automation/*, features/ai/* | /, /kosten, /automatisering, /downloads (page shell importing B's and C's tab components) |
| F Deploy & QA | deploy/**, apps/core/Dockerfile, apps/mocks/Dockerfile, tests/stack/**, .github/workflows/** | — |
| W Web foundation | apps/web foundation, /login, /setup, /instellingen, /logs, /systeem | — |

### Cross-slice Python contracts (implement exactly these signatures in your slice)
- B: `mso.connectors.sabnzbd.SabClient.from_config() -> SabClient`; `.add_url(url: str, *, category: str, name: str | None = None, priority: int | None = None) -> str` (nzo_id); `.add_nzb(data: bytes, filename: str, *, category: str) -> str`; `.categories() -> list[str]`.
- C: `mso.connectors.qbittorrent.QbitClient.from_config() -> QbitClient`; `.add_torrent(*, url: str | None = None, data: bytes | None = None, category: str, paused: bool = False, tags: list[str] | None = None) -> None`; `.categories() -> list[str]`.
- A: `mso.features.search.service.search(query: SearchQuery, *, trace: list[str] | None = None, actor: str = "system") -> SearchResult` where `SearchQuery(q: str | None, media_category: str | None, kind: 'search'|'tvsearch'|'movie', season, episode, absolute_episode, imdb_id, tvdb_id, tmdb_id, protocols: set[str] | None, limit, offset)` and `SearchResult(items: list[NormalizedRelease], sources: list[SourceReport{source_id,label,status:'ok'|'empty'|'timeout'|'error'|'category_error'|'skipped_cycle'|'skipped_scope',count,latency_ms,message}], trace_id: str)`; `mso.features.search.service.fetch_download(release_key_or_guid…)` as you see fit.
- D: `mso.features.recovery.blacklist.is_blacklisted(release_key: str) -> bool`, `.add(release_key, reason, ttl_h, media_ref=None, protocol=None)`.
- All engines write finished/failed downloads into `download_outcomes` via `mso.domain.outcomes.upsert_outcome`.
