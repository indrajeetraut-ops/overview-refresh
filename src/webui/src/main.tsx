import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";
import { setDataSource } from "./data/source";
import { MockSource } from "./data/mock-source";

// Data seam (U1): default to the in-repo fixtures; `VITE_DATA_SOURCE=api` uses the live API
// (source.ts falls back to ApiSource when nothing is pinned here).
if ((import.meta.env.VITE_DATA_SOURCE ?? "mock") !== "api") {
  setDataSource(new MockSource());
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
