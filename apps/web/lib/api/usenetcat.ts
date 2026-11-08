// 1.6.0 WP-C: why SABnzbd puts downloads in '*' (`GET /usenet/categories/diagnosis`, `POST /usenet/categories/propose`).

export type CategoryVerdict = "a" | "b" | "c" | "ok" | "geen_ster" | "onbekend";
export type CategoryCertainty = "zeker" | "waarschijnlijk";
export type MgReason = "niet_gekoppeld" | "offline" | "geen_toegang" | "route_ontbreekt" | "niet_gevonden" | "fout";
export type ClientCategoryStatus = "ok" | "ontbreekt_in_sab" | "geen_categorie" | "uit" | "onbekend";

export interface CategoryHealth { source: string; type: string; message: string }

export interface CategoryInstanceClient {
  name: string | null; category: string | null; enabled: boolean; mediaguard_owned: boolean;
  status: ClientCategoryStatus; fix_in: "mediaguard" | "arr"; certainty: CategoryCertainty | null;
}

export interface CategoryInstance {
  id: string; name: string; app: "sonarr" | "radarr" | string; media_type: string | null;
  expected_category: string | null; provisioned: boolean | null; clients: CategoryInstanceClient[];
  clients_reason: MgReason | null; no_sab_client: boolean; health: CategoryHealth[]; health_source: "live" | "cached" | null;
}

export interface CategoryMissing { category: string; instances: string[]; certainty: CategoryCertainty }
export interface CategoryNoCategory { instance: string; client: string | null; fix_in: "mediaguard" | "arr"; expected: string | null }
export interface CategoryOther { recent_star: number; queue_star: number; from_mso: number; mso_missing: string[]; unexplained: number }

export interface StarDay { day: string; total: number; star: number; star_mso: number }

export interface CategoryProposalRef {
  id: number; status: "preview" | "approved" | "applied" | "rolled_back"; title: string; categories: string[];
  created_at?: string; applied_at?: string | null; rolled_back_at?: string | null;
}

export interface CategoryDiagnosis {
  checked_at: string;
  verdict: CategoryVerdict;
  missing: CategoryMissing[];
  folders: Record<string, string>;
  no_category: CategoryNoCategory[];
  other: CategoryOther | null;
  instances: CategoryInstance[];
  mso: { send_category_mode: string | null; used: Array<{ category: string; count: number }>; missing: string[] };
  sab: {
    configured: boolean; available: boolean; error: string | null; categories: string[]; complete_dir: string | null;
    queue_total: number | null; queue_star: number | null;
  };
  mediaguard: { configured: boolean; available: boolean; reason: MgReason | null; provisioning: boolean; provisioning_reason: MgReason | null };
  star: {
    days: StarDay[]; timezone: string; recent_days: number; recent_total: number; recent_star: number; recent_star_mso: number;
  };
  proposal: CategoryProposalRef | null;
  last_applied: CategoryProposalRef | null;
  media_categories: string[];
}
