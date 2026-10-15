import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { MONO_FONT } from "./fonts";

const css = readFileSync(new URL("../index.css", import.meta.url), "utf8");

describe("MONO_FONT", () => {
  it("is exactly the stack .font-mono uses in index.css", () => {
    const m = css.match(/\.font-mono\s*\{\s*font-family:\s*([^;]+);/);
    expect(m).not.toBeNull();
    expect(m![1].trim()).toBe(MONO_FONT);
  });

  it("is also the stack of .chip, and the Google Fonts import loads its first family", () => {
    const chip = css.match(/\.chip\s*\{[^}]*font-family:\s*([^;]+);/);
    expect(chip![1].trim()).toBe(MONO_FONT);
    expect(css).toContain("family=JetBrains+Mono");
  });

  it("sets Inter once on body (not on *, which would override .font-mono's children)", () => {
    const body = css.match(/\nbody\s*\{[^}]*font-family:\s*([^;]+);/);
    expect(body![1].trim()).toBe("'Inter', system-ui, sans-serif");
    expect(css).not.toMatch(/\n\*\s*\{[^}]*font-family/);
    expect(css).toMatch(/button, input, select, textarea\s*\{\s*font-family:\s*inherit;/);
  });

  it("leaves no retired font behind", () => {
    expect(css).not.toMatch(/Outfit|DM Mono|DM\+Mono/);
  });
});
