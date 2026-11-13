"""Smoke test: the tester viewer builds from the committed fixtures and embeds valid data."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.conftest import ROOT


def test_viewer_builds_with_every_day(tmp_path: Path) -> None:
    out = tmp_path / "viewer.html"
    subprocess.run([sys.executable, str(ROOT / "tools" / "build_viewer.py"), "--out", str(out)], check=True)
    html = out.read_text(encoding="utf-8")
    assert "/*__AATS_DATA__*/" not in html
    start = html.index("const AATS=") + len("const AATS=")
    data = json.loads(html[start : html.index(";</script>", start)])
    single = len(list((ROOT / "fixtures" / "sessions").iterdir()))
    universe = len(list((ROOT / "fixtures" / "universe").iterdir()))
    assert len(data["days"]) == single + universe
    for day in data["days"]:
        assert day["description"] and day["title"] != day["id"], day["id"]
        for sym in day["symbols"]:
            for run in sym["runs"]:
                assert all(t and not t.isupper() for t in run["text"]), run  # plain English, not raw codes
