"""Unit tests for btcbot.config.settings."""

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from btcbot.config.settings import Mode, Settings, get_settings


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolasi test dari environment & file .env lokal developer.

    - Hapus env var terkait.
    - Pindah cwd ke tmp_path (tidak ada .env di sana) agar deterministik.
    - Bersihkan cache get_settings().
    """
    env_keys = [
        "MODE",
        "LIVE_CONFIRMED",
        "DB_URL",
        "LOG_LEVEL",
        "MIN_PRICE",
        "MAX_PRICE",
        "MIN_EDGE",
        "KELLY_FRACTION",
        "MAX_NOTIONAL_ROUND",
        "MAX_BANKROLL_FRACTION",
        "FILL_SAFETY",
        "PAPER_TRADING",
        "PAPER_STARTING_BALANCE",
        "T_ENTRY_SEC",
        "TELEGRAM_ENABLED",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_NOTIFY_CHAT_ID",
        "TELEGRAM_ALLOWED_CHAT_IDS",
    ]
    for key in env_keys:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()


def _make(**overrides: Any) -> Settings:
    """Buat Settings dengan override eksplisit (init kwargs prioritas tertinggi)."""
    return Settings(**overrides)


class TestDefaults:
    def test_default_mode_is_readonly(self) -> None:
        s = _make()
        assert s.mode is Mode.READONLY
        assert s.is_live() is False

    def test_default_live_confirmed_is_no(self) -> None:
        s = _make()
        assert s.live_confirmed == "no"

    def test_default_db_url(self) -> None:
        s = _make()
        assert s.db_url == "sqlite+aiosqlite:///./btcbot.db"


class TestDecimalParsing:
    def test_prices_are_decimal(self) -> None:
        s = _make()
        assert isinstance(s.min_price, Decimal)
        assert isinstance(s.max_price, Decimal)
        assert s.min_price == Decimal("0.80")
        assert s.max_price == Decimal("0.99")

    def test_decimal_from_env_string_exact(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Decimal harus parse persis dari string (tanpa error float)
        monkeypatch.setenv("MIN_EDGE", "0.01")
        monkeypatch.setenv("KELLY_FRACTION", "0.15")
        s = _make()
        assert s.min_edge == Decimal("0.01")
        assert s.kelly_fraction == Decimal("0.15")
        # bukan float yang lossy
        assert isinstance(s.min_edge, Decimal)

    def test_money_fields_are_decimal(self) -> None:
        s = _make()
        assert isinstance(s.max_notional_round, Decimal)
        assert isinstance(s.bankroll_floor, Decimal)
        assert isinstance(s.max_open_exposure_global, Decimal)


class TestLiveGate:
    def test_live_without_confirmed_raises(self) -> None:
        s = _make(mode="live", live_confirmed="no")
        with pytest.raises(RuntimeError, match="LIVE_CONFIRMED=yes"):
            s.assert_live_ok()

    def test_live_with_confirmed_ok(self) -> None:
        s = _make(mode="live", live_confirmed="yes")
        s.assert_live_ok()  # tidak raise
        assert s.is_live() is True

    def test_live_confirmed_case_insensitive(self) -> None:
        s = _make(mode="live", live_confirmed="YES")
        assert s.live_confirmed == "yes"
        s.assert_live_ok()  # tidak raise

    def test_non_live_mode_never_raises(self) -> None:
        for mode in ("readonly", "backtest", "paper"):
            s = _make(mode=mode, live_confirmed="no")
            s.assert_live_ok()  # tidak raise


class TestValidators:
    def test_price_out_of_band_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            _make(max_price="1.5")

    def test_min_price_gt_max_price_rejected(self) -> None:
        with pytest.raises(ValueError, match="min_price"):
            _make(min_price="0.95", max_price="0.90")

    def test_negative_notional_rejected(self) -> None:
        with pytest.raises(ValueError, match="> 0"):
            _make(max_notional_round="-1")

    def test_non_positive_int_rejected(self) -> None:
        with pytest.raises(ValueError, match="> 0"):
            _make(t_entry_sec="0")

    def test_telegram_enabled_requires_token(self) -> None:
        with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
            _make(telegram_enabled=True, telegram_notify_chat_id="123")

    def test_kelly_fraction_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"\(0, 1\]"):
            _make(kelly_fraction="0")

    def test_kelly_fraction_above_one_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"\(0, 1\]"):
            _make(kelly_fraction="1.5")

    def test_max_bankroll_fraction_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"\(0, 1\]"):
            _make(max_bankroll_fraction="0")

    def test_fill_safety_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"\(0, 1\]"):
            _make(fill_safety="1.2")

    def test_max_notional_round_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match="> 0"):
            _make(max_notional_round="0")

    def test_paper_starting_balance_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match="> 0"):
            _make(paper_starting_balance="0")

    def test_book_persist_mode_invalid_rejected(self) -> None:
        with pytest.raises(ValueError, match="changes"):
            _make(book_persist_mode="weird")

    def test_book_sample_ms_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match="> 0"):
            _make(book_sample_ms="0")


class TestSizingDefaults:
    def test_new_sizing_defaults(self) -> None:
        s = _make()
        assert s.kelly_fraction == Decimal("0.25")
        assert s.max_bankroll_fraction == Decimal("0.02")
        assert s.fill_safety == Decimal("0.8")
        assert s.max_notional_round == Decimal("5")

    def test_paper_defaults(self) -> None:
        s = _make()
        assert s.paper_trading is True
        assert s.paper_starting_balance == Decimal("200")


class TestRpcEndpoints:
    def test_primary_only(self) -> None:
        s = _make(polygon_rpc_url="https://primary")
        assert s.rpc_endpoints() == ["https://primary"]

    def test_primary_plus_fallbacks_order_preserved(self) -> None:
        s = _make(
            polygon_rpc_url="https://primary",
            polygon_rpc_fallbacks="https://a, https://b",
        )
        assert s.rpc_endpoints() == ["https://primary", "https://a", "https://b"]

    def test_dedup_and_empty_dropped(self) -> None:
        s = _make(
            polygon_rpc_url="https://primary",
            polygon_rpc_fallbacks="https://primary, ,https://a,https://a",
        )
        assert s.rpc_endpoints() == ["https://primary", "https://a"]


class TestHelpers:
    def test_allowed_chat_ids_empty(self) -> None:
        s = _make()
        assert s.allowed_chat_ids() == []

    def test_allowed_chat_ids_parsed(self) -> None:
        s = _make(telegram_allowed_chat_ids="111, 222 ,333")
        assert s.allowed_chat_ids() == [111, 222, 333]

    def test_get_settings_is_cached(self) -> None:
        get_settings.cache_clear()
        a = get_settings()
        b = get_settings()
        assert a is b
