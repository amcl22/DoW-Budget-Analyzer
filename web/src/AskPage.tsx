import { useEffect, useRef, useState } from "react";
import { ask, fetchAskStatus, type Answer, type Citation, type LineCard } from "./api";
import { fmtMoney } from "./format";
import { Link } from "./router";
import { lastSearchUrl } from "./SearchPage";

interface Turn {
  question: string;
  answer?: Answer;
  error?: string;
}

const EXAMPLES = [
  "What is the FY2027 request for Conventional Prompt Strike?",
  "How much RDT&E does each service request in PB2027?",
  "How has F-35 procurement funding changed since FY2024?",
  "Which Army research programs grow the most in FY2027?",
];

const STATUS_NOTE: Record<Answer["status"], string | null> = {
  answered: null,
  partial: "Partial answer: some of what you asked isn't in the data.",
  no_match: "No match in the budget data.",
  refused: "Declined.",
};

/** Answer text with [n] markers linked to their citation cards and unverified figures marked. */
function AnswerText({ answer, turn }: { answer: Answer; turn: number }) {
  const flagged = answer.unverified_figures;
  const inline = (line: string, key: string) => {
    const parts: React.ReactNode[] = [];
    const re = /\[(\d+)\]/g;
    let last = 0;
    const pushText = (s: string) => {
      // mark each unverified figure; the rest is plain text (never HTML)
      let rest = s;
      while (rest) {
        const hit = flagged.map((f) => ({ f, i: rest.indexOf(f) })).filter((h) => h.i >= 0).sort((a, b) => a.i - b.i)[0];
        if (!hit) { parts.push(rest); break; }
        if (hit.i > 0) parts.push(rest.slice(0, hit.i));
        parts.push(
          <mark key={`${key}-u${parts.length}`} className="unverified" title="This figure doesn't match the cited sources">
            {hit.f} <span aria-label="unverified">⚠</span>
          </mark>,
        );
        rest = rest.slice(hit.i + hit.f.length);
      }
    };
    for (let m = re.exec(line); m; m = re.exec(line)) {
      pushText(line.slice(last, m.index));
      parts.push(
        <a key={`${key}-c${m.index}`} className="cite-mark" href={`#cite-${turn}-${m[1]}`}
           aria-label={`source ${m[1]}`}>{m[1]}</a>,
      );
      last = m.index + m[0].length;
    }
    pushText(line.slice(last));
    return parts;
  };

  const blocks: React.ReactNode[] = [];
  let bullets: string[] = [];
  const flush = () => {
    if (bullets.length) {
      const items = bullets;
      blocks.push(<ul key={`ul${blocks.length}`}>{items.map((b, i) => <li key={i}>{inline(b, `b${blocks.length}-${i}`)}</li>)}</ul>);
      bullets = [];
    }
  };
  answer.answer.split("\n").forEach((raw, i) => {
    const line = raw.trim();
    if (/^[-•*]\s+/.test(line)) { bullets.push(line.replace(/^[-•*]\s+/, "")); return; }
    flush();
    if (line) blocks.push(<p key={`p${i}`}>{inline(line, `p${i}`)}</p>);
  });
  flush();
  return <div className="answer-text">{blocks}</div>;
}

function LineLinks({ l }: { l: LineCard }) {
  return (
    <span className="card-links">
      {l.program_key && <Link href={`/program/${encodeURIComponent(l.program_key)}`}>Program page</Link>}
      {l.source_pdf_link && (
        <a href={l.source_pdf_link} target="_blank" rel="noopener noreferrer">
          {l.exhibit_type} p.{l.source_page_number} ↗
        </a>
      )}
    </span>
  );
}

function CitationCard({ c, turn }: { c: Citation; turn: number }) {
  let body: React.ReactNode;
  if (c.kind === "line_item") {
    body = (
      <>
        <div className="card-title">{c.title}</div>
        <div className="muted small">
          <span className="mono">{c.number}</span> · {c.budget_cycle} {c.exhibit_type} · {c.service_branch} · {c.appropriation_account}
        </div>
        <div className="small">
          FY{c.fiscal_year - 2} {fmtMoney(c.prior_year_amount)} · FY{c.fiscal_year - 1} {fmtMoney(c.current_year_amount)} ·{" "}
          <strong>FY{c.fiscal_year} {fmtMoney(c.budget_year_amount)}</strong>
        </div>
        <LineLinks l={c} />
      </>
    );
  } else if (c.kind === "program") {
    const last = c.series[c.series.length - 1];
    body = (
      <>
        <div className="card-title">{c.title}</div>
        <div className="muted small">
          <span className="mono">{c.program_key}</span> · funding history
          {c.series.length > 0 && `, FY${c.series[0].fiscal_year}–FY${last.fiscal_year}`}
        </div>
        <span className="card-links">
          <Link href={`/program/${encodeURIComponent(c.program_key)}`}>Program page</Link>
          {c.source_pdf_link && (
            <a href={c.source_pdf_link} target="_blank" rel="noopener noreferrer">{c.latest_cycle} source page ↗</a>
          )}
        </span>
      </>
    );
  } else {
    body = (
      <>
        <div className="card-title">Total: {c.label}</div>
        <div className="muted small">
          {c.lines.toLocaleString()} lines{c.in_title_only ? ", accounts inside the exhibit title" : ", all accounts"}
        </div>
        <div className="small">
          FY{c.fiscal_year - 2} {fmtMoney(c.prior_year_amount)} · FY{c.fiscal_year - 1} {fmtMoney(c.current_year_amount)} ·{" "}
          <strong>FY{c.fiscal_year} {fmtMoney(c.budget_year_amount)}</strong>
        </div>
        <span className="card-links">
          <Link href={c.search_url}>See the lines</Link>
          {c.top_lines.map((l) => l.source_pdf_link && (
            <a key={l.line_item_id} href={l.source_pdf_link} target="_blank" rel="noopener noreferrer"
               title={`Largest line: ${l.title}`}>{l.number} p.{l.source_page_number} ↗</a>
          ))}
        </span>
      </>
    );
  }
  return (
    <li id={`cite-${turn}-${c.n}`} className="cite-card">
      <span className="cite-n">{c.n}</span>
      <div>{body}</div>
    </li>
  );
}

