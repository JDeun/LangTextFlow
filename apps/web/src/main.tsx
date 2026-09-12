import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { PreflightPanel } from "./PreflightPanel";
import "./styles.css";

const isOperatorRoute = !/^\/(audience|display)\//.test(window.location.pathname);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {isOperatorRoute && <PreflightPanel />}
    <App />
  </StrictMode>,
);
