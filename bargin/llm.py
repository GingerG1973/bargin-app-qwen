"""LLM client for Claude-powered features."""

import json
from typing import Optional, Dict, Any, List
from datetime import datetime

try:
    import anthropic
except ImportError:
    anthropic = None


class LLMClient:
    """Client for Anthropic Claude API."""
    
    MODEL = "claude-sonnet-4-20250514"
    
    def __init__(self, api_key: str, db_path: Optional[str] = None):
        if not anthropic:
            raise ImportError("anthropic package not installed")
        
        self.api_key = api_key
        self.db_path = db_path
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = self.MODEL
    
    def _record_call(
        self,
        capability: str,
        response: Any,
        request_data: Optional[Dict] = None
    ):
        """Record LLM call for cost tracking."""
        if not self.db_path:
            return
        
        try:
            from bargin.db import record_llm_call
            
            input_tokens = getattr(response, 'usage', None)
            output_tokens = getattr(response, 'usage', None)
            cost_usd = 0.0
            
            # Calculate approximate cost (Sonnet pricing)
            if input_tokens and hasattr(input_tokens, 'input_tokens'):
                input_tokens = input_tokens.input_tokens
                output_tokens = input_tokens.output_tokens if hasattr(input_tokens, 'output_tokens') else 0
                # Approximate: $3/1M input, $15/1M output
                cost_usd = (input_tokens * 3 + output_tokens * 15) / 1_000_000
            
            record_llm_call(
                self.db_path,
                capability=capability,
                model=self.model,
                input_tokens=input_tokens or 0,
                output_tokens=output_tokens or 0,
                cost_usd=cost_usd,
                request_data=request_data,
                response_data={"text": response.text[:2000] if hasattr(response, 'text') else str(response)[:2000]}
            )
        except Exception:
            pass  # Don't fail on logging
    
    def extract_selector_from_html(self, html: str, url: str) -> Optional[str]:
        """
        Use Claude to find a CSS selector for the price in HTML.
        
        This is the selector repair capability - when our existing selector
        stops working, Claude analyzes the page structure to find a new one.
        """
        prompt = f"""Analyze this HTML and find the CSS selector that would select the product price.
Look for patterns like £XX.XX or currency symbols with numbers.

Return ONLY the CSS selector, nothing else. If you can't find a price, return "NONE".

HTML snippet:
{html[:3000]}

CSS selector:"""
        
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}]
            )
            
            self._record_call("selector_repair", response, {"url": url})
            
            selector = response.content[0].text.strip() if hasattr(response, 'content') else response.text.strip()
            
            if selector.upper() == "NONE":
                return None
            
            return selector
        except Exception as e:
            return None
    
    def triage_lead(self, title: str, summary: str) -> Dict[str, Any]:
        """
        Triage a deal feed post to determine if it's worth tracking.
        
        Returns a dict with:
        - is_lead: bool - whether this is a worthwhile deal
        - score: float - 0-1 confidence score
        - reason: str - why it was classified this way
        """
        prompt = f"""Analyze this deal post and determine if it's worth tracking.
A good lead has:
- A clear product name
- A specific price mentioned
- Signs it's genuinely discounted or a glitch

Respond in JSON format:
{{"is_lead": true/false, "score": 0.0-1.0, "reason": "brief explanation"}}

Title: {title}
Summary: {summary[:500]}

Analysis:"""
        
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}]
            )
            
            self._record_call("triage", response, {"title": title})
            
            text = response.content[0].text if hasattr(response, 'content') else response.text
            
            # Parse JSON from response
            import re
            json_match = re.search(r'\{[^}]+\}', text)
            if json_match:
                result = json.loads(json_match.group())
                return {
                    "is_lead": result.get("is_lead", False),
                    "score": result.get("score", 0.5),
                    "reason": result.get("reason", "")
                }
            
            return {"is_lead": False, "score": 0.5, "reason": "Could not parse response"}
        except Exception as e:
            return {"is_lead": False, "score": 0.0, "reason": str(e)}
    
    def detect_glitch(self, product_title: str, price: float, 
                      historical_prices: List[float]) -> Dict[str, Any]:
        """
        Determine if a price looks like a genuine glitch vs a real sale.
        
        Glitches often have:
        - Prices far outside normal range (>70% drop)
        - Round numbers that look like data entry errors
        - Prices that don't match typical discount patterns
        """
        if not historical_prices:
            return {"is_glitch": False, "confidence": 0.5, "reason": "No price history"}
        
        avg_price = sum(historical_prices) / len(historical_prices)
        drop_pct = ((avg_price - price) / avg_price) * 100
        
        prompt = f"""Analyze if this price looks like a data entry glitch vs a real sale.

Product: {product_title}
Current price: £{price:.2f}
Historical prices: {[f"£{p:.2f}" for p in historical_prices[-5:]]}
Price drop: {drop_pct:.0f}%

Respond in JSON:
{{"is_glitch": true/false, "confidence": 0.0-1.0, "reason": "explanation"}}

Analysis:"""
        
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}]
            )
            
            self._record_call("glitch_detection", response, {
                "product": product_title,
                "price": price,
                "drop_pct": drop_pct
            })
            
            text = response.content[0].text if hasattr(response, 'content') else response.text
            
            import re
            json_match = re.search(r'\{[^}]+\}', text)
            if json_match:
                result = json.loads(json_match.group())
                return {
                    "is_glitch": result.get("is_glitch", False),
                    "confidence": result.get("confidence", 0.5),
                    "reason": result.get("reason", "")
                }
            
            # Fallback heuristic
            is_glitch = drop_pct > 80
            return {
                "is_glitch": is_glitch,
                "confidence": 0.7 if is_glitch else 0.3,
                "reason": f"Heuristic: {drop_pct:.0f}% drop from average"
            }
        except Exception as e:
            return {"is_glitch": False, "confidence": 0.0, "reason": str(e)}
    
    def normalize_product_title(self, title: str) -> str:
        """Clean up and normalize a product title for matching."""
        prompt = f"""Normalize this product title by removing marketing fluff.
Keep only the essential product information (brand, model, key specs).
Remove words like "New", "Sale", "Best Price", etc.

Title: {title}

Normalized:"""
        
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}]
            )
            
            self._record_call("normalize_title", response, {"title": title})
            
            return response.content[0].text.strip() if hasattr(response, 'content') else response.text.strip()
        except Exception:
            return title
    
    def extract_product_info(self, html: str) -> Dict[str, Any]:
        """Extract structured product info from HTML."""
        prompt = f"""Extract product information from this HTML.
Return JSON with: title, brand (if found), price (if found as number only), currency.

HTML: {html[:4000]}

JSON:"""
        
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}]
            )
            
            self._record_call("extract_info", response)
            
            text = response.content[0].text if hasattr(response, 'content') else response.text
            
            import re
            json_match = re.search(r'\{[^}]+\}', text)
            if json_match:
                return json.loads(json_match.group())
            
            return {}
        except Exception:
            return {}
    
    def compare_products(self, title1: str, title2: str) -> Dict[str, Any]:
        """Determine if two product titles refer to the same item."""
        prompt = f"""Compare these two product titles and determine if they're the same product.

Product A: {title1[:200]}
Product B: {title2[:200]}

Respond in JSON:
{{"same_product": true/false, "confidence": 0.0-1.0, "reason": "explanation"}}

Analysis:"""
        
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}]
            )
            
            self._record_call("compare_products", response, {
                "title1": title1[:200],
                "title2": title2[:200]
            })
            
            text = response.content[0].text if hasattr(response, 'content') else response.text
            
            import re
            json_match = re.search(r'\{[^}]+\}', text)
            if json_match:
                result = json.loads(json_match.group())
                return {
                    "same_product": result.get("same_product", False),
                    "confidence": result.get("confidence", 0.5),
                    "reason": result.get("reason", "")
                }
            
            return {"same_product": False, "confidence": 0.5, "reason": "Could not parse"}
        except Exception as e:
            return {"same_product": False, "confidence": 0.0, "reason": str(e)}
    
    def generate_search_query(self, product_title: str) -> str:
        """Generate an optimal search query for finding the same product elsewhere."""
        prompt = f"""Generate a concise search query to find this product at other retailers.
Include brand and model, exclude retailer-specific terms.
Keep it under 60 characters.

Product: {product_title}

Search query:"""
        
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=60,
                messages=[{"role": "user", "content": prompt}]
            )
            
            self._record_call("search_query", response, {"title": product_title})
            
            return response.content[0].text.strip() if hasattr(response, 'content') else response.text.strip()
        except Exception:
            return product_title[:60]
    
    def analyze_price_trend(self, prices: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze price history to identify trends."""
        if not prices:
            return {"trend": "unknown", "advice": "Not enough data"}
        
        prompt = f"""Analyze this price history and identify the trend.

Prices over time:
{json.dumps(prices[-10:], indent=2)}

Respond in JSON:
{{"trend": "rising/falling/stable/volatile", "avg_price": number, "best_price": number, "advice": "wait/buy now"}}

Analysis:"""
        
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}]
            )
            
            self._record_call("price_trend", response, {"prices": prices[-10:]})
            
            text = response.content[0].text if hasattr(response, 'content') else response.text
            
            import re
            json_match = re.search(r'\{[^}]+\}', text)
            if json_match:
                return json.loads(json_match.group())
            
            return {"trend": "unknown", "advice": "check manually"}
        except Exception as e:
            return {"trend": "unknown", "advice": str(e)}
    
    def write_search_block(self, html: str, retailer: str, query: str) -> Dict[str, Any]:
        """
        Analyze a search results page and generate a search block configuration.
        
        This is used by search-author to create retailer search configurations.
        """
        prompt = f"""Analyze this search results page HTML and create a search block configuration.

Retailer: {retailer}
Search query used: {query}

I need:
1. The URL pattern for searches (with {query} placeholder)
2. CSS selector for product rows
3. CSS selector for product title within a row
4. CSS selector for price within a row
5. CSS selector for product link within a row

HTML: {html[:5000]}

Return JSON:
{{
    "search_url": "URL template with {{query}}",
    "row_selector": ".css-selector",
    "title_selector": ".css-selector",
    "price_selector": ".css-selector",
    "link_selector": ".css-selector"
}}

Configuration:"""
        
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}]
            )
            
            self._record_call("write_search_block", response, {
                "retailer": retailer,
                "query": query
            })
            
            text = response.content[0].text if hasattr(response, 'content') else response.text
            
            import re
            json_match = re.search(r'\{[^}]+\}', text)
            if json_match:
                return json.loads(json_match.group())
            
            return {}
        except Exception as e:
            return {}
