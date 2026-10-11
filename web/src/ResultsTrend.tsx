import type { SearchResponse, SeriesPoint } from "./api";
import { FundingChart } from "./FundingChart";
import { FLAG_THRESHOLD } from "./format";

/** Total funding by fiscal year for every line matching the search, across all releases: each
 * year's best available figure (actual once reported, else enacted, else the request), taken
 * from the newest release that reports it. */
export function resultsSeries(byCycle: SearchResponse["totals_by_cycle"]): SeriesPoint[] {
  const best = new Map<number, SeriesPoint>();
  for (const c of [...byCycle].sort((a, b) => a.fiscal_year - b.fiscal_year)) {
    const put = (fy: number, v: number | null, type: SeriesPoint["amount_type"]) => {
      if (v !== null) best.set(fy, { fiscal_year: fy, amount_thousands: v, quantity: null, amount_type: type,
                                     source_cycle: c.budget_cycle, change_pct: null, flagged: false });
    };
    put(c.fiscal_year - 2, c.prior_year_total, "actual");
    put(c.fiscal_year - 1, c.current_year_total, "enacted");
    put(c.fiscal_year, c.budget_year_total, "request");
  }
  const series = [...best.values()].sort((a, b) => a.fiscal_year - b.fiscal_year);
  series.forEach((s, i) => {
    const prev = series[i - 1];
    if (prev && prev.amount_thousands) {
      s.change_pct = (s.amount_thousands - prev.amount_thousands) / Math.abs(prev.amount_thousands);
      s.flagged = Math.abs(s.change_pct) >= FLAG_THRESHOLD;
    }
  });
  return series;
}

export function ResultsTrend({ data, budgetYear }: { data: SearchResponse; budgetYear: number | null }) {
  const series = resultsSeries(data.totals_by_cycle);
  if (series.length < 2) return null;
  return (
    <details className="panel results-trend" open>
      <summary><h2>Funding trend for these results</h2></summary>
      <p className="muted small">
        Total of every line matching your search and filters, across all budget releases: each year shows the
        actual once reported, else the enacted amount, else the request. Programs that were renamed or
        restructured between releases can shift the totals.
      </p>
      <FundingChart series={series} budgetYear={budgetYear} height={200} />
    </details>
  );
}
