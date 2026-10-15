/**
 * Overview "PR costs & forecast" card: an org-scoped, compact read of the same E1 endpoint the
 * Economics screen renders in full. It keeps its OWN fetch and `<Block>` (keyed on the timeframe,
 * as before), so it can cold-start or fail independently of the org-overview payload. The
 * median / tail sentence, the envelope + forecast-cone chart with a compact legend, and a link to
 * the full unit economics.
 */
import { Block, UnavailableNote } from "./Block";
import { CostEnvelopeChart } from "./charts";
import { Card, LinkButton, fmtUSD } from "./ui";
import { useData } from "../data/source";
import type { Schemas } from "../data/source";
import { costEnvelopeLabel, costEnvelopePoints } from "../present/overview";
import { useTimeframe } from "../shell/timeframe";

const HEIGHT = 270;

export default function UnitEconomicsCard({ onNavigate }: { onNavigate: (screen: string, param?: string) => void }) {
  const { timeframe } = useTimeframe();
  const detail = useData((src) => src.economics({ scopeType: "org", scopeId: "org" }, timeframe), [timeframe]);
  return (
    <Card title="PR costs & forecast" footer={<LinkButton onClick={() => onNavigate("economics")}>See full unit economics</LinkButton>}>
      <Block state={detail} minHeight={HEIGHT + 60}>
        {(data: Schemas["Economics"]) => {
          const { cost_dist, pr_cost_envelope, forecast_cone } = data;
          if (!pr_cost_envelope) {
            return <UnavailableNote minHeight={HEIGHT + 60} info={{ block: "economics_forecast", requires: "merged PRs org-wide" }} />;
          }
          return (
            <>
              {cost_dist && (
                <p className="text-sm" style={{ color: "var(--muted-foreground)" }}>
                  A PR costs <span style={{ color: "var(--foreground)", fontWeight: 500 }}>{fmtUSD(cost_dist.p50)}</span> at the median — the tail runs to {fmtUSD(cost_dist.p95)}.
                </p>
              )}
              <div role="group" aria-label={costEnvelopeLabel(pr_cost_envelope, forecast_cone)}>
                <CostEnvelopeChart data={costEnvelopePoints(pr_cost_envelope, forecast_cone)} height={HEIGHT} compact />
              </div>
            </>
          );
        }}
      </Block>
    </Card>
  );
}
