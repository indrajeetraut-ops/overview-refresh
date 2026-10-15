import { describe, expect, it } from "vitest";

import type { DataState, Envelope } from "../data/source";
import { resolveBlockState } from "./Block";

const AGG = { modes_present: ["both"], sessions_by_mode: { both: 1 } };
const ready = <T,>(env: Envelope<T>): DataState<Envelope<T>> => ({ status: "ready", data: env, error: null });

describe("resolveBlockState — the five states are distinct", () => {
  it("loading", () => {
    expect(resolveBlockState({ status: "loading", data: null, error: null }).kind).toBe("loading");
  });

  it("error", () => {
    const s = resolveBlockState({ status: "error", data: null, error: new Error("x") });
    expect(s.kind).toBe("error");
  });

  it("unavailable — envelope carries {unavailable}", () => {
    const s = resolveBlockState(ready({ data: null, mode: AGG, unavailable: { block: "prs", requires: "github" } }));
    expect(s).toEqual({ kind: "unavailable", info: { block: "prs", requires: "github" } });
  });

  it("cold-start — data null, no unavailable (NOT the same as unavailable)", () => {
    const s = resolveBlockState(ready({ data: null, mode: AGG, unavailable: null }));
    expect(s.kind).toBe("cold-start");
  });

  it("empty — data present but isEmpty()", () => {
    const s = resolveBlockState(ready({ data: { rows: [] }, mode: AGG, unavailable: null }),
      { isEmpty: (d) => d.rows.length === 0 });
    expect(s.kind).toBe("empty");
  });

  it("ready — hands back the non-null payload", () => {
    const s = resolveBlockState(ready({ data: { rows: [1, 2] }, mode: AGG, unavailable: null }),
      { isEmpty: (d) => d.rows.length === 0 });
    expect(s).toEqual({ kind: "ready", data: { rows: [1, 2] } });
  });

  it("a present payload with a still-computing section reads as cold-start, not empty", () => {
    const s = resolveBlockState(ready({ data: { rows: [1], forecast: null }, mode: AGG, unavailable: null }),
      { isColdStart: (d) => d.forecast === null, isEmpty: (d) => d.rows.length === 0 });
    expect(s.kind).toBe("cold-start");
  });
});
