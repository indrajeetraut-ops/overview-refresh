/** Seat presentation (cost-based seats plan, 2026-09-26): a seat's usage is its spend over its
 * dollar allowance -- the team's per-period budget / members, for the selected window and prorated
 * from the seat's start. The API sends `usage_pct: null` when there is no allowance to compare
 * against (no team budget, or no seat yet); that is "unknown", never 0%. */

/** The subset of a ranked person these helpers read (`Schemas["PersonRanked"]`). */
export type SeatPerson = {
  name: string;
  status: string;
  usage_status?: string | null;
  usage_pct?: number | null;
  low_use: boolean;
  in_grace?: boolean;
};

export type SeatChartPoint = { name: string; usage_pct: number; fill: string };

/** Chart points for "Seat utilisation per person". People with no allowance are LEFT OUT --
 * plotting them at 0% would read as the lowest usage on the team when it is simply unknown.
 * Bars are coloured by the seat band (`usage_status`), not by room to save: this chart is spend
 * vs allowance, so an over-used seat is red whatever its recoverable spend. */
export function seatUsageChartData(people: SeatPerson[]): SeatChartPoint[] {
  return people
    .filter((p) => p.usage_pct != null)
    .map((p) => ({
      name: p.name.split(" ")[0],
      usage_pct: p.usage_pct as number,
      fill: p.usage_status === "over" ? "var(--red)" : p.low_use ? "var(--amber)" : "var(--primary)",
    }));
}

/** The status chip for a ranked person, straight from the API's headline `status`: room to save,
 * then the seat band -- "High use" (at or over the `over` band), "Low use", "New seat" (a seat in
 * its grace period that would otherwise read low use) -- and "On track" only when nothing flags.
 * Same labels as the Usage screen's per-person chips. */
export function seatChip(p: SeatPerson): { label: string; variant: "amber" | "green" | "muted" | "red" } {
  if (p.status === "room-to-save") return { label: "Room to save", variant: "amber" };
  if (p.status === "over") return { label: "High use", variant: "red" };
  if (p.status === "under") return { label: "Low use", variant: "amber" };
  if (p.status === "new") return { label: "New seat", variant: "muted" };
  return { label: "On track", variant: "green" };
}
