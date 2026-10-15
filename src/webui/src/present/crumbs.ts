/**
 * Breadcrumb trail for the top bar (Overview refresh D2). The trail IS the navigation stack:
 * `App.tsx` pushes on every drill-down and a sidebar click resets it, so the crumbs read as the
 * path the viewer actually took (no synthesised "Teams" / "Projects" parents). Pure and tested;
 * the component only lays the result out.
 *
 * Entity names come from a registry the screens fill once their own detail has loaded
 * (`shell/crumbs.tsx`), so no crumb costs a request. Until a name is known the crumb falls back
 * to a generic noun ("Team", "Person", ...).
 */

/** A navigation-stack entry: a section id, or a typed entity (the shape of `ScreenId`). */
export type StackEntry = string | { type: string; id: string };

/** Where a crumb goes: back to a stack index, or to a section root (resets the stack). */
export type CrumbTarget = { kind: "stack"; index: number } | { kind: "screen"; screen: "overview" };

export type Crumb = { key: string; label: string; target: CrumbTarget; current: boolean };

/** Shown in place of a collapsed run of crumbs. `hidden` is how many it stands for. */
export type CrumbEllipsis = { key: string; ellipsis: true; hidden: number };

const SECTION_LABEL: Record<string, string> = {
  overview: "Overview",
  usage: "Usage",
  economics: "Economics",
  "teams-overview": "Teams",
  "projects-overview": "Projects",
  configure: "Configure",
  "my-usage": "My usage",
};

/** Section roots that sit under "Organization" (manager surfaces other than Overview itself). */
const ORG_CHILDREN = new Set(["usage", "economics", "teams-overview", "projects-overview", "configure"]);

const KIND_NOUN: Record<string, string> = {
  team: "Team", person: "Person", project: "Project", feature: "Feature", session: "Session", pr: "PR",
};

/** Names from `GET /v1/scopes` as crumb labels: what a team / project crumb says until its own
 * detail has loaded and published the name. */
export function scopeLabels(
  scopes: { teams: readonly { id: string; name: string }[]; projects: readonly { id: string; name: string }[] } | null,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const t of scopes?.teams ?? []) out[labelKey("team", t.id)] = t.name;
  for (const p of scopes?.projects ?? []) out[labelKey("project", p.id)] = p.name;
  return out;
}

/** The labels a crumb uses: names published by the screens win over the `scopes()` fallbacks,
 * except a blank published name, which never hides a usable fallback. */
export function mergeLabels(
  fallback: Readonly<Record<string, string>> | undefined,
  registry: Readonly<Record<string, string>>,
): Record<string, string> {
  const out: Record<string, string> = { ...fallback };
  for (const [k, v] of Object.entries(registry)) if (v.trim()) out[k] = v;
  return out;
}

/** Registry key for an entity's name. */
export function labelKey(type: string, id: string): string {
  return `${type}:${id}`;
}

function entryLabel(entry: StackEntry, labels: Readonly<Record<string, string>>): string {
  if (typeof entry === "string") return SECTION_LABEL[entry] ?? entry;
  return labels[labelKey(entry.type, entry.id)]?.trim() || KIND_NOUN[entry.type] || entry.type;
}

/** The full trail, oldest first; the last crumb is the current page. Empty stack gives no crumbs. */
export function buildCrumbs(stack: readonly StackEntry[], labels: Readonly<Record<string, string>> = {}): Crumb[] {
  if (stack.length === 0) return [];
  const crumbs: Omit<Crumb, "current">[] = [];
  const first = stack[0];
  if (first === "overview") {
    crumbs.push({ key: "0", label: "Organization", target: { kind: "stack", index: 0 } });
  } else {
    if (typeof first === "string" && ORG_CHILDREN.has(first)) {
      crumbs.push({ key: "org", label: "Organization", target: { kind: "screen", screen: "overview" } });
    }
    crumbs.push({ key: "0", label: entryLabel(first, labels), target: { kind: "stack", index: 0 } });
  }
  for (let i = 1; i < stack.length; i++) {
    crumbs.push({ key: String(i), label: entryLabel(stack[i], labels), target: { kind: "stack", index: i } });
  }
  return crumbs.map((c, i) => ({ ...c, current: i === crumbs.length - 1 }));
}

/** More than `max` crumbs collapse to the first, an ellipsis and the last two. */
export function collapseCrumbs(crumbs: readonly Crumb[], expanded = false, max = 4): (Crumb | CrumbEllipsis)[] {
  if (expanded || crumbs.length <= max) return [...crumbs];
  return [
    crumbs[0],
    { key: "ellipsis", ellipsis: true, hidden: crumbs.length - 3 },
    ...crumbs.slice(-2),
  ];
}

export function isEllipsis(c: Crumb | CrumbEllipsis): c is CrumbEllipsis {
  return "ellipsis" in c;
}
