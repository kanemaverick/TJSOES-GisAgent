from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import contextily as ctx
import folium
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager as fm
from matplotlib.lines import Line2D
from matplotlib_scalebar.scalebar import ScaleBar
from pyproj import CRS, Geod, Transformer
from scipy.interpolate import griddata
from shapely.geometry import Point


@dataclass
class LayerSummary:
    path: str
    crs: str | None
    row_count: int
    geom_types: list[str]
    bounds: tuple[float, float, float, float]
    columns: list[str]
    valid_ratio: float


@dataclass
class SourceSummary:
    path: str
    source_type: str
    row_count: int
    columns: list[str]
    crs: str | None = None


def read_vector(path: str, layer: str | None = None) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path, layer=layer)
    if "geometry" not in gdf.columns:
        raise ValueError(f"{path} has no geometry column.")
    return gdf


def inspect_gdf(gdf: gpd.GeoDataFrame, path: str) -> LayerSummary:
    valid_ratio = float(gdf.geometry.is_valid.mean()) if len(gdf) else 1.0
    geom_types = sorted({str(value) for value in gdf.geometry.geom_type.dropna().unique().tolist()})
    bounds = tuple(gdf.total_bounds.tolist()) if len(gdf) else (0.0, 0.0, 0.0, 0.0)
    return LayerSummary(
        path=path,
        crs=gdf.crs.to_string() if gdf.crs else None,
        row_count=len(gdf),
        geom_types=geom_types,
        bounds=bounds,
        columns=gdf.columns.tolist(),
        valid_ratio=valid_ratio,
    )


def read_table(path: str) -> pd.DataFrame:
    ext = Path(path).suffix.lower()
    if ext in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    if ext == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"Unsupported tabular data source: {path}")


def summarize_sources(paths: list[str]) -> list[dict]:
    summaries: list[dict] = []
    for path in paths:
        ext = Path(path).suffix.lower()
        if ext in {".shp", ".geojson", ".json", ".gpkg", ".parquet"}:
            gdf = read_vector(path)
            geom_types = sorted({str(value) for value in gdf.geometry.geom_type.dropna().unique().tolist()})
            summaries.append(
                {
                    "path": path,
                    "source_type": "vector",
                    "row_count": len(gdf),
                    "columns": gdf.columns.tolist(),
                    "crs": gdf.crs.to_string() if gdf.crs else None,
                    "geom_types": geom_types,
                    "numeric_fields": gdf.select_dtypes(include=["number"]).columns.astype(str).tolist(),
                    "text_fields": gdf.select_dtypes(include=["object", "string"]).columns.astype(str).tolist(),
                }
            )
        elif ext in {".xlsx", ".xls", ".csv"}:
            df = read_table(path)
            summaries.append(
                {
                    "path": path,
                    "source_type": "table",
                    "row_count": len(df),
                    "columns": df.columns.tolist(),
                    "crs": None,
                    "numeric_fields": df.select_dtypes(include=["number"]).columns.astype(str).tolist(),
                    "text_fields": df.select_dtypes(include=["object", "string"]).columns.astype(str).tolist(),
                }
            )
        else:
            summaries.append({"path": path, "source_type": "unknown", "row_count": 0, "columns": [], "crs": None})
    return summaries


def repair_geometry(gdf: gpd.GeoDataFrame, explode_multipart: bool = False) -> gpd.GeoDataFrame:
    fixed = gdf.copy()
    fixed = fixed[fixed.geometry.notna()].copy()
    fixed = fixed[~fixed.geometry.is_empty].copy()
    fixed["geometry"] = fixed.geometry.make_valid()
    if explode_multipart:
        fixed = fixed.explode(index_parts=False).reset_index(drop=True)
    return fixed


def require_crs(gdf: gpd.GeoDataFrame, layer_name: str) -> None:
    if gdf.crs is None:
        raise ValueError(f"Layer '{layer_name}' has no CRS. Provide an EPSG code or WKT before analysis.")


