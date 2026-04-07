from __future__ import annotations

import re
from pathlib import Path

from gis_agent.workflow import MapSpec, WorkflowPlan, WorkflowStep


def build_plan(task: str, data_paths: list[str], output_dir: str) -> WorkflowPlan:
    lowered = task.lower()
    task_type = _detect_task_type(task, data_paths)
    map_spec = MapSpec(
        title=_extract_title(task) or _infer_title(task, task_type),
        output_format="html" if any(token in lowered for token in ["html", "web", "folium", "交互"]) else "png",
        task_type=task_type,
        classify_field=_extract_field(task),
        basemap=any(token in lowered for token in ["底图", "basemap"]),
        color_scheme=_extract_color_scheme(task),
        cartographic_preset=_pick_preset(task_type),
        aggregate_field=_extract_aggregate_field(task),
        aggregate_method=_extract_aggregate_method(task),
        label_points=task_type == "rainfall_surface_map",
    )

    if task_type == "rainfall_surface_map":
        steps = _build_rainfall_plan(task, data_paths, map_spec)
    elif task_type == "point_in_polygon_summary_map":
        steps = _build_point_summary_plan(task, data_paths, map_spec)
    elif task_type == "choropleth_map":
        steps = _build_choropleth_plan(task, data_paths, map_spec)
    else:
        steps = [
            WorkflowStep("load_sources", {"data_paths": data_paths}),
            WorkflowStep("validate_layers", {}),
            WorkflowStep("repair_geometry", {"explode_multipart": False}),
            WorkflowStep("select_analysis_crs", {}),
            WorkflowStep("reproject_layers", {}),
        ]

        if len(data_paths) > 1:
            if any(token in lowered for token in ["空间连接", "spatial join", "join", "落在", "归属"]):
                steps.append(WorkflowStep("spatial_join", {"left": 0, "right": 1, "predicate": "intersects", "how": "left"}))
            elif any(token in lowered for token in ["叠加", "相交", "intersect", "overlay"]):
                steps.append(WorkflowStep("overlay", {"left": 0, "right": 1, "how": "intersection"}))

        steps.append(
            WorkflowStep(
                "render_map",
                {
                    "title": map_spec.title,
                    "output_format": map_spec.output_format,
                    "classify_field": map_spec.classify_field,
                    "basemap": map_spec.basemap,
                    "color_scheme": map_spec.color_scheme,
                },
            )
        )

    return WorkflowPlan(task=task, data_paths=data_paths, output_dir=output_dir, map_spec=map_spec, steps=steps)


def _build_rainfall_plan(task: str, data_paths: list[str], map_spec: MapSpec) -> list[WorkflowStep]:
    steps = [
        WorkflowStep("load_sources", {"data_paths": data_paths}),
        WorkflowStep("prepare_rainfall_inputs", {}),
        WorkflowStep(
            "interpolate_rainfall_surface",
            {
                "grid_size": 260,
                "interpolation": "cubic",
                "fallback": "nearest",
            },
        ),
        WorkflowStep(
            "render_rainfall_map",
            {
                "title": map_spec.title,
                "preset": map_spec.cartographic_preset,
                "label_points": map_spec.label_points,
            },
        ),
    ]
    return steps


def _build_choropleth_plan(task: str, data_paths: list[str], map_spec: MapSpec) -> list[WorkflowStep]:
    return [
        WorkflowStep("load_sources", {"data_paths": data_paths}),
        WorkflowStep("validate_layers", {}),
        WorkflowStep("repair_geometry", {"explode_multipart": False}),
        WorkflowStep("select_analysis_crs", {}),
        WorkflowStep("reproject_layers", {}),
        WorkflowStep(
            "render_choropleth_map",
            {
                "title": map_spec.title,
                "classify_field": map_spec.classify_field,
                "color_scheme": map_spec.color_scheme,
                "preset": map_spec.cartographic_preset,
            },
        ),
    ]


def _build_point_summary_plan(task: str, data_paths: list[str], map_spec: MapSpec) -> list[WorkflowStep]:
    return [
        WorkflowStep("load_sources", {"data_paths": data_paths}),
        WorkflowStep("validate_layers", {}),
        WorkflowStep("repair_geometry", {"explode_multipart": False}),
        WorkflowStep("select_analysis_crs", {}),
        WorkflowStep("reproject_layers", {}),
        WorkflowStep(
            "point_in_polygon_summary",
            {
                "point_index": 0,
                "polygon_index": 1,
                "aggregate_field": map_spec.aggregate_field,
                "aggregate_method": map_spec.aggregate_method,
            },
        ),
        WorkflowStep(
            "render_choropleth_map",
            {
                "title": map_spec.title,
                "classify_field": "__summary_value__",
                "color_scheme": map_spec.color_scheme,
                "preset": "choropleth",
            },
        ),
    ]


