"""Command-line interface for Bargin."""

import sys
import os
import time
from datetime import datetime, timedelta
from typing import Optional

import click

from bargin import __version__
from bargin.config import Config
from bargin.db import (
    init_db, add_product, get_products, update_product_price,
    create_alert, get_alerts, mark_alert_read, add_feed, get_feeds,
    get_feed_items, get_activity, record_llm_call, get_llm_costs,
    set_glitch_watch, set_ua_override, get_ua_override, log_activity,
    get_connection
)
from bargin.fetcher import Fetcher
from bargin.extractor import extract_price, extract_title, check_in_stock


def get_config() -> Config:
    """Get configuration from environment."""
    return Config.load()


def get_db_path() -> str:
    """Get database path from config."""
    config = get_config()
    # Ensure data directory exists
    db_dir = os.path.dirname(config.database_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    return config.database_path


@click.group()
@click.version_option(version=__version__)
def main():
    """Bargin — UK retailer price watcher with glitch detection."""
    pass


@main.command()
def init():
    """Initialize the database."""
    db_path = get_db_path()
    init_db(db_path)
    click.echo(f"Database initialized at {db_path}")
    
    # Log initialization
    with get_connection(db_path) as conn:
        log_activity(conn, "init", None, None, {"version": __version__})
    
    click.echo("Run 'bargin doctor' to check configuration.")


@main.command()
def doctor():
    """Check configuration status."""
    config = get_config()
    
    click.echo("Configuration check:")
    click.echo("-" * 40)
    
    checks = [
        ("Ntfy topic", config.has_ntfy, "BARGIN_NTFY_TOPIC"),
        ("Anthropic API key", config.has_anthropic, "ANTHROPIC_API_KEY"),
        ("eBay credentials", config.has_ebay, "BARGIN_EBAY_CLIENT_ID/SECRET"),
        ("Reddit credentials", config.has_reddit, "BARGIN_REDDIT_CLIENT_ID/SECRET"),
        ("ZenRows API key", config.has_zenrows, "BARGIN_ZENROWS_API_KEY"),
    ]
    
    all_ok = True
    for name, ok, env_var in checks:
        status = "✓" if ok else "✗"
        click.echo(f"  {status} {name}: {env_var}")
        if not ok:
            all_ok = False
    
    click.echo("-" * 40)
    if all_ok:
        click.echo("All credentials configured.")
    else:
        click.echo("Some credentials missing. App will run with reduced functionality.")
    
    # Check database
    db_path = get_db_path()
    if os.path.exists(db_path):
        click.echo(f"✓ Database exists at {db_path}")
    else:
        click.echo(f"✗ Database not found. Run 'bargin init' first.")


@main.command()
@click.option("--push", is_flag=True, help="Also test push notifications")
def preflight(push):
    """Run a full preflight check with live requests."""
    config = get_config()
    db_path = get_db_path()
    
    click.echo("Running preflight checks...")
    click.echo("-" * 40)
    
    # Test database
    try:
        init_db(db_path)
        click.echo("✓ Database accessible")
    except Exception as e:
        click.echo(f"✗ Database error: {e}")
        return
    
    # Test Claude if configured
    if config.has_anthropic:
        try:
            from bargin.llm import LLMClient
            client = LLMClient(config.anthropic_api_key, db_path)
            
            # Simple test - ask Claude to identify a price in sample text
            test_html = '<span class="price">£29.99</span>'
            result = client.extract_selector_from_html(test_html, "test")
            click.echo(f"✓ Claude API working (model: {client.model})")
        except Exception as e:
            click.echo(f"✗ Claude API error: {e}")
    else:
        click.echo("○ Claude not configured (skipping)")
    
    # Test ntfy if configured and --push flag
    if push:
        if config.has_ntfy:
            try:
                from bargin.notify import send_notification
                send_notification(
                    config.ntfy_topic,
                    "Bargin Preflight",
                    "If you're seeing this, push notifications are working!",
                    priority="urgent"
                )
                click.echo(f"✓ Push notification sent to topic '{config.ntfy_topic}'")
            except Exception as e:
                click.echo(f"✗ Push notification failed: {e}")
        else:
            click.echo("○ Ntfy not configured (skipping push test)")
    
    # Test fetcher
    try:
        fetcher = Fetcher(db_path, config.zenrows_api_key)
        html, status, error = fetcher.fetch("https://example.com")
        if status == 200:
            click.echo("✓ Web fetching works")
        else:
            click.echo(f"⚠ Web fetch returned {status}: {error}")
    except Exception as e:
        click.echo(f"✗ Web fetch error: {e}")
    
    click.echo("-" * 40)
    click.echo("Preflight complete.")


@main.command()
@click.argument("url")
@click.option("--target", type=float, help="Target price for alerts")
@click.option("--title", help="Product title (auto-detected if not provided)")
def add(url: str, target: Optional[float], title: Optional[str]):
    """Add a product URL to track."""
    db_path = get_db_path()
    init_db(db_path)
    
    config = get_config()
    fetcher = Fetcher(db_path, config.zenrows_api_key)
    
    click.echo(f"Fetching {url}...")
    html, status, error = fetcher.fetch(url)
    
    if status != 200:
        click.echo(f"✗ Failed to fetch: {error}")
        sys.exit(1)
    
    # Extract title if not provided
    if not title:
        title = extract_title(html)
    
    # Try to detect retailer from domain
    from urllib.parse import urlparse
    domain = urlparse(url).netloc
    retailer = domain.replace("www.", "")
    
    # Try to find a price selector
    from bargin.extractor import find_price_selectors
    candidates = find_price_selectors(html)
    selector = candidates[0] if candidates else None
    
    # Extract current price
    current_price = None
    if selector:
        current_price = extract_price(html, selector)
    
    product_id = add_product(db_path, title, url, retailer, selector, target)
    
    click.echo(f"✓ Added product #{product_id}: {title[:50]}")
    click.echo(f"  Retailer: {retailer}")
    click.echo(f"  Selector: {selector or 'None'}")
    if current_price:
        click.echo(f"  Current price: £{current_price:.2f}")
    if target:
        click.echo(f"  Alert target: £{target:.2f}")


@main.command()
def list():
    """List all tracked products."""
    db_path = get_db_path()
    products = get_products(db_path)
    
    if not products:
        click.echo("No products being tracked.")
        return
    
    click.echo(f"{'ID':<6} {'Title':<40} {'Retailer':<15} {'Price':<10} {'Status'}")
    click.echo("-" * 80)
    
    for product in products:
        for url_info in product.get("urls", []):
            price = url_info.get("last_price")
            price_str = f"£{price:.2f}" if price else "-"
            status = url_info.get("last_status") or "-"
            title = product["title"][:38]
            click.echo(f"{product['id']:<6} {title:<40} {url_info['retailer']:<15} {price_str:<10} {status}")


@main.command()
@click.option("--force", is_flag=True, help="Force check all products now")
def run(force: bool):
    """Run a sweep checking all tracked products."""
    db_path = get_db_path()
    init_db(db_path)
    config = get_config()
    
    products = get_products(db_path)
    if not products:
        click.echo("No products to check.")
        return
    
    fetcher = Fetcher(db_path, config.zenrows_api_key)
    now = datetime.utcnow()
    
    checked = 0
    for product in products:
        for url_info in product.get("urls", []):
            # Check if it's time to check this URL
            last_checked = url_info.get("last_checked")
            glitch_watch = url_info.get("glitch_watch", False)
            
            if last_checked and not force:
                last_dt = datetime.fromisoformat(last_checked) if isinstance(last_checked, str) else last_checked
                interval = timedelta(minutes=5) if glitch_watch else timedelta(minutes=20)
                if now - last_dt < interval:
                    continue
            
            # Fetch and check
            url = url_info["url"]
            selector = url_info.get("selector")
            target_price = url_info.get("target_price")
            
            click.echo(f"Checking {url[:50]}...")
            html, status, error = fetcher.fetch(url)
            
            if status != 200:
                click.echo(f"  ✗ Fetch failed: {error}")
                # Update error status
                with get_connection(db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE product_urls SET last_status = ?, error = ?, last_checked = ?
                        WHERE id = ?
                    """, ("error", error, now, url_info["id"]))
                    conn.commit()
                continue
            
            # Extract price
            price = None
            if selector:
                price = extract_price(html, selector)
            
            in_stock = check_in_stock(html)
            
            if price is not None:
                # Record the price
                update_product_price(db_path, url_info["id"], price, in_stock)
                checked += 1
                
                click.echo(f"  ✓ Price: £{price:.2f} (in stock: {in_stock})")
                
                # Check for alert conditions
                should_alert = False
                alert_type = "price_update"
                alert_title = f"Price update for {product['title'][:30]}"
                
                # Glitch detection - significant price drop
                old_price = url_info.get("last_price")
                if old_price and price < old_price * 0.7:  # 30%+ drop
                    should_alert = True
                    alert_type = "glitch_suspected"
                    alert_title = f"🚨 Possible glitch: {product['title'][:30]}"
                
                # Target price reached
                if target_price and price <= target_price:
                    should_alert = True
                    alert_type = "target_reached"
                    alert_title = f"✓ Target price: {product['title'][:30]}"
                
                if should_alert:
                    create_alert(
                        db_path, url_info["id"], alert_type, alert_title,
                        f"Price dropped to £{price:.2f} (was £{old_price:.2f})" if old_price else f"Price is £{price:.2f}",
                        price, old_price
                    )
                    
                    # Send push notification
                    if config.has_ntfy:
                        from bargin.notify import send_notification
                        send_notification(
                            config.ntfy_topic,
                            alert_title,
                            f"£{price:.2f} at {url_info['retailer']}",
                            priority="urgent" if alert_type == "glitch_suspected" else "default"
                        )
            else:
                click.echo(f"  ⚠ Could not extract price")
                # Try selector repair if Claude is available
                if config.has_anthropic:
                    from bargin.llm import LLMClient
                    client = LLMClient(config.anthropic_api_key, db_path)
                    new_selector = client.extract_selector_from_html(html, url)
                    if new_selector:
                        click.echo(f"  → New selector found: {new_selector}")
                        with get_connection(db_path) as conn:
                            cursor = conn.cursor()
                            cursor.execute("""
                                UPDATE product_urls SET selector = ? WHERE id = ?
                            """, (new_selector, url_info["id"]))
                            conn.commit()
    
    click.echo(f"\nChecked {checked} products.")


@main.command()
def watch():
    """Run continuous monitoring in the foreground."""
    click.echo("Starting continuous monitoring (Ctrl+C to stop)...")
    click.echo("Checking every 5 minutes.")
    
    while True:
        try:
            # Use ctx to invoke run command
            ctx = click.Context(run)
            ctx.invoke(run, force=False)
            time.sleep(300)  # 5 minutes
        except KeyboardInterrupt:
            click.echo("\nStopping.")
            break


@main.command()
def serve():
    """Start the web UI server."""
    from bargin.web.server import create_app
    
    config = get_config()
    app = create_app(get_db_path, config)
    
    click.echo(f"Starting Bargin web UI at http://{config.host}:{config.port}")
    click.echo("Press Ctrl+C to stop.")
    
    app.run(host=config.host, port=config.port, debug=False)


@main.command()
@click.argument("product_id", type=int)
@click.option("--days", type=int, default=30, help="Number of days of history")
def history(product_id: int, days: int):
    """Show price history for a product."""
    db_path = get_db_path()
    
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        since = datetime.utcnow() - timedelta(days=days)
        
        cursor.execute("""
            SELECT pu.url, pu.retailer, ph.price, ph.recorded_at
            FROM price_history ph
            JOIN product_urls pu ON ph.product_url_id = pu.id
            WHERE pu.product_id = ? AND ph.recorded_at >= ?
            ORDER BY ph.recorded_at DESC
        """, (product_id, since))
        
        rows = cursor.fetchall()
    
    if not rows:
        click.echo(f"No price history found for product {product_id}.")
        return
    
    click.echo(f"Price history for product {product_id} (last {days} days):")
    click.echo("-" * 60)
    
    for row in rows:
        date = row["recorded_at"][:10] if row["recorded_at"] else "?"
        click.echo(f"{date} | {row['retailer']:<15} | £{row['price']:.2f}")


@main.command()
def notify_test():
    """Send a test push notification."""
    config = get_config()
    
    if not config.has_ntfy:
        click.echo("✗ Ntfy topic not configured. Set BARGIN_NTFY_TOPIC in .env")
        sys.exit(1)
    
    from bargin.notify import send_notification
    send_notification(
        config.ntfy_topic,
        "Bargin Test",
        "This is a test notification. If you're seeing this, it works!",
        priority="default"
    )
    click.echo(f"✓ Test notification sent to topic '{config.ntfy_topic}'")


@main.command()
@click.option("--triage", is_flag=True, help="Use Claude to triage feed items")
def social(triage: bool):
    """Poll deal feeds now."""
    db_path = get_db_path()
    init_db(db_path)
    config = get_config()
    
    feeds = get_feeds(db_path)
    if not feeds:
        click.echo("No feeds configured. Use 'bargin social-add <url>' to add one.")
        return
    
    import feedparser
    
    for feed in feeds:
        click.echo(f"Polling {feed['name'] or feed['url']}...")
        
        try:
            parsed = feedparser.parse(feed["url"])
            
            for entry in parsed.entries[:10]:  # Limit to 10 items per feed
                item_id = entry.get("id", entry.get("link", entry.title)[:200])
                
                item_id = add_feed_item(
                    db_path, feed["id"], item_id,
                    entry.title,
                    entry.get("link"),
                    entry.get("summary", entry.get("description", ""))[:1000]
                )
                
                click.echo(f"  + {entry.title[:50]}")
                
                # Triage with Claude if enabled
                if triage and config.has_anthropic:
                    from bargin.llm import LLMClient
                    client = LLMClient(config.anthropic_api_key, db_path)
                    
                    result = client.triage_lead(entry.title, entry.get("summary", ""))
                    if result.get("is_lead"):
                        click.echo(f"    → Lead detected! Score: {result.get('score', 0)}")
                        
                        # Create alert
                        create_alert(
                            db_path, feed["id"], "feed_lead",
                            f"Deal found: {entry.title[:50]}",
                            result.get("reason", ""),
                            priority="high"
                        )
        except Exception as e:
            click.echo(f"  ✗ Error: {e}")
    
    click.echo("Feed polling complete.")


@main.command()
@click.argument("url")
def social_add(url: str):
    """Add a deal feed."""
    db_path = get_db_path()
    init_db(db_path)
    
    # Normalize Reddit URLs
    if url.startswith("r/"):
        url = f"https://www.reddit.com/r/{url[2:]}/.rss"
    elif not url.startswith("http"):
        url = f"https://{url}"
    
    feed_id = add_feed(db_path, url, url)
    click.echo(f"✓ Added feed: {url}")


@main.command()
@click.argument("query")
def search(query: str):
    """Search for products across retailers."""
    db_path = get_db_path()
    config = get_config()
    
    click.echo(f"Searching for: {query}")
    click.echo("-" * 60)
    
    # Search eBay if configured
    if config.has_ebay:
        try:
            from bargin.search import ebay_search
            results = ebay_search(query, config.ebay_client_id, config.ebay_client_secret)
            for item in results[:5]:
                click.echo(f"eBay: {item['title'][:50]} - £{item['price']}")
                click.echo(f"     {item['link']}")
        except Exception as e:
            click.echo(f"eBay search error: {e}")
    
    # TODO: Add retailer-specific searches from registry
    
    click.echo("Search complete.")


@main.command()
@click.argument("retailer")
@click.argument("query")
def search_test(retailer: str, query: str):
    """Test a retailer's search block."""
    click.echo(f"Testing search for {retailer}: {query}")
    # TODO: Implement search testing
    click.echo("Not yet implemented.")


@main.command()
@click.argument("domain")
@click.argument("mode", type=click.Choice(["honest", "browser"]))
def ua(domain: str, mode: str):
    """Set User-Agent mode for a domain."""
    db_path = get_db_path()
    init_db(db_path)
    
    set_ua_override(db_path, domain, mode)
    click.echo(f"✓ Set {domain} to use {mode} User-Agent")


if __name__ == "__main__":
    main()
