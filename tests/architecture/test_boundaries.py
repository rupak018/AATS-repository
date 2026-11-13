"""Architecture guards: authority boundaries from design sections 4 and 17, enforced statically."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from aats.domain.enums import Mode

PKG = Path(__file__).resolve().parents[2] / "aats"


def modules() -> list[Path]:
    return sorted(PKG.rglob("*.py"))


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def pkg_of(path: Path) -> str:
    return path.relative_to(PKG).parts[0]


FORBIDDEN = {
    # LLM code may never reach orders, risk policy or configuration writers.
    "llm": ("aats.execution", "aats.risk", "aats.config"),
    # Strategy proposes; it never touches the broker or the risk gate.
    "strategies": ("aats.execution", "aats.risk", "aats.llm"),
    "features": ("aats.execution", "aats.risk", "aats.llm", "aats.strategies"),
    "domain": ("aats.execution", "aats.risk", "aats.llm", "aats.strategies", "aats.replay"),
    "regime": ("aats.execution", "aats.risk", "aats.llm", "aats.strategies", "aats.replay"),
    "selection": ("aats.execution", "aats.risk", "aats.llm", "aats.strategies", "aats.replay"),
    # Risk is deterministic: no LLM imports.
    "risk": ("aats.llm",),
}


@pytest.mark.parametrize("path", modules(), ids=lambda p: str(p.relative_to(PKG)))
def test_forbidden_imports(path: Path) -> None:
    banned = FORBIDDEN.get(pkg_of(path), ())
    bad = [m for m in imports_of(path) if m.startswith(banned)]
    assert not bad, f"{path.relative_to(PKG)} imports {bad}"


@pytest.mark.parametrize("path", modules(), ids=lambda p: str(p.relative_to(PKG)))
def test_no_wall_clock_or_randomness_in_business_logic(path: Path) -> None:
    if path.name == "clock.py":
        return  # the only place a real clock may live
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = {
        f"{n.func.value.id}.{n.func.attr}"
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
    }
    assert not calls & {"datetime.now", "datetime.utcnow", "time.time", "date.today", "uuid.uuid4"}
    assert "random" not in imports_of(path)


@pytest.mark.parametrize("path", modules(), ids=lambda p: str(p.relative_to(PKG)))
def test_no_float_prices(path: Path) -> None:
    """Prices are Decimal; float() conversions in the package are a determinism hazard."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    floats = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "float"
    ]
    assert not floats, f"float() used at lines {floats}"


def test_there_is_no_live_mode() -> None:
    assert {m.value for m in Mode} == {"test", "replay", "paper"}


def test_broker_credentials_only_in_execution() -> None:
    for path in modules():
        if pkg_of(path) in ("execution", "config"):
            continue
        src = path.read_text(encoding="utf-8").lower()
        assert "aats_broker_secret" not in src and "aats_broker_key" not in src, path
