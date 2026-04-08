from __future__ import annotations

import json
import os
from typing import Any

from openai import OpenAI

from gis_agent.prompts import PLANNER_PROMPT_TEMPLATE, SYSTEM_PROMPT
from gis_agent.workflow import MapSpec, WorkflowPlan, WorkflowStep


ALLOWED_TOOLS = {
    "load_sources",
    "load_layers",
    "validate_layers",
    "repair_geometry",
    "select_analysis_crs",
    "reproject_layers",
    "spatial_join",
    "overlay",
    "prepare_rainfall_inputs",
    "interpolate_rainfall_surface",
    "render_rainfall_map",
    "point_in_polygon_summary",
    "render_choropleth_map",
    "render_map",
}

ALLOWED_TASK_TYPES = {
    "generic_map",
    "choropleth_map",
    "point_in_polygon_summary_map",
    "rainfall_surface_map",
}


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
    prompt = PLANNER_PROMPT_TEMPLATE.format(
        task=task,
        layer_summaries=json.dumps(layer_summaries, ensure_ascii=False, indent=2),
    )
    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )
    raw = response.output_text.strip()
    try:
        payload = _extract_json_payload(raw)
        return _build_workflow_from_payload(payload, task=task, data_paths=data_paths, output_dir=output_dir)
    except Exception:
        from gis_agent.planner import build_plan

        return build_plan(task, data_paths, output_dir)


