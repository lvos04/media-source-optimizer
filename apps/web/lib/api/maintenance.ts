// API types for 1.6.0 WP-E2 maintenance (backend: apps/core/mso/retention.py, mso/backups.py).

/** `GET /system/maintenance` */
export interface MaintenanceStatus {
  growth: ResourceGrowth;
  provenance: Record<string, ResourceSource>;
  retention: {
    enabled: boolean;
    last: RetentionRun | null;
    raw: { configured_days: number; effective_days: number; extended: boolean; readers: Array<{ reader: string; days: number }> };
    settings: Record<string, number | null>;
  };
  database: { size_bytes: number; tables: Array<{ name: string; bytes: number; rows: number }> };
  backups: {
    space: BackupSpace;
    kinds: Record<string, { count: number; bytes: number }>;
    excluded_tables: string[];
    safety: { pre_restore_days: number; pre_restore_keep: number; pre_upgrade_keep: number };
  };
}

export interface RetentionRun {
  at: string;
  duration_s: number;
  deleted: Record<string, number>;
  removed_total: number;
  vacuumed: string[];
  raw_days: number;
  complete: boolean;
}

export interface BackupSpace {
  path: string;
  free_bytes: number | null;
  needed_bytes: number;
  reference_bytes: number;
  reference: string;
  reserve_bytes: number;
  ok: boolean;
}

export type ResourceQuality = "gemeten" | "geschat" | "onbekend";

export interface ResourceDay {
  day: string;
  observed_at: string | null;
  values: Record<string, number | null> | null;
  provenance?: Record<string, ResourceSource> | null;
}

export interface ResourceTrend {
  value: number | null;
  from_day: string | null;
  to_day: string | null;
  quality: ResourceQuality;
}

export interface ResourceSource {
  source: string;
  quality: ResourceQuality;
  observed_at: string | null;
  reason?: string | null;
}

export interface ResourceGrowth {
  latest: ResourceDay | null;
  days: ResourceDay[];
  per_day: Record<string, ResourceTrend>;
  tables: Array<{ name: string; bytes: number; per_day: ResourceTrend }>;
}
