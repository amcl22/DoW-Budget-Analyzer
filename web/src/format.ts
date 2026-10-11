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

/** $K -> "+$12.3M" / "−$1.2B": a signed change. */
export function fmtChange(thousands: number | null | undefined): string {
  if (thousands === null || thousands === undefined) return "—";
  if (thousands === 0) return "$0";
  return `${thousands > 0 ? "+" : "−"}${fmtMoney(Math.abs(thousands))}`;
}

/** Below this current-year amount ($K), a percentage change says little: show the $ change. */
export const SMALL_BASE = 5000;

/** How a line's budget year compares with its current year. */
export type ChangeKind = "new" | "ending" | "small-base" | "normal" | "none";

export function changeKind(current: number | null, budget: number | null): ChangeKind {
  const cy = current ?? 0;
  const by = budget ?? 0;
  if (cy === 0 && by === 0) return "none";
  if (cy <= 0 && by > 0) return "new";
  if (cy > 0 && by === 0) return "ending";
  if (cy > 0 && cy < SMALL_BASE) return "small-base";
  return "normal";
}

/** "Research, Development, Test and Evaluation, Air Force" -> "RDT&E, Air Force". */
export function shortAccountTitle(title: string | null | undefined): string {
  if (!title) return "";
  return title
    .replace(/Research,? Development,? Test,? (and|&) Evaluation/i, "RDT&E")
    .replace(/Operation(s)? (and|&) Maintenance/i, "O&M");
}

export function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
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
