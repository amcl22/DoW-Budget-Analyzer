// Typed client for the search API. Amounts are $ thousands, as stored.

export type SortKey =
  | "relevance" | "title" | "number" | "service" | "exhibit" | "account"
  | "budget_activity" | "cycle" | "prior" | "current" | "budget" | "change";

export interface Query {
  q: string;
  cycle: string;            // "" = all releases
  exhibit: string;          // "" = both
  service: string[];
  account: string;
  budgetActivity: string;
  minM: string;             // $ millions as typed
  maxM: string;
  sort: SortKey;
  order: "asc" | "desc";
  page: number;
}

export interface LineItem {
  id: number;
  fiscal_year: number;
  budget_cycle: string;
  exhibit_type: "R-1" | "P-1";
  service_branch: string;
  appropriation_account: string;
  appropriation_title: string;
  organization: string | null;
  budget_activity: string;
  budget_activity_title: string;
  budget_subactivity_title: string | null;
  line_number: string;
  program_element: string | null;
  line_item_number: string | null;
  program_key: string;
  program_id: number;
  program_title: string;
  prior_year_amount: number | null;
  current_year_amount: number | null;
  budget_year_amount: number | null;
  budget_year_discretionary: number | null;
  budget_year_mandatory: number | null;
  prior_year_quantity: number | null;
  current_year_quantity: number | null;
  budget_year_quantity: number | null;
  source_pdf_link: string | null;
  source_page_number: number | null;
  source_kind: "r1_summary" | "p1_summary" | "r2_justification" | "p40_justification" | null;
  include_in_toa: boolean;
  has_description: boolean;
  change_pct: number | null;
  snippet: string | null;
  watched: boolean;
}

export interface SearchResponse {
  total: number;
  budget_year_total: number | null;
  current_year_total: number | null;
  page: number;
  page_size: number;
  results: LineItem[];
}

export interface Facets {
  cycles: { budget_cycle: string; fiscal_year: number; lines: number; exhibits: string[] }[];
  exhibits: { value: string; lines: number }[];
  services: { value: string; lines: number }[];
  accounts: { value: string; title: string; exhibit: string; lines: number }[];
  budget_activities: { value: string; exhibit: string; lines: number }[];
}

export const PAGE_SIZE = 50;

function toThousands(millions: string): string | null {
  const n = Number(millions.replace(/[$,\s]/g, ""));
  return millions.trim() === "" || !Number.isFinite(n) ? null : String(Math.round(n * 1000));
}

export function toParams(q: Query, forExport = false): URLSearchParams {
  const p = new URLSearchParams();
  if (q.q.trim()) p.set("q", q.q.trim());
  if (q.cycle) p.set("cycle", q.cycle);
  if (q.exhibit) p.append("exhibit", q.exhibit);
  q.service.forEach((s) => p.append("service", s));
  if (q.account) p.append("account", q.account);
  if (q.budgetActivity) p.append("budget_activity", q.budgetActivity);
  const min = toThousands(q.minM);
  const max = toThousands(q.maxM);
  if (min !== null) p.set("min_amount", min);
  if (max !== null) p.set("max_amount", max);
  p.set("sort", q.sort);
  p.set("order", q.order);
  if (!forExport) {
    p.set("page", String(q.page));
    p.set("page_size", String(PAGE_SIZE));
  }
  return p;
}

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const resp = await fetch(url, { signal });
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json() as Promise<T>;
}

export const fetchSearch = (q: Query, signal?: AbortSignal) =>
  getJson<SearchResponse>(`/api/search?${toParams(q)}`, signal);

export const fetchFacets = (cycle: string, signal?: AbortSignal) =>
  getJson<Facets>(`/api/facets${cycle ? `?cycle=${encodeURIComponent(cycle)}` : ""}`, signal);

export const exportUrl = (q: Query) => `/api/search.csv?${toParams(q, true)}`;

export type AmountType = "actual" | "enacted" | "cr" | "request" | "estimate";

export interface SeriesPoint {
  fiscal_year: number;
  amount_thousands: number;
  quantity: number | null;
  amount_type: AmountType;
  source_cycle: string;
  change_pct: number | null;
  flagged: boolean;
}

export interface HistoryRow {
  budget_cycle: string;
  release_fiscal_year: number;
  funds_fiscal_year: number;
  amount_type: AmountType;
  amount_thousands: number;
  quantity: number | null;
  is_latest: boolean;
}

export interface SourceRef {
  budget_cycle: string;
  fiscal_year: number;
  exhibit_type: string;
  line_number: string;
  appropriation_account: string;
  budget_activity: string;
  ref_kind: string;
  page_number: number;
  printed_page_label: string | null;
  section: string | null;
  is_primary: boolean;
  amount_verified: boolean;
  link: string;
  document_url: string;
}

export interface CostElementRow {
  line_item_id: number;
  cost_type: string;
  cost_type_title: string;
  is_add: boolean;
  amounts: Record<string, { amount_thousands: number; quantity: number | null }>;
}

export interface ProgramDetail {
  program: { id: number; exhibit_family: "RDTE" | "PROC"; program_key: string; latest_title: string; is_classified_rollup: boolean };
  latest_cycle: string | null;
  latest_lines: LineItem[];
  lines: LineItem[];
  series: SeriesPoint[];
  history: HistoryRow[];
  releases: string[];
  description: { raw_description_text: string; budget_cycle: string } | null;
  sources: SourceRef[];
  cost_elements: CostElementRow[];
  watched: boolean;
  flag_threshold: number;
}

export const fetchProgram = (key: string, signal?: AbortSignal) =>
  getJson<ProgramDetail>(`/api/programs/${encodeURIComponent(key)}`, signal);

export async function setWatch(key: string, watched: boolean): Promise<boolean> {
  const resp = await fetch(`/api/programs/${encodeURIComponent(key)}/watch`, { method: watched ? "PUT" : "DELETE" });
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return ((await resp.json()) as { watched: boolean }).watched;
}
