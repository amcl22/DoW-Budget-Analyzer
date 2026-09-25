import type { LineItem, SortKey } from "./api";
import { FLAG_THRESHOLD, fmtCount, fmtM, fmtPct, snippetParts } from "./format";

interface Props {
  rows: LineItem[];
  fiscalYear: number | null;     // null when showing several releases
  showRelease: boolean;
  sort: SortKey;
  order: "asc" | "desc";
  searching: boolean;
  onSort: (key: SortKey) => void;
}

function SortHeader({ label, sub, k, sort, order, onSort, numeric }: {
  label: string; sub?: string; k: SortKey; sort: SortKey; order: "asc" | "desc";
  onSort: (k: SortKey) => void; numeric?: boolean;
}) {
  const active = sort === k;
  return (
    <th
      className={numeric ? "num" : undefined}
      aria-sort={active ? (order === "asc" ? "ascending" : "descending") : "none"}
    >
      <button className={active ? "sort on" : "sort"} onClick={() => onSort(k)}>
        <span>{label}{sub && <small>{sub}</small>}</span>
        <span className="arrow" aria-hidden="true">{active ? (order === "asc" ? "▲" : "▼") : "↕"}</span>
      </button>
    </th>
  );
}

function Amount({ value, qty }: { value: number | null; qty: number | null }) {
  return (
    <td className="num">
      {fmtM(value)}
      {qty ? <small className="qty">{fmtCount(qty)} qty</small> : null}
    </td>
  );
}

function Change({ row }: { row: LineItem }) {
  if (row.change_pct === null) {
    return <td className="num muted">{row.budget_year_amount ? <span className="badge new">new</span> : "—"}</td>;
  }
  const flagged = Math.abs(row.change_pct) >= FLAG_THRESHOLD;
  const cls = row.change_pct > 0 ? "up" : row.change_pct < 0 ? "down" : "";
  return (
    <td className={`num change ${cls}`}>
      {flagged ? <span className={`badge ${cls}`} title="Change of 20% or more">{fmtPct(row.change_pct)}</span> : fmtPct(row.change_pct)}
    </td>
  );
}

export function ResultsTable({ rows, fiscalYear, showRelease, sort, order, searching, onSort }: Props) {
  if (rows.length === 0) {
    return (
      <div className="empty">
        <p>No line items match.</p>
        <p className="muted">Try fewer words, a PE or BLI number, or clearing filters.</p>
      </div>
    );
  }
  const fy = (offset: number) => (fiscalYear ? `FY${fiscalYear - offset}` : ["Budget yr", "Current yr", "Prior yr"][offset]);
  const h = { sort, order, onSort };

  return (
    <div className="table-wrap">
      <table className="results">
        <thead>
          <tr>
            <SortHeader label="Program" k={searching ? "relevance" : "title"} {...h} />
            {showRelease && <SortHeader label="Release" k="cycle" {...h} />}
            <SortHeader label="Service" k="service" {...h} />
            <SortHeader label={fy(2)} sub={fiscalYear ? "actual" : undefined} k="prior" numeric {...h} />
            <SortHeader label={fy(1)} sub={fiscalYear ? "current" : undefined} k="current" numeric {...h} />
            <SortHeader label={fy(0)} sub={fiscalYear ? "request" : undefined} k="budget" numeric {...h} />
            <SortHeader label="Change" sub="vs. current" k="change" numeric {...h} />
            <th className="source">Source</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td className="program">
                <div className="title">{r.program_title}</div>
                <div className="meta">
                  <span className="mono">{r.program_element ?? r.line_item_number}</span>
                  <span className={`tag ${r.exhibit_type === "R-1" ? "rdte" : "proc"}`}>{r.exhibit_type}</span>
                  <span title={r.appropriation_title}>{r.appropriation_account}</span>
                  <span>BA {r.budget_activity}{r.budget_activity_title ? ` · ${r.budget_activity_title}` : ""}</span>
                  {r.organization && !["A", "N", "F"].includes(r.organization) && <span>{r.organization}</span>}
                  {!r.include_in_toa && <span className="muted" title="Listed outside the exhibit's title total">not in title</span>}
                </div>
                {r.snippet && (
                  <p className="snippet">
                    …{snippetParts(r.snippet).map((p, i) => (p.mark ? <mark key={i}>{p.text}</mark> : <span key={i}>{p.text}</span>))}
                    …
                  </p>
                )}
              </td>
              {showRelease && <td className="nowrap">{r.budget_cycle}</td>}
              <td className="nowrap">{r.service_branch}</td>
              <Amount value={r.prior_year_amount} qty={r.prior_year_quantity} />
              <Amount value={r.current_year_amount} qty={r.current_year_quantity} />
              <Amount value={r.budget_year_amount} qty={r.budget_year_quantity} />
              <Change row={r} />
              <td className="source">
                {r.source_pdf_link ? (
                  <a href={r.source_pdf_link} target="_blank" rel="noopener noreferrer"
                     title={r.source_kind === "r2_justification" ? "Open the R-2 justification page" : `Open the ${r.exhibit_type} page`}>
                    p.{r.source_page_number}<span aria-hidden="true"> ↗</span>
                    <small>{r.source_kind === "r2_justification" ? "R-2" : r.exhibit_type}</small>
                  </a>
                ) : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
