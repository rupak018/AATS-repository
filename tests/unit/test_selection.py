from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from aats.domain.market import DailyBar
from aats.selection import (
    PremarketStats,
    SelectionParams,
    UniverseMember,
    build_watchlist,
    load_selection_params,
    select_stocks_in_play,
)
from tests.conftest import ROOT

D = date(2026, 10, 14)
P = load_selection_params(ROOT / "configs" / "selection" / "selection-1.0.0.yaml")


def history(sym: str, close: str = "50", vol: int = 2_000_000, rng: str = "2.00") -> tuple[DailyBar, ...]:
    c, r = Decimal(close), Decimal(rng)
    return tuple(
        DailyBar(
            symbol=sym,
            session_date=f"2026-09-{i + 1:02d}",
            open=c,
            high=c + r / 2,
            low=c - r / 2,
            close=c,
            volume=Decimal(vol),
        )
        for i in range(15)
    )


def member(sym: str, **kw: Any) -> UniverseMember:
    base: dict[str, Any] = dict(
        symbol=sym,
        security_type="COMMON_STOCK",
        primary_exchange="XNAS",
        sessions_since_listing=500,
        corporate_action_in_window=False,
    )
    base.update(kw)
    return UniverseMember(**base)


def stats(sym: str, ref: str = "52.00", pm_vol: int = 100_000, **hist_kw: Any) -> PremarketStats:
    return PremarketStats(
        symbol=sym,
        prior_close=Decimal("50.00"),
        reference_price=Decimal(ref),
        premarket_volume=pm_vol,
        daily_history=history(sym, **hist_kw),
    )


def failed(wl, sym: str) -> set[str]:  # type: ignore[no-untyped-def]
    e = next(e for e in wl.entries if e.symbol == sym)
    return {g.gate for g in e.gates if not g.passed}


@pytest.mark.parametrize(
    ("name", "m_kw", "s_kw", "gate"),
    [
        ("etf", dict(security_type="ETF"), {}, "SECURITY_TYPE"),
        ("otc", dict(primary_exchange="OTC"), {}, "EXCHANGE"),
        ("recent_ipo", dict(sessions_since_listing=5), {}, "IPO_WINDOW"),
        ("corp_action", dict(corporate_action_in_window=True), {}, "CORPORATE_ACTION"),
        ("cheap", {}, dict(ref="4.99"), "PRICE"),
        ("thin", {}, dict(vol=900_000), "AVG_DAILY_VOLUME"),
        ("low_dollar", {}, dict(close="9", vol=1_000_000), "AVG_DOLLAR_VOLUME"),
        ("quiet", {}, dict(rng="0.40"), "ATR"),
    ],
)
def test_each_gate_rejects(name: str, m_kw: dict[str, Any], s_kw: dict[str, Any], gate: str) -> None:
    wl = build_watchlist(D, [member("ABC", **m_kw)], {"ABC": stats("ABC", **s_kw)}, P)
    assert gate in failed(wl, "ABC")
    assert wl.selected == ()


def test_missing_stats_is_a_failed_gate() -> None:
    wl = build_watchlist(D, [member("ABC")], {}, P)
    assert failed(wl, "ABC") == {"PREMARKET_STATS"}


def test_short_history_fails() -> None:
    s = stats("ABC")
    s = s.model_copy(update={"daily_history": s.daily_history[:5]})
    assert "HISTORY" in failed(build_watchlist(D, [member("ABC")], {"ABC": s}, P), "ABC")


def test_watchlist_rank_and_truncation() -> None:
    p = P.model_copy(update={"watchlist_max": 2, "top_n": 2})
    uni = [member(s) for s in ("AAA", "BBB", "CCC", "DDD")]
    st = {
        "AAA": stats("AAA", ref="51.00"),  # gap 2%
        "BBB": stats("BBB", ref="46.00"),  # gap -8% -> |gap| largest
        "CCC": stats("CCC", ref="53.00", pm_vol=1),  # gap 6%, small pm volume
        "DDD": stats("DDD", ref="53.00"),  # gap 6%, larger pm volume -> ahead of CCC
    }
    wl = build_watchlist(D, uni, st, p)
    assert wl.selected == ("BBB", "DDD")
    assert wl.truncated == ("CCC", "AAA")
    assert {e.symbol: e.rank for e in wl.entries} == {"AAA": 4, "BBB": 1, "CCC": 3, "DDD": 2}


def test_stage2_rvol_rank_ties_by_symbol_and_top_n() -> None:
    p = P.model_copy(update={"top_n": 2})
    uni = [member(s) for s in ("AAA", "BBB", "CCC", "DDD")]
    wl = build_watchlist(D, uni, {s: stats(s) for s in ("AAA", "BBB", "CCC", "DDD")}, p)
    hist = {s: [100] * 14 for s in ("AAA", "BBB", "CCC", "DDD")}
    sip = select_stocks_in_play(wl, {"AAA": 300, "BBB": 300, "CCC": 99, "DDD": 500}, hist, p)
    assert sip.selected == ("DDD", "AAA")  # AAA beats BBB on symbol tie-break
    assert sip.truncated == ("BBB",)
    gates = {e.symbol: e.gate.passed for e in sip.entries}
    assert gates == {"AAA": True, "BBB": True, "CCC": False, "DDD": True}


def test_stage2_missing_volume_or_history_fails_gate() -> None:
    wl = build_watchlist(D, [member("AAA"), member("BBB")], {"AAA": stats("AAA"), "BBB": stats("BBB")}, P)
    sip = select_stocks_in_play(wl, {"AAA": 300}, {"AAA": [100] * 3, "BBB": [100] * 14}, P)
    assert sip.selected == ()
    assert all(e.rvol_open is None for e in sip.entries)


def test_deterministic_and_order_independent() -> None:
    uni = [member(s) for s in ("AAA", "BBB", "CCC")]
    st = {s: stats(s, ref=r) for s, r in (("AAA", "51"), ("BBB", "47"), ("CCC", "55"))}
    a = build_watchlist(D, uni, st, P)
    b = build_watchlist(D, list(reversed(uni)), st, P)
    assert a.content_hash() != "" and a.selected == b.selected and a.entries == b.entries


def test_duplicate_universe_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        build_watchlist(D, [member("AAA"), member("AAA")], {}, P)


def test_policy_version_mismatch_rejected() -> None:
    wl = build_watchlist(D, [member("AAA")], {"AAA": stats("AAA")}, P)
    other = P.model_copy(update={"policy_version": "selection-9.0.0"})
    with pytest.raises(ValueError, match="policy versions"):
        select_stocks_in_play(wl, {}, {}, other)


def test_params_validation() -> None:
    with pytest.raises(ValidationError, match="top_n"):
        SelectionParams.model_validate({**P.model_dump(), "top_n": 60})
    raw = P.model_dump()
    del raw["min_atr"]
    with pytest.raises(ValidationError, match="min_atr"):
        SelectionParams.model_validate(raw)
