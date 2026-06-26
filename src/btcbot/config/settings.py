"""Application settings & secrets loader.

Memuat seluruh environment variable yang didefinisikan di
``docs/11-CONFIG_AND_SECRETS.md`` menggunakan ``pydantic-settings``.

Aturan penting (lihat AGENTS.md & docs/11):
- Tidak ada secret di source/Git; semua via environment / secret manager.
- MODE default ``readonly``.
- Mode ``live`` membutuhkan ``LIVE_CONFIRMED=yes`` (gerbang ganda).
- Semua harga/uang memakai :class:`decimal.Decimal` (JANGAN ``float``).
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Mode(StrEnum):
    """Mode operasi global bot."""

    READONLY = "readonly"
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class Settings(BaseSettings):
    """Konfigurasi tervalidasi untuk btcbot.

    Nilai dibaca dari environment variable (atau file ``.env`` di dev).
    Nama field mengikuti env var di ``docs/11`` (case-insensitive).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- mode ---
    mode: Mode = Mode.READONLY
    live_confirmed: str = "no"

    # --- wallet / chain (SECRET) ---
    polygon_rpc_url: str = ""
    polygon_rpc_fallbacks: str = ""  # comma-separated; boleh kosong
    polygon_rpc_timeout_seconds: int = 10
    wallet_private_key: str = ""
    clob_api_key: str = ""
    clob_api_secret: str = ""
    clob_api_passphrase: str = ""

    # --- endpoints ---
    gamma_base_url: str = ""
    clob_rest_url: str = ""
    clob_wss_url: str = ""
    chainlink_btcusd_source: str = ""
    chainlink_feed_type: str = "data_feed"
    chainlink_max_staleness_sec: int = 120
    ws_app_ping_seconds: int = 10
    ws_stale_seconds: int = 30
    resolve_poll_seconds: int = 30

    # --- strategy params (lihat docs/05) ---
    t_entry_sec: int = 20
    delta_threshold: str = "auto"  # 'auto' = skala dgn volatilitas, atau angka
    min_price: Decimal = Decimal("0.80")
    max_price: Decimal = Decimal("0.99")
    min_edge: Decimal = Decimal("0.01")
    flip_ratio: Decimal = Decimal("0.90")
    hedge_fraction: Decimal = Decimal("0.5")

    # --- sizing & risk (lihat docs/06) ---
    bankroll_floor: Decimal = Decimal("50")
    max_notional_round: Decimal = Decimal("5")
    max_bankroll_fraction: Decimal = Decimal("0.02")
    fill_safety: Decimal = Decimal("0.8")
    max_open_exposure: Decimal = Decimal("10")
    max_daily_loss_pct: Decimal = Decimal("5")
    max_consec_losses: int = 5
    kelly_fraction: Decimal = Decimal("0.25")
    max_orders_per_min: int = 30
    stale_ms: int = 1500

    # --- retensi book_snapshots (Fase 1) ---
    book_persist_mode: str = "changes"  # "changes" (write-on-change+throttle) | "all"
    book_sample_ms: int = 1000  # throttle: maks 1 baris/token per interval ini
    book_finegrain_sec: int = 45  # akhir-window: nonaktifkan throttle (resolusi penuh)

    # --- paper trading ---
    paper_trading: bool = True
    paper_starting_balance: Decimal = Decimal("200")

    # --- infra ---
    db_url: str = "sqlite+aiosqlite:///./btcbot.db"
    log_level: str = "INFO"
    alert_webhook_url: str = ""

    # --- telegram (docs/12) ---
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_allowed_chat_ids: str = ""
    telegram_notify_chat_id: str = ""
    telegram_per_round_notify: bool = True
    telegram_heartbeat_min: int = 30
    telegram_daily_summary: bool = True

    # --- multi-market (docs/14) ---
    markets_config: str = "./markets.yaml"
    max_open_exposure_market: Decimal = Decimal("10")
    max_open_exposure_asset: Decimal = Decimal("15")
    max_correlated_directional: Decimal = Decimal("20")
    max_open_exposure_global: Decimal = Decimal("30")
    chainlink_ethusd_source: str = ""
    chainlink_solusd_source: str = ""

    # ----- validators -----

    @field_validator("live_confirmed", mode="before")
    @classmethod
    def _normalize_live_confirmed(cls, v: object) -> str:
        """Normalisasi ke lowercase string agar perbandingan konsisten."""
        return str(v).strip().lower()

    @field_validator(
        "min_price",
        "max_price",
        "min_edge",
        "flip_ratio",
        "hedge_fraction",
    )
    @classmethod
    def _check_unit_interval(cls, v: Decimal, info: object) -> Decimal:
        """Probabilitas/harga/rasio harus dalam rentang [0, 1]."""
        if not (Decimal("0") <= v <= Decimal("1")):
            field_name = getattr(info, "field_name", "value")
            raise ValueError(f"{field_name} harus di rentang [0, 1], dapat {v}")
        return v

    @field_validator(
        "kelly_fraction",
        "max_bankroll_fraction",
        "fill_safety",
    )
    @classmethod
    def _check_open_unit_interval(cls, v: Decimal, info: object) -> Decimal:
        """Fraksi yang wajib di rentang (0, 1] (tidak boleh 0 atau > 1)."""
        if not (Decimal("0") < v <= Decimal("1")):
            field_name = getattr(info, "field_name", "value")
            raise ValueError(f"{field_name} harus di rentang (0, 1], dapat {v}")
        return v

    @field_validator("max_notional_round", "paper_starting_balance")
    @classmethod
    def _check_positive_decimal(cls, v: Decimal, info: object) -> Decimal:
        """Nilai moneter yang wajib > 0."""
        if v <= Decimal("0"):
            field_name = getattr(info, "field_name", "value")
            raise ValueError(f"{field_name} harus > 0, dapat {v}")
        return v

    @field_validator(
        "bankroll_floor",
        "max_open_exposure",
        "max_daily_loss_pct",
        "max_open_exposure_market",
        "max_open_exposure_asset",
        "max_correlated_directional",
        "max_open_exposure_global",
    )
    @classmethod
    def _check_non_negative(cls, v: Decimal, info: object) -> Decimal:
        """Limit moneter/sizing tidak boleh negatif."""
        if v < Decimal("0"):
            field_name = getattr(info, "field_name", "value")
            raise ValueError(f"{field_name} tidak boleh negatif, dapat {v}")
        return v

    @field_validator(
        "t_entry_sec",
        "max_consec_losses",
        "max_orders_per_min",
        "stale_ms",
        "chainlink_max_staleness_sec",
        "ws_app_ping_seconds",
        "ws_stale_seconds",
        "resolve_poll_seconds",
        "polygon_rpc_timeout_seconds",
        "book_sample_ms",
        "book_finegrain_sec",
    )
    @classmethod
    def _check_positive_int(cls, v: int, info: object) -> int:
        """Parameter waktu/hitungan harus positif."""
        if v <= 0:
            field_name = getattr(info, "field_name", "value")
            raise ValueError(f"{field_name} harus > 0, dapat {v}")
        return v

    @field_validator("book_persist_mode")
    @classmethod
    def _check_persist_mode(cls, v: str) -> str:
        """Mode persistensi book wajib 'changes' atau 'all'."""
        if v not in {"changes", "all"}:
            raise ValueError(f"book_persist_mode harus 'changes'|'all', dapat {v!r}")
        return v

    @model_validator(mode="after")
    def _check_price_band(self) -> Settings:
        """``min_price`` harus <= ``max_price``."""
        if self.min_price > self.max_price:
            raise ValueError(
                f"min_price ({self.min_price}) tidak boleh > max_price ({self.max_price})"
            )
        return self

    @model_validator(mode="after")
    def _check_telegram_consistency(self) -> Settings:
        """Jika Telegram aktif, token & notify chat id wajib ada."""
        if self.telegram_enabled:
            if not self.telegram_bot_token:
                raise ValueError("TELEGRAM_ENABLED=true tapi TELEGRAM_BOT_TOKEN kosong")
            if not self.telegram_notify_chat_id:
                raise ValueError("TELEGRAM_ENABLED=true tapi TELEGRAM_NOTIFY_CHAT_ID kosong")
        return self

    # ----- helpers -----

    def assert_live_ok(self) -> None:
        """Raise jika mode ``live`` tetapi belum dikonfirmasi.

        Gerbang ganda: ``MODE=live`` membutuhkan ``LIVE_CONFIRMED=yes``.

        Raises:
            RuntimeError: bila ``mode == live`` dan ``live_confirmed != "yes"``.
        """
        if self.mode is Mode.LIVE and self.live_confirmed != "yes":
            raise RuntimeError("live mode butuh LIVE_CONFIRMED=yes")

    def is_live(self) -> bool:
        """True jika mode operasi adalah live."""
        return self.mode is Mode.LIVE

    def allowed_chat_ids(self) -> list[int]:
        """Parse whitelist chat id Telegram menjadi list int."""
        raw = self.telegram_allowed_chat_ids.strip()
        if not raw:
            return []
        return [int(part.strip()) for part in raw.split(",") if part.strip()]

    def rpc_endpoints(self) -> list[str]:
        """Daftar RPC terurut: primary + fallbacks (buang kosong/duplikat).

        Urutan dipertahankan; duplikat dibuang (de-dup pertama menang).
        """
        candidates = [self.polygon_rpc_url, *self.polygon_rpc_fallbacks.split(",")]
        seen: set[str] = set()
        out: list[str] = []
        for raw in candidates:
            url = raw.strip()
            if url and url not in seen:
                seen.add(url)
                out.append(url)
        return out


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Kembalikan instance :class:`Settings` ber-cache (singleton).

    Pemanggilan berikutnya mengembalikan instance yang sama. Untuk
    memuat ulang (mis. dalam test), panggil ``get_settings.cache_clear()``.
    """
    return Settings()
