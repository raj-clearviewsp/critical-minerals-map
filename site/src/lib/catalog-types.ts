export interface Evidence {
  source_id: string; record_id?: string; url?: string; grade?: string; date?: string; note?: string;
}
export interface Site {
  id: string; name: string; country_iso3s: string[]; minerals: string[]; source_ids: string[];
  lat: number | null; lon: number | null; location_precision?: string; entity_level?: string;
  kind?: string; maturity_rung: number | null; maturity_reason?: string; status?: string;
  reporting_system?: string; aliases?: string[]; evidence?: Evidence[]; record_ids?: string[];
  deposit_type?: string; operator?: string; review_state?: string;
  coincident_named_entries?: number; publisher_view_counterparts?: string[]; national_system?: boolean; has_unreviewed_candidates?: boolean; status_as_of?: string; production_families?: string[]; operator_evidence?: Evidence | Evidence[];
}
export interface Source {
  id: string; title: string; url?: string; license?: string; retrieved_at?: string;
  attribution?: string; redistribution_ok?: boolean; currency?: string; grade?: string;
}
export interface ContextFact {
  label: string; value?: string | number | null; unit?: string; year?: string | number | null;
  source_id?: string; url?: string; grade?: string; note?: string;
}
export interface Profile { code: string; name: string; context?: ContextFact[]; }
export interface Download {
  path: string; label: string; format: string; table: string; rows?: number | null;
  sha256: string; bytes?: number; description?: string;
}
export interface Release {
  schema_version: string; release_tag: string; data_as_of: string | null; sites: Site[];
  sources: Source[]; countries: Profile[]; minerals: Profile[]; downloads: Download[];
}
