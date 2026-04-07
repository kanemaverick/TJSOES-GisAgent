from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json

import geopandas as gpd
import pandas as pd

from gis_agent.runtime import (
    build_rainfall_surface,
    choose_analysis_crs,
    detect_station_columns,
    harmonize_crs,
    inspect_gdf,
    make_choropleth_map,
    make_rainfall_map,
    make_static_map,
    make_web_map,
    read_table,
    read_vector,
    repair_geometry,
    require_crs,
)
from gis_agent.tool_registry import ToolRegistry
from gis_agent.workflow import WorkflowPlan


@dataclass
class ExecutionContext:
    plan: WorkflowPlan
    layers: list[gpd.GeoDataFrame] = field(default_factory=list)
    tables: list[pd.DataFrame] = field(default_factory=list)
    layer_summaries: list[dict] = field(default_factory=list)
    analysis_crs: str | None = None
    active_layer_index: int = 0
    outputs: dict = field(default_factory=dict)
    derived: dict = field(default_factory=dict)


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register("load_sources", load_sources, "Read vector and tabular sources from disk")
    registry.register("load_layers", load_sources, "Backward-compatible source loading")
    registry.register("validate_layers", validate_layers, "Check CRS and geometry availability")
    registry.register("repair_geometry", repair_layers, "Repair invalid and empty geometries")
    registry.register("select_analysis_crs", select_analysis_crs, "Choose a projected CRS for analysis")
    registry.register("reproject_layers", reproject_layers, "Reproject all layers into analysis CRS")
    registry.register("spatial_join", spatial_join_layers, "Run spatial join on two layers")
    registry.register("overlay", overlay_layers, "Run overlay between two layers")
    registry.register("prepare_rainfall_inputs", prepare_rainfall_inputs, "Build station points and boundary for rainfall tasks")
    registry.register("interpolate_rainfall_surface", interpolate_rainfall_surface, "Interpolate rainfall surface from stations")
    registry.register("render_rainfall_map", render_rainfall_map, "Render refined rainfall thematic map")
    registry.register("point_in_polygon_summary", point_in_polygon_summary, "Aggregate point data into polygons")
    registry.register("render_choropleth_map", render_choropleth_map, "Render classified polygon thematic map")
    registry.register("render_map", render_map, "Create output map from active layer")
    return registry


def execute_plan(plan: WorkflowPlan) -> ExecutionContext:
    context = ExecutionContext(plan=plan)
    registry = build_registry()
    for step in plan.steps:
        registry.get(step.tool).handler(context, **step.params)
    _write_execution_report(context)
    return context


def load_sources(context: ExecutionContext, data_paths: list[str]) -> None:
    context.layers = []
    context.tables = []
    summaries = []
    for path in data_paths:
        suffix = Path(path).suffix.lower()
        if suffix in {".shp", ".geojson", ".json", ".gpkg", ".parquet"}:
            layer = read_vector(path)
            context.layers.append(layer)
            summaries.append(inspect_gdf(layer, path).__dict__)
        elif suffix in {".xlsx", ".xls", ".csv"}:
            table = read_table(path)
            context.tables.append(table)
            summaries.append(
                {
                    "path": path,
                    "source_type": "table",
                    "row_count": len(table),
                    "columns": table.columns.tolist(),
                    "crs": None,
                }
            )
        else:
            raise ValueError(f"Unsupported input source: {path}")
    context.layer_summaries = summaries


def validate_layers(context: ExecutionContext) -> None:
    for idx, layer in enumerate(context.layers):
        require_crs(layer, f"layer_{idx}")
        if "geometry" not in layer.columns:
            raise ValueError(f"Layer {idx} has no geometry column.")


def repair_layers(context: ExecutionContext, explode_multipart: bool = False) -> None:
    context.layers = [repair_geometry(layer, explode_multipart=explode_multipart) for layer in context.layers]
    context.layer_summaries = [
        inspect_gdf(layer, path).__dict__ for layer, path in zip(context.layers, context.plan.data_paths)
    ]


def select_analysis_crs(context: ExecutionContext) -> None:
    context.analysis_crs = choose_analysis_crs(context.layers[0])
    context.plan.analysis_crs = context.analysis_crs


def reproject_layers(context: ExecutionContext) -> None:
    if not context.analysis_crs:
        raise ValueError("Analysis CRS not selected before reprojection.")
    context.layers = harmonize_crs(context.layers, context.analysis_crs)


def spatial_join_layers(
    context: ExecutionContext,
    left: int,
    right: int,
    predicate: str = "intersects",
    how: str = "left",
) -> None:
    result = gpd.sjoin(context.layers[left], context.layers[right], predicate=predicate, how=how)
    context.layers = [result]
    context.active_layer_index = 0
    context.layer_summaries = [inspect_gdf(result, "spatial_join_result").__dict__]


def overlay_layers(context: ExecutionContext, left: int, right: int, how: str = "intersection") -> None:
    result = gpd.overlay(context.layers[left], context.layers[right], how=how)
    context.layers = [result]
    context.active_layer_index = 0
    context.layer_summaries = [inspect_gdf(result, "overlay_result").__dict__]


