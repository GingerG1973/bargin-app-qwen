"""Configuration management for Bargin."""

import os
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
    
    # eBay API
    ebay_client_id: Optional[str] = None
    ebay_client_secret: Optional[str] = None
    
    # Reddit API
    reddit_client_id: Optional[str] = None
    reddit_client_secret: Optional[str] = None
    reddit_user_agent: str = "macos:bargin:0.9 (by /u/yourname)"
    
    # ZenRows paid fetch tier
    zenrows_api_key: Optional[str] = None
    zenrows_daily_max_requests: int = 200
    
    # Database path
    database_path: str = "data/bargin.db"
    
    # Web server
    host: str = "127.0.0.1"
    port: int = 8787
    
    @classmethod
    def load(cls) -> "Config":
        """Load configuration from environment."""
        load_dotenv()
        
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
        
        return cls(
            ntfy_topic=get_env("BARGIN_NTFY_TOPIC"),
            anthropic_api_key=get_env("ANTHROPIC_API_KEY"),
            llm_daily_budget_usd=get_float("BARGIN_LLM_DAILY_BUDGET_USD", 1.00),
            ebay_client_id=get_env("BARGIN_EBAY_CLIENT_ID"),
            ebay_client_secret=get_env("BARGIN_EBAY_CLIENT_SECRET"),
            reddit_client_id=get_env("BARGIN_REDDIT_CLIENT_ID"),
            reddit_client_secret=get_env("BARGIN_REDDIT_CLIENT_SECRET"),
            reddit_user_agent=get_env("BARGIN_REDDIT_USER_AGENT", "macos:bargin:0.9 (by /u/yourname)"),
            zenrows_api_key=get_env("BARGIN_ZENROWS_API_KEY"),
            zenrows_daily_max_requests=get_int("BARGIN_ZENROWS_DAILY_MAX_REQUESTS", 200),
            database_path=get_env("BARGIN_DATABASE_PATH", "data/bargin.db"),
            host=get_env("BARGIN_HOST", "127.0.0.1"),
            port=get_int("BARGIN_PORT", 8787),
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
        return bool(self.zenrows_api_key)
