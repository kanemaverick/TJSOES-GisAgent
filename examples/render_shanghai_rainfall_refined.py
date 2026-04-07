from __future__ import annotations

from pathlib import Path
import warnings

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager as fm
from matplotlib.colors import Normalize
from matplotlib.lines import Line2D
from mpl_toolkits.axes_grid1.inset_locator import inset_axes
from pyproj import Geod, Transformer
from scipy.interpolate import griddata
from shapely.geometry import Point


BASE = Path(
    "/home/xzkane/Documents/xwechat_files/wxid_owngkl8qdwq222_33c5/msg/file/2026-04/课后实践作业-1/Data"
)
OUT_DIR = Path("/home/xzkane/gis-agent/output/shanghai_rainfall_case")
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_PNG = OUT_DIR / "shanghai_2016_rainfall_thematic_map_refined.png"


def configure_fonts() -> None:
    warnings.filterwarnings("ignore", message="Glyph .* missing from font")
    for font_path in [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]:
        path = Path(font_path)
        if path.exists():
            fm.fontManager.addfont(str(path))
            plt.rcParams["font.family"] = fm.FontProperties(fname=str(path)).get_name()
            break
    plt.rcParams["axes.unicode_minus"] = False


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


def draw_north_arrow(ax) -> None:
    cx, cy = 0.86, 0.75
    ax.annotate(
        "",
        xy=(cx, cy + 0.035),
        xytext=(cx, cy - 0.035),
        xycoords="axes fraction",
        arrowprops=dict(arrowstyle="-|>", lw=1.0, color="#303030"),
    )
    circ = plt.Circle((cx, cy), 0.014, transform=ax.transAxes, fill=False, lw=0.8, color="#303030")
    ax.add_patch(circ)
    ax.text(cx, cy + 0.05, "N", transform=ax.transAxes, ha="center", va="center", fontsize=7)
    ax.text(cx, cy - 0.05, "S", transform=ax.transAxes, ha="center", va="center", fontsize=7)
    ax.text(cx - 0.04, cy, "W", transform=ax.transAxes, ha="center", va="center", fontsize=7)
    ax.text(cx + 0.04, cy, "E", transform=ax.transAxes, ha="center", va="center", fontsize=7)


def draw_scale_bar(ax, bounds: np.ndarray) -> None:
    geod = Geod(ellps="WGS84")
    start_lon = bounds[0] + 0.06
    start_lat = bounds[1] + 0.045
    seg_km = 17
    lon1, lat1, _ = geod.fwd(start_lon, start_lat, 90, seg_km * 1000)
    lon2, lat2, _ = geod.fwd(lon1, lat1, 90, seg_km * 1000)
    color = "#303030"
    ax.plot([start_lon, lon1], [start_lat, lat1], color=color, linewidth=1.2, zorder=10)
    ax.plot([lon1, lon2], [lat1, lat2], color=color, linewidth=1.2, zorder=10)
    for lon, lat in [(start_lon, start_lat), (lon1, lat1), (lon2, lat2)]:
        ax.plot([lon, lon], [lat, lat + 0.006], color=color, linewidth=0.9, zorder=10)
    ax.text(start_lon, start_lat + 0.010, "0", fontsize=6, ha="center", color=color)
    ax.text(lon1, lat1 + 0.010, "17", fontsize=6, ha="center", color=color)
    ax.text(lon2, lat2 + 0.010, "34 千米", fontsize=6, ha="center", color=color)


