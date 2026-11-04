/**
 * MSO core API contracts (`/api/v1`). Shared by every page — add new feature contracts at the
 * bottom of this file (or in a feature file next to it) instead of redefining shapes inline.
 */

// ---------------------------------------------------------------- auth & setup
export type Role = "admin" | "operator" | "viewer";
export const ROLES: Role[] = ["admin", "operator", "viewer"];

export interface User { id: number | string; username: string; role: Role }

/** `GET /auth/me`, `POST /auth/login` (and possibly `POST /setup/admin`). */
export interface Me { user: User; csrf_token: string }
export type LoginResult = Me;

/** `GET /setup/status` */
export interface SetupStatus { setup_required: boolean; admin_exists: boolean; [key: string]: unknown }

/** `GET /users` rows */
export interface UserRow {
  id: number | string; username: string; role: Role; disabled?: boolean;
  created_at?: string | null; last_login_at?: string | null;
}

// ---------------------------------------------------------------- system
export type OperatingMode = "read_only" | "manage";
/** `GET /system/mode` */
export interface SystemMode { mode: OperatingMode; paused: boolean; [key: string]: unknown }

export type HealthStatus = "healthy" | "degraded" | "offline";
/** `GET /system/health` */
export interface SystemHealth { status: HealthStatus; components: Array<{ name: string; label?: string | null; status: HealthStatus | string; message?: string | null }> }

/** `GET /system/info` */
export interface SystemInfo {
  version: string; schema_version: number | string; uptime_s: number;
  migrations: Array<{ version: number | string; name: string; applied_at: string | null }>;
}

// ---------------------------------------------------------------- connectors
export type ConnectorId = "prowlarr" | "sabnzbd" | "qbittorrent" | "mediaguard" | (string & {});
export interface SecretField { name: string; label: string; status: "ingesteld" | "leeg" }

/** `GET /connectors` rows */
export interface Connector {
  id: ConnectorId; label: string; kind: string; url: string | null; enabled: boolean;
  secret_fields: SecretField[]; status: string | null; version: string | null;
  capabilities: string[] | Record<string, unknown> | null; last_checked_at: string | null; last_error: string | null;
  options?: Record<string, unknown> | null;
}

/** `POST /connectors/{id}/test` */
export interface ConnectorTestResult {
  ok: boolean; status: string; version: string | null; message: string | null; latency_ms: number | null;
  capabilities: string[] | Record<string, unknown> | null;
}

// ---------------------------------------------------------------- settings
export type SettingType = "bool" | "int" | "float" | "string" | "enum" | "list" | "secret" | "duration";
export type Risk = "advies" | "automatisch_veilig" | "toestemming_nodig";
export type SettingOption = string | { value: string; label: string };

export interface SettingDef {
  key: string; label: string; help?: string | null; type: SettingType; default: unknown;
  min?: number | null; max?: number | null; options?: SettingOption[] | null; category: string;
  risk?: Risk | null; locked: boolean; restart: boolean; unit?: string | null;
}
/** `GET /settings` */
export interface SettingsPayload { definitions: SettingDef[]; values: Record<string, unknown> }

// ---------------------------------------------------------------- change sets
export type ChangeStatus = "preview" | "approved" | "applied" | "rolled_back" | "failed";
export interface ChangeOp { path: string; label: string; before: unknown; after: unknown }
export interface ChangeSet {
  id: string | number; title: string; target: string; risk: Risk; status: ChangeStatus;
  ops: ChangeOp[]; explanation?: string | null; impact?: unknown;
}

// ---------------------------------------------------------------- audit, backups, jobs
export interface AuditEntry {
  id: number | string; ts: string; actor: string | null; action: string; target: string | null;
  summary: string | null; detail: unknown; change_id: string | number | null;
}
/** `GET /audit` */
export interface AuditPage { items: AuditEntry[]; next_before_id: number | string | null }

export interface Backup { id: string | number; file: string; created_at: string; size_bytes: number | null; kind: string; includes_secrets: boolean }
export interface BackupList { items: Backup[] }

export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled" | (string & {});
export interface Job {
  id: string | number; kind: string; status: JobStatus; attempts: number; max_attempts: number;
  created_at: string; updated_at: string | null; error: string | null; progress: number | null; summary: string | null;
}
export interface JobList { items: Job[] }

/** Lists may come back as a bare array or as `{items: [...]}` — normalise both. */
export function itemsOf<T>(value: T[] | { items: T[] } | null | undefined): T[] {
  if (!value) return [];
  return Array.isArray(value) ? value : value.items ?? [];
}
