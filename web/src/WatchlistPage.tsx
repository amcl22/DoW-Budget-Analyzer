import { useEffect, useState } from "react";
import { fetchDashboard, setWatch, type Dashboard } from "./api";
import { fmtM, fmtMoney, fmtPct } from "./format";
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
      setError(String(e));
    }
  };

  return (
    <div className="app">
      <Link className="back" href={lastSearchUrl()}>← Back to search</Link>
      <header className="program-head">
        <div>
          <h1>Team watchlist</h1>
          <p className="muted">
            Programs anyone on the team has watched, largest year-over-year moves first.
            {data && ` Changes of ${Math.round(data.flag_threshold * 100)}% or more are flagged.`}
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
                        {w.change_pct === null ? "—" : w.flagged
                          ? <span className={`badge ${cls}`}>{fmtPct(w.change_pct)}</span>
                          : <span className={`change ${cls}`}>{fmtPct(w.change_pct)}</span>}
                      </td>
                      <td><Sparkline series={w.series} /></td>
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
    </div>
  );
}
