import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import { AppRouter } from "./AppRouter";
import "./styles.css";

// V2-P5-014. `BrowserRouter` here, `MemoryRouter` in tests: the router lives at the root and
// `AppRouter` contains only `<Routes>`, so the component under test never brings its own
// history along. Real paths (not hashes) because the dev server and the FastAPI app both
// serve the SPA shell, and a research terminal's addresses are meant to be pasted to people.
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <AppRouter />
    </BrowserRouter>
  </StrictMode>
);
