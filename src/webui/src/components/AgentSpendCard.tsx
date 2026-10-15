/**
 * Overview "Spend by agent" card: the spend donut with its share / dollar list (`agent_spend`,
 * which sums to the headline), and under it "Task usage" (`agent_task_mix`: tabs All + one per
 * agent, a bar per session type, share by session count). Names and colours go through
 * `agentName` / `categoryHue`, so the raw ids the API sends and the display names the mock sends
 * agree everywhere in the card.
 */
import { UnavailableNote } from "./Block";
import { AgentDonut } from "./charts";
import { Card, TaskUsageBars } from "./ui";
import type { Schemas } from "../data/source";
import { agentHueMap, agentSlices } from "../present/overview";

export default function AgentSpendCard({ agentSpend, taskMix }: {
  agentSpend: Schemas["AgentSpend"][];
  taskMix: Schemas["AgentTaskMix"][];
}) {
  // One colour map for the donut, its list, the tabs and the bars.
  const hues = agentHueMap(agentSpend, taskMix);
  const slices = agentSlices(agentSpend, hues);
  return (
    <Card title="Spend by agent">
      {slices.length > 0
        ? <AgentDonut data={slices} bare />
        : <UnavailableNote info={{ block: "agent_spend", requires: "session activity in range" }} minHeight={160} />}
      <div className="mt-1 flex flex-col gap-2 pt-3" style={{ borderTop: "1px solid var(--border)" }}>
        <h3 className="text-[10px] font-medium uppercase tracking-wider" style={{ color: "var(--muted-foreground)" }}>Task usage</h3>
        {taskMix.length > 0
          ? <TaskUsageBars mix={taskMix} agentHues={hues} />
          : <UnavailableNote info={{ block: "agent_task_mix", requires: "sessions classified by type in this window" }} minHeight={120} />}
      </div>
    </Card>
  );
}
