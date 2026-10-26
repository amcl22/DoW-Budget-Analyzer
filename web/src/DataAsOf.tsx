import { useEffect, useState } from "react";
import { fetchStatus, type DataStatus } from "./api";
import { fmtDate } from "./format";
import { Link } from "./router";

let cached: Promise<DataStatus> | null = null;

/** When the data was loaded, from which releases, and that it passed review before publishing. */
export function DataAsOf({ long = false }: { long?: boolean }) {
  const [s, setS] = useState<DataStatus | null>(null);
  useEffect(() => {
    cached ??= fetchStatus();
    cached.then(setS, () => setS(null));
  }, []);
  if (!s || s.releases.length === 0) return null;
  const loaded = s.releases.map((r) => r.loaded_at ?? "").sort().slice(-1)[0];
  const cycles = [...new Set(s.releases.map((r) => r.budget_cycle))].sort();
  const latest = cycles[cycles.length - 1];
  const fy = Number(latest.slice(2));
  return (
    <span className="data-as-of">
      Data as of <strong>{loaded ? fmtDate(loaded) : "—"}</strong> · President's Budget {cycles[0]}–{latest}
      {" · "}every release passed validation and was published by the data owner
      {" ("}<Link href="/validation">checks</Link>{")"}
      {long && (
        <> · FY{fy - 1} “current” figures are as reported in {latest}; later enacted appropriations aren't loaded.</>
      )}
    </span>
  );
}
