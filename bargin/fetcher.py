"""Web page fetching with robots.txt compliance and bot detection."""

import requests
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
from typing import Optional, Tuple, Dict
import time
import re


class Fetcher:
    """HTTP fetcher with robots.txt compliance and retry logic."""
    
    def __init__(self, db_path: Optional[str] = None, zenrows_key: Optional[str] = None,
                 zenrows_daily_limit: int = 30, zenrows_cooldown_hours: int = 24,
                 honest_ua: str = "Bargin/0.9 (+https://github.com/user/bargin)",
                 browser_ua: str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"):
        self.db_path = db_path
        self.zenrows_key = zenrows_key
        self.zenrows_daily_limit = zenrows_daily_limit
        self.zenrows_cooldown_hours = zenrows_cooldown_hours
        self.honest_ua = honest_ua
        self.browser_ua = browser_ua
        self._rp_cache: Dict[str, Tuple[RobotFileParser, float]] = {}
    
    def _get_domain(self, url: str) -> str:
        """Extract domain from URL."""
        parsed = urlparse(url)
        return parsed.netloc
    
    def _get_robots_parser(self, domain: str) -> RobotFileParser:
        """Get or cached robots.txt parser for a domain."""
        # Check cache (valid for 1 hour)
        if domain in self._rp_cache:
            rp, fetched_at = self._rp_cache[domain]
            if time.time() - fetched_at < 3600:
                return rp
        
        # Fetch robots.txt
        rp = RobotFileParser()
        robots_url = f"https://{domain}/robots.txt"
        try:
            response = requests.get(robots_url, timeout=5, headers={"User-Agent": self.honest_ua})
            if response.status_code == 200:
                rp.parse(response.text.splitlines())
        except Exception:
            pass  # If we can't fetch robots.txt, assume allowed
        
        self._rp_cache[domain] = (rp, time.time())
        return rp
    
    def check_robots(self, url: str) -> bool:
        """Check if robots.txt allows fetching this URL. Returns True if allowed."""
        domain = self._get_domain(url)
        rp = self._get_robots_parser(domain)
        
        if not rp.mtime():
            return True  # No robots.txt found, assume allowed
        
        return rp.can_fetch(self.HONEST_UA, url)
    
    def _get_ua_for_domain(self, domain: str) -> str:
        """Get appropriate User-Agent for domain."""
        if self.db_path:
            from bargin.db import get_ua_override
            mode = get_ua_override(self.db_path, domain)
            if mode == "browser":
                return self.browser_ua
        return self.honest_ua
    
    def fetch(self, url: str, use_zenrows: bool = False) -> Tuple[Optional[str], int, str]:
        """
        Fetch a URL. Returns (html_content, status_code, error_message).
        
        If use_zenrows is True and zenrows_key is set, uses ZenRows for fetching.
        """
        domain = self._get_domain(url)
        
        # Check robots.txt first (only for honest UA)
        if not self.check_robots(url):
            return None, 0, "robots.txt disallows"
        
        ua = self._get_ua_for_domain(domain)
        headers = {"User-Agent": ua}
        
        paid_attempted = False

        def fetch_zenrows():
            nonlocal paid_attempted
            if paid_attempted:
                return None, 0, "ZenRows already attempted for this fetch"
            paid_attempted = True
            if not self.zenrows_key:
                return None, 0, "ZenRows is not configured"
            if not re.fullmatch(r"[0-9a-f]{40}", self.zenrows_key):
                return None, 401, "ZenRows API key has an invalid format"
            usage_id = None
            if self.db_path:
                from bargin.db import reserve_zenrows_request
                usage_id, reservation = reserve_zenrows_request(
                    self.db_path, url, self.zenrows_daily_limit,
                    self.zenrows_cooldown_hours,
                )
                if usage_id is None:
                    return None, 429, reservation
            try:
                response = requests.get(
                    "https://api.zenrows.com/v1",
                    params={
                        "url": url,
                        "apikey": self.zenrows_key,
                        "js_render": "true",
                        "premium_proxy": "true",
                    },
                    timeout=30,
                )
                if response.status_code == 200:
                    if self.db_path and usage_id:
                        from bargin.db import finish_zenrows_request
                        finish_zenrows_request(self.db_path, usage_id, True, 200, "success")
                    return response.text, 200, ""
                reason = f"ZenRows HTTP {response.status_code}"
                if self.db_path and usage_id:
                    from bargin.db import finish_zenrows_request
                    finish_zenrows_request(self.db_path, usage_id, False, response.status_code, reason)
                return None, response.status_code, reason
            except requests.RequestException as exc:
                reason = f"ZenRows request failed: {exc}"
                if self.db_path and usage_id:
                    from bargin.db import finish_zenrows_request
                    finish_zenrows_request(self.db_path, usage_id, False, 0, reason)
                return None, 0, reason
        
        # Regular fetch
        try:
            response = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
            
            if response.status_code == 200:
                return response.text, 200, ""
            
            # 403 might mean bot detection - try browser UA once
            if response.status_code == 403 and ua == self.honest_ua:
                headers["User-Agent"] = self.browser_ua
                response = requests.get(url, headers=headers, timeout=30)
                if response.status_code == 200:
                    # Remember to use browser UA for this domain
                    if self.db_path:
                        from bargin.db import set_ua_override
                        set_ua_override(self.db_path, domain, "browser")
                    return response.text, 200, ""
            
            if response.status_code == 403 and self.zenrows_key:
                zenrows_html, zenrows_status, zenrows_error = fetch_zenrows()
                if zenrows_html is not None:
                    return zenrows_html, zenrows_status, zenrows_error
                return None, response.status_code, (
                    f"HTTP 403 (site blocked automated fetch); {zenrows_error}"
                )

            return None, response.status_code, f"HTTP {response.status_code}"
            
        except requests.Timeout:
            return None, 0, "timeout"
        except requests.RequestException as e:
            return None, 0, str(e)
