import { useEffect, useMemo, useState } from "react";
import { fetchProgram, reportIssue, setWatch, setWatcherName, watcherName, type Explanation, type HistoryRow, type ProgramDetail } from "./api";
import { BudgetExecution } from "./BudgetExecution";
import { FundingChart, TYPE_LABEL } from "./FundingChart";
import { changeKind, fmtChange, fmtCount, fmtDate, fmtM, fmtMoney, fmtPct, SMALL_BASE } from "./format";
import { AskLink } from "./AskLink";
import { Link } from "./router";
import { lastSearchUrl } from "./SearchPage";

function ChangeBadge({ pct, threshold, base }: { pct: number | null; threshold: number; base?: number | null }) {
  if (pct === null) return <span className="muted">—</span>;
  if (base !== undefined && base !== null && Math.abs(base) < SMALL_BASE) {
    return <span className="change small-base" title={`From only ${fmtMoney(base)}: compare the dollar amounts`}>{fmtPct(pct)}<small>small base</small></span>;
  }
  const cls = pct > 0 ? "up" : pct < 0 ? "down" : "";
  return Math.abs(pct) >= threshold
    ? <span className={`badge ${cls}`} title={`Change of ${Math.round(threshold * 100)}% or more`}>{fmtPct(pct)}</span>
    : <span className={`change ${cls}`}>{fmtPct(pct)}</span>;
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: React.ReactNode }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {sub && <span className="stat-sub">{sub}</span>}
    </div>
  );
}

/** The government's own reasons, quoted from the R-2 justification book, newest release first. */
function WhyChanged({ items, cycle, exhibitFamily, service }: {
  items: Explanation[]; cycle: string | null; exhibitFamily: string; service: string | undefined;
}) {
  const latest = items.filter((e) => e.budget_cycle === cycle);
  const earlier = items.filter((e) => e.budget_cycle !== cycle);
  const list = (rows: Explanation[]) => (
    <ul className="why-list">
      {rows.map((e, i) => (
        <li key={i}>
          <div className="why-head">
            {e.kind === "change_summary"
              ? <strong>Change from the previous budget's plan</strong>
              : <strong>{e.title ?? e.project ?? "Project"}{e.from_fy && e.to_fy ? `: FY${e.from_fy} → FY${e.to_fy}` : ""}</strong>}
            {e.kind !== "change_summary" && e.project && e.title && <span className="muted small"> · Project {e.project}</span>}
            <a href={e.link} target="_blank" rel="noopener noreferrer" className="small"> {e.budget_cycle} R-2 p.{e.page_number} ↗</a>
          </div>
          <blockquote>{e.body}</blockquote>
        </li>
      ))}
    </ul>
  );
  return (
    <section className="panel">
      <h2>Why did funding change?</h2>
      {items.length === 0 ? (
        <p className="muted small">
          No official explanation is available for this {exhibitFamily === "PROC" ? "procurement line" : "program"}.
          {" "}Explanations are quoted from the R-2 justification books, which are loaded for Defense-Wide programs
          (FY2024–FY2027) and Navy programs (FY2027).
          {exhibitFamily === "PROC" ? " Procurement justifications (P-40) aren't loaded yet."
            : service && !["Defense-Wide", "Navy"].includes(service) ? ` ${service} books aren't loaded yet.` : ""}
          {" "}The source pages below have the full justification.
        </p>
      ) : (
        <>
          <p className="muted small">Quoted from the {cycle ?? "latest"} budget justification, not summarized. Follow the page link for context.</p>
          {latest.length > 0 ? list(latest) : <p className="muted small">None in the {cycle} release.</p>}
          {earlier.length > 0 && (
            <details>
              <summary className="small">Earlier releases ({earlier.length})</summary>
              {list(earlier)}
            </details>
          )}
        </>
      )}
    </section>
  );
}

const REF_LABEL: Record<string, string> = {
  r1_summary: "R-1", p1_summary: "P-1", r2_justification: "R-2 justification", p40_justification: "P-40",
};

