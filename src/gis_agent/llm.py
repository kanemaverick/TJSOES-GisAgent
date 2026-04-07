from __future__ import annotations

import os

from openai import OpenAI

from gis_agent.prompts import SYSTEM_PROMPT
from gis_agent.workflow import WorkflowPlan, WorkflowStep, MapSpec


def has_llm_credentials() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def build_client() -> OpenAI:
    return OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
    )


def generate_script(task: str, layer_summaries: list[dict], output_dir: str) -> str:
    client = build_client()
    model = os.getenv("OPENAI_MODEL", "gpt-4.1")
    prompt = (
        f"User task:\n{task}\n\n"
        f"Layer summaries:\n{layer_summaries}\n\n"
        f"Output directory:\n{output_dir}\n\n"
        "Write one complete Python script that uses gis_agent.runtime helpers."
    )
    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )
    return response.output_text.strip()


def repair_script(task: str, layer_summaries: list[dict], broken_script: str, error_text: str, output_dir: str) -> str:
    client = build_client()
    model = os.getenv("OPENAI_MODEL", "gpt-4.1")
    prompt = (
        f"Original task:\n{task}\n\n"
        f"Layer summaries:\n{layer_summaries}\n\n"
        f"Output directory:\n{output_dir}\n\n"
        f"Broken script:\n{broken_script}\n\n"
        f"Execution error:\n{error_text}\n\n"
        "Return a full corrected Python script. Keep CRS safety rules."
    )
    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )
    return response.output_text.strip()


def build_plan_with_llm(task: str, layer_summaries: list[dict], output_dir: str, data_paths: list[str]) -> WorkflowPlan:
    client = build_client()
    model = os.getenv("OPENAI_MODEL", "gpt-4.1")
    prompt = (
        f"Task:\n{task}\n\n"
        f"Layer summaries:\n{layer_summaries}\n\n"
        "Return a JSON object with keys: title, output_format, classify_field, basemap, color_scheme, "
        "and steps. Each step must use only these tool names: load_layers, validate_layers, repair_geometry, "
        "select_analysis_crs, reproject_layers, spatial_join, overlay, render_map."
    )
    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )
    raw = response.output_text.strip()
    import json

    payload = json.loads(raw)
    map_spec = MapSpec(
        title=payload.get("title", "GIS Map"),
        output_format=payload.get("output_format", "png"),
        classify_field=payload.get("classify_field"),
        basemap=bool(payload.get("basemap", False)),
        color_scheme=payload.get("color_scheme", "Blues"),
    )
    steps = [WorkflowStep(tool=step["tool"], params=step.get("params", {})) for step in payload.get("steps", [])]
    return WorkflowPlan(task=task, data_paths=data_paths, output_dir=output_dir, map_spec=map_spec, steps=steps)