def _detect_task_type(task: str, data_paths: list[str]) -> str:
    lowered = task.lower()
    suffixes = {Path(path).suffix.lower() for path in data_paths}
    rainfall_tokens = ["降雨", "雨量", "等值线", "插值", "空间分布", "contour", "rainfall", "excel"]
    has_table = bool(suffixes & {".xlsx", ".xls", ".csv"})
    if has_table and any(token in lowered for token in rainfall_tokens):
        return "rainfall_surface_map"
    point_summary_tokens = ["点落面", "落在", "落入", "按面统计", "统计每个", "poi", "空间连接统计", "归属后统计"]
    if len(data_paths) >= 2 and any(token in lowered for token in point_summary_tokens):
        return "point_in_polygon_summary_map"
    choropleth_tokens = ["分级设色", "专题图", "按", "字段", "choropleth", "分类图"]
    vector_only = not bool(suffixes & {".xlsx", ".xls", ".csv"})
    if vector_only and any(token in lowered for token in choropleth_tokens):
        return "choropleth_map"
    return "generic_map"


def _infer_title(task: str, task_type: str) -> str:
    if task_type == "rainfall_surface_map":
        cleaned = task
        for token in ["一步生成", "生成", "要求包含", "根据", "使用", "利用"]:
            cleaned = cleaned.replace(token, "")
        city_match = re.search(r"([^\s，。、“”\"']{1,12}市)", cleaned)
        year_match = re.search(r"((?:19|20)\d{2})", task)
        city = city_match.group(1) if city_match else ""
        year = year_match.group(1) if year_match else ""
        if city and year:
            return f"{city}{year}年度降雨量空间分布专题图"
        if city:
            return f"{city}降雨量空间分布专题图"
        return "降雨量空间分布专题图"
    if task_type == "point_in_polygon_summary_map":
        subject = _extract_area_subject(task)
        return f"{subject}点落面统计专题图" if subject else "点落面统计专题图"
    if task_type == "choropleth_map":
        subject = _extract_area_subject(task)
        field = _extract_field(task)
        if subject and field:
            return f"{subject}{field}专题图"
        if subject:
            return f"{subject}专题图"
    return "GIS Map"


def _extract_title(task: str) -> str | None:
    markers = ["标题叫", "标题为", "title is", "title:"]
    for marker in markers:
        if marker in task:
            raw = task.split(marker, 1)[1].strip().strip("“”\"'")
            for stop in ["，输出", ", output", "，导出", ", 保存", "，保存", "。", "\n"]:
                if stop in raw:
                    raw = raw.split(stop, 1)[0].strip()
            return raw.strip("“”\"'，,。")
    return None


def _extract_field(task: str) -> str | None:
    lowered = task.lower()
    patterns = [
        r"按\s*([A-Za-z_][\w]*)\s*字段",
        r"按\s*([A-Za-z_][\w]*)\s*分级",
        r"根据\s*([A-Za-z_][\w]*)\s*字段",
        r"field\s*[:=]?\s*([A-Za-z_][\w]*)",
    ]
    for pattern in patterns:
        match = re.search(pattern, task, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    if "value" in lowered:
        return "value"
    return None


def _extract_color_scheme(task: str) -> str:
    lowered = task.lower()
    if "红" in task or "red" in lowered:
        return "Reds"
    if "绿" in task or "green" in lowered:
        return "Greens"
    if "灰" in task or "gray" in lowered or "grey" in lowered:
        return "Greys"
    return "Blues"


def _extract_aggregate_field(task: str) -> str | None:
    patterns = [
        r"统计每个[^\s，。、“”\"']*的\s*([A-Za-z_][\w]*)",
        r"按面统计\s*([A-Za-z_][\w]*)",
        r"汇总\s*([A-Za-z_][\w]*)",
    ]
    for pattern in patterns:
        match = re.search(pattern, task, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _extract_aggregate_method(task: str) -> str:
    lowered = task.lower()
    if any(token in task for token in ["平均", "均值"]) or "mean" in lowered:
        return "mean"
    if any(token in task for token in ["求和", "总和", "总量"]) or re.search(r"\bsum\b", lowered):
        return "sum"
    return "count"


def _extract_area_subject(task: str) -> str | None:
    match = re.search(r"([^\s，。、“”\"']{1,16}(?:市|省|区|县))", task)
    return match.group(1) if match else None


def _pick_preset(task_type: str) -> str:
    if task_type == "rainfall_surface_map":
        return "rainfall_refined"
    if task_type in {"choropleth_map", "point_in_polygon_summary_map"}:
        return "choropleth"
    return "standard"
