"""Web page fetching with robots.txt compliance and bot detection."""

import requests
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
from typing import Optional, Tuple, Dict
import time


class Fetcher:
    """HTTP fetcher with robots.txt compliance and retry logic."""
    
    HONEST_UA = "Bargin/0.9 (UK price watcher; https://github.com/bargin)"
    BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    
    def __init__(self, db_path: Optional[str] = None, zenrows_key: Optional[str] = None):
        self.db_path = db_path
        self.zenrows_key = zenrows_key
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
            response = requests.get(robots_url, timeout=5, headers={"User-Agent": self.HONEST_UA})
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
                return self.BROWSER_UA
        return self.HONEST_UA
    
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
        
        # Try ZenRows if enabled and requested
        if use_zenrows and self.zenrows_key:
            try:
                response = requests.get(
                    "https://api.zenrows.com/v1",
                    params={"url": url, "apikey": self.zenrows_key},
                    timeout=30
                )
                if response.status_code == 200:
                    return response.text, 200, ""
                # Fall through to regular fetch on failure
            except Exception as e:
                pass  # Fall through to regular fetch
        
        # Regular fetch
        try:
            response = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
            
            if response.status_code == 200:
                return response.text, 200, ""
            
            # 403 might mean bot detection - try browser UA once
            if response.status_code == 403 and ua == self.HONEST_UA:
                headers["User-Agent"] = self.BROWSER_UA
                response = requests.get(url, headers=headers, timeout=30)
                if response.status_code == 200:
                    # Remember to use browser UA for this domain
                    if self.db_path:
                        from bargin.db import set_ua_override
                        set_ua_override(self.db_path, domain, "browser")
                    return response.text, 200, ""
            
            return None, response.status_code, f"HTTP {response.status_code}"
            
        except requests.Timeout:
            return None, 0, "timeout"
        except requests.RequestException as e:
            return None, 0, str(e)
