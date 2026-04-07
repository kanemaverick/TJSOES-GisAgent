from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class ToolSpec:
    name: str
    handler: Callable
    description: str


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, name: str, handler: Callable, description: str) -> None:
        self._tools[name] = ToolSpec(name=name, handler=handler, description=description)

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"Unknown GIS tool: {name}")
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools.keys())
