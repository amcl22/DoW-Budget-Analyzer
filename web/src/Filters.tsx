import type { Facets, Query } from "./api";

interface Props {
  facets: Facets | null;
  query: Query;
  cycle: string | null;
  latest: string | undefined;
  onChange: (patch: Partial<Query>) => void;
  onReset: () => void;
}

const EXHIBITS = [
  { value: "", label: "All" },
  { value: "R-1", label: "RDT&E (R-1)" },
  { value: "P-1", label: "Procurement (P-1)" },
];

export function Filters({ facets, query, cycle, latest, onChange, onReset }: Props) {
  const accounts = (facets?.accounts ?? []).filter((a) => !query.exhibit || a.exhibit === query.exhibit);
  const activities = (facets?.budget_activities ?? []).filter((b) => !query.exhibit || b.exhibit === query.exhibit);
  const toggleService = (s: string) =>
    onChange({ service: query.service.includes(s) ? query.service.filter((x) => x !== s) : [...query.service, s] });
  const active =
    query.exhibit || query.service.length || query.account || query.budgetActivity || query.minM || query.maxM ||
    query.q || (cycle !== latest);

  return (
    <section className="filters" aria-label="Filters">
      <label className="field">
        <span>Release</span>
        <select
          value={cycle ?? ""}
          onChange={(e) => onChange({ cycle: e.target.value, account: "", budgetActivity: "" })}
        >
          {(facets?.cycles ?? []).map((c) => (
            <option key={c.budget_cycle} value={c.budget_cycle}>
              {c.budget_cycle} (FY{c.fiscal_year})
            </option>
          ))}
          <option value="">All releases</option>
        </select>
      </label>

      <div className="field">
        <span>Exhibit</span>
        <div className="segmented" role="radiogroup" aria-label="Exhibit">
          {EXHIBITS.map((x) => (
            <button
              key={x.value}
              role="radio"
              aria-checked={query.exhibit === x.value}
              className={query.exhibit === x.value ? "on" : ""}
              onClick={() => onChange({ exhibit: x.value, account: "", budgetActivity: "" })}
            >
              {x.label}
            </button>
          ))}
        </div>
      </div>

      <div className="field">
        <span>Service</span>
        <div className="chips">
          {(facets?.services ?? []).map((s) => (
            <button
              key={s.value}
              aria-pressed={query.service.includes(s.value)}
              className={query.service.includes(s.value) ? "chip on" : "chip"}
              onClick={() => toggleService(s.value)}
            >
              {s.value}
            </button>
          ))}
        </div>
      </div>

      <label className="field grow">
        <span>Appropriation</span>
        <select value={query.account} onChange={(e) => onChange({ account: e.target.value })}>
          <option value="">All appropriations</option>
          {accounts.map((a) => (
            <option key={a.value} value={a.value}>
              {a.value} · {a.title}
            </option>
          ))}
        </select>
      </label>

      <label className="field grow">
        <span>Budget activity</span>
        <select value={query.budgetActivity} onChange={(e) => onChange({ budgetActivity: e.target.value })}>
          <option value="">All budget activities</option>
          {activities.map((b) => (
            <option key={`${b.exhibit}|${b.value}`} value={b.value}>
              {query.exhibit ? b.value : `${b.exhibit} · ${b.value}`}
            </option>
          ))}
        </select>
      </label>

      <div className="field">
        <span>Budget year ($M)</span>
        <div className="range">
          <input inputMode="decimal" placeholder="min" value={query.minM} aria-label="Minimum budget-year amount, $ millions"
            onChange={(e) => onChange({ minM: e.target.value })} />
          <span aria-hidden="true">–</span>
          <input inputMode="decimal" placeholder="max" value={query.maxM} aria-label="Maximum budget-year amount, $ millions"
            onChange={(e) => onChange({ maxM: e.target.value })} />
        </div>
      </div>

      {active ? <button className="reset" onClick={onReset}>Clear all</button> : null}
    </section>
  );
}
