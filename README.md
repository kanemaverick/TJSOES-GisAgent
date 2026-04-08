# TJSOES-GisAgent

![Python](https://img.shields.io/badge/python-3.11%2B-1f6feb)
![GIS](https://img.shields.io/badge/GIS-deterministic%20workflow-2d6a4f)
![UI](https://img.shields.io/badge/UI-streamlit-bd5d38)
![Deploy](https://img.shields.io/badge/deploy-docker%20%7C%20compose-005f73)

`TJSOES-GisAgent` is a deployable natural-language GIS mapping engine for deterministic spatial workflows.

It is designed for users who want an agent-like GIS experience, but do not want unconstrained code generation. Instead of asking a model to freely write spatial scripts, this project routes requests into fixed GIS workflows with CRS safeguards, geometry repair, auditable plans, and standardized map outputs.

## Table of Contents

- [Highlights](#highlights)
- [Gallery](#gallery)
- [Why This Project](#why-this-project)
- [Core Capabilities](#core-capabilities)
- [Project Structure](#project-structure)
- [Quick Start](#quick-start)
- [Deployment](#deployment)
- [Web UI](#web-ui)
- [How The Agent Works](#how-the-agent-works)
- [Example Commands](#example-commands)
- [Outputs](#outputs)
- [Environment Variables](#environment-variables)
- [Production Notes](#production-notes)
- [Comparison With MapGPT Agent](#comparison-with-mapgpt-agent)
- [Known Limitations](#known-limitations)
- [Roadmap](#roadmap)
- [LLM Mode](#llm-mode)

## Highlights

- Natural language to deterministic GIS workflow
- Static map and Web UI support
- CRS checks and geometry repair built in
- Choropleth, rainfall interpolation, and point-in-polygon summary templates
- Layer-role inference and field semantics inference
- Deployable with local Python, Docker, or Docker Compose

## Gallery

### Choropleth Map
![分级设色图示例](assets/choropleth.png)

### Rainfall Surface Map
![降雨量专题图示例](assets/rainfall.png)

## Why This Project

Many GIS agents look good in demos but break in real workflows because they:

- ignore CRS or treat all coordinates as analysis-ready
- hallucinate tool calls or analysis parameters
- fail to distinguish point, line, polygon, and tabular sources
- generate non-standard map output without proper cartographic elements
- lack auditable execution traces

`TJSOES-GisAgent` takes the opposite approach:

- it plans first
- it executes only supported tools
- it preserves GIS guardrails
- it records workflow and execution outputs

## Core Capabilities

### Deterministic GIS Engine

- Mixed-source ingestion for vector layers and tabular data
- Mandatory CRS validation for vector workflows
- Geometry repair before overlay and polygon-based operations
- Automatic projected CRS selection for metric analysis
- Output artifacts saved for every run:
  - `workflow_plan.json`
  - `execution_report.json`
  - rendered map output

### Agent Semantics

- Layer-role inference
  - identifies likely `boundary`, `polygon_thematic`, `points`, `stations`, `stations_table`
  - infers polygon layer index, point layer index, and boundary layer index for multi-source tasks
- Field semantics inference
  - selects likely thematic fields when the user does not specify one
  - prioritizes fields resembling `population`, `density`, `rainfall`, `count`, `value`, `score`, `rate`
- Task-level reasoning improvements
  - point-in-polygon tasks no longer assume fixed input order
  - choropleth tasks prefer more plausible thematic fields
  - rainfall tasks prioritize station tables and boundary polygons

### Supported Workflow Templates

1. Rainfall surface map
   - Input: `Excel/CSV + polygon boundary`
   - Output: interpolated rainfall surface, contours, stations, legend, scale bar, north arrow
   - Route: `rainfall_surface_map`

2. Choropleth map
   - Input: polygon layer with numeric attributes
   - Output: classified thematic polygon map
   - Route: `choropleth_map`

3. Point-in-polygon summary map
   - Input: point layer + polygon layer
   - Output: aggregated polygon thematic map
   - Route: `point_in_polygon_summary_map`

### Rendering

- Standard static polygon map renderer
- Refined rainfall renderer for assignment-style output
- Classified administrative choropleth renderer
- Web map export for interactive map output

## Project Structure

```text
app.py
Dockerfile
docker-compose.yml
Makefile
src/gis_agent/
  cli.py
  planner.py
  semantics.py
  engine.py
  runtime.py
  workflow.py
  webapp.py
  tool_registry.py
  llm.py
  prompts.py
examples/
  create_sample_data.py
  render_shanghai_rainfall_refined.py
```

## Quick Start

### Local Python

```bash
git clone <your-repo-url>
cd TJSOES-GisAgent
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

### Start Web UI

```bash
source .venv/bin/activate
gis-agent-web
```

Or:

```bash
streamlit run app.py
```

Or:

```bash
make install
make web
```

Then open:

```text
http://localhost:8501
```

### Start CLI

```bash
source .venv/bin/activate
gis-agent --help
```

Or:

```bash
make cli
```

## Deployment

### Docker

```bash
git clone <your-repo-url>
cd TJSOES-GisAgent
docker build -t tjsoes-gis-agent .
docker run --rm -p 8501:8501 tjsoes-gis-agent
```

### Docker Compose

```bash
git clone <your-repo-url>
cd TJSOES-GisAgent
cp .env.example .env
docker compose up --build
```

Recommended delivery layout:

- `examples/`: read-only sample data
- `runs/`: actual task outputs
- `.env`: LLM credentials and model configuration

## Web UI

The current Web UI supports:

- natural-language task input
- GIS file upload
- built-in prompt shortcuts
- run status overview
- result map preview
- workflow plan and execution report download
- recent task history
- non-interactive startup suitable for deployment

## How The Agent Works

The current execution flow has four layers:

1. Natural-language task parsing
   - identifies whether the task is closer to choropleth, point-in-polygon summary, rainfall interpolation, or generic mapping
2. Data semantics inference
   - identifies likely roles for each source
   - infers the most plausible thematic field
3. Deterministic workflow planning
   - generates a fixed tool sequence rather than unconstrained code
4. GIS execution and map rendering
   - runs CRS checks, geometry repair, reprojection, analysis, and standardized rendering

## Example Commands

### Example 1: Choropleth Map

```bash
gis-agent \
  --task "用示例行政区数据按 value 字段生成分级设色专题图，标题为 Sample Choropleth" \
  --data examples/data/sample_polygons.geojson \
  --output-dir output/template_choropleth \
  --mode template \
  --run
```

### Example 2: Point-in-Polygon Summary

```bash
gis-agent \
  --task "把示例点数据落入行政区后统计每个区的点数量并生成专题图，标题为 Sample Point Summary" \
  --data examples/data/sample_points.geojson examples/data/sample_polygons.geojson \
  --output-dir output/template_point_summary_v2 \
  --mode template \
  --run
```

For this type of task, the agent automatically infers:

- which input is more likely the point layer
- which input is more likely the polygon boundary layer
- whether the aggregation should behave like `count`, `sum`, or `mean`

### Example 3: Rainfall Surface Map

```bash
gis-agent \
  --task "根据监测站位及降雨量 Excel 表和省界线 shp，一步生成上海市2016年度降雨量空间分布专题图，要求包含监测站点、等值线、降雨场插值、经纬网、图例、比例尺、图名" \
  --data "/path/to/监测站位及降雨量.xlsx" "/path/to/sjZJ.shp" \
  --output-dir output/algorithm_v2_demo3 \
  --mode template \
  --run
```

## Outputs

Each task run produces:

- `workflow_plan.json`: workflow execution plan
- `execution_report.json`: execution report
- `result.png` or `result.html`: rendered output

Some workflows also export derived GIS intermediate layers.

## Environment Variables

To enable LLM planning mode:

```bash
export OPENAI_API_KEY=...
export OPENAI_MODEL=gpt-4.1
export OPENAI_BASE_URL=https://your-compatible-endpoint/v1
```

Docker Compose reads `.env` automatically.

## Production Notes

- Prefer `docker compose up --build`
- Mount `runs/` to the host machine to retain outputs
- Use Nginx or Caddy in front if exposing the service publicly
- Add user isolation and task queueing before multi-user deployment

## Comparison With MapGPT Agent

### MapGPT Agent Strengths

- faster prompt-to-image experience
- better out-of-the-box experience for lightweight demos
- often stronger first-pass visual polish when inputs are already clean

### TJSOES-GisAgent Strengths

- deterministic workflow execution instead of unconstrained code generation
- explicit routing and tool control
- easier to test and harden
- better foundation for ArcGIS Pro-like geoprocessing behavior
- auditable workflow and execution outputs
- layer-role and field semantics inference

### Practical Difference

- `MapGPT Agent` behaves more like a natural-language cartography assistant
- `TJSOES-GisAgent` behaves more like a controllable GIS execution engine
- `TJSOES-GisAgent` emphasizes identifying source roles before analysis

## Known Limitations

- not yet a full replacement for ArcGIS Pro
- CRS strategy is improved but not yet complete across all spatial scales
- layout quality is still template-driven rather than managed by a full layout engine
- label placement and collision handling remain basic
- Web UI is still designed for single-task interactive usage
- field semantics inference is currently heuristic, not model-based
- layer-role inference still relies on naming and attribute cues for complex business layers
- more workflow templates are still needed:
  - kernel density
  - buffer analysis
  - overlay analysis
  - network / accessibility analysis

## Roadmap

1. Add more thematic map templates
2. Add task history, result archiving, and batch support to the Web UI
3. Improve field alias understanding, Chinese field recognition, and layer purpose scoring
4. Build regression tests for workflow routing and output quality
5. Improve title extraction and field inference
6. Add stronger cartographic layout presets and layout scoring

## LLM Mode

The project still supports OpenAI-compatible planning mode.

When `--mode auto` is used:

- if valid LLM credentials are present, the engine uses LLM planning
- otherwise it falls back to deterministic template planning
