export type LinkConfidence = "confirmed" | "suggested" | "unmatched";

export interface ClientMapRow {
  id: string;
  tag_code: string;
  client_name: string;
  qb_customer_ids: string[];
  qb_customer_names: string[];
  teamwork_company_ids: number[];
  teamwork_company_names: string[];
  city: string | null;
  state: string | null;
  current_am: string | null;
  status: string | null;
  source: string | null;
  highest_value: string | null;
  is_internal: boolean;
  link_confidence: LinkConfidence;
  link_reason: string | null;
  notes: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface UnmatchedResponse {
  teamwork: Array<{ id: number | null; name: string }>;
  quickbooks: Array<{ qbo_id: string; display_name: string }>;
}

export interface LinkResult {
  confirmed: number;
  suggested: number;
  teamwork_tag?: number;
}

export interface JobOverride {
  id: string;
  site_id: string;
  project_id: number;
  client_map_id: string | null;
  qb_customer_ids: string[];
  qb_customer_names: string[];
  link_confidence: LinkConfidence;
  notes: string | null;
  created_at?: string;
  updated_at?: string;
}

export type ClientMapCorePatch = Pick<
  ClientMapRow,
  "tag_code" | "client_name" | "current_am" | "status" | "is_internal"
>;
