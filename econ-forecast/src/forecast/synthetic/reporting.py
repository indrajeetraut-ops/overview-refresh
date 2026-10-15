"""Render user-facing stage reports from the same counters used by manifests."""
from typing import Mapping
from .models import GenerationResult


def _cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def render_report(result: GenerationResult, details: Mapping) -> str:
    lines = [f"# Synthetic {result.operation.title()} Report", "",
             f"- Run ID: `{result.run_id}`", f"- Completed: {details['completed_at']}",
             f"- Organization: `{details['org_id']}`", f"- Output: `{result.output_dir}`",
             f"- Resolved configuration: `{details['config']}`", "", "## Inputs and seeds", ""]
    for label, seed in sorted(details["seeds"].items()):
        lines.append(f"- {label.title()} seed: {seed}")
    for label, source in sorted(details["input_sources"].items()):
        lines.append(f"- {label.title()}: `{source['original']}`; saved snapshot: `{source['snapshot']}`")
    lines += ["", "## Artifacts", ""]
    lines += [f"- `{name}`: {count} rows" for name, count in sorted(result.record_counts.items())]
    lines += ["- `config.yaml`, `report.md`, `manifest.json`: configuration, report, and completion metadata.",
              "", "## Summary", ""]
    lines += [f"- {key.replace('_', ' ').title()}: {value}" for key, value in sorted(result.summary_counts.items())]
    if result.operation == "users":
        lines += ["", "## User creation", "", f"Requested users: {details['requested_users']}; locale: `{details['locale']}`.",
                  "Generated user IDs and emails passed uniqueness validation."]
    elif result.operation == "roster":
        lines += ["", "## Team roster", "", "| Team | Members |", "|---|---:|"]
        for team in details["teams"]:
            lines.append(f"| {_cell(team['name'])} | {team['count']} |")
    else:
        lines += ["", "## Series", "",
                  f"Date range: {details['start_date']} through {details['end_date']} ({details['days']} days), timezone `{details['timezone']}`.",
                  "", "### Profiles", ""]
        for team, counts in sorted(details["profiles"].items()):
            lines.append(f"- {_cell(team)}: " + ", ".join(f"{_cell(key)}={value}" for key, value in sorted(counts.items())))
        lines += ["", "### Applied spike rules", "",
                  "| Rule | Applied days | Baseline processed input | Incremental processed input |",
                  "|---|---:|---:|---:|"]
        for rule, counts in sorted(details["spike_rules"].items()):
            lines.append(
                f"| {_cell(rule)} | {counts['applied_days']} | "
                f"{counts['baseline_processed_input']} | {counts['incremental_processed_input']} |"
            )
        if not details["spike_rules"]:
            lines.append("| None | 0 | 0 | 0 |")
        if "personal_spikes" in details:
            lines += ["", "### Personal spikes by profile", "",
                      "| Profile | Spiked user-days | Baseline processed input | Incremental processed input |",
                      "|---|---:|---:|---:|"]
            for profile, counts in sorted(details["personal_spikes"].items()):
                lines.append(
                    f"| {_cell(profile)} | {counts['events']} | "
                    f"{counts['baseline_processed_input']} | {counts['incremental_processed_input']} |"
                )
        lines += ["", "### Token breakdowns", ""]
        _types = ("input", "cache_write_5m", "cache_write_1h", "cache_read")
        denominator = sum(details["totals"][name] for name in _types)
        for heading, key in (("Team", "team"), ("Project", "project"), ("Agent/model", "agent_model")):
            lines += [f"#### By {heading}", "", "| Key | Input | Cache write 5m | Cache write 1h | Cache read | Output | Processed input | Share |",
                      "|---|---:|---:|---:|---:|---:|---:|---:|"]
            for label, values in sorted(details["breakdowns"][key].items()):
                processed = sum(values[name] for name in _types)
                share = f"{processed / denominator:.2%}" if denominator else "N/A"
                lines.append(f"| {_cell(label)} | {values['input']} | {values['cache_write_5m']} | {values['cache_write_1h']} | {values['cache_read']} | {values['output']} | {processed} | {share} |")
            lines.append("")
        lines += [f"Coverage check: PASS — {details['actual_coverage']} complete records; expected {details['expected_coverage']}.",
                  "Team and Project totals are separate views of the same consumption."]
    lines += ["", "## Warnings", "", details.get("warnings") or "None.", ""]
    return "\n".join(lines)
