from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from aats.config import AppSettings, load_settings
from aats.strategies.orb_vwap import OrbVwapParams, load_params
from tests.conftest import PARAMS_FILE, ROOT


def settings(**kw: object) -> AppSettings:
    base: dict[str, object] = {
        "mode": "replay",
        "broker": {"adapter": "simulated", "base_url": "simulated://localhost"},
        "data_root": "fixtures",
        "event_log_dir": "var",
    }
    base.update(kw)
    return AppSettings.model_validate(base)


@pytest.mark.parametrize("mode", ["live", "LIVE", "production", "prod", "real-money", "live_paper"])
def test_live_modes_fail_at_startup(mode: str) -> None:
    with pytest.raises(ValidationError, match="out of scope"):
        settings(mode=mode)


def test_unknown_mode_rejected() -> None:
    with pytest.raises(ValidationError):
        settings(mode="turbo")


def test_broker_url_must_be_paper() -> None:
    with pytest.raises(ValidationError, match="paper/simulated"):
        settings(mode="paper", broker={"adapter": "alpaca_paper", "base_url": "https://api.alpaca.markets"})


def test_paper_mode_needs_paper_adapter() -> None:
    with pytest.raises(ValidationError, match="paper broker adapter"):
        settings(mode="paper")


def test_committed_profiles_load() -> None:
    assert load_settings(ROOT / "configs" / "replay.yaml").mode.value == "replay"
    assert load_settings(ROOT / "configs" / "paper.example.yaml").broker.adapter == "alpaca_paper"


def test_strategy_params_load_and_hash_is_stable(params: OrbVwapParams) -> None:
    assert params.parameter_hash() == load_params(PARAMS_FILE).parameter_hash()


def test_missing_parameter_disables_strategy(tmp_path: Path) -> None:
    raw = yaml.safe_load(PARAMS_FILE.read_text())
    del raw["max_extension_atr"]
    f = tmp_path / "p.yaml"
    f.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValidationError, match="max_extension_atr"):
        load_params(f)


def test_band_must_be_ordered() -> None:
    raw = yaml.safe_load(PARAMS_FILE.read_text())
    raw.update(orb_width_atr_min="0.7", orb_width_atr_max="0.6")
    with pytest.raises(ValidationError, match="orb_width_atr_min"):
        OrbVwapParams.model_validate(raw)
