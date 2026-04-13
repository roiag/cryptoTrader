from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # Bybit
    BYBIT_API_KEY: str = ""
    BYBIT_SECRET: str = ""
    PAPER_TRADING: bool = True

    # Claude
    ANTHROPIC_API_KEY: str = ""

    # Telegram
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # News (optional)
    CRYPTOPANIC_TOKEN: str = ""

    # Trading
    TIMEFRAME: str = "15m"
    CANDLES_LIMIT: int = 200
    RUN_INTERVAL_MINUTES: int = 15

    # Risk
    MAX_RISK_PER_TRADE: float = Field(default=0.01, ge=0.001, le=0.05)
    MAX_DAILY_LOSS: float = Field(default=0.05, ge=0.01, le=0.20)
    MAX_OPEN_POSITIONS: int = Field(default=3, ge=1, le=10)

    # Trailing Stop
    TRAILING_STOP_ENABLED: bool = False
    TRAILING_STOP_PCT: float = Field(default=0.008, ge=0.001, le=0.05)  # 0.8% מהשיא

    # EOD (End of Day) - ברירת מחדל כבוי, קריפטו פתוח 24/7
    EOD_CLOSE_ENABLED: bool = False
    EOD_CLOSE_HOUR: int = Field(default=23, ge=0, le=23)
    EOD_CLOSE_MINUTE: int = Field(default=30, ge=0, le=59)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
