from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import geopandas as gpd
import pandas as pd

from gis_agent.runtime import read_table, read_vector


ROLE_KEYWORDS = {
    "boundary": ["boundary", "admin", "district", "county", "province", "city", "界", "行政", "区划", "县", "市", "省"],
    "stations": ["station", "gauge", "monitor", "monitoring", "rainfall", "precip", "站", "雨量", "降雨", "监测"],
    "points": ["point", "poi", "event", "sample", "点", "样点", "事件"],
    "lines": ["road", "river", "line", "network", "路线", "河流", "线"],
}

AREA_NAME_COLUMNS = {"name", "NAME", "fullname", "省名", "市名", "县名", "区名", "标注", "名称"}
STATION_NAME_COLUMNS = {"station", "station_name", "name", "站名", "监测点", "监测站"}
LONGITUDE_HINTS = {"lon", "longitude", "经度", "x", "lng"}
LATITUDE_HINTS = {"lat", "latitude", "纬度", "y"}
RAINFALL_HINTS = {"rain", "precip", "降雨", "雨量", "precipitation"}
POPULATION_HINTS = {"population", "pop", "人口"}
DENSITY_HINTS = {"density", "密度"}
COUNT_HINTS = {"count", "num", "数量", "总数"}
VALUE_HINTS = {"value", "score", "rate", "ratio", "指数"}


@dataclass
class SourceDescriptor:
    path: str
    source_type: str
    geom_category: str | None
    row_count: int
    columns: list[str]
    numeric_fields: list[str]
    text_fields: list[str]
    semantic_role: str
    likely_name_field: str | None
    likely_value_field: str | None


def describe_sources(data_paths: list[str]) -> list[SourceDescriptor]:
    descriptors: list[SourceDescriptor] = []
    for path in data_paths:
        suffix = Path(path).suffix.lower()
        if suffix in {".shp", ".geojson", ".json", ".gpkg", ".parquet"}:
            gdf = read_vector(path)
            descriptors.append(_describe_vector(path, gdf))
        elif suffix in {".xlsx", ".xls", ".csv"}:
            df = read_table(path)
            descriptors.append(_describe_table(path, df))
    return descriptors


def infer_primary_field(task: str, descriptor: SourceDescriptor | None) -> str | None:
    requested = _extract_requested_field(task)
    if requested:
        return requested
    if descriptor and descriptor.likely_value_field:
        return descriptor.likely_value_field
    return None


def infer_layer_roles(task: str, descriptors: list[SourceDescriptor]) -> dict[str, int | None]:
    result: dict[str, int | None] = {
        "polygon_index": None,
        "point_index": None,
        "table_index": None,
        "boundary_index": None,
        "station_table_index": None,
    }
    polygon_candidates = [idx for idx, item in enumerate(descriptors) if item.geom_category == "polygon"]
    point_candidates = [idx for idx, item in enumerate(descriptors) if item.geom_category == "point"]
    table_candidates = [idx for idx, item in enumerate(descriptors) if item.source_type == "table"]

    if polygon_candidates:
        boundary_ranked = sorted(
            polygon_candidates,
            key=lambda idx: (
                descriptors[idx].semantic_role != "boundary",
                descriptors[idx].likely_name_field is None,
                -descriptors[idx].row_count,
            ),
        )
        result["polygon_index"] = boundary_ranked[0]
        result["boundary_index"] = boundary_ranked[0]
    if point_candidates:
        point_ranked = sorted(
            point_candidates,
            key=lambda idx: (
                descriptors[idx].semantic_role not in {"points", "stations"},
                descriptors[idx].semantic_role != "stations",
                -descriptors[idx].row_count,
            ),
        )
        result["point_index"] = point_ranked[0]
    if table_candidates:
        result["table_index"] = table_candidates[0]
        station_ranked = sorted(
            table_candidates,
            key=lambda idx: (
                descriptors[idx].semantic_role != "stations_table",
                descriptors[idx].likely_value_field is None,
                -descriptors[idx].row_count,
            ),
        )
        result["station_table_index"] = station_ranked[0]

    lowered = task.lower()
    if any(token in lowered for token in ["站", "降雨", "雨量", "rain", "precip"]):
        if table_candidates:
            result["station_table_index"] = station_ranked[0]
        if polygon_candidates and result["boundary_index"] is None:
            result["boundary_index"] = polygon_candidates[0]
    return result


def _describe_vector(path: str, gdf: gpd.GeoDataFrame) -> SourceDescriptor:
    geom_category = _geom_category(gdf)
    numeric_fields = _numeric_fields(gdf)
    text_fields = _text_fields(gdf)
    semantic_role = _infer_vector_role(path, gdf, geom_category, numeric_fields, text_fields)
    likely_name_field = _pick_name_field(gdf.columns.tolist())
    likely_value_field = _pick_value_field(gdf.columns.tolist(), numeric_fields)
    return SourceDescriptor(
        path=path,
        source_type="vector",
        geom_category=geom_category,
        row_count=len(gdf),
        columns=gdf.columns.tolist(),
        numeric_fields=numeric_fields,
        text_fields=text_fields,
        semantic_role=semantic_role,
        likely_name_field=likely_name_field,
        likely_value_field=likely_value_field,
    )


