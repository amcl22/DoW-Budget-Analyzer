// Display formatting. Source amounts are $ thousands.

const millions = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const whole = new Intl.NumberFormat("en-US");

/** $K -> "1,234.5" ($ millions, the table's unit). */
export function fmtM(thousands: number | null | undefined): string {
  if (thousands === null || thousands === undefined) return "—";
  return millions.format(thousands / 1000);
}

/** $K -> "$12.3B" / "$456.7M" for totals. */
export function fmtMoney(thousands: number | null | undefined): string {
  if (thousands === null || thousands === undefined) return "—";
  const abs = Math.abs(thousands);
  if (abs >= 1_000_000) return `$${(thousands / 1_000_000).toFixed(abs >= 10_000_000 ? 1 : 2)}B`;
  return `$${millions.format(thousands / 1000)}M`;
}

export function fmtCount(n: number): string {
  return whole.format(n);
}

export function fmtPct(fraction: number | null): string {
  if (fraction === null) return "—";
  const pct = fraction * 100;
  const sign = pct > 0 ? "+" : "";
  return `${sign}${Math.abs(pct) >= 100 ? pct.toFixed(0) : pct.toFixed(1)}%`;
}

/** Year-over-year change worth flagging (spec 4.2: e.g. ±20%). */
export const FLAG_THRESHOLD = 0.2;

/** Split a snippet at the server's \u0002/\u0003 markers, for safe highlighting. */
export function snippetParts(snippet: string): { text: string; mark: boolean }[] {
  const parts: { text: string; mark: boolean }[] = [];
  const re = /\u0002([^\u0003]*)\u0003/g;
  let last = 0;
  for (let m = re.exec(snippet); m; m = re.exec(snippet)) {
    if (m.index > last) parts.push({ text: snippet.slice(last, m.index), mark: false });
    parts.push({ text: m[1], mark: true });
    last = m.index + m[0].length;
  }
  if (last < snippet.length) parts.push({ text: snippet.slice(last), mark: false });
  return parts;
}
