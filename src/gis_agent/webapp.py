from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from textwrap import dedent

import streamlit as st
import streamlit.components.v1 as components

from gis_agent.engine import execute_plan
from gis_agent.llm import build_plan_with_llm, has_llm_credentials
from gis_agent.planner import build_plan
from gis_agent.runtime import summarize_sources


DATA_EXTENSIONS = {".shp", ".geojson", ".json", ".gpkg", ".parquet", ".csv", ".xlsx", ".xls"}
SIDECAR_EXTENSIONS = {".dbf", ".shx", ".prj", ".cpg", ".sbn", ".sbx"}
SUGGESTED_TASKS = [
    "用县级行政区数据按 value 字段生成分级设色专题图，标题为 Hainan Counties",
    "把示例点数据落入行政区后统计每个区的点数量并生成专题图，标题为 Point Summary",
    "根据监测站位及降雨量 Excel 表和省界线 shp 生成降雨量空间分布专题图，要求包含等值线、图例、比例尺、指北针",
]


def render_app() -> None:
    st.set_page_config(
        page_title="TJSOES-GisAgent",
        page_icon="🗺️",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inject_styles()
    if "last_run" not in st.session_state:
        st.session_state["last_run"] = None
    if "task_history" not in st.session_state:
        st.session_state["task_history"] = []
    if "task_input" not in st.session_state:
        st.session_state["task_input"] = ""

    _render_hero()

    with st.sidebar:
        st.subheader("Run Settings")
        mode = st.selectbox(
            "Planner Mode",
            options=["auto", "template", "llm"],
            help="Auto uses LLM planning when credentials are available; otherwise it falls back to template mode.",
        )
        default_output = Path.cwd() / "runs" / datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = st.text_input("Output Directory", value=str(default_output))
        st.markdown("**Deployment Notes**")
        st.caption("For Shapefile upload all sidecar files together. LLM mode needs valid OpenAI-compatible credentials.")
        st.markdown("**Recent Tasks**")
        if st.session_state["task_history"]:
            for item in st.session_state["task_history"][:5]:
                st.code(item["task"], language="text")
        else:
            st.caption("No runs yet.")

    top_left, top_right = st.columns([1.45, 1])
    with top_left:
        st.markdown("### Task")
        _render_prompt_shortcuts()
        task = st.text_area(
            "Natural-language GIS Task",
            key="task_input",
            height=150,
            placeholder="例如：用上传的海南县级 GeoJSON 按 value 字段生成分级设色专题图，标题为 Hainan Province",
            label_visibility="collapsed",
        )
        uploads = st.file_uploader(
            "Upload GIS data files",
            type=None,
            accept_multiple_files=True,
            help="Upload GeoJSON/Shapefile components/GPKG/CSV/Excel. For Shapefile, include .shp + .dbf + .shx + .prj together.",
        )
        action_left, action_mid, action_right = st.columns([1.2, 1, 1])
        with action_left:
            run_clicked = st.button("Run Workflow", type="primary", use_container_width=True)
        with action_mid:
            st.button("Clear Results", use_container_width=True, on_click=_clear_result_state)
        with action_right:
            st.button("Clear Prompt", use_container_width=True, on_click=_clear_prompt)
    with top_right:
        st.markdown("### Capabilities")
        st.markdown(
            dedent(
                """
                <div class="capability-card">
                  <div class="capability-title">Deterministic Execution</div>
                  <div class="capability-text">Natural language is routed into auditable GIS workflows instead of unconstrained code generation.</div>
                </div>
                <div class="capability-card">
                  <div class="capability-title">GIS Guardrails</div>
                  <div class="capability-text">CRS checks, geometry repair, analysis reprojection, and workflow reports are enforced by the engine.</div>
                </div>
                <div class="capability-card">
                  <div class="capability-title">Deployable UI</div>
                  <div class="capability-text">Upload data, run tasks, inspect workflow plans, and review final outputs in one page.</div>
                </div>
                """,
            ),
            unsafe_allow_html=True,
        )
        _render_status_panel(mode, uploads)

    if uploads:
        st.info("Uploaded files: " + ", ".join(file.name for file in uploads))

    if run_clicked:
        if not task.strip():
            st.error("Task is required.")
            return
        if not uploads:
            st.error("At least one dataset must be uploaded.")
            return
        try:
            run_result = _run_uploaded_task(task=task, uploads=uploads, output_dir=Path(output_dir), mode=mode)
        except Exception as exc:  # noqa: BLE001
            st.exception(exc)
            return
        st.session_state["last_run"] = run_result
        _append_history(run_result)

    if st.session_state["last_run"]:
        _render_result(st.session_state["last_run"])


def _clear_result_state() -> None:
    st.session_state["last_run"] = None


def _clear_prompt() -> None:
    st.session_state["task_input"] = ""


def _append_history(result: dict) -> None:
    history = st.session_state["task_history"]
    summary = {
        "task": result["task"],
        "mode": result["mode"],
        "output_dir": result["output_dir"],
        "result_path": result["result_path"],
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    history.insert(0, summary)
    st.session_state["task_history"] = history[:10]


def _run_uploaded_task(task: str, uploads: list, output_dir: Path, mode: str) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    input_dir = output_dir / "inputs"
    if input_dir.exists():
        shutil.rmtree(input_dir)
    input_dir.mkdir(parents=True, exist_ok=True)

    for uploaded in uploads:
        target = input_dir / uploaded.name
        target.write_bytes(uploaded.getbuffer())

    data_paths = _collect_primary_data_paths(input_dir)
    if not data_paths:
        raise ValueError("No supported GIS data source was found in the uploaded files.")

    summaries = summarize_sources(data_paths)
    resolved_mode = _resolve_mode(mode)
    if resolved_mode == "llm":
        plan = build_plan_with_llm(task, summaries, str(output_dir), data_paths)
    else:
        plan = build_plan(task, data_paths, str(output_dir))
    plan_path = output_dir / "workflow_plan.json"
    plan.write(str(plan_path))

    context = execute_plan(plan)
    report_path = output_dir / "execution_report.json"
    result_path = Path(context.outputs["map"]) if "map" in context.outputs else None
    return {
        "task": task,
        "mode": resolved_mode,
        "data_paths": data_paths,
        "output_dir": str(output_dir),
        "plan_path": str(plan_path),
        "report_path": str(report_path),
        "result_path": str(result_path) if result_path else None,
        "plan": plan.to_dict(),
        "report": json.loads(report_path.read_text(encoding="utf-8")),
    }


def _collect_primary_data_paths(input_dir: Path) -> list[str]:
    data_paths: list[str] = []
    seen_shapefiles: set[str] = set()
    for path in sorted(input_dir.iterdir()):
        suffix = path.suffix.lower()
        if suffix == ".shp":
            seen_shapefiles.add(path.stem)
            data_paths.append(str(path))
            continue
        if suffix in DATA_EXTENSIONS and suffix != ".shp":
            data_paths.append(str(path))
            continue
        if suffix in SIDECAR_EXTENSIONS and path.stem in seen_shapefiles:
            continue
    return data_paths


def _resolve_mode(requested: str) -> str:
    if requested == "auto":
        return "llm" if has_llm_credentials() else "template"
    return requested


def _render_result(result: dict) -> None:
    st.markdown("## Execution Result")
    metric_1, metric_2, metric_3, metric_4 = st.columns(4)
    metric_1.metric("Planner", result["mode"])
    metric_2.metric("Sources", str(len(result["data_paths"])))
    metric_3.metric("Steps", str(len(result["plan"]["steps"])))
    metric_4.metric("Output", Path(result["result_path"]).suffix if result.get("result_path") else "n/a")
    result_path = result.get("result_path")
    preview_col, detail_col = st.columns([1.5, 1])
    with preview_col:
        st.markdown("### Map Preview")
        if result_path:
            suffix = Path(result_path).suffix.lower()
            if suffix == ".png":
                st.image(result_path, caption="Rendered map", use_container_width=True)
            elif suffix == ".html":
                html = Path(result_path).read_text(encoding="utf-8")
                components.html(html, height=680, scrolling=True)
    with detail_col:
        st.markdown("### Run Summary")
        st.write(f"Task: `{result['task']}`")
        st.write(f"Output directory: `{result['output_dir']}`")
        st.write("Input sources:")
        for path in result["data_paths"]:
            st.code(path, language="text")
        if result.get("plan_path"):
            st.download_button(
                "Download Workflow Plan",
                data=json.dumps(result["plan"], ensure_ascii=False, indent=2),
                file_name="workflow_plan.json",
                mime="application/json",
                use_container_width=True,
            )
        if result.get("report_path"):
            st.download_button(
                "Download Execution Report",
                data=json.dumps(result["report"], ensure_ascii=False, indent=2),
                file_name="execution_report.json",
                mime="application/json",
                use_container_width=True,
            )
        if result_path and Path(result_path).exists():
            st.download_button(
                "Download Result",
                data=Path(result_path).read_bytes(),
                file_name=Path(result_path).name,
                mime="application/octet-stream",
                use_container_width=True,
            )

    tab_plan, tab_report, tab_history = st.tabs(["Workflow Plan", "Execution Report", "History"])
    with tab_plan:
        st.json(result["plan"])
    with tab_report:
        st.json(result["report"])
    with tab_history:
        if st.session_state["task_history"]:
            for item in st.session_state["task_history"]:
                st.markdown(
                    f"- `{item['timestamp']}` | `{item['mode']}` | `{item['output_dir']}`\n\n  {item['task']}"
                )
        else:
            st.caption("No run history.")


def _render_hero() -> None:
    st.markdown(
        dedent(
            """
            <div class="hero-shell">
              <div class="hero-copy">
                <div class="hero-kicker">TJSOES-GisAgent</div>
                <h1>Natural-language GIS maps with deterministic execution</h1>
                <p>Built for deployable GIS workflows: upload data, route the task into a fixed analysis plan, and produce auditable maps with CRS safeguards.</p>
              </div>
              <div class="hero-grid">
                <div class="hero-stat"><span>3</span><label>task templates</label></div>
                <div class="hero-stat"><span>CLI</span><label>automation ready</label></div>
                <div class="hero-stat"><span>Web</span><label>browser based</label></div>
                <div class="hero-stat"><span>JSON</span><label>auditable outputs</label></div>
              </div>
            </div>
            """,
        ),
        unsafe_allow_html=True,
    )


def _render_prompt_shortcuts() -> None:
    st.caption("Prompt shortcuts")
    cols = st.columns(len(SUGGESTED_TASKS))
    for idx, text in enumerate(SUGGESTED_TASKS):
        label = f"Template {idx + 1}"
        if cols[idx].button(label, use_container_width=True):
            st.session_state["task_input"] = text


def _render_status_panel(mode: str, uploads: list | None) -> None:
    llm_state = "Available" if has_llm_credentials() else "Not configured"
    upload_count = len(uploads) if uploads else 0
    st.markdown(
        dedent(
            f"""
            <div class="status-panel">
              <div><strong>Planner mode:</strong> {mode}</div>
              <div><strong>LLM credentials:</strong> {llm_state}</div>
              <div><strong>Uploaded files:</strong> {upload_count}</div>
            </div>
            """,
        ),
        unsafe_allow_html=True,
    )


def _inject_styles() -> None:
    st.markdown(
        dedent(
            """
            <style>
            .stApp {
              background:
                radial-gradient(circle at top left, rgba(36, 82, 122, 0.18), transparent 30%),
                radial-gradient(circle at top right, rgba(203, 171, 90, 0.16), transparent 24%),
                linear-gradient(180deg, #eef4f7 0%, #f8faf8 100%);
            }
            .hero-shell {
              display: grid;
              grid-template-columns: 1.6fr 1fr;
              gap: 1.2rem;
              background: linear-gradient(135deg, #163247 0%, #28506d 56%, #d1b173 160%);
              color: #f5f5ef;
              border-radius: 22px;
              padding: 1.4rem 1.6rem;
              margin-bottom: 1rem;
              box-shadow: 0 18px 40px rgba(25, 51, 73, 0.18);
            }
            .hero-kicker {
              text-transform: uppercase;
              letter-spacing: 0.18em;
              font-size: 0.74rem;
              opacity: 0.8;
            }
            .hero-shell h1 {
              margin: 0.35rem 0 0.5rem 0;
              font-size: 2rem;
              line-height: 1.1;
            }
            .hero-shell p {
              margin: 0;
              max-width: 48rem;
              color: rgba(245, 245, 239, 0.88);
              font-size: 1rem;
            }
            .hero-grid {
              display: grid;
              grid-template-columns: repeat(2, minmax(0, 1fr));
              gap: 0.8rem;
            }
            .hero-stat, .capability-card, .status-panel {
              background: rgba(255, 255, 255, 0.08);
              border: 1px solid rgba(255, 255, 255, 0.18);
              border-radius: 16px;
              padding: 0.9rem 1rem;
              backdrop-filter: blur(10px);
            }
            .hero-stat span {
              display: block;
              font-size: 1.4rem;
              font-weight: 700;
            }
            .hero-stat label {
              display: block;
              margin-top: 0.1rem;
              font-size: 0.82rem;
              opacity: 0.82;
            }
            .capability-card {
              background: rgba(255, 255, 255, 0.78);
              border-color: rgba(22, 50, 71, 0.08);
              margin-bottom: 0.85rem;
              color: #24394b;
            }
            .capability-title {
              font-weight: 700;
              margin-bottom: 0.24rem;
            }
            .capability-text {
              color: #445766;
              font-size: 0.94rem;
            }
            .status-panel {
              background: linear-gradient(180deg, rgba(40, 80, 109, 0.95), rgba(30, 60, 82, 0.95));
              color: #f4f7f7;
            }
            @media (max-width: 980px) {
              .hero-shell {
                grid-template-columns: 1fr;
              }
            }
            </style>
            """,
        ),
        unsafe_allow_html=True,
    )


def main() -> None:
    from streamlit.web import cli as stcli

    os.environ.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    os.environ.setdefault("STREAMLIT_SERVER_HEADLESS", "true")
    sys.argv = [
        "streamlit",
        "run",
        str(Path(__file__).resolve()),
        "--server.address=0.0.0.0",
        "--server.headless=true",
        "--browser.gatherUsageStats=false",
    ]
    raise SystemExit(stcli.main())


if __name__ == "__main__":
    render_app()
