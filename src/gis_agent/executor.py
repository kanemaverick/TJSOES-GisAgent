from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run_script(python_executable: str, script_path: str, log_path: str) -> tuple[int, str]:
    result = subprocess.run(
        [python_executable, script_path],
        capture_output=True,
        text=True,
        check=False,
    )
    log_text = f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
    Path(log_path).write_text(log_text, encoding="utf-8")
    return result.returncode, log_text
