import { useEffect, useMemo, useRef, useState } from "react";
import { exportUrl, fetchFacets, fetchSearch, PAGE_SIZE, type Facets, type Query, type SearchResponse, type SortKey } from "./api";
import { Filters } from "./Filters";
import { ResultsTable } from "./ResultsTable";
import { fmtCount, fmtMoney } from "./format";
import { Link } from "./router";

const LATEST = "latest";   // URL has no cycle: use the newest release once facets load

const DEFAULTS: Query = {
  q: "", cycle: LATEST, exhibit: "", service: [], account: "", budgetActivity: "",
  minM: "", maxM: "", sort: "relevance", order: "desc", page: 1,
};

function readUrl(): Query {
  const p = new URLSearchParams(window.location.search);
  const cycle = p.get("cycle");
  return {
    ...DEFAULTS,
    q: p.get("q") ?? "",
    cycle: cycle === null ? LATEST : cycle === "all" ? "" : cycle,
    exhibit: p.get("exhibit") ?? "",
    service: p.getAll("service"),
    account: p.get("account") ?? "",
    budgetActivity: p.get("ba") ?? "",
    minM: p.get("min") ?? "",
    maxM: p.get("max") ?? "",
    sort: (p.get("sort") as SortKey) ?? DEFAULTS.sort,
    order: p.get("order") === "asc" ? "asc" : "desc",
    page: Math.max(1, Number(p.get("page")) || 1),
  };
}

function writeUrl(q: Query) {
  const p = new URLSearchParams();
  if (q.q) p.set("q", q.q);
  if (q.cycle !== LATEST) p.set("cycle", q.cycle || "all");
  if (q.exhibit) p.set("exhibit", q.exhibit);
  q.service.forEach((s) => p.append("service", s));
  if (q.account) p.set("account", q.account);
  if (q.budgetActivity) p.set("ba", q.budgetActivity);
  if (q.minM) p.set("min", q.minM);
  if (q.maxM) p.set("max", q.maxM);
  if (q.sort !== DEFAULTS.sort) p.set("sort", q.sort);
  if (q.order !== DEFAULTS.order) p.set("order", q.order);
  if (q.page > 1) p.set("page", String(q.page));
  const url = `/${p.toString() ? `?${p}` : ""}`;
  lastSearch = url;
  if (window.location.pathname === "/") window.history.replaceState(null, "", url);
}

// the search a program page's "back to search" link returns to
let lastSearch = "/";
export const lastSearchUrl = () => lastSearch;

