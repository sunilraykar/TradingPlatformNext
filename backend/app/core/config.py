from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "TradingPlatformNext"
    app_env: str = "development"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    log_level: str = "INFO"

    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_database: str = "trading_platform_v2"
    mysql_user: str = "trading_app"
    mysql_password: str = ""
    mysql_pool_size: int = 5

    kite_api_key: str = ""
    kite_api_secret: str = ""
    kite_access_token: str = ""
    kite_redirect_url: str = "http://127.0.0.1:8000/api/v1/kite/callback"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def mysql_url(self) -> str:
        from urllib.parse import quote_plus
        password = quote_plus(self.mysql_password)
        return (
            f"mysql+pymysql://{self.mysql_user}:{password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
            "?charset=utf8mb4"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
