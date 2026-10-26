import { useEffect, useState } from "react";
import type { SeriesPoint } from "./api";
import { DataAsOf } from "./DataAsOf";
import { FLAG_THRESHOLD, fmtChange, fmtMoney, fmtPct } from "./format";
import { FundingChart } from "./FundingChart";
import { Link, programPath } from "./router";
import { lastSearchUrl } from "./SearchPage";

interface Chg { from: number; to: number; change: number; change_pct: number | null }
interface Group extends Chg { key: string; label: string; lines: number }
interface Prog extends Chg { program_key: string; title: string; service: string; exhibit: string }
interface ExecRow { program_key?: string; title?: string; label?: string; service?: string; requested: number; enacted: number; actual: number; congress: Chg | null; execution: Chg | null }
interface AI { text?: string; model?: string; verified?: number; withheld?: string }
interface Trends { kind: "trends"; cycle: string; fiscal_year: number; total: Chg; services: Group[]; exhibits: Group[];
  growing_sectors: Group[]; shrinking_sectors: Group[]; top_increases: Prog[]; top_decreases: Prog[]; new_starts: Prog[];
  series: { fiscal_year: number; amount_thousands: number; amount_type: SeriesPoint["amount_type"]; source_cycle: string }[];
  takeaways: string[]; ai?: AI; ai_available: boolean }
interface Execution { kind: "execution"; fiscal_year: number; releases: Record<string, string>; enacted_kind: "cr" | "enacted";
  available: Record<string, boolean>; programs_compared: number; total: ExecRow; services: ExecRow[];
  congress_adds: ExecRow[]; congress_cuts: ExecRow[]; over_enacted: ExecRow[]; under_enacted: ExecRow[];
  takeaways: string[]; ai?: AI; ai_available: boolean }

const SERVICES = ["Air Force", "Army", "Defense-Wide", "Navy", "Space Force"];

