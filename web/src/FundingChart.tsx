import { useEffect, useRef, useState } from "react";
import type { AmountType, SeriesPoint } from "./api";
import { fmtCount, fmtMoney, fmtPct } from "./format";

// One hue, darker = firmer figure. Validated as an ordinal ramp against both app surfaces
// (dataviz validate_palette.js --ordinal: monotone lightness, visible step gaps, light end >= 2:1).
export const TYPE_LABEL: Record<AmountType, string> = {
  actual: "Actual",
  enacted: "Enacted",
  cr: "CR level",
  request: "Request",
  estimate: "Out-year estimate",
};
const TYPE_ORDER: AmountType[] = ["actual", "enacted", "cr", "request", "estimate"];
const typeVar = (t: AmountType) => `var(--amt-${t === "cr" ? "enacted" : t})`;

const HEIGHT = 260;
const M = { top: 22, right: 12, bottom: 30, left: 60 };

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const mag = 10 ** Math.floor(Math.log10(v));
  const step = [1, 2, 2.5, 5, 10].find((s) => s * mag * 4 >= v) ?? 10;
  return step * mag * 4;
}

function useWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(640);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(280, e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

/** Bar with only its data end rounded (4px), anchored square at the baseline. */
function barPath(x: number, w: number, y0: number, y1: number): string {
  const up = y1 < y0;
  const h = Math.abs(y1 - y0);
  const r = Math.min(4, w / 2, h);
  if (up) {
    return `M${x},${y0} V${y1 + r} Q${x},${y1} ${x + r},${y1} H${x + w - r} Q${x + w},${y1} ${x + w},${y1 + r} V${y0} Z`;
  }
  return `M${x},${y0} V${y1 - r} Q${x},${y1} ${x + r},${y1} H${x + w - r} Q${x + w},${y1} ${x + w},${y1 - r} V${y0} Z`;
}

export function FundingChart({ series, budgetYear }: { series: SeriesPoint[]; budgetYear: number | null }) {
  const [wrap, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  if (series.length === 0) return <p className="muted">No funding reported.</p>;

  const values = series.map((s) => s.amount_thousands);
  const top = niceMax(Math.max(...values, 0));
  const bottom = Math.min(0, ...values) < 0 ? -niceMax(-Math.min(...values)) : 0;
  const plotW = width - M.left - M.right;
  const plotH = HEIGHT - M.top - M.bottom;
  const y = (v: number) => M.top + ((top - v) / (top - bottom)) * plotH;
  const band = plotW / series.length;
  const barW = Math.min(56, band * 0.62);
  const ticks = [0, 1, 2, 3, 4].map((i) => bottom + ((top - bottom) * i) / 4);
  const present = TYPE_ORDER.filter((t) => series.some((s) => s.amount_type === t));
  const h = hover !== null ? series[hover] : null;

  return (
    <div className="chart" ref={wrap}>
      <ul className="legend" aria-label="Legend">
        {present.map((t) => (
          <li key={t}>
            <svg width="14" height="14" aria-hidden="true">
              <rect width="14" height="14" rx="3" fill={typeVar(t)} />
              {t === "estimate" && <rect width="14" height="14" rx="3" fill="url(#hatch)" />}
            </svg>
            {TYPE_LABEL[t]}
          </li>
        ))}
      </ul>
      <svg width={width} height={HEIGHT} role="img"
           aria-label={`Funding by fiscal year, ${series[0].fiscal_year} to ${series[series.length - 1].fiscal_year}`}>
        <defs>
          <pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="6" stroke="var(--amt-hatch)" strokeWidth="2" />
          </pattern>
        </defs>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={M.left} x2={width - M.right} y1={y(t)} y2={y(t)}
                  className={t === 0 ? "axis-zero" : "grid"} />
            <text x={M.left - 8} y={y(t)} dy="0.32em" textAnchor="end" className="tick">
              {fmtMoney(t).replace(".0M", "M")}
            </text>
          </g>
        ))}
        {series.map((s, i) => {
          const cx = M.left + band * i + band / 2;
          const x = cx - barW / 2;
          const d = barPath(x, barW, y(0), y(s.amount_thousands));
          const isBudgetYear = s.fiscal_year === budgetYear;
          return (
            <g key={s.fiscal_year}
               onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
               onFocus={() => setHover(i)} onBlur={() => setHover(null)}
               tabIndex={0}
               aria-label={`FY${s.fiscal_year}: ${fmtMoney(s.amount_thousands)}, ${TYPE_LABEL[s.amount_type]}, from ${s.source_cycle}`}
               className="bar">
              <rect x={cx - band / 2} y={M.top} width={band} height={plotH} className={hover === i ? "hit on" : "hit"} />
              {s.amount_thousands !== 0 && <path d={d} fill={typeVar(s.amount_type)} />}
              {s.amount_type === "estimate" && s.amount_thousands !== 0 && <path d={d} fill="url(#hatch)" />}
              {isBudgetYear && (
                <text x={cx} y={y(Math.max(0, s.amount_thousands)) - 6} textAnchor="middle" className="direct">
                  {fmtMoney(s.amount_thousands)}
                </text>
              )}
              <text x={cx} y={HEIGHT - 10} textAnchor="middle" className={isBudgetYear ? "tick strong" : "tick"}>
                FY{String(s.fiscal_year).slice(2)}
              </text>
            </g>
          );
        })}
      </svg>
      {h && hover !== null && (
        <div className="tooltip" style={{
          left: Math.min(width - 190, Math.max(0, M.left + band * hover + band / 2 - 95)),
          top: Math.max(0, y(Math.max(0, h.amount_thousands)) - 86),
        }}>
          <strong>FY{h.fiscal_year}</strong> · {TYPE_LABEL[h.amount_type]}
          <div className="tt-value">{fmtMoney(h.amount_thousands)}</div>
          {h.quantity ? <div>{fmtCount(h.quantity)} units</div> : null}
          <div className="muted">
            {h.change_pct !== null && <>{fmtPct(h.change_pct)} vs FY{h.fiscal_year - 1} · </>}from {h.source_cycle}
          </div>
        </div>
      )}
    </div>
  );
}
