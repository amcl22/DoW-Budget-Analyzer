import { useEffect, useMemo, useState } from "react";
import { PrivateError, reportIssue, type DataStatus } from "./api";
import { fmtCount, fmtDate } from "./format";
import { Link } from "./router";
import { lastSearchUrl } from "./SearchPage";

// ---- the evaluation results file (scripts/evaluate.py), only the fields shown here
interface E1Failure { id: number; release: string; exhibit: string; number: string; title: string; failed: string[]; pages: number[] }
interface Results {
  run_id?: string;
  seed: number;
  trace?: { step: string; started: string; seconds: number }[];
  e1_lines: { sample: number; pass: number; amounts_checked: number; amounts_ok: number; amounts_derived: number; failures: E1Failure[] };
  e2_totals: { checks: number; match: number; rows: { release: string; exhibit: string; group: string; published: number; database: number; match: boolean }[] };
  e3_explanations: { population: number; sample: number; text_on_cited_page: number; right_program_section: number; failures: { id: number; release: string; pe: string; page: number; start: string }[] };
  e4_outliers: { lines: number; confirmed: number; rows: { number: string; title: string; current: number | null; budget: number; confirmed_on_page: boolean }[] };
  e5_search: { queries: number; top1: number; top5: number; median_ms: number; rows: { query: string; expected: string; rank: number | null; top_result: string | null }[] };
  e7_summary?: { checks: number; matches: number; mismatches: string[]; execution_fy2025_coverage: Record<string, number>;
    takeaways_checked: number; takeaway_figures_unverified: string[] };
  e6_coverage: { rows: { budget_cycle: string; exhibit_type: string; service_branch: string; lines: number; with_justification: number; with_explanation: number }[] };
}
interface Payload { results: Results; status: DataStatus; program_keys: Record<string, string> }

type Kind = "CODE" | "RULES" | "RULES · OWNER" | "OWNER GATE";
type State = "PASS" | "WARN" | "FAIL" | "WAITING" | "IDLE";
interface Step { id: string; title: string; kind: Kind; state: State; summary: string; detail?: React.ReactNode }
interface Escalation { id: string; step: string; to: string; blocks: boolean; title: string; body: string; actions: { label: string; primary?: boolean; run: () => Promise<string> | string }[] }

// ---- scenarios: the real last run, and simulated failure paths (nothing is changed)
const SCENARIOS = [
  { id: "real", label: "PB2027 release (last evaluation)", typical: true },
  { id: "totals", label: "Totals don't reconcile" },
  { id: "blocked", label: "Source site blocks download" },
  { id: "amount", label: "Amount not on its page" },
  { id: "garbled", label: "Garbled explanation" },
] as const;
type ScenarioId = (typeof SCENARIOS)[number]["id"];

const pct = (k: number, n: number) => (n ? `${((100 * k) / n).toFixed(1)}%` : "—");
const field = (f: string[]) => f.map((k) => k.replace(/_amount$/, "").replace("_year", "-year") + " amount").join(", ");
const clock = (iso: string) => new Date(iso).toLocaleTimeString("en-US", { hour12: false });

function Badge({ kind }: { kind: Kind }) {
  return <span className={`vs-kind ${kind.includes("OWNER") ? "owner" : kind === "RULES" ? "rules" : ""}`}>{kind}</span>;
}