export function AskPage() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ enabled: boolean; max_question_chars: number } | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    document.title = "Ask · DoW Budget Search";
    const ctl = new AbortController();
    fetchAskStatus(ctl.signal).then(setStatus).catch((e) => { if (e.name !== "AbortError") setStatusError(String(e.message ?? e)); });
    return () => { ctl.abort(); document.title = "DoW Budget Search"; };
  }, []);

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [turns]);

  const submit = async (q: string) => {
    q = q.trim();
    if (!q || busy) return;
    const history = turns.filter((t) => t.answer).map((t) => ({ question: t.question, answer: t.answer!.answer }));
    setTurns((ts) => [...ts, { question: q }]);
    setQuestion("");
    setBusy(true);
    try {
      const answer = await ask(q, history);
      setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? { ...t, answer } : t)));
    } catch (e) {
      setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? { ...t, error: (e as Error).message } : t)));
    } finally {
      setBusy(false);
      box.current?.focus();
    }
  };

  const max = status?.max_question_chars ?? 1000;
  const disabled = status !== null && !status.enabled;

  return (
    <div className="app ask-page">
      <nav className="crumbs">
        <Link className="back" href={lastSearchUrl()}>← Back to search</Link>
        <Link className="back" href="/watchlist">★ Team watchlist</Link>
      </nav>
      <header className="program-head">
        <div>
          <h1>Ask the budget</h1>
          <p className="muted">
            Questions in plain English, answered from the R-1, P-1 and R-2 data. Every dollar figure cites the
            line item, program or total it came from, with a link to the source page. Check anything important
            against the source.
          </p>
        </div>
        {turns.length > 0 && !busy && (
          <button className="reset" onClick={() => setTurns([])}>New conversation</button>
        )}
      </header>

      {statusError && <div className="empty"><p>Could not load: {statusError}</p></div>}
      {disabled && (
        <div className="empty"><p>Q&amp;A isn't switched on for this server yet (it needs an Anthropic API key).</p></div>
      )}

      {turns.length === 0 && !disabled && (
        <section className="panel">
          <h2>Try</h2>
          <ul className="examples">
            {EXAMPLES.map((e) => (
              <li key={e}><button className="chip" onClick={() => submit(e)} disabled={busy}>{e}</button></li>
            ))}
          </ul>
        </section>
      )}

      <ol className="turns">
        {turns.map((t, i) => (
          <li key={i} className="turn">
            <div className="question">{t.question}</div>
            {!t.answer && !t.error && <p className="loading" role="status">Looking it up…</p>}
            {t.error && <div className="answer error" role="alert">{t.error}</div>}
            {t.answer && (
              <div className={`answer status-${t.answer.status}`}>
                {STATUS_NOTE[t.answer.status] && <div className="status-note">{STATUS_NOTE[t.answer.status]}</div>}
                <AnswerText answer={t.answer} turn={i} />
                {t.answer.warnings.map((w) => <p key={w} className="warning small">⚠ {w}</p>)}
                {t.answer.citations.length > 0 && (
                  <>
                    <h3 className="sources-head">Sources</h3>
                    <ol className="cite-cards">
                      {t.answer.citations.map((c) => <CitationCard key={c.ref} c={c} turn={i} />)}
                    </ol>
                  </>
                )}
                {t.answer.lookups.length > 0 && (
                  <details className="lookups small muted">
                    <summary>{t.answer.lookups.length} lookup{t.answer.lookups.length > 1 ? "s" : ""}</summary>
                    <ul>
                      {t.answer.lookups.map((l, j) => (
                        <li key={j}><span className="mono">{l.tool}</span> {JSON.stringify(
                          Object.fromEntries(Object.entries(l.input).filter(([, v]) => v !== null && v !== "")))}</li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            )}
          </li>
        ))}
      </ol>
      <div ref={bottom} />

      {!disabled && (
        <form className="ask-form" onSubmit={(e) => { e.preventDefault(); submit(question); }}>
          <label htmlFor="ask-box" className="visually-hidden">Your question</label>
          <textarea
            id="ask-box" ref={box} rows={2} maxLength={max} value={question} autoFocus
            placeholder={turns.length ? "Ask a follow-up…" : "e.g. What does the Navy request for hypersonics in FY2027?"}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(question); } }}
          />
          <button type="submit" className="primary" disabled={busy || !question.trim()}>
            {busy ? "Working…" : "Ask"}
          </button>
        </form>
      )}
    </div>
  );
}
