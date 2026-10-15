import { describe, expect, it } from "vitest";

import { errorMessage, periodParams } from "./source";

describe("errorMessage", () => {
  it("prefers the API's 400 reason", () => {
    expect(errorMessage(400, "Bad Request", "/v1/budget-cycle", '{"reason":"custom_end must not be before anchor_date"}'))
      .toBe("custom_end must not be before anchor_date");
  });
  it("joins a 422 detail list, naming the field", () => {
    const body = JSON.stringify({ detail: [
      { loc: ["body", "anchor_date"], msg: "Input should be a valid date" },
      { loc: ["body"], msg: "Field required" },
    ] });
    expect(errorMessage(422, "Unprocessable", "/v1/budget-cycle", body))
      .toBe("anchor_date: Input should be a valid date; Field required");
  });
  it("accepts a plain string detail", () => {
    expect(errorMessage(403, "Forbidden", "/x", '{"detail":"Not allowed"}')).toBe("Not allowed");
  });
  it("falls back to the status line for anything else", () => {
    expect(errorMessage(500, "Server Error", "/v1/x", "<html>")).toBe("500 Server Error for /v1/x");
    expect(errorMessage(404, "Not Found", "/v1/x", "{}")).toBe("404 Not Found for /v1/x");
    expect(errorMessage(404, "Not Found", "/v1/x", "")).toBe("404 Not Found for /v1/x");
  });
});

describe("periodParams", () => {
  it("sends the resolved window", () => {
    expect(periodParams({ from: "2026-10-01T00:00:00Z", to: "2026-10-14T00:00:00Z" }))
      .toEqual({ from: "2026-10-01T00:00:00Z", to: "2026-10-14T00:00:00Z" });
  });
});
