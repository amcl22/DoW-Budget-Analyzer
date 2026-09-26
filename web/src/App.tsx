import { AskPage } from "./AskPage";
import { ProgramPage } from "./ProgramPage";
import { usePath } from "./router";
import { SearchPage } from "./SearchPage";
import { WatchlistPage } from "./WatchlistPage";

export default function App() {
  const path = usePath();
  const m = path.match(/^\/program\/(.+)$/);
  if (m) return <ProgramPage programKey={decodeURIComponent(m[1])} />;
  if (path === "/watchlist") return <WatchlistPage />;
  if (path === "/ask") return <AskPage />;
  return <SearchPage key="search" />;
}
