"""Price extraction from HTML using CSS selectors."""

from bs4 import BeautifulSoup
from typing import Optional, Tuple, List
import re


def extract_price(html: str, selector: str) -> Optional[float]:
    """
    Extract a price from HTML using a CSS selector.
    
    Returns the price as a float (e.g., 29.99) or None if not found.
    """
    soup = BeautifulSoup(html, 'lxml')
    
    elements = soup.select(selector)
    if not elements:
        return None
    
    for el in elements:
        price = _parse_price(el.get_text())
        if price is not None:
            return price
    
    # Try common price attributes
    for el in elements:
        for attr in ['content', 'data-price', 'value']:
            if el.has_attr(attr):
                price = _parse_price(el[attr])
                if price is not None:
                    return price
    
    return None


def _parse_price(text: str) -> Optional[float]:
    """Parse a price string into a float."""
    if not text:
        return None
    
    # Clean up the text
    text = text.strip()
    
    # Remove currency symbols and common prefixes
    text = re.sub(r'[£€$]', '', text)
    text = re.sub(r'(was|now|only|from)\s*', '', text, flags=re.IGNORECASE)
    
    # Find decimal numbers
    matches = re.findall(r'(\d+)[,.](\d{2})\b', text)
    if matches:
        # Take the first match
        whole, frac = matches[0]
        try:
            return float(f"{whole}.{frac}")
        except ValueError:
            pass
    
    # Try integer prices
    matches = re.findall(r'\b(\d+)\b', text)
    if matches:
        # Filter out likely non-prices (years, quantities)
        for m in matches:
            val = int(m)
            if 1 <= val <= 10000:  # Reasonable price range
                return float(val)
    
    return None


def extract_title(html: str) -> str:
    """Extract product title from HTML."""
    soup = BeautifulSoup(html, 'lxml')
    
    # Try common title selectors
    title_selectors = [
        'h1',
        '.product-title',
        '.product-name',
        '[itemprop="name"]',
        'meta[name="title"][content]',
    ]
    
    for selector in title_selectors:
        if selector.startswith('meta['):
            el = soup.select_one(selector)
            if el and el.has_attr('content'):
                return el['content'].strip()
        else:
            el = soup.select_one(selector)
            if el:
                return el.get_text(strip=True)[:500]
    
    # Fall back to page title
    title_tag = soup.find('title')
    if title_tag:
        return title_tag.get_text(strip=True)[:500]
    
    return "Unknown Product"


def check_in_stock(html: str, in_stock_selector: Optional[str] = None,
                   out_of_stock_selector: Optional[str] = None) -> bool:
    """
    Check if a product is in stock.
    
    Returns True if in stock, False if out of stock.
    """
    soup = BeautifulSoup(html, 'lxml')
    
    # If we have explicit selectors, use them
    if out_of_stock_selector:
        if soup.select_one(out_of_stock_selector):
            return False
    
    if in_stock_selector:
        if soup.select_one(in_stock_selector):
            return True
    
    # Heuristics
    text = soup.get_text().lower()
    
    out_of_stock_phrases = [
        'out of stock',
        'out of stock online',
        'currently unavailable',
        'sold out',
        'temporarily out of stock',
    ]
    
    for phrase in out_of_stock_phrases:
        if phrase in text:
            return False
    
    # Default to in stock if no indicators found
    return True


def find_price_selectors(html: str) -> List[str]:
    """
    Attempt to auto-discover price selectors from HTML.
    
    Returns a list of candidate CSS selectors that might select the price.
    """
    soup = BeautifulSoup(html, 'lxml')
    candidates = []
    
    # Look for elements with price-like content
    for el in soup.find_all(['span', 'div', 'p', 'strong']):
        text = el.get_text(strip=True)
        if _parse_price(text) is not None:
            # Build a selector for this element
            selector = _build_selector(el)
            if selector:
                candidates.append(selector)
    
    # Look for common price class names
    price_classes = [
        '.price', '.product-price', '.sale-price', '.current-price',
        '[class*="price"]', '[itemprop="price"]'
    ]
    
    for selector in price_classes:
        if soup.select_one(selector):
            candidates.append(selector)
    
    return candidates[:10]  # Limit candidates


def _build_selector(el) -> Optional[str]:
    """Build a CSS selector for an element."""
    if el.has_attr('id'):
        return f"#{el['id']}"
    
    classes = el.get('class', [])
    if classes:
        class_str = '.'.join(classes[:2])  # Use up to 2 classes
        return f"{el.name}.{class_str}"
    
    # Walk up to parent
    parent = el.parent
    if parent and parent.name != '[document]':
        if parent.has_attr('class'):
            class_str = '.'.join(parent['class'][:1])
            return f"{parent.name}.{class_str} > {el.name}"
    
    return None
