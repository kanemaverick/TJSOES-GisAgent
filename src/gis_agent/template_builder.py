from __future__ import annotations

from pathlib import Path


def build_template_script(task: str, data_paths: list[str], output_dir: str) -> str:
    data_path = data_paths[0]
    output_png = str(Path(output_dir) / "result.png")
    output_html = str(Path(output_dir) / "result.html")
    title = _extract_title(task) or "GIS Map"
    wants_web = any(keyword in task.lower() for keyword in ["html", "web", "folium", "交互"])
    color_field_hint = _extract_field(task)
    return f'''from pathlib import Path

from gis_agent.runtime import (
    available_numeric_fields,
    inspect_gdf,
    make_static_map,
    make_web_map,
    read_vector,
    repair_geometry,
    choose_analysis_crs,
)


DATA_PATH = r"{data_path}"
OUTPUT_DIR = Path(r"{output_dir}")
TITLE = {title!r}
FIELD_HINT = {color_field_hint!r}


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    gdf = read_vector(DATA_PATH)
    summary = inspect_gdf(gdf, DATA_PATH)
    print("Layer summary:", summary)
    gdf = repair_geometry(gdf)
    target_crs = choose_analysis_crs(gdf)
    gdf = gdf.to_crs(target_crs)
    numeric_fields = available_numeric_fields(gdf)
    classify_field = FIELD_HINT if FIELD_HINT in gdf.columns else (numeric_fields[0] if numeric_fields else None)
    if {wants_web!r}:
        output = make_web_map(gdf, r"{output_html}", tooltip_fields=gdf.columns[: min(3, len(gdf.columns))].tolist())
    else:
        output = make_static_map(
            gdf,
            r"{output_png}",
            title=TITLE,
            classify_field=classify_field,
            basemap=False,
            data_source=DATA_PATH,
        )
    print("Generated:", output)


if __name__ == "__main__":
    main()
'''


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
    for marker in ["按", "根据", "by field", "field"]:
        if marker in task:
            suffix = task.split(marker, 1)[1].strip()
            token = suffix.split()[0].strip("“”\"'，,。")
            if token:
                return token
    if "value" in lowered:
        return "value"
    return None
