import { describe, expect, it } from "vitest";

import { canSeeManagerNav } from "./viewer";

describe("role gate (F2)", () => {
  it("hides the manager nav from a dev", () => {
    expect(canSeeManagerNav("dev")).toBe(false);
    expect(canSeeManagerNav("lead")).toBe(true);
    expect(canSeeManagerNav("admin")).toBe(true);
  });
});
