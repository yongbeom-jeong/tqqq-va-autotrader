from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dotenv import load_dotenv


class ConfigurationError(ValueError):
    """Raised when required runtime configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    account_seq: str | None
    symbol: str = "TQQQ"
    reserve_symbol: str = "SGOV"
    va_path_file: Path = Path("data/va_path.csv")
    dry_run: bool = True
    base_url: str = "https://openapi.tossinvest.com"
    timeout_seconds: float = 10.0
    max_retries: int = 2
    max_order_amount_usd: Decimal = Decimal("5000")
    order_fill_timeout_seconds: float = 60.0
    order_poll_interval_seconds: float = 2.0
    execution_state_file: Path = Path("data/execution_state.json")

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        client_id = os.getenv("TOSS_CLIENT_ID", "").strip()
        client_secret = os.getenv("TOSS_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            raise ConfigurationError("TOSS_CLIENT_ID and TOSS_CLIENT_SECRET must be set in .env or the environment.")
        symbol = os.getenv("SYMBOL", "TQQQ").strip().upper()
        if symbol != "TQQQ":
            raise ConfigurationError("Only SYMBOL=TQQQ is allowed in this phase.")
        reserve_symbol = os.getenv("RESERVE_SYMBOL", "SGOV").strip().upper()
        if reserve_symbol != "SGOV":
            raise ConfigurationError("Only RESERVE_SYMBOL=SGOV is allowed in this phase.")
        dry_run_text = os.getenv("DRY_RUN", "true").strip().lower()
        if dry_run_text not in {"true", "false", "1", "0", "yes", "no"}:
            raise ConfigurationError("DRY_RUN must be true or false.")
        try:
            timeout = float(os.getenv("TOSS_TIMEOUT_SECONDS", "10"))
            retries = int(os.getenv("TOSS_MAX_RETRIES", "2"))
            fill_timeout = float(os.getenv("ORDER_FILL_TIMEOUT_SECONDS", "60"))
            poll_interval = float(os.getenv("ORDER_POLL_INTERVAL_SECONDS", "2"))
            max_order_amount = Decimal(os.getenv("MAX_ORDER_AMOUNT_USD", "5000"))
        except (ValueError, InvalidOperation) as exc:
            raise ConfigurationError("Timeout and retry settings must be numeric.") from exc
        if timeout <= 0 or retries < 0 or fill_timeout <= 0 or poll_interval <= 0 or max_order_amount <= 0:
            raise ConfigurationError("Timeouts and max order amount must be positive; retries cannot be negative.")
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            account_seq=os.getenv("TOSS_ACCOUNT_SEQ", "").strip() or None,
            symbol=symbol,
            reserve_symbol=reserve_symbol,
            va_path_file=Path(os.getenv("VA_PATH_FILE", "data/va_path.csv")),
            dry_run=dry_run_text in {"true", "1", "yes"},
            base_url=os.getenv("TOSS_BASE_URL", "https://openapi.tossinvest.com").rstrip("/"),
            timeout_seconds=timeout,
            max_retries=retries,
            max_order_amount_usd=max_order_amount,
            order_fill_timeout_seconds=fill_timeout,
            order_poll_interval_seconds=poll_interval,
            execution_state_file=Path(os.getenv("EXECUTION_STATE_FILE", "data/execution_state.json")),
        )
