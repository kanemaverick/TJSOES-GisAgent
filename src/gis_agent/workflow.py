from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import json


@dataclass
class MapSpec:
    title: str = "GIS Map"
    output_format: str = "png"
    task_type: str = "generic_map"
    classify_field: str | None = None
    basemap: bool = False
    color_scheme: str = "Blues"
    cartographic_preset: str = "standard"
    aggregate_field: str | None = None
    aggregate_method: str = "count"
    tooltip_fields: list[str] = field(default_factory=list)
    add_legend: bool = True
    add_north_arrow: bool = True
    add_scale_bar: bool = True
    add_axis_labels: bool = True
    label_points: bool = False


@dataclass
class WorkflowStep:
    tool: str
    params: dict


@dataclass
class WorkflowPlan:
    task: str
    data_paths: list[str]
    output_dir: str
    analysis_crs: str | None = None
    map_spec: MapSpec = field(default_factory=MapSpec)
    steps: list[WorkflowStep] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["steps"] = [asdict(step) for step in self.steps]
        return data

    def write(self, path: str) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