def choose_analysis_crs(gdf: gpd.GeoDataFrame) -> str:
    require_crs(gdf, "input")
    if gdf.crs and CRS(gdf.crs).is_projected:
        return gdf.crs.to_string()
    bounds = gdf.to_crs(4326).total_bounds
    center_lon = (bounds[0] + bounds[2]) / 2
    center_lat = (bounds[1] + bounds[3]) / 2
    zone = int((center_lon + 180) // 6) + 1
    epsg = 32600 + zone if center_lat >= 0 else 32700 + zone
    return f"EPSG:{epsg}"


def harmonize_crs(layers: Iterable[gpd.GeoDataFrame], target_crs: str) -> list[gpd.GeoDataFrame]:
    result = []
    for idx, gdf in enumerate(layers):
        require_crs(gdf, f"layer_{idx}")
        result.append(gdf.to_crs(target_crs))
    return result


def add_north_arrow(ax, x: float = 0.08, y: float = 0.92, size: float = 0.08) -> None:
    ax.annotate(
        "N",
        xy=(x, y),
        xytext=(x, y - size),
        arrowprops=dict(facecolor="black", width=3, headwidth=10),
        ha="center",
        va="center",
        fontsize=12,
        xycoords=ax.transAxes,
    )


def add_scale_bar(ax, crs: str) -> None:
    projected = CRS(crs).is_projected
    if projected:
        ax.add_artist(ScaleBar(dx=1, units="m", location="lower left", box_alpha=0.6))


def configure_plot_fonts() -> None:
    candidates = [
        "Noto Sans CJK SC",
        "WenQuanYi Zen Hei",
        "Noto Sans",
        "DejaVu Sans",
    ]
    available = {font.name for font in fm.fontManager.ttflist}
    family = next((name for name in candidates if name in available), "DejaVu Sans")
    plt.rcParams["font.family"] = family
    plt.rcParams["axes.unicode_minus"] = False


def finalize_axes(ax, title: str, crs: str, data_source: str) -> None:
    ax.set_title(title, fontsize=16)
    axis_x = "Longitude" if CRS(crs).is_geographic else "Easting (m)"
    axis_y = "Latitude" if CRS(crs).is_geographic else "Northing (m)"
    ax.set_xlabel(axis_x)
    ax.set_ylabel(axis_y)
    ax.text(
        0.01,
        0.01,
        f"CRS: {crs}\nData Source: {data_source}",
        transform=ax.transAxes,
        fontsize=9,
        va="bottom",
        ha="left",
        bbox=dict(facecolor="white", alpha=0.7, edgecolor="none"),
    )


def make_static_map(
    gdf: gpd.GeoDataFrame,
    output_path: str,
    title: str,
    classify_field: str | None = None,
    basemap: bool = False,
    cmap: str = "Blues",
    legend: bool = True,
    figsize: tuple[int, int] = (12, 10),
    data_source: str = "User input",
) -> str:
    if gdf.crs is None:
        raise ValueError("Static map generation requires a CRS.")
    configure_plot_fonts()
    fig, ax = plt.subplots(figsize=figsize)
    if classify_field:
        gdf.plot(
            ax=ax,
            column=classify_field,
            cmap=cmap,
            legend=legend,
            scheme="Quantiles",
            k=max(1, min(5, int(gdf[classify_field].nunique()))),
            edgecolor="black",
            linewidth=0.6,
        )
    else:
        gdf.plot(ax=ax, color="#8fbbe8", edgecolor="#1f3a5f", linewidth=0.8)
        if legend:
            ax.plot([], [], color="#8fbbe8", label="Layer")
            ax.legend(loc="lower right")
    if basemap:
        ctx.add_basemap(ax, crs=gdf.crs.to_string(), source=ctx.providers.CartoDB.Positron)
    add_north_arrow(ax)
    add_scale_bar(ax, gdf.crs.to_string())
    finalize_axes(ax, title, gdf.crs.to_string(), data_source)
    plt.tight_layout()
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return str(output)


def make_choropleth_map(
    gdf: gpd.GeoDataFrame,
    output_path: str,
    title: str,
    classify_field: str,
    cmap: str = "Blues",
    preset: str = "choropleth",
    data_source: str = "User input",
) -> str:
    if gdf.crs is None:
        raise ValueError("Choropleth map generation requires a CRS.")
    if classify_field not in gdf.columns:
        raise ValueError(f"Field '{classify_field}' not found in layer.")
    configure_plot_fonts()
    fig, ax = plt.subplots(figsize=(10.5, 8.6), facecolor="white")
    ax.set_facecolor("white")
    values = gdf[classify_field].fillna(0)
    k = max(1, min(6, int(values.nunique())))
    gdf.plot(
        ax=ax,
        column=classify_field,
        cmap=cmap,
        legend=True,
        scheme="Quantiles" if k > 1 else None,
        k=k if k > 1 else None,
        edgecolor="#505862",
        linewidth=0.8,
        zorder=2,
    )
    for _, row in gdf.iterrows():
        pt = row.geometry.representative_point()
        label = None
        for candidate in ["name", "NAME", "省名", "标注", "fullname"]:
            if candidate in gdf.columns and pd.notna(row[candidate]):
                label = str(row[candidate])
                break
        if label:
            ax.text(pt.x, pt.y, label, fontsize=7, ha="center", va="center", color="#2f3740", zorder=3)
    add_north_arrow(ax, x=0.90, y=0.90, size=0.08)
    add_scale_bar(ax, gdf.crs.to_string())
    finalize_axes(ax, title, gdf.crs.to_string(), data_source)
    ax.grid(False)
    fig.subplots_adjust(left=0.08, right=0.92, top=0.92, bottom=0.08)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return str(output)


def make_web_map(
    gdf: gpd.GeoDataFrame,
    output_html: str,
    tooltip_fields: list[str] | None = None,
    popup_fields: list[str] | None = None,
    title: str = "GIS Map",
    data_source: str = "User input",
) -> str:
    if gdf.crs is None:
        raise ValueError("Web map generation requires a CRS.")
    view = gdf.to_crs(4326)
    center = [view.geometry.centroid.y.mean(), view.geometry.centroid.x.mean()]
    fmap = folium.Map(location=center, zoom_start=8, tiles="CartoDB positron", control_scale=True)
    folium.Marker(
        center,
        icon=folium.DivIcon(
            html=f"""
            <div style="font-size:14px;background:rgba(255,255,255,0.85);padding:6px 8px;border:1px solid #777;">
              <strong>{title}</strong><br/>Data Source: {data_source}
            </div>
            """
        ),
    ).add_to(fmap)
    folium.GeoJson(
        view,
        tooltip=folium.GeoJsonTooltip(fields=tooltip_fields) if tooltip_fields else None,
        popup=folium.GeoJsonPopup(fields=popup_fields) if popup_fields else None,
    ).add_to(fmap)
    north_html = """
    <div style="position: fixed; top: 12px; left: 12px; z-index: 9999; background: white; padding: 6px 10px; border: 1px solid #666;">
      <strong>N</strong><br/>↑
    </div>
    """
    legend_html = """
    <div style="position: fixed; bottom: 18px; right: 18px; z-index: 9999; background: white; padding: 8px 10px; border: 1px solid #666;">
      <strong>Legend</strong><br/>Primary layer
    </div>
    """
    fmap.get_root().html.add_child(folium.Element(north_html))
    fmap.get_root().html.add_child(folium.Element(legend_html))
    folium.LayerControl().add_to(fmap)
    Path(output_html).parent.mkdir(parents=True, exist_ok=True)
    fmap.save(output_html)
    return output_html


def summarize_layers(paths: list[str]) -> list[dict]:
    return summarize_sources(paths)


def available_numeric_fields(gdf: gpd.GeoDataFrame) -> list[str]:
    return gdf.select_dtypes(include=["number"]).columns.tolist()


def available_text_fields(gdf: gpd.GeoDataFrame) -> list[str]:
    return gdf.select_dtypes(include=["object", "string"]).columns.tolist()


def detect_station_columns(df: pd.DataFrame) -> dict[str, str]:
    cols = {str(col): str(col).lower() for col in df.columns}
    lon_col = next((name for name, low in cols.items() if "经度" in name or "longitude" in low or low == "lon"), None)
    lat_col = next((name for name, low in cols.items() if "纬度" in name or "latitude" in low or low == "lat"), None)
    value_col = next(
        (
            name
            for name, low in cols.items()
            if "降雨" in name or "雨量" in name or "rain" in low or "precip" in low
        ),
        None,
    )
    label_col = next((name for name, low in cols.items() if "站名" in name or "name" in low), None)
    if not all([lon_col, lat_col, value_col]):
        raise ValueError(f"Could not detect longitude/latitude/value columns from {df.columns.tolist()}")
    return {"lon": lon_col, "lat": lat_col, "value": value_col, "label": label_col or value_col}


def format_lon(value: float) -> str:
    deg = int(value)
    minute = int(round((value - deg) * 60))
    if minute == 60:
        deg += 1
        minute = 0
    return f"{deg}°{minute:02d}′东"


def format_lat(value: float) -> str:
    deg = int(value)
    minute = int(round((value - deg) * 60))
    if minute == 60:
        deg += 1
        minute = 0
    return f"{deg}°{minute:02d}′北"


def build_rainfall_surface(
    points: gpd.GeoDataFrame,
    boundary: gpd.GeoDataFrame,
    value_col: str,
    grid_size: int = 260,
    interpolation: str = "cubic",
    fallback: str = "nearest",
) -> dict:
    analysis_crs = choose_analysis_crs(boundary)
    points_proj = points.to_crs(analysis_crs)
    boundary_proj = boundary.to_crs(analysis_crs)
    minx, miny, maxx, maxy = boundary_proj.total_bounds
    pad_x = (maxx - minx) * 0.05
    pad_y = (maxy - miny) * 0.05
    x = np.linspace(minx - pad_x, maxx + pad_x, grid_size)
    y = np.linspace(miny - pad_y, maxy + pad_y, grid_size)
    xx, yy = np.meshgrid(x, y)
    union = boundary_proj.union_all()
    mask = np.array([union.contains(Point(px, py)) or union.touches(Point(px, py)) for px, py in zip(xx.ravel(), yy.ravel())])
    mask = mask.reshape(xx.shape)
    station_xy = np.column_stack([points_proj.geometry.x.values, points_proj.geometry.y.values])
    values = points_proj[value_col].astype(float).values
    zz = griddata(station_xy, values, (xx, yy), method=interpolation)
    if fallback:
        zz_fallback = griddata(station_xy, values, (xx, yy), method=fallback)
        zz = np.where(np.isnan(zz), zz_fallback, zz)
    zz = np.ma.array(zz, mask=~mask)
    lon, lat = Transformer.from_crs(analysis_crs, boundary.crs, always_xy=True).transform(xx, yy)
    return {
        "analysis_crs": analysis_crs,
        "points_proj": points_proj,
        "boundary_proj": boundary_proj,
        "lon": lon,
        "lat": lat,
        "surface": zz,
        "values": values,
    }


def make_rainfall_map(
    boundary: gpd.GeoDataFrame,
    points: gpd.GeoDataFrame,
    surface_payload: dict,
    output_path: str,
    title: str,
    value_col: str,
    label_col: str | None = None,
    preset: str = "rainfall_refined",
    label_points: bool = True,
) -> str:
    configure_plot_fonts()
    lon = surface_payload["lon"]
    lat = surface_payload["lat"]
    zz = surface_payload["surface"]
    values = surface_payload["values"]
    bounds = boundary.total_bounds
    fig, ax = plt.subplots(figsize=(8.4, 6.0), facecolor="white" if preset == "rainfall_refined" else "#f7fbff")
    ax.set_facecolor("white" if preset == "rainfall_refined" else "#f7fbff")
    levels_fill = np.linspace(float(values.min()), float(values.max()), 12)
    levels_line = np.linspace(float(values.min()), float(values.max()), 8)
    cmap = plt.get_cmap("turbo")
    filled = ax.contourf(lon, lat, zz, levels=levels_fill, cmap=cmap, alpha=0.98, antialiased=True, zorder=1)
    contours = ax.contour(lon, lat, zz, levels=levels_line, colors="#8d3fae", linewidths=0.8, zorder=2)
    ax.clabel(contours, fmt="%.0f", fontsize=6 if preset == "rainfall_refined" else 7, inline=True, colors="#5e336e")
    boundary.boundary.plot(ax=ax, color="#3c6a8e" if preset == "rainfall_refined" else "#2c4a68", linewidth=0.7 if preset == "rainfall_refined" else 1.4, zorder=3)
    points.plot(ax=ax, color="#2f88d8" if preset == "rainfall_refined" else "#202020", marker="v" if preset == "rainfall_refined" else "o", markersize=14 if preset == "rainfall_refined" else 12, zorder=4)
    if label_points and label_col and label_col in points.columns:
        for _, row in points.iterrows():
            ax.text(row.geometry.x + 0.015, row.geometry.y + 0.012, str(row[label_col]), fontsize=6.5, color="#1f2a35", zorder=5)
    ax.set_xlim(bounds[0] - 0.08, bounds[2] + (0.16 if preset == "rainfall_refined" else 0.08))
    ax.set_ylim(bounds[1] - 0.08, bounds[3] + 0.08)
    if preset == "rainfall_refined":
        xticks = np.array([120 + 40 / 60, 121, 121 + 20 / 60, 121 + 40 / 60, 122, 122 + 20 / 60, 122 + 40 / 60])
        yticks = np.array([30 + 40 / 60, 31, 31 + 20 / 60, 31 + 40 / 60, 32])
        ax.set_xticks(xticks)
        ax.set_yticks(yticks)
        ax.set_xticklabels([format_lon(v) for v in xticks], fontsize=7)
        ax.set_yticklabels([format_lat(v) for v in yticks], fontsize=7)
        ax.text(0.50, 0.83, title.replace("专题图", "分布图"), transform=ax.transAxes, ha="center", va="center", fontsize=11, color="#333333")
        draw_north_arrow_rose(ax)
        draw_scale_bar_geographic(ax, bounds, 17, 2)
        draw_rainfall_legend_panel(ax, cmap, float(values.min()), float(values.max()))
    else:
        ax.set_xlabel("Longitude", fontsize=10)
        ax.set_ylabel("Latitude", fontsize=10)
        ax.set_title(title, fontsize=22, color="#0d6db8", weight="bold", pad=16)
        cbar = fig.colorbar(filled, ax=ax, shrink=0.72, pad=0.02)
        cbar.set_label(f"{value_col}", fontsize=9)
        cbar.ax.tick_params(labelsize=8)
        add_north_arrow(ax, x=0.92, y=0.90, size=0.10)
        draw_scale_bar_geographic(ax, bounds, 20, 1)
        legend_handles = [
            Line2D([0], [0], marker="o", color="none", markerfacecolor="#202020", markersize=5, label="监测站点"),
            Line2D([0], [0], color="#4b4b4b", linewidth=0.9, label="降雨量等值线"),
            Line2D([0], [0], color="#2c4a68", linewidth=1.4, label="边界"),
        ]
        leg = ax.legend(handles=legend_handles, loc="lower right", frameon=True, framealpha=0.92, fontsize=8, title="图例")
        leg.get_frame().set_edgecolor("#c9d2db")
        leg.get_frame().set_facecolor("white")
    ax.grid(True, linestyle="-" if preset == "rainfall_refined" else "--", linewidth=0.45, color="#999999", alpha=0.8)
    for spine in ax.spines.values():
        spine.set_linewidth(0.8)
        spine.set_color("#404040")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if preset == "rainfall_refined":
        fig.subplots_adjust(left=0.08, right=0.90, top=0.92, bottom=0.10)
    else:
        plt.tight_layout()
    fig.savefig(output, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return str(output)


def draw_north_arrow_rose(ax) -> None:
    cx, cy = 0.86, 0.75
    ax.annotate("", xy=(cx, cy + 0.035), xytext=(cx, cy - 0.035), xycoords="axes fraction", arrowprops=dict(arrowstyle="-|>", lw=1.0, color="#303030"))
    circ = plt.Circle((cx, cy), 0.014, transform=ax.transAxes, fill=False, lw=0.8, color="#303030")
    ax.add_patch(circ)
    ax.text(cx, cy + 0.05, "N", transform=ax.transAxes, ha="center", va="center", fontsize=7)
    ax.text(cx, cy - 0.05, "S", transform=ax.transAxes, ha="center", va="center", fontsize=7)
    ax.text(cx - 0.04, cy, "W", transform=ax.transAxes, ha="center", va="center", fontsize=7)
    ax.text(cx + 0.04, cy, "E", transform=ax.transAxes, ha="center", va="center", fontsize=7)


def draw_scale_bar_geographic(ax, bounds: np.ndarray, segment_km: int, segments: int) -> None:
    geod = Geod(ellps="WGS84")
    start_lon = bounds[0] + 0.06
    start_lat = bounds[1] + 0.045
    points = [(start_lon, start_lat)]
    current_lon, current_lat = start_lon, start_lat
    for _ in range(segments):
        current_lon, current_lat, _ = geod.fwd(current_lon, current_lat, 90, segment_km * 1000)
        points.append((current_lon, current_lat))
    color = "#303030"
    for i in range(len(points) - 1):
        ax.plot([points[i][0], points[i + 1][0]], [points[i][1], points[i + 1][1]], color=color, linewidth=1.2 if segments > 1 else 3, zorder=10)
    for idx, (lon, lat) in enumerate(points):
        ax.plot([lon, lon], [lat, lat + 0.006], color=color, linewidth=0.9, zorder=10)
        label = "0" if idx == 0 else f"{segment_km * idx}"
        if idx == len(points) - 1:
            label += " 千米"
        ax.text(lon, lat + 0.010, label, fontsize=6, ha="center", color=color)


def draw_rainfall_legend_panel(ax, cmap, min_value: float, max_value: float) -> None:
    ax.text(0.72, 0.46, "图例", transform=ax.transAxes, fontsize=8, color="#333333")
    ax.add_line(Line2D([0.72, 0.75], [0.42, 0.42], transform=ax.transAxes, color="#2f88d8", marker="v", markersize=5, linewidth=0))
    ax.text(0.76, 0.415, "监测站点", transform=ax.transAxes, fontsize=7, color="#333333")
    ax.add_line(Line2D([0.72, 0.75], [0.385, 0.385], transform=ax.transAxes, color="#8d3fae", linewidth=0.8))
    ax.text(0.76, 0.38, "Contour_降水量", transform=ax.transAxes, fontsize=7, color="#333333")
    ax.text(0.72, 0.34, "Extract_r_降雨量", transform=ax.transAxes, fontsize=7, color="#333333")
    ax.text(0.72, 0.305, "值", transform=ax.transAxes, fontsize=7, color="#333333")
    from mpl_toolkits.axes_grid1.inset_locator import inset_axes
    from matplotlib.colors import Normalize
    cax = inset_axes(ax, width="3.0%", height="12%", loc="lower left", bbox_to_anchor=(0.72, 0.24, 1, 1), bbox_transform=ax.transAxes, borderpad=0)
    norm = Normalize(vmin=min_value, vmax=max_value)
    sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    cb = plt.gcf().colorbar(sm, cax=cax, orientation="vertical")
    cb.outline.set_visible(False)
    cb.set_ticks([max_value, min_value])
    cb.set_ticklabels([f"{max_value:.2f}", f"{min_value:.2f}"])
    cb.ax.tick_params(labelsize=7, length=0, pad=2)