def prepare_rainfall_inputs(context: ExecutionContext) -> None:
    if not context.tables:
        raise ValueError("Rainfall workflow requires at least one tabular source.")
    if not context.layers:
        raise ValueError("Rainfall workflow requires at least one boundary layer.")
    table = context.tables[0]
    columns = detect_station_columns(table)
    points = gpd.GeoDataFrame(
        table.copy(),
        geometry=gpd.points_from_xy(table[columns["lon"]], table[columns["lat"]]),
        crs="EPSG:4490",
    )
    polygon_layers = [layer for layer in context.layers if any("Polygon" in geom for geom in layer.geometry.geom_type.unique())]
    if not polygon_layers:
        raise ValueError("No polygon boundary layer found for rainfall workflow.")
    boundary = polygon_layers[0].copy()
    # Prefer Shanghai-like rows when available; otherwise dissolve the first polygon layer.
    for candidate_col in ["省名", "name", "NAME", "标注"]:
        if candidate_col in boundary.columns:
            matches = boundary[boundary[candidate_col].astype(str).str.contains("上海", na=False)]
            if not matches.empty:
                boundary = matches.copy()
                break
    boundary["geometry"] = boundary.geometry.make_valid()
    boundary = boundary.to_crs("EPSG:4490")
    context.derived["station_columns"] = columns
    context.derived["rainfall_points"] = points
    context.derived["rainfall_boundary"] = boundary
    context.layer_summaries.append({"path": "derived_rainfall_points", "source_type": "vector", "row_count": len(points), "columns": points.columns.tolist(), "crs": points.crs.to_string() if points.crs else None})


def interpolate_rainfall_surface(
    context: ExecutionContext,
    grid_size: int = 260,
    interpolation: str = "cubic",
    fallback: str = "nearest",
) -> None:
    points = context.derived["rainfall_points"]
    boundary = context.derived["rainfall_boundary"]
    value_col = context.derived["station_columns"]["value"]
    surface = build_rainfall_surface(points, boundary, value_col=value_col, grid_size=grid_size, interpolation=interpolation, fallback=fallback)
    context.analysis_crs = surface["analysis_crs"]
    context.plan.analysis_crs = context.analysis_crs
    context.derived["rainfall_surface"] = surface


def render_rainfall_map(context: ExecutionContext, title: str, preset: str = "rainfall_refined", label_points: bool = True) -> None:
    points = context.derived["rainfall_points"]
    boundary = context.derived["rainfall_boundary"]
    surface = context.derived["rainfall_surface"]
    columns = context.derived["station_columns"]
    output_dir = Path(context.plan.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "result.png"
    rendered = make_rainfall_map(
        boundary=boundary,
        points=points,
        surface_payload=surface,
        output_path=str(output_path),
        title=title,
        value_col=columns["value"],
        label_col=columns.get("label"),
        preset=preset,
        label_points=label_points,
    )
    points.to_file(output_dir / "derived_points.geojson", driver="GeoJSON")
    boundary.to_file(output_dir / "derived_boundary.geojson", driver="GeoJSON")
    context.outputs["map"] = rendered


def point_in_polygon_summary(
    context: ExecutionContext,
    point_index: int,
    polygon_index: int,
    aggregate_field: str | None = None,
    aggregate_method: str = "count",
) -> None:
    points = context.layers[point_index]
    polygons = context.layers[polygon_index]
    joined = gpd.sjoin(points, polygons, predicate="intersects", how="left")
    polygon_id = "__poly_id__"
    polygons = polygons.reset_index(drop=True).copy()
    polygons[polygon_id] = polygons.index
    joined = gpd.sjoin(points, polygons, predicate="intersects", how="left")
    if aggregate_method == "count":
        summary = joined.groupby("index_right").size().rename("__summary_value__")
    elif aggregate_field and aggregate_field in joined.columns and aggregate_method == "sum":
        summary = joined.groupby("index_right")[aggregate_field].sum().rename("__summary_value__")
    elif aggregate_field and aggregate_field in joined.columns and aggregate_method == "mean":
        summary = joined.groupby("index_right")[aggregate_field].mean().rename("__summary_value__")
    else:
        summary = joined.groupby("index_right").size().rename("__summary_value__")
    result = polygons.join(summary, how="left").fillna({"__summary_value__": 0})
    context.layers = [result]
    context.active_layer_index = 0
    context.layer_summaries = [inspect_gdf(result, "point_in_polygon_summary").__dict__]


def render_choropleth_map(
    context: ExecutionContext,
    title: str,
    classify_field: str,
    color_scheme: str = "Blues",
    preset: str = "choropleth",
) -> None:
    gdf = context.layers[context.active_layer_index]
    output_dir = Path(context.plan.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "result.png"
    rendered = make_choropleth_map(
        gdf=gdf,
        output_path=str(output_path),
        title=title,
        classify_field=classify_field,
        cmap=color_scheme,
        preset=preset,
        data_source=", ".join(context.plan.data_paths),
    )
    context.outputs["map"] = rendered


def render_map(
    context: ExecutionContext,
    title: str,
    output_format: str,
    classify_field: str | None = None,
    basemap: bool = False,
    color_scheme: str = "Blues",
) -> None:
    gdf = context.layers[context.active_layer_index]
    output_dir = Path(context.plan.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if output_format == "html":
        output_path = output_dir / "result.html"
        rendered = make_web_map(
            gdf,
            str(output_path),
            tooltip_fields=gdf.columns[: min(3, len(gdf.columns))].tolist(),
            title=title,
            data_source=context.plan.data_paths[0],
        )
    else:
        output_path = output_dir / "result.png"
        rendered = make_static_map(
            gdf,
            str(output_path),
            title=title,
            classify_field=classify_field if classify_field in gdf.columns else None,
            basemap=basemap,
            cmap=color_scheme,
            data_source=", ".join(context.plan.data_paths),
        )
    context.outputs["map"] = rendered


def _write_execution_report(context: ExecutionContext) -> None:
    report = {
        "task": context.plan.task,
        "analysis_crs": context.analysis_crs,
        "layer_summaries": context.layer_summaries,
        "outputs": context.outputs,
        "steps": [step.tool for step in context.plan.steps],
    }
    Path(context.plan.output_dir, "execution_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