export function SearchPage() {
  const [query, setQuery] = useState<Query>(readUrl);
  const [draftQ, setDraftQ] = useState(query.q);
  const [facets, setFacets] = useState<Facets | null>(null);
  const [data, setData] = useState<SearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const searchBox = useRef<HTMLInputElement>(null);

  const latest = facets?.cycles[0]?.budget_cycle;
  const cycle = query.cycle === LATEST ? latest ?? null : query.cycle;
  const effective = useMemo<Query | null>(
    () => (cycle === null ? null : { ...query, cycle }),
    [query, cycle],
  );
  const fiscalYear = facets?.cycles.find((c) => c.budget_cycle === cycle)?.fiscal_year ?? null;

  const update = (patch: Partial<Query>) =>
    setQuery((q) => ({ ...q, ...patch, page: "page" in patch ? patch.page! : 1 }));

  // debounce typing into the search box
  useEffect(() => {
    const t = window.setTimeout(() => {
      if (draftQ !== query.q) {
        update({ q: draftQ, sort: draftQ.trim() ? "relevance" : query.sort === "relevance" ? "budget" : query.sort });
      }
    }, 250);
    return () => window.clearTimeout(t);
  }, [draftQ]);

  // "/" focuses search
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "/" && document.activeElement?.tagName !== "INPUT") {
        e.preventDefault();
        searchBox.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    const ctl = new AbortController();
    fetchFacets(cycle ?? "", ctl.signal).then(setFacets).catch((e) => {
      if (e.name !== "AbortError") setError(e.message ?? String(e));
    });
    return () => ctl.abort();
  }, [cycle]);

  useEffect(() => {
    writeUrl(query);
    if (!effective) return;
    const ctl = new AbortController();
    setLoading(true);
    fetchSearch(effective, ctl.signal)
      .then((d) => { setData(d); setError(null); })
      .catch((e) => { if (e.name !== "AbortError") setError(e.message ?? String(e)); })
      .finally(() => { if (!ctl.signal.aborted) setLoading(false); });
    return () => ctl.abort();
  }, [effective]);

  const onSort = (key: SortKey) =>
    update(query.sort === key
      ? { sort: key, order: query.order === "desc" ? "asc" : "desc" }
      : { sort: key, order: key === "title" || key === "number" || key === "service" ? "asc" : "desc" });

  const reset = () => {
    setDraftQ("");
    setQuery({ ...DEFAULTS, sort: "budget" });
  };

  const from = data && data.total ? (data.page - 1) * data.page_size + 1 : 0;
  const to = data ? Math.min(data.total, data.page * data.page_size) : 0;
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <div className="app">
      <header className="masthead">
        <div className="brand">
          <h1>DoW Budget Search <Link className="nav-link" href="/watchlist">★ Team watchlist</Link></h1>
          <p>
            RDT&amp;E (R-1) and Procurement (P-1) line items, President's Budget
            {facets && facets.cycles.length > 0 &&
              ` ${facets.cycles[facets.cycles.length - 1].budget_cycle}–${facets.cycles[0].budget_cycle}`}
            . Amounts in $ millions; every row links to its source page.
          </p>
        </div>
        <div className="search">
          <svg aria-hidden="true" viewBox="0 0 20 20" className="search-icon">
            <circle cx="8.5" cy="8.5" r="5.5" fill="none" stroke="currentColor" strokeWidth="2" />
            <path d="M13 13l4.5 4.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
          <input
            ref={searchBox}
            type="search"
            value={draftQ}
            onChange={(e) => setDraftQ(e.target.value)}
            placeholder="Search programs, PE or BLI numbers, descriptions…  (press / )"
            aria-label="Search"
            autoFocus
          />
        </div>
      </header>

      <Filters facets={facets} query={query} cycle={cycle} latest={latest} onChange={update} onReset={reset} />

      <section className="summary" aria-live="polite">
        {error ? (
          <span className="error">{error.includes("private") ? error : `Could not load results: ${error}`}</span>
        ) : data ? (
          <>
            <span>
              <strong>{fmtCount(data.total)}</strong> {data.total === 1 ? "line" : "lines"}
              {fiscalYear && data.budget_year_total !== null && (
                <> · FY{fiscalYear} request <strong>{fmtMoney(data.budget_year_total)}</strong></>
              )}
              {loading && <span className="loading"> · updating…</span>}
            </span>
            {effective && data.total > 0 && (
              <a className="export" href={exportUrl(effective)} download>Export CSV</a>
            )}
          </>
        ) : (
          <span className="loading">Loading…</span>
        )}
      </section>

      {data && (
        <ResultsTable
          rows={data.results}
          fiscalYear={fiscalYear}
          showRelease={!cycle}
          sort={query.sort}
          order={query.order}
          searching={!!query.q.trim()}
          onSort={onSort}
        />
      )}

      {data && data.total > PAGE_SIZE && (
        <nav className="pager" aria-label="Pages">
          <button onClick={() => update({ page: query.page - 1 })} disabled={query.page <= 1}>← Previous</button>
          <span>{fmtCount(from)}–{fmtCount(to)} of {fmtCount(data.total)}</span>
          <button onClick={() => update({ page: query.page + 1 })} disabled={query.page >= pages}>Next →</button>
        </nav>
      )}
    </div>
  );
}
