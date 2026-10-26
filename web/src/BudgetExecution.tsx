import type { HistoryRow } from "./api";
import { fmtChange, fmtM, fmtPct } from "./format";

/** For each fiscal year: what was requested, what Congress enacted (or the CR level), and what
 * was actually spent, each from the release that reports it, with the differences. */
export function BudgetExecution({ history }: { history: HistoryRow[] }) {
  const years = [...new Set(history.map((h) => h.funds_fiscal_year))].sort();
  const pick = (fy: number, types: string[]) =>
    history.filter((h) => h.funds_fiscal_year === fy && types.includes(h.amount_type))
      .sort((a, b) => b.release_fiscal_year - a.release_fiscal_year)[0];
  const rows = years.map((fy) => ({
    fy, request: pick(fy, ["request"]), enacted: pick(fy, ["enacted", "cr"]), actual: pick(fy, ["actual"]),
  })).filter((r) => [r.request, r.enacted, r.actual].filter(Boolean).length >= 2);
  if (rows.length === 0) return null;
  const diff = (a?: HistoryRow, b?: HistoryRow) => {
    if (!a || !b) return <span className="muted">—</span>;
    const d = b.amount_thousands - a.amount_thousands;
    const p = a.amount_thousands ? d / Math.abs(a.amount_thousands) : null;
    const cls = d > 0 ? "up" : d < 0 ? "down" : "";
    return <span className={`change ${cls}`}>{fmtChange(d)}{p !== null && <small className="qty">{fmtPct(p)}</small>}</span>;
  };
  const cell = (h?: HistoryRow) => h
    ? <>{fmtM(h.amount_thousands)}<small className="qty">{h.amount_type === "cr" ? "CR level" : h.budget_cycle}</small></>
    : <span className="muted">not loaded</span>;
  return (
    <section className="panel">
      <h2>Request → enacted → actual <span className="muted small">($ millions)</span></h2>
      <p className="muted small">
        How each year's budget moved: the President's request, what Congress enacted (or the continuing-resolution
        level, when no full-year appropriation was reported), and the actual reported two years later. "Congress"
        is enacted minus request; "Execution" is actual minus enacted, above or below budget.
      </p>
      <div className="table-wrap">
        <table className="results matrix">
          <thead>
            <tr>
              <th className="plain">Fiscal year</th><th className="plain num">Requested</th><th className="plain num">Enacted</th>
              <th className="plain num">Congress</th><th className="plain num">Actual</th><th className="plain num">Execution</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.fy}>
                <td className="nowrap"><strong>FY{r.fy}</strong></td>
                <td className="num">{cell(r.request)}</td>
                <td className="num">{cell(r.enacted)}</td>
                <td className="num">{r.enacted?.amount_type === "cr"
                  ? <span className="muted" title="Under a continuing resolution: no full-year appropriation to compare">CR</span>
                  : diff(r.request, r.enacted)}</td>
                <td className="num">{cell(r.actual)}</td>
                <td className="num">{diff(r.enacted, r.actual)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
