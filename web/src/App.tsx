import { ProgramPage } from "./ProgramPage";
import { usePath } from "./router";
import { SearchPage } from "./SearchPage";

export default function App() {
  const path = usePath();
  const m = path.match(/^\/program\/(.+)$/);
  if (m) return <ProgramPage programKey={decodeURIComponent(m[1])} />;
  return <SearchPage key="search" />;
}