export function ValidationPage() {
  const [data, setData] = useState<Payload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scenario, setScenario] = useState<ScenarioId>("real");
  const [allowDerived, setAllowDerived] = useState(true);
  const [booksAll, setBooksAll] = useState(false);
  const [published, setPublished] = useState(false);
  const [resolved, setResolved] = useState<Record<string, string>>({});
  const [log, setLog] = useState<{ at: string; step: string; text: string; tone: string }[]>([]);
  const [open, setOpen] = useState<Record<string, boolean>>({});

  useEffect(() => {
    document.title = "Validation Studio · DoW Budget Search";
    fetch("/api/validation")
      .then(async (r) => {
        if (r.status === 401) throw new PrivateError();
        if (!r.ok) throw new Error(r.status === 404 ? "No evaluation results yet." : `${r.status} ${r.statusText}`);
        setData(await r.json());
      })
      .catch((e) => setError(e.message ?? String(e)));
    return () => { document.title = "DoW Budget Search"; };
  }, []);

  const reset = () => { setPublished(false); setResolved({}); setLog([]); setAllowDerived(true); setBooksAll(false); };
  const note = (step: string, text: string, tone = "info") =>
    setLog((l) => [...l, { at: new Date().toISOString(), step, text, tone }]);

  const model = useMemo(() => {
    if (!data) return null;
    const r = data.results;
    const e1 = r.e1_lines;
    const e1Pass = allowDerived ? e1.pass : e1.pass - Math.min(e1.amounts_derived, e1.pass);
    const cov = r.e6_coverage.rows.filter((c) => c.budget_cycle === "PB2027" && c.exhibit_type === "R-1");
    const missingBooks = cov.filter((c) => c.with_justification === 0).map((c) => c.service_branch);

    const steps: Step[] = [
      {
        id: "S1", title: "Ingest releases", kind: "CODE",
        state: scenario === "blocked" ? "FAIL" : "PASS",
        summary: scenario === "blocked"
          ? "Simulated: the Army site refused the download (HTTP 403). The release stays unpublished; the last good data stays live."
          : `${data.status.releases.length} exhibit releases loaded from content-hashed source files; unchanged files are skipped.`,
      },
      {
        id: "S2", title: "Reconcile to published totals", kind: "CODE",
        state: scenario === "totals" ? "FAIL" : r.e2_totals.match === r.e2_totals.checks ? "PASS" : "FAIL",
        summary: scenario === "totals"
          ? "Simulated: PB2027 P-1 is $1,250K short of the printed grand total. Hard check: publishing is blocked."
          : `${r.e2_totals.match}/${r.e2_totals.checks} totals match the totals printed in each exhibit, to the thousand dollars.`,
        detail: (
          <table className="vs-table"><thead><tr><th>Release</th><th>Group</th><th className="num">Printed ($K)</th><th className="num">Database ($K)</th><th /></tr></thead>
            <tbody>{r.e2_totals.rows.map((t, i) => (
              <tr key={i}><td>{t.release} {t.exhibit}</td><td>{t.group}</td><td className="num">{fmtCount(t.published)}</td>
                <td className="num">{fmtCount(scenario === "totals" && i === 9 ? t.database - 1250 : t.database)}</td>
                <td>{scenario === "totals" && i === 9 ? "✕" : t.match ? "✓" : "✕"}</td></tr>))}</tbody></table>
        ),
      },
      {
        id: "S3", title: "Verify every line on its source page", kind: "CODE",
        state: scenario === "amount" ? "FAIL" : "PASS",
        summary: scenario === "amount"
          ? "Simulated: 3 lines' amounts aren't on their linked page. They load but the release can't be published until they're fixed."
          : `${fmtCount(data.status.lines)} of ${fmtCount(data.status.lines)} lines: the spreadsheet amounts were found on the linked PDF page at load.`,
      },
      {
        id: "S4", title: "Independent sample re-check", kind: "CODE",
        state: e1Pass === e1.sample ? "PASS" : "WARN",
        summary: `${e1Pass}/${e1.sample} sampled lines (${pct(e1Pass, e1.sample)}) re-found on the source PDF by separate code; ` +
          `${allowDerived ? e1.amounts_ok : e1.amounts_ok - e1.amounts_derived}/${e1.amounts_checked} amounts.` +
          (allowDerived ? ` ${e1.amounts_derived} accepted as gross less advance procurement.` : " Derived amounts not accepted."),
        detail: e1.failures.length > 0 && (
          <ul className="vs-list">{e1.failures.map((f) => (
            <li key={f.id}><strong>{f.title}</strong> ({f.release} {f.exhibit} {f.number}): {field(f.failed)} not on page {f.pages.join("/")}</li>))}</ul>
        ),
      },
      {
        id: "S5", title: "Outlier review", kind: "RULES · OWNER",
        state: r.e4_outliers.confirmed === r.e4_outliers.lines ? "PASS" : "WARN",
        summary: `${r.e4_outliers.confirmed}/${r.e4_outliers.lines} PB2027 outliers (500%+ jumps, or $100M+ new starts) confirmed on their source page.`,
        detail: (
          <ul className="vs-list">{r.e4_outliers.rows.slice(0, 8).map((o, i) => (
            <li key={i}>{o.title} ({o.number}): {o.current ? `$${fmtCount(o.current)}K → ` : "new start, "}${fmtCount(o.budget)}K {o.confirmed_on_page ? "✓" : "✕"}</li>))}
            <li className="muted">…and {Math.max(0, r.e4_outliers.lines - 8)} more</li></ul>
        ),
      },
      {
        id: "S6", title: "Quote official explanations", kind: "CODE",
        state: scenario === "garbled" ? "WARN" : r.e3_explanations.text_on_cited_page === r.e3_explanations.sample ? "PASS" : "WARN",
        summary: `${r.e3_explanations.text_on_cited_page + (scenario === "garbled" ? -5 : 0)}/${r.e3_explanations.sample} sampled quotes found on the cited page; ` +
          `${r.e3_explanations.right_program_section}/${r.e3_explanations.sample} in the right program's section (${fmtCount(r.e3_explanations.population)} quotes in all).`,
        detail: (
          <ul className="vs-list">{r.e3_explanations.failures.map((f) => (
            <li key={f.id}>{f.release} {f.pe} p.{f.page}: “{f.start}…”</li>))}</ul>
        ),
      },
      {
        id: "S7", title: "Search benchmark", kind: "CODE",
        state: r.e5_search.top5 === r.e5_search.queries ? "PASS" : "WARN",
        summary: `${r.e5_search.top5}/${r.e5_search.queries} analyst searches find the right program in the top 5 (${r.e5_search.top1} first); median ${Math.round(r.e5_search.median_ms)} ms.`,
        detail: (
          <table className="vs-table"><thead><tr><th>Search</th><th>Rank</th><th>Top result</th></tr></thead>
            <tbody>{r.e5_search.rows.map((s) => (
              <tr key={s.query}><td>“{s.query}”</td><td>{s.rank ?? "miss"}</td><td>{s.top_result ?? "no results"}</td></tr>))}</tbody></table>
        ),
      },
      {
        id: "S8", title: "Justification coverage", kind: "RULES",
        state: booksAll || missingBooks.length === 0 ? "PASS" : "WARN",
        summary: booksAll
          ? "Simulated: all services' justification books available."
          : `PB2027 RDT&E lines with justification detail: ${cov.map((c) => `${c.service_branch} ${pct(c.with_justification, c.lines)}`).join(", ")}.`,
      },
    ];
    if (r.e7_summary) {
      const e7 = r.e7_summary;
      const cov = Object.values(e7.execution_fy2025_coverage);
      steps.push({
        id: "S9", title: "1-page summary figures", kind: "CODE",
        state: e7.matches === e7.checks && e7.takeaway_figures_unverified.length === 0 ? "PASS" : "WARN",
        summary: `${e7.matches}/${e7.checks} summary totals match an independent recomputation from the raw amounts; ` +
          `${e7.takeaways_checked} written takeaways, ${e7.takeaway_figures_unverified.length} unverified figures. ` +
          `Execution view covers ${pct(Math.min(...cov), 1)}–${pct(Math.max(...cov), 1)} of FY2025 dollars (programs in all three releases).`,
        detail: e7.mismatches.length > 0 && <ul className="vs-list">{e7.mismatches.map((m) => <li key={m}>{m}</li>)}</ul>,
      });
    }
    const blocking = steps.some((s) => s.state === "FAIL");
    steps.push({
      id: "S10", title: "Publish gate", kind: "OWNER GATE",
      state: blocking ? "IDLE" : published ? "PASS" : "WAITING",
      summary: blocking ? "Not reached: a hard check failed, so this release can't be published."
        : published ? "Published by the data owner. Team sees the release." : "Checks done. The release reaches the team only when the data owner publishes it.",
    });

    const esc: Escalation[] = [];
    if (scenario === "totals") esc.push({ id: "x-tot", step: "S2", to: "Data owner", blocks: true, title: "PB2027 P-1 total off by $1,250K",
      body: "Simulated. Likely a misread row or a new layout. Compare the validation report with the PDF's summary page, fix the column map, and re-run.",
      actions: [{ label: "Hold release and re-run", run: () => "Release held; re-run queued after the fix (simulated)." }] });
    if (scenario === "blocked") esc.push({ id: "x-blk", step: "S1", to: "Data owner", blocks: true, title: "Army justification site refused the download",
      body: "Simulated. Download the books by hand into data/manual/ and re-run; until then the previous release stays live.",
      actions: [{ label: "Mark manual download needed", run: () => "Manual download noted (simulated)." }] });
    if (scenario === "amount") esc.push({ id: "x-amt", step: "S3", to: "Data owner", blocks: true, title: "3 amounts not found on their page",
      body: "Simulated. Usually a page layout change. The lines load but stay unpublished until the link pass is fixed.",
      actions: [{ label: "Open the failed pages", run: () => "Pages listed in the validation report (simulated)." }] });
    for (const f of r.e1_lines.failures) {
      const key = data.program_keys[String(f.id)];
      esc.push({ id: `e1-${f.id}`, step: "S4", to: "Data owner", blocks: false,
        title: `${f.title}: ${field(f.failed)} doesn't match page ${f.pages.join("/")}`,
        body: `${f.release} ${f.exhibit} ${f.number}. The stored figure appears to include the “Completion PY Shipbuild” rows printed beneath it. Users should cite the PDF until fixed.`,
        actions: [
          ...(key ? [{ label: "Open problem report", primary: true, run: async () => {
            await reportIssue(key, `Validation S4: ${f.failed.join(", ")} for ${f.release} ${f.exhibit} ${f.number} doesn't match page ${f.pages.join("/")}; appears to include Completion PY Shipbuild rows.`);
            return `Problem report opened on ${f.title}; it now shows on the program page and the watchlist.`;
          } }] : []),
          { label: "Accept as known issue", run: () => "Accepted as a known issue for this release." },
        ] });
    }
    const misses = r.e5_search.rows.filter((s) => !s.rank || s.rank > 5);
    if (misses.length) esc.push({ id: "e5", step: "S7", to: "Data owner", blocks: false,
      title: `${misses.length} searches miss: ${misses.map((m) => `“${m.query}”`).join(", ")}`,
      body: "Keyword search needs every word in the title or description, and doesn't know nicknames or services in free text. Candidates for synonyms.",
      actions: [{ label: "Accept for v1", run: () => "Search gaps accepted for v1; synonyms are on the backlog." }] });
    if (r.e3_explanations.failures.length || scenario === "garbled") esc.push({ id: "e3", step: "S6", to: "Data owner", blocks: false,
      title: `${r.e3_explanations.failures.length + (scenario === "garbled" ? 5 : 0)} quotes don't match their page word for word`,
      body: "Two-column page layouts can reorder words. The page link on each quote lets users read the original.",
      actions: [{ label: "Accept (quotes link to the page)", run: () => "Accepted: every quote links to its page." }] });
    return { steps, esc, blocking };
  }, [data, scenario, allowDerived, booksAll, published]);

  if (error) return <div className="app"><Link className="back" href={lastSearchUrl()}>← Back to search</Link><div className="empty"><p>{error}</p></div></div>;
  if (!data || !model) return <div className="app"><p className="loading">Loading…</p></div>;
  const r = data.results;
  const runAt = r.trace?.[0]?.started;
  const stepOf: Record<string, string> = { e2_totals: "S2", e1_lines: "S4", e4_outliers: "S5", e3_explanations: "S6", e6_coverage: "S8", e5_search: "S7", e7_summary: "S9" };
  const stepById = Object.fromEntries(model.steps.map((s) => [s.id, s]));
  const act = async (e: Escalation, a: Escalation["actions"][number]) => {
    try {
      const msg = await a.run();
      setResolved((x) => ({ ...x, [e.id]: msg }));
      note(e.step, msg, "ok");
    } catch (err) {
      note(e.step, `Failed: ${String(err)}`, "bad");
    }
  };
  const openEsc = model.esc.filter((e) => !resolved[e.id]);

  return (
    <div className="app vs">
      <nav className="crumbs">
        <Link className="back" href={lastSearchUrl()}>← Back to search</Link>
        <Link className="back" href="/glossary">Glossary &amp; coverage</Link>
      </nav>
      <header className="vs-head">
        <h1>Validation Studio <span className="muted">v1</span></h1>
        <p className="muted">
          How each budget release is checked before the team sees it. Code ingests, links and reconciles every line; the
          evaluation re-checks a sample against the source PDFs with separate code; the data owner holds the publish gate.
          Results from evaluation run <span className="mono">{r.run_id ?? `seed ${r.seed}`}</span>{runAt ? `, ${fmtDate(runAt)}` : ""}.
        </p>
      </header>

      <section className="panel vs-scenarios">
        <span className="vs-label">Typical</span>
        {SCENARIOS.filter((s) => "typical" in s).map((s) => (
          <button key={s.id} className={`chip ${scenario === s.id ? "on" : ""}`} onClick={() => { setScenario(s.id); reset(); }}>{s.label}</button>))}
        <span className="vs-label">Escalation &amp; failure paths (simulated)</span>
        {SCENARIOS.filter((s) => !("typical" in s)).map((s) => (
          <button key={s.id} className={`chip dashed ${scenario === s.id ? "on" : ""}`} onClick={() => { setScenario(s.id); reset(); }}>{s.label}</button>))}
        <p className="muted small">
          {scenario === "real" ? "Real results from the last evaluation run." : "Simulated failure: shows what the pipeline does and who it escalates to. Nothing in the data is changed."}
        </p>
      </section>

      <div className="vs-grid">
        <aside className="panel vs-side">
          <h2>Run conditions</h2>
          <label className="vs-check"><input type="checkbox" checked={allowDerived} onChange={(e) => setAllowDerived(e.target.checked)} />
            Accept P-1 amounts derived as gross less advance procurement</label>
          <label className="vs-check"><input type="checkbox" checked={booksAll} onChange={(e) => setBooksAll(e.target.checked)} />
            Army / Air Force / Space Force books available</label>
          <h2 style={{ marginTop: 16 }}>Live data</h2>
          <dl className="vs-facts">
            <div><dt>Releases</dt><dd>{data.status.releases.length}</dd></div>
            <div><dt>Line items</dt><dd>{fmtCount(data.status.lines)}</dd></div>
            <div><dt>Explanations</dt><dd>{fmtCount(data.status.explanations)}</dd></div>
            <div><dt>Open problem reports</dt><dd>{data.status.open_issues}</dd></div>
          </dl>
          <button className="reset" onClick={reset}>Reset</button>
          <p className="muted small">Code steps run on their own. The release stops at the publish gate (S10) and at any failed hard check (S1–S3).</p>
        </aside>

        <div className="vs-steps">
          {model.steps.map((s) => (
            <article key={s.id} className={`vs-step ${s.state.toLowerCase()}`}>
              <div className="vs-step-head">
                <span className="vs-id">{s.id}</span>
                <h3>{s.title}</h3>
                <Badge kind={s.kind} />
                <span className={`vs-state ${s.state.toLowerCase()}`}>{s.state}</span>
              </div>
              <p>{s.summary}</p>
              {s.id === "S10" && s.state === "WAITING" && (
                <div className="vs-action">
                  <button className="primary-btn" onClick={() => { setPublished(true); note("S10", "Release published by the data owner (simulated here; the real command is budget publish).", "ok"); }}>
                    Publish as data owner
                  </button>
                  <span className="muted small">Simulated here. In production the data owner runs <span className="mono">budget publish</span> after reading the report.</span>
                </div>
              )}
              {s.detail && (
                <details open={!!open[s.id]} onToggle={(e) => setOpen((o) => ({ ...o, [s.id]: (e.target as HTMLDetailsElement).open }))}>
                  <summary className="small">What this step produced</summary>
                  {s.detail}
                </details>
              )}
            </article>
          ))}
        </div>
      </div>

      <section className="panel vs-esc">
        <h2>Escalations</h2>
        {openEsc.length === 0 && <p className="muted">None open.</p>}
        {openEsc.map((e) => (
          <div key={e.id} className={`vs-esc-item ${e.blocks ? "blocks" : ""}`}>
            <div className="small muted"><span className="mono">{e.step}</span> → <strong className="vs-to">{e.to}</strong> {e.blocks ? "blocks publish" : "flags for review"}</div>
            <h3>{e.title}</h3>
            <p>{e.body}</p>
            <div className="vs-buttons">{e.actions.map((a) => (
              <button key={a.label} className={a.primary ? "primary-btn" : "outline-btn"} onClick={() => act(e, a)}>{a.label}</button>))}</div>
          </div>
        ))}
        {Object.keys(resolved).length > 0 && <p className="muted small">{Object.keys(resolved).length} handled this session.</p>}
      </section>

      <section className="panel vs-trace">
        <h2>Run trace</h2>
        <p className="muted small">Which step ran, in order, with what it produced.</p>
        <ol>
          {r.trace?.flatMap((t) => {
            const id = stepOf[t.step];
            const s = stepById[id];
            const done = new Date(new Date(t.started).getTime() + t.seconds * 1000).toISOString();
            return [
              <li key={t.step} className={s?.state.toLowerCase()}>
                <span className="mono">{clock(t.started)}</span><span className="vs-id">{id}</span>
                <span>{s?.summary} <span className="muted small">({t.seconds}s)</span></span>
              </li>,
              ...model.esc.filter((e) => e.step === id).map((e) => (
                <li key={`t-${e.id}`} className="waiting"><span className="mono">{clock(done)}</span><span className="vs-id">{e.step}</span>
                  <span>Escalated to {e.to}: {e.title}</span></li>
              )),
            ];
          })}
          {log.map((l, i) => (
            <li key={i} className={l.tone === "ok" ? "pass" : l.tone === "bad" ? "fail" : ""}>
              <span className="mono">{clock(l.at)}</span><span className="vs-id">{l.step}</span><span>{l.text}</span>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
