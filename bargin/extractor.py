"""Price extraction from HTML using CSS selectors."""

from bs4 import BeautifulSoup
from typing import Optional, Tuple, List, Any
import re
import json


def extract_price(html: str, selector: str) -> Optional[float]:
    """
    Extract a price from HTML using a CSS selector.
    
    Returns the price as a float (e.g., 29.99) or None if not found.
    """
    if selector == "__jsonld_product_offer__":
        return extract_structured_price(html)

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


def _structured_objects(value: Any) -> List[dict]:
    """Flatten common JSON-LD graph/list shapes."""
    if isinstance(value, list):
        result = []
        for item in value:
            result.extend(_structured_objects(item))
        return result
    if isinstance(value, dict):
        result = [value]
        if isinstance(value.get("@graph"), list):
            result.extend(_structured_objects(value["@graph"]))
        return result
    return []


def _offer_price(obj: dict) -> Optional[float]:
    offers = obj.get("offers")
    offers = offers if isinstance(offers, list) else [offers]
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        price = offer.get("price") or offer.get("lowPrice")
        if price is not None:
            try:
                return float(str(price).replace(",", ""))
            except ValueError:
                continue
    return None


def extract_structured_price(html: str) -> Optional[float]:
    """Extract the price from a single Schema.org Product JSON-LD object."""
    soup = BeautifulSoup(html, 'lxml')
    products = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        for obj in _structured_objects(data):
            types = obj.get("@type", [])
            types = types if isinstance(types, list) else [types]
            if "Product" in types and _offer_price(obj) is not None:
                products.append(obj)

    # A list/category page can contain many products and has no single price.
    if len(products) != 1:
        return None
    return _offer_price(products[0])


def is_collection_page(html: str) -> bool:
    """Return whether structured data identifies a multi-product page."""
    soup = BeautifulSoup(html, 'lxml')
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        for obj in _structured_objects(data):
            types = obj.get("@type", [])
            types = types if isinstance(types, list) else [types]
            if "CollectionPage" in types or "ItemList" in types:
                return True
    return False


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

    if extract_structured_price(html) is not None:
        candidates.append("__jsonld_product_offer__")
    elif is_collection_page(html):
        return []
    
    # Look for elements with price-like content
    for el in soup.find_all(['span', 'p', 'strong']):
        text = el.get_text(strip=True)
        if _parse_price(text) is not None and len(text) <= 80:
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
    
    # Prefer explicit price classes over generic selectors.
    candidates.sort(key=lambda selector: (
        0 if selector == "__jsonld_product_offer__" else 1,
        0 if "price" in selector.lower() else 1,
        len(selector),
    ))
    return list(dict.fromkeys(candidates))[:10]


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