export function ProgramPage({ programKey }: { programKey: string }) {
  const [data, setData] = useState<ProgramDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [watchBusy, setWatchBusy] = useState(false);
  const [showFull, setShowFull] = useState(false);

  useEffect(() => {
    const ctl = new AbortController();
    setData(null);
    setError(null);
    fetchProgram(programKey, ctl.signal)
      .then((d) => { setData(d); document.title = `${d.program.latest_title} · DoW Budget Search`; })
      .catch((e) => { if (e.name !== "AbortError") setError(String(e)); });
    return () => { ctl.abort(); document.title = "DoW Budget Search"; };
  }, [programKey]);

  // fiscal year x release matrix: how each year's figure moved from request to enacted to actual
  const matrix = useMemo(() => {
    const cell = new Map<string, HistoryRow>();
    data?.history.forEach((h) => cell.set(`${h.funds_fiscal_year}|${h.budget_cycle}`, h));
    return cell;
  }, [data]);

  if (error) {
    return (
      <div className="app">
        <Link className="back" href={lastSearchUrl()}>← Back to search</Link>
        <div className="empty"><p>{error.startsWith("404") ? "No such program." : `Could not load: ${error}`}</p></div>
      </div>
    );
  }
  if (!data) return <div className="app"><p className="loading">Loading…</p></div>;

  const { program, series, latest_lines: latest, flag_threshold: threshold } = data;
  const lead = latest[0];
  const fy = lead?.fiscal_year ?? null;
  const sum = (f: (l: typeof lead) => number | null) =>
    latest.some((l) => f(l) !== null) ? latest.reduce((a, l) => a + (f(l) ?? 0), 0) : null;
  const by = sum((l) => l.budget_year_amount);
  const cy = sum((l) => l.current_year_amount);
  const py = sum((l) => l.prior_year_amount);
  const byQty = sum((l) => l.budget_year_quantity);
  const change = cy ? ((by ?? 0) - cy) / cy : null;
  const kind = changeKind(cy, by);
  const outyears = series.filter((s) => s.amount_type === "estimate");
  const isProc = program.exhibit_family === "PROC";
  const description = data.description?.raw_description_text ?? "";
  const longDescription = description.length > 900;

  const report = async () => {
    const message = window.prompt(
      "What looks wrong? (e.g. an amount that doesn't match the PDF, a wrong explanation, a missing year)", "");
    if (!message || message.trim().length < 3) return;
    try {
      await reportIssue(program.program_key, message.trim());
      const fresh = await fetchProgram(program.program_key);
      setData(fresh);
    } catch (e) {
      setError(String(e));
    }
  };

  const toggleWatch = async () => {
    if (!data.watched && watcherName() === null) {
      // asked once per browser; Cancel or an empty answer stars anonymously and isn't asked again
      setWatcherName(window.prompt("Your name, shown on the team watchlist next to this star (optional):", "") ?? "");
    }
    setWatchBusy(true);
    try {
      const watched = await setWatch(program.program_key, !data.watched);
      setData({ ...data, watched, watch: watched ? { added_by: watcherName() || null, created_at: new Date().toISOString() } : null });
    } catch (e) {
      if (!String(e).includes("name is needed")) setError(String(e));
    } finally {
      setWatchBusy(false);
    }
  };

  const years = series.map((s) => s.fiscal_year);
  const sourcesByCycle = data.releases.slice().reverse().map((c) => ({
    cycle: c,
    lines: data.lines.filter((l) => l.budget_cycle === c),
    // one link per page: two lines on the same page share it
    refs: data.sources.filter((s, i, all) => s.budget_cycle === c &&
      all.findIndex((o) => o.budget_cycle === c && o.link === s.link) === i),
  }));

  return (
    <div className="app program-page">
      <nav className="crumbs">
        <Link className="back" href={lastSearchUrl()}>← Back to search</Link>
        <span className="crumb-right"><AskLink className="back" /><Link className="back" href="/watchlist">★ Team watchlist</Link><Link className="back" href="/glossary">Glossary</Link></span>
      </nav>

      <header className="program-head">
        <div>
          <h1>{program.latest_title}</h1>
          <div className="meta">
            <span className="mono">{lead?.program_element ?? lead?.line_item_number ?? program.program_key}</span>
            <span className={`tag ${isProc ? "proc" : "rdte"}`}>{isProc ? "Procurement (P-1)" : "RDT&E (R-1)"}</span>
            {lead && <span>{lead.service_branch}</span>}
            {lead && <span title={lead.appropriation_title}>{lead.appropriation_account} · {lead.appropriation_title}</span>}
            {lead && <span>BA {lead.budget_activity}{lead.budget_activity_title ? ` · ${lead.budget_activity_title}` : ""}</span>}
            {lead?.organization && !["A", "N", "F"].includes(lead.organization) && <span>{lead.organization}</span>}
            {latest.length > 1 && <span>{latest.length} lines in {data.latest_cycle}</span>}
          </div>
        </div>
        <div className="watch-box">
          <button className={data.watched ? "watch on" : "watch"} onClick={toggleWatch} disabled={watchBusy}
                  aria-pressed={data.watched}>
            <span aria-hidden="true">{data.watched ? "★" : "☆"}</span> {data.watched ? "Watching" : "Watch"}
          </button>
          {data.watched && data.watch && (
            <span className="muted small watch-by">
              Starred{data.watch.added_by ? ` by ${data.watch.added_by}` : ""} on {fmtDate(data.watch.created_at)}
            </span>
          )}
        </div>
      </header>

      {fy && (
        <section className="stats" aria-label={`${data.latest_cycle} summary`}>
          <Stat label={`FY${fy} request`} value={fmtMoney(by)}
                sub={kind === "new" ? <><span className="badge new">New start</span> no FY{fy - 1} funding</>
                  : kind === "ending" ? <><span className="badge ending">Ending</span> none requested</>
                  : <><ChangeBadge pct={change} threshold={threshold} />{kind === "small-base" && <span className="muted"> (small base)</span>}
                    {" "}{fmtChange((by ?? 0) - (cy ?? 0))} vs FY{fy - 1}{byQty ? ` · ${fmtCount(byQty)} units` : ""}</>} />
          <Stat label={`FY${fy - 1} current`} value={fmtMoney(cy)} sub={data.latest_cycle ?? undefined} />
          <Stat label={`FY${fy - 2} actual`} value={fmtMoney(py)} />
          {outyears.length > 0 && (
            <Stat label={`FY${outyears[0].fiscal_year}–${String(outyears[outyears.length - 1].fiscal_year).slice(2)} plan`}
                  value={fmtMoney(outyears.reduce((a, s) => a + s.amount_thousands, 0))} sub="R-2 out-year estimates" />
          )}
        </section>
      )}

      <WhyChanged items={data.explanations} cycle={data.latest_cycle} exhibitFamily={program.exhibit_family}
                  service={lead?.service_branch} />

      <section className="panel">
        <h2>Funding history</h2>
        <p className="muted small">
          Best available figure for each fiscal year: the actual once reported, else the enacted amount, else the
          request. Hover or tab to a bar for its source release.
        </p>
        <FundingChart series={series} budgetYear={fy} />
      </section>

      <section className="panel">
        <h2>By release <span className="muted small">($ millions)</span></h2>
        <p className="muted small">
          Each budget release's figure for each fiscal year. Highlighted cells are the best available figure;
          the last column is the year-over-year change between them ({fmtPct(threshold).replace("+", "±")} flagged).
        </p>
        <div className="table-wrap">
          <table className="results matrix">
            <thead>
              <tr>
                <th className="plain">Fiscal year</th>
                {data.releases.map((c) => <th key={c} className="plain num">{c}</th>)}
                <th className="plain num">Change</th>
              </tr>
            </thead>
            <tbody>
              {years.map((y, yi) => {
                const best = series.find((s) => s.fiscal_year === y)!;
                const prevBest = yi > 0 ? series.find((s) => s.fiscal_year === years[yi - 1]) : undefined;
                return (
                  <tr key={y}>
                    <td className="nowrap"><strong>FY{y}</strong></td>
                    {data.releases.map((c) => {
                      const h = matrix.get(`${y}|${c}`);
                      if (!h) return <td key={c} className="num muted">·</td>;
                      return (
                        <td key={c} className={h.is_latest && best.source_cycle === c ? "num best" : "num"}>
                          {fmtM(h.amount_thousands)}
                          <small className="qty">{TYPE_LABEL[h.amount_type].toLowerCase()}{h.quantity ? ` · ${fmtCount(h.quantity)} qty` : ""}</small>
                        </td>
                      );
                    })}
                    <td className="num"><ChangeBadge pct={best.change_pct} threshold={threshold} base={prevBest?.amount_thousands} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <BudgetExecution history={data.history} />

      {description && (
        <section className="panel">
          <h2>Mission description <span className="muted small">(R-2, {data.description!.budget_cycle})</span></h2>
          <p className="description">
            {showFull || !longDescription ? description : `${description.slice(0, 900).replace(/\s+\S*$/, "")}…`}
          </p>
          {longDescription && (
            <button className="linklike" onClick={() => setShowFull(!showFull)}>{showFull ? "Show less" : "Show all"}</button>
          )}
        </section>
      )}

      {isProc && data.cost_elements.length > 0 && fy && (
        <section className="panel">
          <h2>Cost breakdown <span className="muted small">({data.latest_cycle}, $ millions)</span></h2>
          <div className="table-wrap">
            <table className="results matrix">
              <thead>
                <tr>
                  <th className="plain">Cost type</th>
                  {[fy - 2, fy - 1, fy].map((y) => <th key={y} className="plain num">FY{y}</th>)}
                </tr>
              </thead>
              <tbody>
                {data.cost_elements.map((e, i) => (
                  <tr key={i} className={e.is_add ? "" : "memo"}>
                    <td>{e.cost_type_title}{!e.is_add && <span className="muted small"> · memo, not added</span>}</td>
                    {[fy - 2, fy - 1, fy].map((y) => {
                      const a = e.amounts[String(y)];
                      return (
                        <td key={y} className="num">
                          {a ? fmtM(a.amount_thousands) : "—"}
                          {a?.quantity ? <small className="qty">{fmtCount(a.quantity)} qty</small> : null}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="panel">
        <h2>Spot a problem?</h2>
        <p className="muted small">
          If a figure doesn't match its source page or an explanation looks wrong, report it. Reports go to the data
          owner, who checks the source and fixes or answers them; open reports show here and on the team watchlist.
          Until it's resolved, cite the source page, not this app.
        </p>
        {data.issues.length > 0 && (
          <ul className="events">
            {data.issues.map((i) => (
              <li key={i.id}><span className="badge ending">Open</span> {i.message}
                <span className="muted small"> · {i.reporter ?? "anonymous"}, {fmtDate(i.created_at)}</span></li>
            ))}
          </ul>
        )}
        <button className="linklike" onClick={report}>Report a problem with this data</button>
      </section>

      <section className="panel">
        <h2>Where it appears</h2>
        <p className="muted small">Every source page for this program, newest release first.</p>
        <ul className="appearances">
          {sourcesByCycle.map(({ cycle, lines, refs }) => (
            <li key={cycle}>
              <div className="release">{cycle}</div>
              <div>
                {lines.map((l) => (
                  <div key={l.id} className="placement">
                    {l.exhibit_type} line {l.line_number} · {l.appropriation_account} · BA {l.budget_activity}
                    {l.program_title !== program.latest_title && <span className="muted"> · titled “{l.program_title}”</span>}
                  </div>
                ))}
                <div className="refs">
                  {refs.map((r, i) => (
                    <a key={i} href={r.link} target="_blank" rel="noopener noreferrer"
                       title={r.section ?? r.document_url.split("/").pop()}>
                      {REF_LABEL[r.ref_kind] ?? r.ref_kind} p.{r.page_number} ↗
                    </a>
                  ))}
                </div>
              </div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
