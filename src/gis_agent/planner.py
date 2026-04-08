from __future__ import annotations

import re
from pathlib import Path

from gis_agent.semantics import SourceDescriptor, describe_sources, infer_layer_roles, infer_primary_field
from gis_agent.workflow import MapSpec, WorkflowPlan, WorkflowStep


def build_plan(task: str, data_paths: list[str], output_dir: str) -> WorkflowPlan:
    lowered = task.lower()
    descriptors = describe_sources(data_paths)
    roles = infer_layer_roles(task, descriptors)
    task_type = _detect_task_type(task, descriptors, data_paths)
    primary_descriptor = _pick_primary_descriptor(task_type, descriptors, roles)
    classify_field = infer_primary_field(task, primary_descriptor)
    map_spec = MapSpec(
        title=_extract_title(task) or _infer_title(task, task_type, primary_descriptor),
        output_format="html" if any(token in lowered for token in ["html", "web", "folium", "交互"]) else "png",
        task_type=task_type,
        classify_field=classify_field,
        primary_layer_index=_pick_primary_layer_index(task_type, roles),
        polygon_layer_index=roles.get("polygon_index"),
        point_layer_index=roles.get("point_index"),
        table_layer_index=roles.get("table_index"),
        basemap=any(token in lowered for token in ["底图", "basemap"]),
        color_scheme=_extract_color_scheme(task, primary_descriptor),
        cartographic_preset=_pick_preset(task_type),
        aggregate_field=_extract_aggregate_field(task, primary_descriptor),
        aggregate_method=_extract_aggregate_method(task),
        label_points=task_type == "rainfall_surface_map",
    )

    if task_type == "rainfall_surface_map":
        steps = _build_rainfall_plan(data_paths, map_spec)
    elif task_type == "point_in_polygon_summary_map":
        steps = _build_point_summary_plan(data_paths, map_spec)
    elif task_type == "choropleth_map":
        steps = _build_choropleth_plan(data_paths, map_spec)
    else:
        steps = _build_generic_plan(lowered, data_paths, map_spec)

    return WorkflowPlan(task=task, data_paths=data_paths, output_dir=output_dir, map_spec=map_spec, steps=steps)