def _describe_table(path: str, df: pd.DataFrame) -> SourceDescriptor:
    numeric_fields = df.select_dtypes(include=["number"]).columns.astype(str).tolist()
    text_fields = df.select_dtypes(include=["object", "string"]).columns.astype(str).tolist()
    semantic_role = _infer_table_role(path, df)
    likely_name_field = _pick_name_field(df.columns.astype(str).tolist(), include_station_names=True)
    likely_value_field = _pick_value_field(df.columns.astype(str).tolist(), numeric_fields)
    return SourceDescriptor(
        path=path,
        source_type="table",
        geom_category=None,
        row_count=len(df),
        columns=df.columns.astype(str).tolist(),
        numeric_fields=numeric_fields,
        text_fields=text_fields,
        semantic_role=semantic_role,
        likely_name_field=likely_name_field,
        likely_value_field=likely_value_field,
    )


def _infer_vector_role(
    path: str,
    gdf: gpd.GeoDataFrame,
    geom_category: str | None,
    numeric_fields: list[str],
    text_fields: list[str],
) -> str:
    path_text = Path(path).stem.lower()
    cols_text = " ".join(map(str, gdf.columns)).lower()
    if geom_category == "polygon":
        if _contains_keywords(path_text, ROLE_KEYWORDS["boundary"]) or _contains_keywords(cols_text, ROLE_KEYWORDS["boundary"]):
            return "boundary"
        if any(col in AREA_NAME_COLUMNS for col in gdf.columns.astype(str)):
            return "boundary"
        if numeric_fields:
            return "polygon_thematic"
        return "polygon"
    if geom_category == "point":
        if _contains_keywords(path_text, ROLE_KEYWORDS["stations"]) or _contains_keywords(cols_text, ROLE_KEYWORDS["stations"]):
            return "stations"
        if any(str(col) in STATION_NAME_COLUMNS for col in gdf.columns):
            return "stations"
        return "points"
    if geom_category == "line":
        return "lines"
    return "generic_vector"


def _infer_table_role(path: str, df: pd.DataFrame) -> str:
    path_text = Path(path).stem.lower()
    columns = [str(col) for col in df.columns]
    low_columns = [col.lower() for col in columns]
    if any(any(hint in col for hint in LONGITUDE_HINTS) for col in low_columns) and any(
        any(hint in col for hint in LATITUDE_HINTS) for col in low_columns
    ):
        if any(any(hint in col for hint in RAINFALL_HINTS) for col in low_columns) or _contains_keywords(path_text, ROLE_KEYWORDS["stations"]):
            return "stations_table"
        return "coordinate_table"
    if any(any(hint in col for hint in RAINFALL_HINTS) for col in low_columns):
        return "stations_table"
    return "table"


def _extract_requested_field(task: str) -> str | None:
    patterns = [
        r"按\s*([A-Za-z_][\w]*)\s*字段",
        r"根据\s*([A-Za-z_][\w]*)\s*字段",
        r"使用\s*([A-Za-z_][\w]*)\s*字段",
        r"用\s*([A-Za-z_][\w]*)\s*字段",
        r"field\s*[:=]?\s*([A-Za-z_][\w]*)",
    ]
    for pattern in patterns:
        match = re.search(pattern, task, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _pick_name_field(columns: list[str], include_station_names: bool = False) -> str | None:
    priorities = list(AREA_NAME_COLUMNS)
    if include_station_names:
        priorities = list(STATION_NAME_COLUMNS) + priorities
    for candidate in priorities:
        for col in columns:
            if str(col) == candidate:
                return str(col)
    return None


def _pick_value_field(columns: list[str], numeric_fields: list[str]) -> str | None:
    ordered_hints = [
        POPULATION_HINTS,
        DENSITY_HINTS,
        RAINFALL_HINTS,
        COUNT_HINTS,
        VALUE_HINTS,
    ]
    numeric_lookup = {field.lower(): field for field in numeric_fields}
    for hint_group in ordered_hints:
        for low, original in numeric_lookup.items():
            if any(hint in low for hint in hint_group):
                return original
    if "value" in numeric_lookup:
        return numeric_lookup["value"]
    return numeric_fields[0] if numeric_fields else None


def _numeric_fields(gdf: gpd.GeoDataFrame) -> list[str]:
    cols = gdf.select_dtypes(include=["number"]).columns.astype(str).tolist()
    return [col for col in cols if col != "geometry"]


def _text_fields(gdf: gpd.GeoDataFrame) -> list[str]:
    return gdf.select_dtypes(include=["object", "string"]).columns.astype(str).tolist()


def _geom_category(gdf: gpd.GeoDataFrame) -> str | None:
    geom_types = {str(item) for item in gdf.geometry.geom_type.dropna().tolist()}
    if any("Polygon" in item for item in geom_types):
        return "polygon"
    if any("Point" in item for item in geom_types):
        return "point"
    if any("Line" in item for item in geom_types):
        return "line"
    return None


def _contains_keywords(text: str, keywords: list[str]) -> bool:
    text = text.lower()
    return any(keyword.lower() in text for keyword in keywords)