def main() -> None:
    configure_fonts()

    df = pd.read_excel(BASE / "监测站位及降雨量.xlsx", sheet_name="y2016a_point")
    points = gpd.GeoDataFrame(
        df.copy(),
        geometry=gpd.points_from_xy(df["经度坐标"], df["纬度坐标"]),
        crs="EPSG:4490",
    )

    prov = gpd.read_file(BASE / "省界线" / "sjZJ.shp")
    prov["geometry"] = prov.geometry.make_valid()
    shanghai = prov[prov["省名"] == "上海"].copy().to_crs("EPSG:4490")

    proj_crs = "EPSG:32651"
    points_proj = points.to_crs(proj_crs)
    shanghai_proj = shanghai.to_crs(proj_crs)

    minx, miny, maxx, maxy = shanghai_proj.total_bounds
    pad_x = (maxx - minx) * 0.05
    pad_y = (maxy - miny) * 0.05
    x = np.linspace(minx - pad_x, maxx + pad_x, 260)
    y = np.linspace(miny - pad_y, maxy + pad_y, 260)
    xx, yy = np.meshgrid(x, y)

    union = shanghai_proj.union_all()
    mask = np.array([union.contains(Point(px, py)) or union.touches(Point(px, py)) for px, py in zip(xx.ravel(), yy.ravel())])
    mask = mask.reshape(xx.shape)

    station_xy = np.column_stack([points_proj.geometry.x.values, points_proj.geometry.y.values])
    rain = points_proj["降雨量(2016)"].astype(float).values
    zz = griddata(station_xy, rain, (xx, yy), method="cubic")
    zz = np.where(np.isnan(zz), griddata(station_xy, rain, (xx, yy), method="nearest"), zz)
    zz = np.ma.array(zz, mask=~mask)

    lon, lat = Transformer.from_crs(proj_crs, "EPSG:4490", always_xy=True).transform(xx, yy)

    fig, ax = plt.subplots(figsize=(8.4, 6.0), facecolor="white")
    ax.set_facecolor("white")

    levels_fill = np.linspace(float(rain.min()), float(rain.max()), 12)
    levels_line = np.linspace(float(rain.min()), float(rain.max()), 8)
    cmap = plt.get_cmap("turbo")
    ax.contourf(lon, lat, zz, levels=levels_fill, cmap=cmap, alpha=0.98, antialiased=True, zorder=1)
    contours = ax.contour(lon, lat, zz, levels=levels_line, colors="#8d3fae", linewidths=0.8, zorder=2)
    ax.clabel(contours, fmt="%.0f", fontsize=6, inline=True, colors="#5e336e")

    shanghai.boundary.plot(ax=ax, color="#3c6a8e", linewidth=0.7, zorder=3)
    points.plot(ax=ax, color="#2f88d8", marker="v", markersize=14, zorder=4)

    bounds = shanghai.total_bounds
    ax.set_xlim(bounds[0] - 0.08, bounds[2] + 0.16)
    ax.set_ylim(bounds[1] - 0.08, bounds[3] + 0.08)

    xticks = np.array([120 + 40 / 60, 121, 121 + 20 / 60, 121 + 40 / 60, 122, 122 + 20 / 60, 122 + 40 / 60])
    yticks = np.array([30 + 40 / 60, 31, 31 + 20 / 60, 31 + 40 / 60, 32])
    ax.set_xticks(xticks)
    ax.set_yticks(yticks)
    ax.set_xticklabels([format_lon(v) for v in xticks], fontsize=7)
    ax.set_yticklabels([format_lat(v) for v in yticks], fontsize=7)
    ax.grid(True, linestyle="-", linewidth=0.45, color="#999999", alpha=0.8)

    for spine in ax.spines.values():
        spine.set_linewidth(0.8)
        spine.set_color("#404040")

    ax.text(0.50, 0.83, "上海市2016年降雨量分布图", transform=ax.transAxes, ha="center", va="center", fontsize=11, color="#333333")

    draw_north_arrow(ax)
    draw_scale_bar(ax, bounds)

    ax.text(0.72, 0.46, "图例", transform=ax.transAxes, fontsize=8, color="#333333")
    ax.add_line(Line2D([0.72, 0.75], [0.42, 0.42], transform=ax.transAxes, color="#2f88d8", marker="v", markersize=5, linewidth=0))
    ax.text(0.76, 0.415, "监测站点", transform=ax.transAxes, fontsize=7, color="#333333")
    ax.add_line(Line2D([0.72, 0.75], [0.385, 0.385], transform=ax.transAxes, color="#8d3fae", linewidth=0.8))
    ax.text(0.76, 0.38, "Contour_降水量", transform=ax.transAxes, fontsize=7, color="#333333")
    ax.text(0.72, 0.34, "Extract_r_降雨量", transform=ax.transAxes, fontsize=7, color="#333333")
    ax.text(0.72, 0.305, "值", transform=ax.transAxes, fontsize=7, color="#333333")

    cax = inset_axes(ax, width="3.0%", height="12%", loc="lower left", bbox_to_anchor=(0.72, 0.24, 1, 1), bbox_transform=ax.transAxes, borderpad=0)
    norm = Normalize(vmin=float(rain.min()), vmax=float(rain.max()))
    sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    cb = fig.colorbar(sm, cax=cax, orientation="vertical")
    cb.outline.set_visible(False)
    cb.set_ticks([float(rain.max()), float(rain.min())])
    cb.set_ticklabels([f"{float(rain.max()):.2f}", f"{float(rain.min()):.2f}"])
    cb.ax.tick_params(labelsize=7, length=0, pad=2)

    plt.tight_layout()
    fig.savefig(OUT_PNG, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(OUT_PNG)


if __name__ == "__main__":
    main()
