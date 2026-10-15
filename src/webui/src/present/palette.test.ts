import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { AGENT_HUE, UNKNOWN_AGENT_RAMP, agentName, assignAgentHues, categoryHue } from "./palette";

const css = readFileSync(new URL("../index.css", import.meta.url), "utf8");

/** `--name: #hex` declarations of the first block that starts with `selector {`. */
function tokens(selector: string): Record<string, string> {
  const start = css.indexOf(`${selector} {`);
  const body = css.slice(start, css.indexOf("\n}", start));
  return Object.fromEntries([...body.matchAll(/--([a-z-]+):\s*(#[0-9A-Fa-f]{6})\s*;/g)].map((m) => [m[1], m[2].toUpperCase()]));
}
const rgb = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
const dist = (a: string, b: string) => Math.hypot(...rgb(a).map((v, i) => v - rgb(b)[i]));
const nameOf = (hue: string) => hue.replace(/^var\(--|\)$/g, "");

describe("agent colours in the stylesheet", () => {
  const themes = { light: tokens(":root"), dark: tokens(".dark") };
  const pinned = Object.values(AGENT_HUE).map(nameOf);

  it("every ramp colour is defined in both themes", () => {
    for (const [theme, t] of Object.entries(themes)) {
      for (const hue of UNKNOWN_AGENT_RAMP) expect(t[nameOf(hue)], `${theme} ${hue}`).toBeDefined();
    }
  });

  it("the ramp has no repeats, no pinned agent colour, no --blue (it is --primary) and no status red", () => {
    const names = UNKNOWN_AGENT_RAMP.map(nameOf);
    expect(new Set(names).size).toBe(names.length);
    for (const n of names) {
      expect(pinned).not.toContain(n);
      expect(["blue", "primary", "accent", "red", "green", "amber"]).not.toContain(n);
    }
    expect(names.length).toBeGreaterThanOrEqual(6);
  });

  it("in light and dark, a ramp colour looks different from every pinned agent colour, red and the others", () => {
    for (const [theme, t] of Object.entries(themes)) {
      const names = UNKNOWN_AGENT_RAMP.map(nameOf);
      const against = [...pinned, "red", "primary"];
      for (const n of names) {
        for (const o of against) expect(dist(t[n], t[o]), `${theme}: ${n} vs ${o}`).toBeGreaterThan(55);
      }
      for (let i = 0; i < names.length; i++) {
        for (let j = i + 1; j < names.length; j++) expect(dist(t[names[i]], t[names[j]]), `${theme}: ${names[i]} vs ${names[j]}`).toBeGreaterThan(40);
      }
    }
  });
});

describe("assignAgentHues", () => {
  // Two names whose hash slot is the same, found rather than hard-coded.
  const names = Array.from({ length: 60 }, (_, i) => `Agent ${i}`);
  const slot = (n: string) => UNKNOWN_AGENT_RAMP.indexOf(categoryHue("agent", n) as never);
  const pair = (() => {
    for (const a of names) for (const b of names) if (a < b && slot(a) === slot(b)) return [a, b] as const;
    throw new Error("no colliding pair");
  })();

  it("pinned agents keep their colours, from ids or display names", () => {
    expect(assignAgentHues(["claude_code", "Codex", "cursor"])).toEqual({
      "Claude Code": "var(--primary)", Codex: "var(--green)", Cursor: "var(--amber)",
    });
  });

  it("two unknown agents whose names hash alike still get different colours", () => {
    expect(categoryHue("agent", pair[0])).toBe(categoryHue("agent", pair[1])); // the single-name lookup does collide
    const h = assignAgentHues([pair[0], pair[1]]);
    expect(h[pair[0]]).not.toBe(h[pair[1]]);
    expect(UNKNOWN_AGENT_RAMP).toContain(h[pair[0]]);
    expect(UNKNOWN_AGENT_RAMP).toContain(h[pair[1]]);
  });

  it("depends on the set of names, not their order or spelling", () => {
    const a = assignAgentHues([pair[1], "claude_code", pair[0], "x_y"]);
    const b = assignAgentHues(["X Y", pair[0], "Claude Code", pair[1], pair[1]]);
    expect(a).toEqual(b);
  });

  it("as many unknown agents as the ramp has colours all differ, and none uses a pinned colour", () => {
    const set = ["Claude Code", "Codex", "Cursor", ...names.slice(0, UNKNOWN_AGENT_RAMP.length)];
    const h = assignAgentHues(set);
    const unknown = names.slice(0, UNKNOWN_AGENT_RAMP.length).map((n) => h[n]);
    expect(new Set(unknown).size).toBe(UNKNOWN_AGENT_RAMP.length);
    for (const hue of unknown) expect(Object.values(AGENT_HUE)).not.toContain(hue);
  });

  it("more unknown agents than colours reuse some, deterministically", () => {
    const many = names.slice(0, UNKNOWN_AGENT_RAMP.length + 3);
    const h = assignAgentHues(many);
    expect(Object.keys(h)).toHaveLength(many.length);
    expect(assignAgentHues([...many].reverse())).toEqual(h);
    for (const n of many) expect(UNKNOWN_AGENT_RAMP).toContain(h[n]);
  });

  it("a missing id is the one agent called Unknown", () => {
    expect(assignAgentHues(["", "  ", "_"])).toEqual({ Unknown: expect.stringMatching(/^var\(--/) });
    expect(agentName(null)).toBe("Unknown");
  });
});
