from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gis_agent.engine import execute_plan
from gis_agent.llm import build_plan_with_llm, has_llm_credentials
from gis_agent.planner import build_plan
from gis_agent.runtime import summarize_sources


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Natural-language GIS map generation agent")
    parser.add_argument("--task", required=True, help="Natural-language GIS task")
    parser.add_argument("--data", nargs="+", required=True, help="Input vector dataset paths")
    parser.add_argument("--output-dir", required=True, help="Directory for script and map outputs")
    parser.add_argument("--mode", choices=["auto", "llm", "template"], default="auto")
    parser.add_argument("--run", action="store_true", help="Execute the deterministic GIS workflow")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    layer_summaries = summarize_sources(args.data)
    mode = resolve_mode(args.mode)
    if mode == "llm":
        plan = build_plan_with_llm(args.task, layer_summaries, str(output_dir), args.data)
    else:
        plan = build_plan(args.task, args.data, str(output_dir))
    plan_path = output_dir / "workflow_plan.json"
    plan.write(str(plan_path))
    print(f"Generated workflow: {plan_path}")

    if not args.run:
        return

    context = execute_plan(plan)
    print(f"Execution report: {output_dir / 'execution_report.json'}")
    if "map" in context.outputs:
        print(f"Map output: {context.outputs['map']}")


def resolve_mode(requested: str) -> str:
    if requested == "auto":
        return "llm" if has_llm_credentials() else "template"
    return requested


if __name__ == "__main__":
    main()
