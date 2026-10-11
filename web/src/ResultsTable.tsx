import type { LineItem, SortKey } from "./api";
import { changeKind, FLAG_THRESHOLD, fmtChange, fmtCount, fmtM, fmtMoney, fmtPct, shortAccountTitle, snippetParts } from "./format";
import { Link, programPath } from "./router";
import { Sparkline } from "./Sparkline";

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

/** % change, except where a percentage would mislead: a new start (nothing this year), a line
 * that ends, or a tiny current-year base, where the $ change column tells the story. */
function Change({ row }: { row: LineItem }) {
  const kind = changeKind(row.current_year_amount, row.budget_year_amount);
  if (kind === "none") return <td className="num muted">—</td>;
  if (kind === "new") {
    return <td className="num"><span className="badge new" title="No funding in the current year: a new start">New start</span></td>;
  }
  if (kind === "ending") {
    return <td className="num"><span className="badge ending" title="No funding requested in the budget year">Ending</span></td>;
  }
  const pct = row.change_pct ?? 0;
  const cls = pct > 0 ? "up" : pct < 0 ? "down" : "";
  if (kind === "small-base") {
    return (
      <td className={`num change ${cls}`}>
        <span className="small-base" title={`The current year is only ${fmtMoney(row.current_year_amount)}, so the percentage is large; compare the $ change`}>
          {fmtPct(pct)}<small>small base</small>
        </span>
      </td>
    );
  }
  const flagged = Math.abs(pct) >= FLAG_THRESHOLD;
  return (
    <td className={`num change ${cls}`}>
      {flagged ? <span className={`badge ${cls}`} title="Change of 20% or more">{fmtPct(pct)}</span> : fmtPct(pct)}
    </td>
  );
}

function DollarChange({ row }: { row: LineItem }) {
  if (changeKind(row.current_year_amount, row.budget_year_amount) === "none") return <td className="num muted">—</td>;
  const v = row.change_amount;
  return <td className={`num ${v > 0 ? "up" : v < 0 ? "down" : "muted"}`}>{fmtChange(v)}</td>;
}

/** Prior -> current -> budget year, the three figures in the row. */
function RowTrend({ row }: { row: LineItem }) {
  const pts = [
    { fiscal_year: row.fiscal_year - 2, amount_thousands: row.prior_year_amount, amount_type: "actual" as const },
    { fiscal_year: row.fiscal_year - 1, amount_thousands: row.current_year_amount, amount_type: "enacted" as const },
    { fiscal_year: row.fiscal_year, amount_thousands: row.budget_year_amount, amount_type: "request" as const },
  ].filter((p): p is { fiscal_year: number; amount_thousands: number; amount_type: "actual" | "enacted" | "request" } =>
    p.amount_thousands !== null);
  return <td className="trend"><Sparkline series={pts} width={72} height={24} /></td>;
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
            <SortHeader label="$ change" sub="vs. current" k="change_amount" numeric {...h} />
            <SortHeader label="% change" k="change" numeric {...h} />
            <th className="plain trend" title="Prior, current and budget year">Trend</th>
            <th className="source">Source</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td className="program">
                <div className="title">
                  <Link href={programPath(r.program_key)}>{r.program_title}</Link>
                  {r.watched && <span className="watched-dot" title="On the team watchlist" aria-label="Watched">★</span>}
                </div>
                <div className="meta">
                  <span className="mono">{r.program_element ?? r.line_item_number}</span>
                  <span className={`tag ${r.exhibit_type === "R-1" ? "rdte" : "proc"}`}>{r.exhibit_type}</span>
                  <span title={r.appropriation_title}>{r.appropriation_account} · {shortAccountTitle(r.appropriation_title)}</span>
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
              <DollarChange row={r} />
              <Change row={r} />
              <RowTrend row={r} />
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
