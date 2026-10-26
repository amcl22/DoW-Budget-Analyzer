import { useEffect, useState } from "react";
import { fetchDashboard, setWatch, type Dashboard } from "./api";
import { changeKind, fmtDate, fmtM, fmtMoney, fmtPct } from "./format";
import { Link, programPath } from "./router";
import { lastSearchUrl } from "./SearchPage";
import { Sparkline } from "./Sparkline";

export function WatchlistPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = (signal?: AbortSignal) =>
    fetchDashboard(signal).then(setData).catch((e) => { if (e.name !== "AbortError") setError(e.message ?? String(e)); });

  useEffect(() => {
    const ctl = new AbortController();
    document.title = "Team watchlist · DoW Budget Search";
    load(ctl.signal);
    return () => { ctl.abort(); document.title = "DoW Budget Search"; };
  }, []);

  const unwatch = async (key: string) => {
    try {
      await setWatch(key, false);
      await load();
    } catch (e) {
      if (!String(e).includes("name is needed")) setError(String(e));
    }
  };

  const undo = async (key: string) => {
    try {
      await setWatch(key, true);
      await load();
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="app">
      <nav className="crumbs">
        <Link className="back" href={lastSearchUrl()}>← Back to search</Link>
        <Link className="back" href="/glossary">Glossary &amp; coverage</Link>
      </nav>
      <header className="program-head">
        <div>
          <h1>Team watchlist</h1>
          <p className="muted">
            Programs anyone on the team has watched, largest year-over-year moves first.
            {data && ` Changes of ${Math.round(data.flag_threshold * 100)}% or more are flagged.`}
          </p>
          <p className="muted small how-shared">
            <strong>How it works:</strong> there are no logins, so this list is shared by everyone with the team
            link. Adding a star asks for your name (optional); <strong>removing one requires a name</strong>, and
            {" "}<a href="#history">Recent changes</a> below records every add and remove, with an Undo for removals.
          </p>
        </div>
      </header>

      {error && <div className="empty"><p className="error">{error}</p></div>}
      {!error && !data && <p className="loading">Loading…</p>}

      {data && data.watched.length === 0 && (
        <div className="empty">
          <p>Nothing is watched yet.</p>
          <p className="muted">Open a program from search and press <strong>☆ Watch</strong> to add it here.</p>
        </div>
      )}

      {data && data.watched.length > 0 && (
        <>
          <section className="stats" aria-label="Watchlist summary">
            <div className="stat"><span className="stat-label">Programs</span><span className="stat-value">{data.watched.length}</span></div>
            <div className="stat"><span className="stat-label">Flagged changes</span><span className="stat-value">{data.flagged}</span></div>
            <div className="stat"><span className="stat-label">Budget-year request</span><span className="stat-value">{fmtMoney(data.budget_year_total)}</span>
              <span className="stat-sub">vs {fmtMoney(data.current_year_total)} current year</span></div>
          </section>
          <div className="table-wrap">
            <table className="results">
              <thead>
                <tr>
                  <th className="plain">Program</th>
                  <th className="plain">Service</th>
                  <th className="plain num">Current year</th>
                  <th className="plain num">Budget year</th>
                  <th className="plain num">Change</th>
                  <th className="plain">Trend</th>
                  <th className="plain">Starred</th>
                  <th className="plain"><span className="sr-only">Remove</span></th>
                </tr>
              </thead>
              <tbody>
                {data.watched.map((w) => {
                  const cls = (w.change_pct ?? 0) > 0 ? "up" : (w.change_pct ?? 0) < 0 ? "down" : "";
                  return (
                    <tr key={w.program_id}>
                      <td className="program">
                        <div className="title"><Link href={programPath(w.program_key)}>{w.latest_title}</Link></div>
                        <div className="meta">
                          <span className="mono">{w.program_key}</span>
                          <span className={`tag ${w.exhibit_family === "PROC" ? "proc" : "rdte"}`}>{w.exhibit_family === "PROC" ? "P-1" : "R-1"}</span>
                          {w.budget_cycle && <span>{w.budget_cycle}</span>}
                        </div>
                      </td>
                      <td className="nowrap">{w.service_branch ?? "—"}</td>
                      <td className="num">{fmtM(w.current_year_amount)}{w.fiscal_year && <small className="qty">FY{w.fiscal_year - 1}</small>}</td>
                      <td className="num">{fmtM(w.budget_year_amount)}{w.fiscal_year && <small className="qty">FY{w.fiscal_year}</small>}</td>
                      <td className="num">
                        {changeKind(w.current_year_amount, w.budget_year_amount) === "new" ? <span className="badge new">New start</span>
                          : changeKind(w.current_year_amount, w.budget_year_amount) === "small-base" && w.change_pct !== null
                            ? <span className={`change small-base ${cls}`} title="From a current year under $5M: compare the dollar amounts">{fmtPct(w.change_pct)}<small>small base</small></span>
                          : w.change_pct === null ? "—" : w.flagged
                          ? <span className={`badge ${cls}`}>{fmtPct(w.change_pct)}</span>
                          : <span className={`change ${cls}`}>{fmtPct(w.change_pct)}</span>}
                      </td>
                      <td><Sparkline series={w.series} /></td>
                      <td className="small nowrap">{w.added_by ?? <span className="muted">anonymous</span>}<br />
                        <span className="muted">{fmtDate(w.created_at)}</span></td>
                      <td><button className="linklike" onClick={() => unwatch(w.program_key)}>Remove</button></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="muted small" style={{ marginTop: 8 }}>
            Amounts in $ millions. Trend: best available figure per fiscal year; dashed where it is an out-year estimate.
          </p>
        </>
      )}

      {data && data.issues.length > 0 && (
        <section className="panel" style={{ marginTop: 16 }}>
          <h2>Open data problem reports</h2>
          <p className="muted small">Reported from program pages; the data owner checks each against the source and resolves it.</p>
          <ul className="events">
            {data.issues.map((i) => (
              <li key={i.id}>
                <span className="muted">{fmtDate(i.created_at)}</span>{" "}
                <Link href={programPath(i.program_key)}>{i.latest_title}</Link>: {i.message}
                <span className="muted"> ({i.reporter ?? "anonymous"})</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {data && data.events.length > 0 && (
        <section className="panel" id="history" style={{ marginTop: 16 }}>
          <h2>Recent changes</h2>
          <ul className="events">
            {data.events.map((e, i) => {
              // Undo only on a program's latest change, when that change removed it
              const latest = data.events.findIndex((x) => x.program_key === e.program_key) === i;
              const stillOff = !data.watched.some((w) => w.program_key === e.program_key);
              return (
                <li key={i}>
                  <span className="muted">{fmtDate(e.created_at)}</span>{" "}
                  <strong>{e.actor ?? "Someone"}</strong> {e.action === "watch" ? "starred" : "removed"}{" "}
                  <Link href={programPath(e.program_key)}>{e.latest_title}</Link>
                  {e.action === "unwatch" && latest && stillOff && (
                    <> · <button className="linklike" onClick={() => undo(e.program_key)}>Undo</button></>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );
}
