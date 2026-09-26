import { useEffect, useState } from "react";
import { askEnabled } from "./api";
import { Link } from "./router";

/** "Ask a question", shown only where the server has Q&A switched on. */
export function AskLink({ className }: { className: string }) {
  const [enabled, setEnabled] = useState(false);
  useEffect(() => {
    let live = true;
    askEnabled().then((on) => { if (live) setEnabled(on); });
    return () => { live = false; };
  }, []);
  return enabled ? <Link className={className} href="/ask">Ask a question</Link> : null;
}
