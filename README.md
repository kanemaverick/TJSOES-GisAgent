# GIS Agent

`gis-agent` is a deterministic GIS workflow engine for natural-language map tasks.

It started as a simple map generator and has been refactored into a small template library with task routing, fixed tool execution, CRS safeguards, and specialized renderers for common thematic-map workflows.

## Current Positioning

- Natural-language task -> structured workflow JSON
- Deterministic execution instead of free-form code generation
- Task-aware routing for multiple GIS map templates
- Designed to move closer to ArcGIS Pro style geoprocessing behavior

## Implemented Capabilities

### Core engine

- Reads mixed sources: vector layers and tabular files
- Enforces CRS presence for vector workflows
- Repairs invalid geometries before analysis
- Chooses an analysis CRS before projected analysis
- Writes `workflow_plan.json` and `execution_report.json` for every run

### Task templates

1. Rainfall / interpolation thematic map
- Input: `Excel/CSV + polygon boundary`
- Output: interpolated rainfall surface, contour lines, station points, graticule, legend, scale bar, north arrow
- Route: `rainfall_surface_map`

2. Choropleth map
- Input: polygon layer with numeric field
- Output: classified polygon thematic map
- Route: `choropleth_map`

3. Point-in-polygon summary map
- Input: point layer + polygon layer
- Output: polygon summary map after spatial aggregation
- Route: `point_in_polygon_summary_map`

### Rendering behavior

- Standard polygon map renderer
- Refined rainfall map renderer tuned toward coursework-style output
- Choropleth renderer for administrative thematic maps
- Web-map output support for generic vector layers

## Project Structure

```text
src/gis_agent/
  cli.py
  planner.py
  engine.py
  runtime.py
  workflow.py
  tool_registry.py
  llm.py
examples/
  create_sample_data.py
  render_shanghai_rainfall_refined.py
```

## Quick Start

```bash
cd /home/xzkane/gis-agent
source .venv/bin/activate
python -m pip install -e .
python examples/create_sample_data.py
```

### Example 1: Choropleth

```bash
gis-agent \
  --task "用示例行政区数据按 value 字段生成分级设色专题图，标题为 Sample Choropleth" \
  --data examples/data/sample_polygons.geojson \
  --output-dir output/template_choropleth \
  --mode template \
  --run
```

### Example 2: Point-in-polygon summary

```bash
gis-agent \
  --task "把示例点数据落入行政区后统计每个区的点数量并生成专题图，标题为 Sample Point Summary" \
  --data examples/data/sample_points.geojson examples/data/sample_polygons.geojson \
  --output-dir output/template_point_summary_v2 \
  --mode template \
  --run
```

### Example 3: Rainfall thematic map

```bash
gis-agent \
  --task "根据监测站位及降雨量 Excel 表和省界线 shp，一步生成上海市2016年度降雨量空间分布专题图，要求包含监测站点、等值线、降雨场插值、经纬网、图例、比例尺、图名" \
  --data "/path/to/监测站位及降雨量.xlsx" "/path/to/sjZJ.shp" \
  --output-dir output/algorithm_v2_demo3 \
  --mode template \
  --run
```

## Verified Runs In This Iteration

### 1. Hainan island administrative divisions

- Source: `geojson.cn` China Atlas API `1.6.3`
- Output: `/home/xzkane/gis-agent/output/hainan_case/hainan_island_admin_divisions_colored_map.png`
- Notes:
  - Removed Sansha / island-group units
  - Kept only Hainan Island mainland administrative divisions
  - Added categorical coloring by unit

### 2. Shanghai 2016 rainfall thematic map

- Input:
  - station Excel
  - Shanghai boundary extracted from provincial shapefile
- Output:
  - `/home/xzkane/gis-agent/output/shanghai_rainfall_case/shanghai_2016_rainfall_thematic_map_refined.png`
  - `/home/xzkane/gis-agent/output/algorithm_v2_demo3/result.png`
- Notes:
  - Interpolation route automatically detected as `rainfall_surface_map`
  - Analysis CRS: `EPSG:32651`
  - First-pass output now uses the refined rainfall cartographic preset

### 3. Template library verification

- Choropleth:
  - `/home/xzkane/gis-agent/output/template_choropleth/result.png`
- Point summary:
  - `/home/xzkane/gis-agent/output/template_point_summary_v2/result.png`

## Workflow Outputs

Every run emits:

- `workflow_plan.json`
- `execution_report.json`
- `result.png` or `result.html`

For some specialized routes, derived GIS data is also exported.

## Comparison With MapGPT Agent

### Where `MapGPT Agent` is stronger

- Faster “prompt to picture” experience
- Better out-of-the-box feeling for lightweight map authoring demos
- Often more visually pleasing on the first attempt when the input data is already well prepared

### Where `gis-agent` is stronger

- Deterministic workflow instead of unconstrained code generation
- Explicit task routing
- Easier to test and harden
- Easier to extend into ArcGIS Pro style geoprocessing patterns
- Produces auditable workflow and execution artifacts

### Practical difference

- `MapGPT Agent` is closer to a natural-language cartography assistant
- `gis-agent` is closer to a controllable GIS execution engine

## Known Gaps

- Not yet a full ArcGIS Pro equivalent
- CRS strategy is improved but still not complete for all geography scales
- Layout quality is template-driven, not yet a full composition engine
- Automatic label placement and collision handling remain basic
- More task templates are still needed:
  - kernel density
  - buffer analysis maps
  - overlay analysis maps
  - network / accessibility maps

## Roadmap

Next useful steps:

1. Add more thematic-map templates
2. Add regression tests for workflow routing and outputs
3. Improve title extraction and field inference further
4. Add stronger cartographic layout scoring / presets

## LLM Mode

OpenAI-compatible planning mode is still supported:

```bash
export OPENAI_API_KEY=...
export OPENAI_MODEL=gpt-4.1
export OPENAI_BASE_URL=https://your-compatible-endpoint/v1
```

In `auto` mode, the engine uses LLM planning when credentials exist; otherwise it falls back to template planning.
