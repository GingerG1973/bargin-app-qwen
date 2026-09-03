"""Push notification service using ntfy."""

import requests
from typing import Optional


def send_notification(
    topic: str,
    title: str,
    message: str,
    priority: str = "default",
    tags: Optional[list] = None
) -> bool:
    """
    Send a push notification via ntfy.sh.
    
    Args:
        topic: The ntfy topic (acts as authentication)
        title: Notification title
        message: Notification body
        priority: One of "min", "low", "default", "high", "urgent"
        tags: Optional list of emoji tags
    
    Returns:
        True if sent successfully
    """
    url = f"https://ntfy.sh/{topic}"
    
    headers = {
        "Title": title,
        "Priority": priority,
    }
    
    if tags:
        headers["Tags"] = ",".join(tags)
    
    try:
        response = requests.post(
            url,
            data=message.encode('utf-8'),
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            return True
        else:
            raise Exception(f"ntfy returned {response.status_code}: {response.text}")
    
    except requests.RequestException as e:
        raise Exception(f"Failed to send notification: {e}")


def send_price_alert(
    topic: str,
    product_title: str,
    price: float,
    retailer: str,
    old_price: Optional[float] = None,
    is_glitch: bool = False
) -> bool:
    """Send a price alert notification."""
    
    if is_glitch:
        title = "🚨 Possible Price Glitch!"
        priority = "urgent"
        tags = ["warning", "money"]
    else:
        title = "Price Alert"
        priority = "high"
        tags = ["shopping", "money"]
    
    message = f"{product_title}\n"
    message += f"£{price:.2f} at {retailer}"
    
    if old_price:
        drop = ((old_price - price) / old_price) * 100
        message += f"\nWas £{old_price:.2f} (-{drop:.0f}%)"
    
    return send_notification(topic, title, message, priority, tags)
