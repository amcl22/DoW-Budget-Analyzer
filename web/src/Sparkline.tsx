import type { DashboardItem } from "./api";
import { fmtMoney } from "./format";

/** Trend of the best-available figure per fiscal year. One series, so no legend; the exact
 * numbers are on the program page and in each point's tooltip. */
export function Sparkline({ series, width = 132, height = 32 }: { series: DashboardItem["series"]; width?: number; height?: number }) {
  if (series.length < 2) return <span className="muted">—</span>;
  const vals = series.map((s) => s.amount_thousands);
  const lo = Math.min(0, ...vals);
  const hi = Math.max(...vals) || 1;
  const pad = 4;
  const x = (i: number) => pad + (i * (width - 2 * pad)) / (series.length - 1);
  const y = (v: number) => pad + ((hi - v) / (hi - lo)) * (height - 2 * pad);
  const firm = series.filter((s) => s.amount_type !== "estimate");
  const line = (pts: typeof series, offset: number) =>
    pts.map((s, i) => `${i ? "L" : "M"}${x(i + offset).toFixed(1)},${y(s.amount_thousands).toFixed(1)}`).join(" ");
  const est = series.slice(Math.max(0, firm.length - 1));
  const last = series[series.length - 1];
  return (
    <svg width={width} height={height} className="spark" role="img"
         aria-label={`FY${series[0].fiscal_year}–FY${last.fiscal_year}: ${fmtMoney(series[0].amount_thousands)} to ${fmtMoney(last.amount_thousands)}`}>
      <path d={line(firm, 0)} className="spark-line" />
      {est.length > 1 && <path d={line(est, firm.length - 1)} className="spark-line est" />}
      {series.map((s, i) => (
        <circle key={s.fiscal_year} cx={x(i)} cy={y(s.amount_thousands)} r={i === firm.length - 1 ? 4 : 7}
                className={i === firm.length - 1 ? "spark-dot" : "spark-hit"}>
          <title>{`FY${s.fiscal_year}: ${fmtMoney(s.amount_thousands)} (${s.amount_type})`}</title>
        </circle>
      ))}
    </svg>
  );
}
