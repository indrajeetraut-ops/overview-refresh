import { describe, expect, it } from "vitest";

import { buildCrumbs, collapseCrumbs, isEllipsis, labelKey, mergeLabels, scopeLabels } from "./crumbs";

const labels = (c: { label: string }[]) => c.map((x) => x.label);

describe("buildCrumbs: roots", () => {
  it("overview is the single crumb Organization", () => {
    const c = buildCrumbs(["overview"]);
    expect(labels(c)).toEqual(["Organization"]);
    expect(c[0].current).toBe(true);
    expect(c[0].target).toEqual({ kind: "stack", index: 0 });
  });

  it("other manager sections sit under an Organization crumb that resets to Overview", () => {
    for (const [screen, label] of [
      ["usage", "Usage"], ["economics", "Economics"], ["teams-overview", "Teams"],
      ["projects-overview", "Projects"], ["configure", "Configure"],
    ] as const) {
      const c = buildCrumbs([screen]);
      expect(labels(c)).toEqual(["Organization", label]);
      expect(c[0].target).toEqual({ kind: "screen", screen: "overview" });
      expect(c[1].target).toEqual({ kind: "stack", index: 0 });
      expect(c.map((x) => x.current)).toEqual([false, true]);
    }
  });

  it("my-usage (a dev viewer) has no Organization crumb", () => {
    expect(labels(buildCrumbs(["my-usage"]))).toEqual(["My usage"]);
  });
});

describe("buildCrumbs: trail", () => {
  it("is the stack as walked, with no synthesised Teams crumb", () => {
    const c = buildCrumbs(["overview", { type: "team", id: "platform" }], {
      [labelKey("team", "platform")]: "Platform",
    });
    expect(labels(c)).toEqual(["Organization", "Platform"]);
  });

  it("walks Teams > team > person with registry names", () => {
    const c = buildCrumbs(
      ["teams-overview", { type: "team", id: "t1" }, { type: "person", id: "p1" }],
      { [labelKey("team", "t1")]: "Platform", [labelKey("person", "p1")]: "Ada Lovelace" },
    );
    expect(labels(c)).toEqual(["Organization", "Teams", "Platform", "Ada Lovelace"]);
    // indices point at the stack, so a click can slice it
    expect(c.slice(1).map((x) => x.target)).toEqual([
      { kind: "stack", index: 0 }, { kind: "stack", index: 1 }, { kind: "stack", index: 2 },
    ]);
    expect(c.map((x) => x.current)).toEqual([false, false, false, true]);
  });

  it("falls back to a generic noun while a name is unknown, and for blank names", () => {
    const stack = [
      "overview", { type: "team", id: "a" }, { type: "person", id: "b" }, { type: "project", id: "c" },
      { type: "feature", id: "d" }, { type: "session", id: "e" }, { type: "pr", id: "f" },
    ] as const;
    expect(labels(buildCrumbs(stack))).toEqual(
      ["Organization", "Team", "Person", "Project", "Feature", "Session", "PR"],
    );
    expect(labels(buildCrumbs(stack, { [labelKey("team", "a")]: "  " }))[1]).toBe("Team");
  });

  it("names a later section entry plainly", () => {
    expect(labels(buildCrumbs(["overview", "economics"]))).toEqual(["Organization", "Economics"]);
  });

  it("an empty stack has no crumbs", () => {
    expect(buildCrumbs([])).toEqual([]);
  });
});

describe("collapseCrumbs", () => {
  const long = buildCrumbs([
    "teams-overview", { type: "team", id: "t" }, { type: "person", id: "p" },
    { type: "session", id: "s" }, { type: "pr", id: "r" },
  ]);

  it("leaves a trail of four or fewer alone", () => {
    expect(collapseCrumbs(long.slice(0, 4))).toHaveLength(4);
  });

  it("keeps the first crumb, an ellipsis and the last two", () => {
    const out = collapseCrumbs(long);
    expect(out).toHaveLength(4);
    expect(out[0]).toEqual(long[0]);
    expect(isEllipsis(out[1]) && out[1].hidden).toBe(long.length - 3);
    expect(out.slice(2)).toEqual(long.slice(-2));
  });

  it("shows everything when expanded", () => {
    expect(collapseCrumbs(long, true)).toEqual(long);
  });
});

describe("scopeLabels", () => {
  it("keys team and project names like the registry does", () => {
    const l = scopeLabels({ teams: [{ id: "t1", name: "Platform" }], projects: [{ id: "p1", name: "Atlas" }] });
    expect(l).toEqual({ "team:t1": "Platform", "project:p1": "Atlas" });
    expect(labels(buildCrumbs(["overview", { type: "team", id: "t1" }], l))).toEqual(["Organization", "Platform"]);
  });

  it("is empty without scopes", () => {
    expect(scopeLabels(null)).toEqual({});
  });
});

describe("mergeLabels", () => {
  const fallback = { "team:t1": "Platform (scopes)", "project:p1": "Atlas" };

  it("lets a published name win over the scopes() fallback", () => {
    const m = mergeLabels(fallback, { "team:t1": "Platform Engineering" });
    expect(m["team:t1"]).toBe("Platform Engineering");
    expect(m["project:p1"]).toBe("Atlas");
  });

  it("does not let a blank published name hide the fallback", () => {
    const m = mergeLabels(fallback, { "team:t1": "   ", "person:x": "Ada" });
    expect(m["team:t1"]).toBe("Platform (scopes)");
    expect(m["person:x"]).toBe("Ada");
  });

  it("works without a fallback, and feeds buildCrumbs", () => {
    expect(mergeLabels(undefined, { "team:t1": "A" })).toEqual({ "team:t1": "A" });
    const c = buildCrumbs(["overview", { type: "team", id: "t1" }], mergeLabels(fallback, {}));
    expect(labels(c)).toEqual(["Organization", "Platform (scopes)"]);
  });
});
