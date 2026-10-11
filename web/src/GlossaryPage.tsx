import { useEffect } from "react";
import { Link } from "./router";
import { lastSearchUrl } from "./SearchPage";

const TERMS: [string, string][] = [
  ["DoW / DoD", "Department of War, also styled the Department of Defense."],
  ["PB", "President's Budget: the administration's annual budget request to Congress. PB2027 is the request for fiscal year 2027."],
  ["FY", "Fiscal year, October 1 to September 30. FY2027 starts October 1, 2026."],
  ["PY / CY / BY", "Prior year (an actual), current year (enacted, or an estimate) and budget year (the request). In PB2027 they are FY2025, FY2026 and FY2027."],
  ["RDT&E", "Research, Development, Test and Evaluation: money to invent, prototype and test, before something is bought in quantity."],
  ["R-1", "The RDT&E summary exhibit: one line per program element, with its prior, current and budget-year amounts."],
  ["R-2 / R-2A", "RDT&E justification pages: the mission description, cost by year (including out-year estimates), and the reasons for funding changes, by program (R-2) and by project (R-2A)."],
  ["P-1", "The procurement summary exhibit: one line per budget line item, with quantities and cost."],
  ["P-40", "Procurement justification page for one line item (not yet loaded in this tool)."],
  ["PE", "Program element: the RDT&E line number, e.g. 0604853F. The ending shows who owns it: A Army, N Navy, F Air Force, SF Space Force; other endings (D8Z, E, BR, ...) are Defense-Wide agencies."],
  ["BLI", "Budget line item: the procurement line number, e.g. CTRSPC."],
  ["Appropriation / account", "The pot of money Congress provides, e.g. 3600F (RDT&E, Air Force) or 3022F (Procurement, Space Force)."],
  ["BA", "Budget activity: a grouping inside an account. RDT&E runs from BA 1 Basic Research and BA 2 Applied Research through BA 4 Advanced Component Development & Prototypes, BA 5 System Development & Demonstration, BA 6 Management Support and BA 7 Operational Systems Development, to BA 8 Software & Digital Technology Pilots."],
  ["TOA", "Total obligational authority: the total the exhibits add up. Accounts listed outside an exhibit's title are shown but not counted in its totals (marked “not in title”)."],
  ["Discretionary / mandatory", "Discretionary money is set in annual appropriations; mandatory money comes from other laws. FY2026 and FY2027 figures include defense funding from P.L. 119-21 (the 2025 reconciliation act) as mandatory amounts. Amounts here are the total of both."],
  ["Advance procurement (AP)", "Money in one year for parts with long lead times on something bought in a later year."],
  ["CR", "Continuing resolution: temporary funding at the prior year's level when appropriations aren't enacted on time."],
  ["SBIR / STTR", "Small Business Innovation Research / Small Business Technology Transfer: set-asides moved out of programs each year (often seen as small adjustments)."],
  ["DARPA, MDA, SOCOM, OSD", "Defense Advanced Research Projects Agency, Missile Defense Agency, Special Operations Command, Office of the Secretary of Defense: Defense-Wide organizations with their own programs."],
  ["Classified Programs", "Rollup lines for classified work. Only the total is published; there is no detail to show."],
];

const READING: [string, string][] = [
  ["Amounts", "In $ millions in tables and charts (the exhibits publish $ thousands; exports keep $ thousands)."],
  ["$ change and % change", "Budget year minus current year. The percentage is flagged at ±20% or more."],
  ["New start", "No current-year funding and a budget-year request: a percentage would be meaningless, so none is shown."],
  ["Ending", "Current-year funding but nothing requested in the budget year."],
  ["Small base", "Current year under $5 million: the percentage can look extreme (e.g. Counterspace Systems procurement, $2.0M to $459.5M, +22,567%), so compare the $ change."],
  ["Best available figure", "For each fiscal year, the actual once reported, else the enacted amount, else the request, from the newest release that reports it."],
  ["Source links", "Every line links to the page of the official PDF it came from; the amounts were matched to that page when the data was loaded."],
];

export function GlossaryPage() {
  useEffect(() => {
    document.title = "Glossary & coverage · DoW Budget Search";
    return () => { document.title = "DoW Budget Search"; };
  }, []);
  return (
    <div className="app glossary-page">
      <Link className="back" href={lastSearchUrl()}>← Back to search</Link>
      <h1>Glossary &amp; coverage</h1>

      <section className="panel">
        <h2>What's in the data</h2>
        <ul className="plain-list">
          <li><strong>RDT&amp;E programs (R-1)</strong> and <strong>procurement lines (P-1)</strong> from the President's Budget
            requests for FY2024, FY2025, FY2026 and FY2027 (about 8,300 line items), covering the Army, Navy and Marine Corps,
            Air Force, Space Force and Defense-Wide agencies.</li>
          <li>Each release's prior-year actual, current-year amount and budget-year request, with quantities for procurement.</li>
          <li><strong>R-2 justification detail</strong> (mission descriptions, out-year estimates and the official reasons for
            funding changes) for <strong>Defense-Wide</strong> programs in all four releases and <strong>Navy</strong> programs in FY2027.</li>
        </ul>
      </section>

      <section className="panel">
        <h2>What's not included</h2>
        <ul className="plain-list">
          <li><strong>Other parts of the budget:</strong> Operation &amp; Maintenance (O&amp;M), Military Personnel (MILPERS),
            Military Construction and Family Housing (MILCON), and revolving funds. Only RDT&amp;E and procurement are covered.</li>
          <li><strong>Army, Air Force and Space Force R-2 justification books</strong>: those services' websites block automated
            downloads, so their programs have amounts and source pages but no descriptions or explanations yet.</li>
          <li><strong>Procurement justification pages (P-40, P-5)</strong>, so procurement lines have no official change explanations yet.</li>
          <li><strong>What Congress finally enacts</strong> for the budget year: figures are requests until a later release reports them as enacted or actual.</li>
          <li><strong>Releases before PB2024</strong>, and any detail of classified programs.</li>
        </ul>
      </section>

      <section className="panel">
        <h2>Reading the numbers</h2>
        <dl className="terms">
          {READING.map(([t, d]) => <div key={t}><dt>{t}</dt><dd>{d}</dd></div>)}
        </dl>
      </section>

      <section className="panel">
        <h2>Acronyms and terms</h2>
        <dl className="terms">
          {TERMS.map(([t, d]) => <div key={t}><dt>{t}</dt><dd>{d}</dd></div>)}
        </dl>
      </section>
    </div>
  );
}
