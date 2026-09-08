"""Configuration management for Bargin."""

import os
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv


@dataclass
class Config:
    """Application configuration loaded from environment variables."""
    
    # Ntfy push notifications
    ntfy_topic: Optional[str] = None
    
    # Anthropic Claude
    anthropic_api_key: Optional[str] = None
    llm_daily_budget_usd: float = 1.00
    llm_model: str = "claude-sonnet-4-20250514"
    llm_simple_model: str = "claude-haiku-4-5-20251001"
    llm_enabled: bool = True
    llm_preflight_enabled: bool = False
    llm_pricing_overrides: dict = field(default_factory=dict)
    
    # eBay API
    ebay_client_id: Optional[str] = None
    ebay_client_secret: Optional[str] = None
    
    # Reddit API
    reddit_client_id: Optional[str] = None
    reddit_client_secret: Optional[str] = None
    reddit_user_agent: str = "macos:bargin:0.9 (by /u/yourname)"
    
    # ZenRows paid fetch tier
    zenrows_api_key: Optional[str] = None
    zenrows_daily_max_requests: int = 30
    zenrows_cooldown_hours: int = 24
    
    # Database path
    database_path: str = "data/bargin.db"

    # UK display and marketplace defaults
    locale: str = "en-GB"
    currency: str = "GBP"
    timezone: str = "Europe/London"
    ebay_marketplace: str = "EBAY_GB"
    
    # User-Agent strings
    honest_ua: str = "Bargin/0.9 (+https://github.com/user/bargin)"
    browser_ua: str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    
    # Glitch detection
    glitch_threshold_pct: int = 30
    glitch_watch_interval_min: int = 5
    normal_interval_min: int = 20
    
    # Web server
    host: str = "127.0.0.1"
    port: int = 8500
    
    @classmethod
    def load(cls) -> "Config":
        """Load configuration from environment."""
        load_dotenv()
        project_root = Path(__file__).resolve().parent.parent
        
        def get_env(name: str, default=None):
            return os.environ.get(name, default)
        
        def get_float(name: str, default: float) -> float:
            val = get_env(name)
            if val is None:
                return default
            try:
                return float(val)
            except ValueError:
                return default
        
        def get_int(name: str, default: int) -> int:
            val = get_env(name)
            if val is None:
                return default
            try:
                return int(val)
            except ValueError:
                return default

        def get_bool(name: str, default: bool) -> bool:
            val = get_env(name)
            if val is None:
                return default
            return str(val).lower() in {"1", "true", "yes", "on"}
        
        database_path = get_env("BARGIN_DATABASE_PATH", "data/bargin.db")
        database_path = str(Path(database_path) if os.path.isabs(database_path)
                    else project_root / database_path)

        return cls(
            ntfy_topic=get_env("BARGIN_NTFY_TOPIC"),
            anthropic_api_key=get_env("ANTHROPIC_API_KEY"),
            llm_daily_budget_usd=get_float("BARGIN_LLM_DAILY_BUDGET_USD", 1.00),
            llm_model=get_env("BARGIN_ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
            llm_simple_model=get_env("BARGIN_ANTHROPIC_SIMPLE_MODEL", "claude-haiku-4-5-20251001"),
            llm_enabled=get_bool("BARGIN_LLM_ENABLED", True),
            llm_preflight_enabled=get_bool("BARGIN_LLM_PREFLIGHT_ENABLED", False),
            llm_pricing_overrides={},
            ebay_client_id=get_env("BARGIN_EBAY_CLIENT_ID"),
            ebay_client_secret=get_env("BARGIN_EBAY_CLIENT_SECRET"),
            ebay_marketplace=get_env("BARGIN_EBAY_MARKETPLACE", "EBAY_GB"),
            reddit_client_id=get_env("BARGIN_REDDIT_CLIENT_ID"),
            reddit_client_secret=get_env("BARGIN_REDDIT_CLIENT_SECRET"),
            reddit_user_agent=get_env("BARGIN_REDDIT_USER_AGENT", "macos:bargin:0.9 (by /u/yourname)"),
            zenrows_api_key=get_env("BARGIN_ZENROWS_API_KEY"),
            zenrows_daily_max_requests=get_int("BARGIN_ZENROWS_DAILY_MAX_REQUESTS", 30),
            zenrows_cooldown_hours=get_int("BARGIN_ZENROWS_COOLDOWN_HOURS", 24),
            database_path=database_path,
            locale=get_env("BARGIN_LOCALE", "en-GB"),
            currency=get_env("BARGIN_CURRENCY", "GBP"),
            timezone=get_env("BARGIN_TIMEZONE", "Europe/London"),
            honest_ua=get_env("BARGIN_HONEST_UA", "Bargin/0.9 (+https://github.com/user/bargin)"),
            browser_ua=get_env("BARGIN_BROWSER_UA", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"),
            glitch_threshold_pct=get_int("BARGIN_GLITCH_THRESHOLD_PCT", 30),
            glitch_watch_interval_min=get_int("BARGIN_GLITCH_WATCH_INTERVAL_MIN", 5),
            normal_interval_min=get_int("BARGIN_NORMAL_INTERVAL_MIN", 20),
            host=get_env("BARGIN_HOST", "127.0.0.1"),
            port=get_int("BARGIN_PORT", 8500),
        )
    
    @property
    def has_ntfy(self) -> bool:
        return bool(self.ntfy_topic)
    
    @property
    def has_anthropic(self) -> bool:
        return bool(self.anthropic_api_key)
    
    @property
    def has_ebay(self) -> bool:
        return bool(self.ebay_client_id and self.ebay_client_secret)
    
    @property
    def has_reddit(self) -> bool:
        return bool(self.reddit_client_id and self.reddit_client_secret)
    
    @property
    def has_zenrows(self) -> bool:
        return bool(self.zenrows_api_key and
                re.fullmatch(r"[0-9a-f]{40}", self.zenrows_api_key))
