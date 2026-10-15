/**
 * Routing from typed entity refs (MVP plan U2 — the §1 consequence). The API returns a
 * finding plus a typed `{kind, id}` ref; it carries **no** `screen`/`screenParam`. The client
 * decides the route here, in one place, so a new screen or a renamed route is a single edit.
 */
export type EntityKind =
  | "org" | "usage" | "economics" | "my-usage" | "teams-overview" | "projects-overview"
  | "team" | "person" | "project" | "feature" | "session" | "pr";

/** A ref to something the dashboard can navigate to. `id` is required for the entity kinds. */
export type EntityRef = { kind: EntityKind; id?: string };

/** What `App`'s navigation consumes today (screen name + optional path param). */
export type Route = { screen: string; param?: string };

const NEEDS_ID: ReadonlySet<EntityKind> = new Set([
  "team", "person", "project", "feature", "session", "pr",
]);

export function entityRoute(ref: EntityRef): Route {
  if (NEEDS_ID.has(ref.kind)) {
    if (!ref.id) throw new Error(`entityRoute: kind "${ref.kind}" requires an id`);
    return { screen: ref.kind, param: ref.id };
  }
  return { screen: ref.kind };
}

/** A worth-a-look / finding payload from the API: text + action label + the entity it points at. */
export type Finding = {
  id: string;
  text: string;
  action: string;
  severity: "red" | "amber" | "green";
  entity: EntityRef;
};

export function findingRoute(finding: Finding): Route {
  return entityRoute(finding.entity);
}