function Kpi({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return <div className="op-kpi"><span>{label}</span><strong>{value}</strong>{sub && <small>{sub}</small>}</div>;
}

function Change({ c }: { c: Chg | null }) {
  if (!c) return <span className="muted">—</span>;
  const cls = c.change > 0 ? "up" : c.change < 0 ? "down" : "";
  return <span className={`change ${cls}`}>{fmtChange(c.change)} <small>{c.change_pct === null ? "new" : fmtPct(c.change_pct)}</small></span>;
}

function GroupTable({ title, rows, fy }: { title: string; rows: Group[]; fy: number }) {
  return (
    <div>
      <h3>{title}</h3>
      {rows.length === 0 ? <p className="muted small">None.</p> : (
        <table className="op-table"><thead><tr><th>Sector</th><th className="num">FY{fy - 1}</th><th className="num">FY{fy}</th><th className="num">Change</th></tr></thead>
          <tbody>{rows.map((g) => (
            <tr key={g.key}><td>{g.label}</td><td className="num">{fmtMoney(g.from)}</td><td className="num">{fmtMoney(g.to)}</td><td className="num"><Change c={g} /></td></tr>))}</tbody></table>
      )}
    </div>
  );
}

function ProgTable({ title, rows, fy, newStarts }: { title: string; rows: Prog[]; fy: number; newStarts?: boolean }) {
  return (
    <div>
      <h3>{title}</h3>
      {rows.length === 0 ? <p className="muted small">None.</p> : (
        <table className="op-table"><thead><tr><th>Program</th>{!newStarts && <th className="num">FY{fy - 1}</th>}<th className="num">FY{fy}</th>{!newStarts && <th className="num">Change</th>}</tr></thead>
          <tbody>{rows.map((p) => (
            <tr key={p.program_key}><td><Link href={programPath(p.program_key)}>{p.title}</Link> <span className="muted">{p.service} · {p.exhibit}</span></td>
              {!newStarts && <td className="num">{fmtMoney(p.from)}</td>}<td className="num">{fmtMoney(p.to)}</td>{!newStarts && <td className="num"><Change c={p} /></td>}</tr>))}</tbody></table>
      )}
    </div>
  );
}

function ExecTable({ title, rows, which }: { title: string; rows: ExecRow[]; which: "congress" | "execution" }) {
  const live = rows.filter((r) => r[which] && r[which]!.change !== 0).slice(0, 6);
  return (
    <div>
      <h3>{title}</h3>
      {live.length === 0 ? <p className="muted small">None.</p> : (
        <table className="op-table"><thead><tr><th>Program</th><th className="num">{which === "congress" ? "Requested" : "Enacted"}</th>
          <th className="num">{which === "congress" ? "Enacted" : "Actual"}</th><th className="num">Difference</th></tr></thead>
          <tbody>{live.map((r) => (
            <tr key={r.program_key}><td><Link href={programPath(r.program_key!)}>{r.title}</Link> <span className="muted">{r.service}</span></td>
              <td className="num">{fmtMoney(r[which]!.from)}</td><td className="num">{fmtMoney(r[which]!.to)}</td><td className="num"><Change c={r[which]} /></td></tr>))}</tbody></table>
      )}
    </div>
  );
}

function Narrative({ ai, takeaways }: { ai?: AI; takeaways: string[] }) {
  return (
    <section className="op-text">
      <h3>Key takeaways <span className="muted small">computed from the data</span></h3>
      <ul>{takeaways.map((t, i) => <li key={i}>{t}</li>)}</ul>
      {ai?.text && (
        <div className="op-ai">
          <strong>AI-written summary</strong> <span className="muted small">
            ({ai.model ?? "Claude"}; {ai.verified} figure{ai.verified === 1 ? "" : "s"}, each checked against the data above. Review before relying on it.)</span>
          <p>{ai.text}</p>
        </div>
      )}
      {ai?.withheld && <p className="muted small">{ai.withheld}</p>}
    </section>
  );
}

export function SummaryPage() {
  const params = new URLSearchParams(window.location.search);
  const [kind, setKind] = useState<"trends" | "execution">(params.get("kind") === "execution" ? "execution" : "trends");
  const [fy, setFy] = useState(Number(params.get("fy")) || 2025);
  const [exhibit, setExhibit] = useState(params.get("exhibit") ?? "");
  const [service, setService] = useState(params.get("service") ?? "");
  const [ai, setAi] = useState(false);
  const [data, setData] = useState<Trends | Execution | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    document.title = "1-page summary · DoW Budget Search";
    const p = new URLSearchParams({ kind });
    if (kind === "execution") p.set("fy", String(fy));
    if (exhibit) p.set("exhibit", exhibit);
    if (service) p.set("service", service);
    window.history.replaceState(null, "", `/summary?${p}`);
    if (ai) p.set("ai", "1");
    const ctl = new AbortController();
    setLoading(true);
    fetch(`/api/summary?${p}`, { signal: ctl.signal })
      .then(async (r) => { if (!r.ok) throw new Error(`${r.status} ${r.statusText}`); setData(await r.json()); setError(null); })
      .catch((e) => { if (e.name !== "AbortError") setError(String(e.message ?? e)); })
      .finally(() => { if (!ctl.signal.aborted) setLoading(false); });
    return () => { ctl.abort(); document.title = "DoW Budget Search"; };
  }, [kind, fy, exhibit, service, ai]);

  const scope = [exhibit === "R-1" ? "RDT&E (R-1)" : exhibit === "P-1" ? "Procurement (P-1)" : "RDT&E and procurement", service || "all services"].join(" · ");

  return (
    <div className="app summary-page">
      <div className="no-print">
        <nav className="crumbs">
          <Link className="back" href={lastSearchUrl()}>← Back to search</Link>
          <Link className="back" href="/validation">Validation Studio</Link>
        </nav>
        <section className="panel op-controls">
          <div className="segmented" role="radiogroup" aria-label="Summary type">
            <button className={kind === "trends" ? "on" : ""} onClick={() => setKind("trends")}>Funding trends</button>
            <button className={kind === "execution" ? "on" : ""} onClick={() => setKind("execution")}>Budget execution</button>
          </div>
          {kind === "execution" && (
            <label className="field"><span>Fiscal year</span>
              <select value={fy} onChange={(e) => setFy(Number(e.target.value))}>
                <option value={2025}>FY2025 (request, enacted, actual)</option>
                <option value={2024}>FY2024 (request, CR level, actual)</option>
                <option value={2026}>FY2026 (request, enacted; no actual yet)</option>
              </select></label>
          )}
          <label className="field"><span>Exhibit</span>
            <select value={exhibit} onChange={(e) => setExhibit(e.target.value)}>
              <option value="">RDT&amp;E and procurement</option><option value="R-1">RDT&amp;E (R-1)</option><option value="P-1">Procurement (P-1)</option>
            </select></label>
          <label className="field"><span>Service</span>
            <select value={service} onChange={(e) => setService(e.target.value)}>
              <option value="">All services</option>{SERVICES.map((s) => <option key={s}>{s}</option>)}
            </select></label>
          {data?.ai_available && (
            <label className="vs-check"><input type="checkbox" checked={ai} onChange={(e) => setAi(e.target.checked)} /> Add AI-written narrative (figures checked)</label>
          )}
          <button className="primary-btn" onClick={() => window.print()}>Print / save as PDF</button>
        </section>
      </div>

      {error && <div className="empty"><p>Could not load: {error}</p></div>}
      {!data && !error && <p className="loading">Loading…</p>}
      {data && (
        <article className={`one-pager ${loading ? "stale" : ""}`}>
          <header className="op-head">
            <div>
              <h1>{data.kind === "trends" ? `FY${data.fiscal_year} funding trends` : `FY${data.fiscal_year} budget execution`}</h1>
              <p className="muted small">{scope} · {data.kind === "trends" ? `${data.cycle} request vs FY${data.fiscal_year - 1}`
                : `request ${data.releases.requested}, ${data.enacted_kind === "cr" ? "CR level" : "enacted"} ${data.releases.enacted}, actual ${data.releases.actual}`}</p>
            </div>
            <p className="muted small op-asof"><DataAsOf /></p>
          </header>

          {data.kind === "trends" ? (
            <>
              <section className="op-kpis">
                <Kpi label={`FY${data.fiscal_year} request`} value={fmtMoney(data.total.to)} />
                <Kpi label={`FY${data.fiscal_year - 1}`} value={fmtMoney(data.total.from)} />
                <Kpi label="Change" value={fmtChange(data.total.change)} sub={fmtPct(data.total.change_pct)} />
                {data.services.slice(0, 2).map((s) => <Kpi key={s.key} label={s.label} value={fmtMoney(s.to)} sub={`${fmtPct(s.change_pct)} vs FY${data.fiscal_year - 1}`} />)}
              </section>
              <Narrative ai={data.ai} takeaways={data.takeaways} />
              <section className="op-chart">
                <h3>Funding by fiscal year <span className="muted small">best available figure: actual, else enacted, else request</span></h3>
                <FundingChart height={150} budgetYear={data.fiscal_year} series={data.series.map((s, i, all) => {
                  const prev = all[i - 1];
                  const change_pct = prev && prev.amount_thousands ? (s.amount_thousands - prev.amount_thousands) / Math.abs(prev.amount_thousands) : null;
                  return { ...s, quantity: null, change_pct, flagged: change_pct !== null && Math.abs(change_pct) >= FLAG_THRESHOLD };
                })} />
              </section>
              <section className="op-cols">
                <GroupTable title="Growing sectors" rows={data.growing_sectors} fy={data.fiscal_year} />
                <GroupTable title="Shrinking sectors" rows={data.shrinking_sectors} fy={data.fiscal_year} />
                <ProgTable title="Largest program increases" rows={data.top_increases.slice(0, 6)} fy={data.fiscal_year} />
                <ProgTable title="Largest program decreases" rows={data.top_decreases.slice(0, 6)} fy={data.fiscal_year} />
              </section>
              <section className="op-cols one">
                <ProgTable title="Largest new starts" rows={data.new_starts} fy={data.fiscal_year} newStarts />
              </section>
            </>
          ) : (
            <>
              <section className="op-kpis">
                <Kpi label="Requested" value={fmtMoney(data.total.requested)} sub={data.releases.requested} />
                <Kpi label={data.enacted_kind === "cr" ? "CR level" : "Enacted"} value={fmtMoney(data.total.enacted)} sub={data.releases.enacted} />
                <Kpi label="Actual" value={data.available.actual ? fmtMoney(data.total.actual) : "not yet"} sub={data.releases.actual} />
                <Kpi label="Congress vs request" value={data.enacted_kind === "cr" ? "CR" : data.total.congress ? fmtChange(data.total.congress.change) : "—"}
                     sub={data.enacted_kind === "cr" ? "no full-year appropriation" : data.total.congress ? fmtPct(data.total.congress.change_pct) : ""} />
                <Kpi label="Actual vs enacted" value={data.total.execution ? fmtChange(data.total.execution.change) : "—"}
                     sub={data.total.execution ? fmtPct(data.total.execution.change_pct) : "no actual yet"} />
              </section>
              <Narrative ai={data.ai} takeaways={data.takeaways} />
              <section className="op-cols one">
                <div>
                  <h3>By service</h3>
                  <table className="op-table"><thead><tr><th>Service</th><th className="num">Requested</th><th className="num">{data.enacted_kind === "cr" ? "CR level" : "Enacted"}</th>
                    <th className="num">Congress</th><th className="num">Actual</th><th className="num">Above / below budget</th></tr></thead>
                    <tbody>{data.services.map((s) => (
                      <tr key={s.label}><td>{s.label}</td><td className="num">{fmtMoney(s.requested)}</td><td className="num">{fmtMoney(s.enacted)}</td>
                        <td className="num">{data.enacted_kind === "cr" ? <span className="muted">CR</span> : <Change c={s.congress} />}</td>
                        <td className="num">{data.available.actual ? fmtMoney(s.actual) : "—"}</td><td className="num"><Change c={s.execution} /></td></tr>))}</tbody></table>
                </div>
              </section>
              <section className="op-cols">
                {data.available.actual && <ExecTable title="Furthest below budget (actual < enacted)" rows={data.under_enacted} which="execution" />}
                {data.available.actual && <ExecTable title="Furthest above budget (actual > enacted)" rows={data.over_enacted} which="execution" />}
                {data.enacted_kind !== "cr" && <ExecTable title="Largest congressional adds" rows={data.congress_adds} which="congress" />}
                {data.enacted_kind !== "cr" && <ExecTable title="Largest congressional cuts" rows={data.congress_cuts} which="congress" />}
              </section>
            </>
          )}
          <footer className="op-foot muted small">
            Source: President's Budget R-1 and P-1 exhibits (DoW Comptroller), exhibit-title lines only. Amounts in dollars,
            published in $ thousands. FY2026–27 include mandatory (reconciliation) funding. “Actual” is the prior-year figure in the
            release two years later. A program that moved accounts shows as a decrease in one place and an increase in another.
            Every program links to its source pages in DoW Budget Search. Figures are computed by code; no AI was used to compute them.
          </footer>
        </article>
      )}
    </div>
  );
}
