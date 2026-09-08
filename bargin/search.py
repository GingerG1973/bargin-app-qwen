"""eBay search integration."""

import requests
import base64
from typing import List, Dict, Any


def ebay_search(query: str, client_id: str, client_secret: str,
                marketplace: str = "EBAY_GB") -> List[Dict[str, Any]]:
    """
    Search eBay for products.
    
    Uses the eBay Browse API to search for items.
    """
    # First get an access token
    token_url = "https://api.ebay.com/identity/v1/oauth2/token"
    
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": "Basic " + base64.b64encode(
            f"{client_id}:{client_secret}".encode("utf-8")
        ).decode("ascii")
    }
    
    data = {
        "grant_type": "client_credentials",
        "scope": "https://api.ebay.com/oauth/api_scope"
    }
    
    response = requests.post(token_url, headers=headers, data=data, timeout=30)
    response.raise_for_status()
    
    token_data = response.json()
    access_token = token_data.get("access_token")
    
    if not access_token:
        raise Exception("No access token received from eBay")
    
    # Now search
    search_url = "https://api.ebay.com/buy/browse/v1/item_summary/search"
    
    search_headers = {
        "Authorization": f"Bearer {access_token}",
        "X-EBAY-C-MARKETPLACE-ID": marketplace,
        "Accept": "application/json"
    }
    
    params = {
        "q": query,
        "limit": 20
    }
    
    response = requests.get(search_url, headers=search_headers, params=params, timeout=30)
    response.raise_for_status()
    
    results = response.json()
    items = results.get("itemSummaries", [])
    
    formatted = []
    for item in items:
        formatted.append({
            "title": item.get("title", ""),
            "price": float(item.get("price", {}).get("value", 0)) if item.get("price") else 0,
            "currency": item.get("price", {}).get("currency", "GBP"),
            "link": item.get("itemWebUrl", ""),
            "retailer": "eBay",
            "condition": item.get("condition", ""),
            "image": item.get("image", {}).get("imageUrl", "") if item.get("image") else ""
        })
    
    return formatted
