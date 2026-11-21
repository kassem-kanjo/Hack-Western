"""scripts/edge_deploy.sh must stay syntactically valid (bash -n)."""

from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "edge_deploy.sh"


def test_edge_deploy_script_parses() -> None:
    result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_edge_deploy_mentions_the_qnxcam_path() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "--qnxcam" in text
    assert "qnxfile:/tmp/qnxcam.frame" in text
    assert "gcc -O2" in text and "-lcamapi" in text