def _build_rainfall_plan(data_paths: list[str], map_spec: MapSpec) -> list[WorkflowStep]:
    return [
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


def _build_choropleth_plan(data_paths: list[str], map_spec: MapSpec) -> list[WorkflowStep]:
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


def _build_point_summary_plan(data_paths: list[str], map_spec: MapSpec) -> list[WorkflowStep]:
    point_index = map_spec.point_layer_index if map_spec.point_layer_index is not None else 0
    polygon_index = map_spec.polygon_layer_index if map_spec.polygon_layer_index is not None else 1
    return [
        WorkflowStep("load_sources", {"data_paths": data_paths}),
        WorkflowStep("validate_layers", {}),
        WorkflowStep("repair_geometry", {"explode_multipart": False}),
        WorkflowStep("select_analysis_crs", {}),
        WorkflowStep("reproject_layers", {}),
        WorkflowStep(
            "point_in_polygon_summary",
            {
                "point_index": point_index,
                "polygon_index": polygon_index,
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


def _build_generic_plan(lowered: str, data_paths: list[str], map_spec: MapSpec) -> list[WorkflowStep]:
    steps = [
        WorkflowStep("load_sources", {"data_paths": data_paths}),
        WorkflowStep("validate_layers", {}),
        WorkflowStep("repair_geometry", {"explode_multipart": False}),
        WorkflowStep("select_analysis_crs", {}),
        WorkflowStep("reproject_layers", {}),
    ]
    if len(data_paths) > 1:
        if any(token in lowered for token in ["空间连接", "spatial join", "join", "落在", "归属"]):
            left = map_spec.point_layer_index if map_spec.point_layer_index is not None else 0
            right = map_spec.polygon_layer_index if map_spec.polygon_layer_index is not None else 1
            steps.append(WorkflowStep("spatial_join", {"left": left, "right": right, "predicate": "intersects", "how": "left"}))
        elif any(token in lowered for token in ["叠加", "相交", "intersect", "overlay"]):
            left = map_spec.primary_layer_index
            right = map_spec.polygon_layer_index if map_spec.polygon_layer_index is not None else 1
            steps.append(WorkflowStep("overlay", {"left": left, "right": right, "how": "intersection"}))
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
    return steps


def _detect_task_type(task: str, descriptors: list[SourceDescriptor], data_paths: list[str]) -> str:
    lowered = task.lower()
    suffixes = {Path(path).suffix.lower() for path in data_paths}
    has_table = bool(suffixes & {".xlsx", ".xls", ".csv"})
    has_polygon = any(item.geom_category == "polygon" for item in descriptors)
    has_point = any(item.geom_category == "point" for item in descriptors)
    rainfall_tokens = ["降雨", "雨量", "等值线", "插值", "空间分布", "contour", "rainfall", "precip"]
    if has_table and has_polygon and any(token in lowered for token in rainfall_tokens):
        return "rainfall_surface_map"
    point_summary_tokens = ["点落面", "落在", "落入", "按面统计", "统计每个", "poi", "空间连接统计", "归属后统计"]
    if has_point and has_polygon and any(token in lowered for token in point_summary_tokens):
        return "point_in_polygon_summary_map"
    choropleth_tokens = ["分级设色", "专题图", "字段", "choropleth", "分类图", "按"]
    if has_polygon and any(token in lowered for token in choropleth_tokens):
        return "choropleth_map"
    if has_polygon and any(item.likely_value_field for item in descriptors if item.geom_category == "polygon"):
        return "choropleth_map"
    return "generic_map"


def _pick_primary_descriptor(task_type: str, descriptors: list[SourceDescriptor], roles: dict[str, int | None]) -> SourceDescriptor | None:
    if not descriptors:
        return None
    if task_type == "rainfall_surface_map":
        idx = roles.get("boundary_index")
        return descriptors[idx] if idx is not None else descriptors[0]
    if task_type in {"choropleth_map", "point_in_polygon_summary_map"}:
        idx = roles.get("polygon_index")
        return descriptors[idx] if idx is not None else descriptors[0]
    idx = roles.get("polygon_index")
    if idx is not None:
        return descriptors[idx]
    return descriptors[0]


def _pick_primary_layer_index(task_type: str, roles: dict[str, int | None]) -> int:
    if task_type in {"choropleth_map", "point_in_polygon_summary_map", "rainfall_surface_map"} and roles.get("polygon_index") is not None:
        return int(roles["polygon_index"])
    if roles.get("point_index") is not None:
        return int(roles["point_index"])
    return 0


def _infer_title(task: str, task_type: str, descriptor: SourceDescriptor | None) -> str:
    subject = _extract_area_subject(task)
    if not subject and descriptor:
        stem = Path(descriptor.path).stem.replace("_", " ").replace("-", " ")
        subject = stem[:32]
    if task_type == "rainfall_surface_map":
        year_match = re.search(r"((?:19|20)\d{2})", task)
        year = year_match.group(1) if year_match else ""
        if subject and year:
            return f"{subject}{year}年度降雨量空间分布专题图"
        if subject:
            return f"{subject}降雨量空间分布专题图"
        return "降雨量空间分布专题图"
    if task_type == "point_in_polygon_summary_map":
        return f"{subject}点落面统计专题图" if subject else "点落面统计专题图"
    if task_type == "choropleth_map":
        field = _extract_requested_field(task) or (descriptor.likely_value_field if descriptor else None)
        if subject and field:
            return f"{subject}{field}专题图"
        if subject:
            return f"{subject}专题图"
    return "GIS Map"


def _extract_title(task: str) -> str | None:
    markers = ["标题叫", "标题为", "标题:", "title is", "title:", "title ="]
    for marker in markers:
        if marker in task:
            raw = task.split(marker, 1)[1].strip().strip("“”\"'")
            for stop in ["，输出", ", output", "，导出", ", 保存", "，保存", "，并", ", and", "。", "\n"]:
                if stop in raw:
                    raw = raw.split(stop, 1)[0].strip()
            return raw.strip("“”\"'，,。")
    return None


def _extract_requested_field(task: str) -> str | None:
    patterns = [
        r"按\s*([A-Za-z_][\w]*)\s*字段",
        r"按\s*([A-Za-z_][\w]*)\s*分级",
        r"根据\s*([A-Za-z_][\w]*)\s*字段",
        r"使用\s*([A-Za-z_][\w]*)\s*字段",
        r"用\s*([A-Za-z_][\w]*)\s*字段",
        r"field\s*[:=]?\s*([A-Za-z_][\w]*)",
    ]
    for pattern in patterns:
        match = re.search(pattern, task, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    if "value" in task.lower():
        return "value"
    return None


def _extract_color_scheme(task: str, descriptor: SourceDescriptor | None) -> str:
    lowered = task.lower()
    if "彩虹" in task or "turbo" in lowered or "rainbow" in lowered:
        return "turbo"
    if "黄" in task or "orange" in lowered or "yellow" in lowered:
        return "YlOrBr"
    if "红" in task or "red" in lowered:
        return "Reds"
    if "绿" in task or "green" in lowered:
        return "Greens"
    if "紫" in task or "purple" in lowered:
        return "Purples"
    if "灰" in task or "gray" in lowered or "grey" in lowered:
        return "Greys"
    if "蓝绿" in task or "teal" in lowered:
        return "YlGnBu"
    if descriptor and descriptor.likely_value_field:
        field_name = descriptor.likely_value_field.lower()
        if "rain" in field_name or "降雨" in descriptor.likely_value_field:
            return "turbo"
        if "population" in field_name or "人口" in descriptor.likely_value_field:
            return "YlOrRd"
        if "density" in field_name or "密度" in descriptor.likely_value_field:
            return "YlGnBu"
    return "Blues"


def _extract_aggregate_field(task: str, descriptor: SourceDescriptor | None) -> str | None:
    patterns = [
        r"统计每个[^\s，。、“”\"']*的\s*([A-Za-z_][\w]*)",
        r"按面统计\s*([A-Za-z_][\w]*)",
        r"汇总\s*([A-Za-z_][\w]*)",
    ]
    for pattern in patterns:
        match = re.search(pattern, task, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    if descriptor and descriptor.likely_value_field:
        return descriptor.likely_value_field
    return None


def _extract_aggregate_method(task: str) -> str:
    lowered = task.lower()
    if any(token in task for token in ["平均", "均值"]) or "mean" in lowered:
        return "mean"
    if any(token in task for token in ["求和", "总和", "总量"]) or re.search(r"\bsum\b", lowered):
        return "sum"
    return "count"


def _extract_area_subject(task: str) -> str | None:
    match = re.search(r"([^\s，。、“”\"']{1,20}(?:自治区|自治州|特别行政区|市|省|区|县))", task)
    return match.group(1) if match else None


def _pick_preset(task_type: str) -> str:
    if task_type == "rainfall_surface_map":
        return "rainfall_refined"
    if task_type in {"choropleth_map", "point_in_polygon_summary_map"}:
        return "choropleth"
    return "standard"