def _extract_json_payload(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or start >= end:
            raise ValueError("LLM did not return valid JSON.")
        payload = json.loads(raw[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("LLM JSON payload must be an object.")
    return payload


def _build_workflow_from_payload(
    payload: dict[str, Any],
    task: str,
    data_paths: list[str],
    output_dir: str,
) -> WorkflowPlan:
    steps = _sanitize_steps(payload.get("steps", []), data_paths=data_paths)
    task_type = payload.get("task_type")
    task_type = task_type if task_type in ALLOWED_TASK_TYPES else _infer_task_type_from_steps(steps)
    map_spec = MapSpec(
        title=str(payload.get("title", "GIS Map")).strip() or "GIS Map",
        output_format=_sanitize_output_format(payload.get("output_format", "png")),
        task_type=task_type,
        classify_field=_none_if_blank(payload.get("classify_field")),
        basemap=bool(payload.get("basemap", False)),
        color_scheme=str(payload.get("color_scheme", "Blues")) or "Blues",
        cartographic_preset=str(payload.get("cartographic_preset", _default_preset(task_type))) or _default_preset(task_type),
        label_points=bool(payload.get("label_points", task_type == "rainfall_surface_map")),
    )
    return WorkflowPlan(
        task=task,
        data_paths=data_paths,
        output_dir=output_dir,
        map_spec=map_spec,
        steps=steps,
    )


def _sanitize_steps(raw_steps: Any, data_paths: list[str]) -> list[WorkflowStep]:
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("LLM returned no workflow steps.")
    steps: list[WorkflowStep] = []
    for item in raw_steps:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool", "")).strip()
        if tool not in ALLOWED_TOOLS:
            continue
        params = item.get("params", {})
        if not isinstance(params, dict):
            params = {}
        params = _normalize_step_params(tool, params, data_paths)
        steps.append(WorkflowStep(tool=tool, params=params))
    if not steps:
        raise ValueError("No valid GIS steps were retained after sanitization.")
    if steps[0].tool not in {"load_sources", "load_layers"}:
        steps.insert(0, WorkflowStep(tool="load_sources", params={"data_paths": data_paths}))
    steps = _ensure_required_preconditions(steps, data_paths)
    return steps


def _normalize_step_params(tool: str, params: dict[str, Any], data_paths: list[str]) -> dict[str, Any]:
    normalized = dict(params)
    if tool in {"load_sources", "load_layers"}:
        normalized["data_paths"] = data_paths
    elif tool == "render_map":
        normalized["title"] = str(normalized.get("title", "GIS Map")) or "GIS Map"
        normalized["output_format"] = _sanitize_output_format(normalized.get("output_format", "png"))
        normalized["basemap"] = bool(normalized.get("basemap", False))
        normalized["color_scheme"] = str(normalized.get("color_scheme", "Blues")) or "Blues"
    elif tool == "render_choropleth_map":
        normalized["title"] = str(normalized.get("title", "GIS Map")) or "GIS Map"
        normalized["classify_field"] = _none_if_blank(normalized.get("classify_field"))
        normalized["color_scheme"] = str(normalized.get("color_scheme", "Blues")) or "Blues"
        normalized["preset"] = str(normalized.get("preset", "choropleth")) or "choropleth"
    elif tool == "render_rainfall_map":
        normalized["title"] = str(normalized.get("title", "Rainfall Map")) or "Rainfall Map"
        normalized["preset"] = str(normalized.get("preset", "rainfall_refined")) or "rainfall_refined"
        normalized["label_points"] = bool(normalized.get("label_points", True))
    elif tool == "point_in_polygon_summary":
        normalized["point_index"] = int(normalized.get("point_index", 0))
        normalized["polygon_index"] = int(normalized.get("polygon_index", 1))
        normalized["aggregate_method"] = str(normalized.get("aggregate_method", "count")) or "count"
    elif tool in {"spatial_join", "overlay"}:
        normalized["left"] = int(normalized.get("left", 0))
        normalized["right"] = int(normalized.get("right", 1))
    elif tool == "repair_geometry":
        normalized["explode_multipart"] = bool(normalized.get("explode_multipart", False))
    elif tool == "interpolate_rainfall_surface":
        normalized["grid_size"] = int(normalized.get("grid_size", 260))
        normalized["interpolation"] = str(normalized.get("interpolation", "cubic")) or "cubic"
        normalized["fallback"] = str(normalized.get("fallback", "nearest")) or "nearest"
    return normalized


def _ensure_required_preconditions(steps: list[WorkflowStep], data_paths: list[str]) -> list[WorkflowStep]:
    tool_names = [step.tool for step in steps]
    required_prefix: list[WorkflowStep] = []
    if any(name in tool_names for name in {"render_choropleth_map", "render_map", "point_in_polygon_summary", "spatial_join", "overlay"}):
        required_prefix.extend(
            [
                WorkflowStep("validate_layers", {}),
                WorkflowStep("repair_geometry", {"explode_multipart": False}),
            ]
        )
    if any(name in tool_names for name in {"render_choropleth_map", "point_in_polygon_summary", "spatial_join", "overlay"}):
        required_prefix.extend(
            [
                WorkflowStep("select_analysis_crs", {}),
                WorkflowStep("reproject_layers", {}),
            ]
        )
    if any(name in tool_names for name in {"prepare_rainfall_inputs", "interpolate_rainfall_surface", "render_rainfall_map"}):
        if steps[0].tool not in {"load_sources", "load_layers"}:
            steps.insert(0, WorkflowStep("load_sources", {"data_paths": data_paths}))
        return steps
    if not required_prefix:
        return steps
    existing = {step.tool for step in steps}
    prefix = [step for step in required_prefix if step.tool not in existing]
    insert_at = 1 if steps and steps[0].tool in {"load_sources", "load_layers"} else 0
    return steps[:insert_at] + prefix + steps[insert_at:]


def _infer_task_type_from_steps(steps: list[WorkflowStep]) -> str:
    tool_names = {step.tool for step in steps}
    if "render_rainfall_map" in tool_names or "interpolate_rainfall_surface" in tool_names:
        return "rainfall_surface_map"
    if "point_in_polygon_summary" in tool_names:
        return "point_in_polygon_summary_map"
    if "render_choropleth_map" in tool_names:
        return "choropleth_map"
    return "generic_map"


def _default_preset(task_type: str) -> str:
    if task_type == "rainfall_surface_map":
        return "rainfall_refined"
    if task_type in {"choropleth_map", "point_in_polygon_summary_map"}:
        return "choropleth"
    return "standard"


def _sanitize_output_format(value: Any) -> str:
    output = str(value or "png").lower()
    return "html" if output == "html" else "png"


def _none_if_blank(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
