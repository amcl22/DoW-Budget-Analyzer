import { AskPage } from "./AskPage";
import { GlossaryPage } from "./GlossaryPage";
import { ProgramPage } from "./ProgramPage";
import { Link, usePath } from "./router";
import { SearchPage } from "./SearchPage";
import { ValidationPage } from "./ValidationPage";
import { WatchlistPage } from "./WatchlistPage";

function Page({ path }: { path: string }) {
  const m = path.match(/^\/program\/(.+)$/);
  if (m) return <ProgramPage programKey={decodeURIComponent(m[1])} />;
  if (path === "/watchlist") return <WatchlistPage />;
  if (path === "/ask") return <AskPage />;
  if (path === "/glossary") return <GlossaryPage />;
  if (path === "/validation") return <ValidationPage />;
  return <SearchPage key="search" />;
}

export default function App() {
  const path = usePath();
  return (
    <>
      <Page path={path} />
      <footer className="site-footer">
        <span>Source: President's Budget R-1, P-1 and R-2 exhibits published by the DoW Comptroller and the services.</span>
        <span className="footer-links"><Link href="/validation">Validation Studio</Link><Link href="/glossary">Glossary &amp; what's covered</Link></span>
      </footer>
    </>
  );
}
