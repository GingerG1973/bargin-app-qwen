"""Threaded runner module for Bargin sweeps."""

import threading
import time
from datetime import datetime, timedelta
from typing import Optional, Tuple, List, Dict, Any

from bargin.db import (
    init_db, get_products, get_connection, update_product_price,
    create_alert, claim_alert_condition, log_activity, get_feeds,
    add_feed_item, get_feed_item, mark_feed_item_triaged,
)
from bargin.fetcher import Fetcher
from bargin.extractor import (
    extract_price, check_in_stock, extract_structured_price, is_collection_page,
)
from bargin.config import Config


running_sweeps: set[str] = set()
running_sweeps_lock = threading.Lock()


def run_sweep(
    db_path: str,
    config: Config,
    action: str = "products",
    force: bool = False,
) -> Tuple[int, List[str]]:
    """
    Run a sweep checking products or polling feeds.
    
    Returns (checked_count, errors_list).
    """
    sweep_key = f"{action}:{db_path}"
    with running_sweeps_lock:
        if sweep_key in running_sweeps:
            return 0, [f"Sweep already running for {action}"]
        running_sweeps.add(sweep_key)
    
    init_db(db_path)
    errors: List[str] = []
    checked = 0
    now = datetime.utcnow()
    
    try:
        if action == "products":
            products = get_products(db_path)
            if not products:
                return 0, []
            
            fetcher = Fetcher(
                db_path, config.zenrows_api_key,
                config.zenrows_daily_max_requests,
                config.zenrows_cooldown_hours,
            )
            
            for product in products:
                for url_info in product.get("urls", []):
                    last_checked = url_info.get("last_checked")
                    glitch_watch = url_info.get("glitch_watch", False)
                    
                    if last_checked and not force:
                        last_dt = datetime.fromisoformat(last_checked) if isinstance(last_checked, str) else last_checked
                        interval = timedelta(minutes=config.glitch_watch_interval_min) if glitch_watch else timedelta(minutes=config.normal_interval_min)
                        if now - last_dt < interval:
                            continue
                    
                    url = url_info["url"]
                    selector = url_info.get("selector")
                    target_price = url_info.get("target_price")
                    url_id = url_info["id"]
                    
                    try:
                        html, status, error = fetcher.fetch(
                            url, use_zenrows=bool(url_info.get("use_zenrows", False))
                        )
                        
                        if status != 200:
                            errors.append(f"{url}: {error}")
                            with get_connection(db_path) as conn:
                                cursor = conn.cursor()
                                cursor.execute("""
                                    UPDATE product_urls SET last_status = ?, error = ?, last_checked = ?
                                    WHERE id = ?
                                """, ("error", error, now, url_id))
                                conn.commit()
                            continue
                        
                        price = None
                        structured_price = extract_structured_price(html)
                        if structured_price is not None:
                            price = structured_price
                            if selector != "__jsonld_product_offer__":
                                selector = "__jsonld_product_offer__"
                                with get_connection(db_path) as conn:
                                    conn.execute(
                                        "UPDATE product_urls SET selector = ? WHERE id = ?",
                                        (selector, url_id),
                                    )
                                    conn.commit()
                        elif selector and selector not in {"#holder", "#container"}:
                            price = extract_price(html, selector)
                        
                        in_stock = check_in_stock(html)
                        
                        if price is not None:
                            update_product_price(db_path, url_id, price, in_stock)
                            checked += 1
                            
                            should_alert = False
                            alert_type = "price_update"
                            alert_title = f"Price update for {product['title'][:30]}"
                            
                            old_price = url_info.get("last_price")
                            if old_price and price < old_price * (1 - config.glitch_threshold_pct / 100):
                                should_alert = True
                                alert_type = "glitch_suspected"
                                alert_title = f"Possible glitch: {product['title'][:30]}"
                            
                            if target_price and price <= target_price:
                                should_alert = True
                                alert_type = "target_reached"
                                alert_title = f"Target price: {product['title'][:30]}"
                            
                            if not should_alert:
                                claim_alert_condition(db_path, url_id, alert_type, False)
                            elif claim_alert_condition(db_path, url_id, alert_type, True):
                                alert_id = create_alert(
                                    db_path, url_id, alert_type, alert_title,
                                    f"Price dropped to {price:.2f} (was {old_price:.2f})" if old_price else f"Price is {price:.2f}",
                                    price, old_price
                                )
                                
                                if config.has_ntfy:
                                    from bargin.notify import send_notification
                                    delivered = send_notification(
                                        config.ntfy_topic,
                                        alert_title,
                                        f"{price:.2f} at {url_info['retailer']}",
                                        priority="urgent" if alert_type == "glitch_suspected" else "default"
                                    )
                                    if delivered:
                                        from bargin.db import mark_alert_sent
                                        mark_alert_sent(db_path, alert_id)
                        else:
                            if is_collection_page(html):
                                with get_connection(db_path) as conn:
                                    conn.execute("""
                                        UPDATE product_urls
                                        SET last_price = NULL, last_status = 'not_product',
                                            error = 'URL is a collection or listing page'
                                        WHERE id = ?
                                    """, (url_id,))
                                    conn.commit()
                            if config.has_anthropic:
                                from bargin.llm import LLMClient
                                client = LLMClient(config.anthropic_api_key, db_path)
                                new_selector = client.extract_selector_from_html(html, url)
                                if new_selector:
                                    with get_connection(db_path) as conn:
                                        cursor = conn.cursor()
                                        cursor.execute("""
                                            UPDATE product_urls SET selector = ? WHERE id = ?
                                        """, (new_selector, url_id))
                                        conn.commit()
                    except Exception as e:
                        errors.append(f"{url}: {str(e)}")
                        with get_connection(db_path) as conn:
                            cursor = conn.cursor()
                            cursor.execute("""
                                UPDATE product_urls SET last_status = ?, error = ?, last_checked = ?
                                WHERE id = ?
                            """, ("error", str(e), now, url_id))
                            conn.commit()
        
        elif action == "feeds":
            feeds = get_feeds(db_path)
            if not feeds:
                return 0, []
            
            import feedparser
            
            triage_calls = 0
            for feed in feeds:
                try:
                    parsed = feedparser.parse(feed["url"])
                    
                    for entry in parsed.entries[:10]:
                        item_id = entry.get("id", entry.get("link", entry.title)[:200])
                        
                        existing_item = get_feed_item(db_path, feed["id"], item_id)
                        item_db_id = add_feed_item(
                            db_path, feed["id"], item_id,
                            entry.title,
                            entry.get("link"),
                            entry.get("summary", entry.get("description", ""))[:1000]
                        )
                        
                        if (config.has_anthropic and triage_calls < 5
                            and not (existing_item and existing_item.get("triaged"))):
                            from bargin.llm import LLMClient
                            client = LLMClient(config.anthropic_api_key, db_path)
                            
                            result = client.triage_lead(entry.title, entry.get("summary", ""))
                            triage_calls += 1
                            mark_feed_item_triaged(db_path, item_db_id, result)
                            if result.get("is_lead"):
                                with get_connection(db_path) as conn:
                                    log_activity(conn, "feed_lead", "feed_item", item_id, {
                                        "title": entry.title,
                                        "reason": result.get("reason", ""),
                                        "score": result.get("score", 0),
                                    })
                                    conn.commit()
                except Exception as e:
                    errors.append(f"Feed {feed['url']}: {str(e)}")
    
    finally:
        with running_sweeps_lock:
            running_sweeps.discard(sweep_key)
    
    return checked, errors


def start_task(db_path: str, action: str) -> int:
    """Insert a task record and return task_id."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO tasks (action, status, started_at, result_json, error)
            VALUES (?, 'running', ?, '{}', '')
        """, (action, datetime.utcnow()))
        conn.commit()
        return cursor.lastrowid


def finish_task(db_path: str, task_id: int, checked: int, errors: List[str]) -> None:
    """Update task record with results."""
    import json
    with get_connection(db_path) as conn:
        conn.execute("""
            UPDATE tasks SET status = ?, finished_at = ?, result_json = ?, error = ?
            WHERE id = ?
        """, ("completed" if not errors else "completed_with_errors", datetime.utcnow(),
              json.dumps({"checked": checked, "errors": errors}), 
              "\n".join(errors) if errors else None, task_id))
        conn.commit()


def get_task(db_path: str, task_id: int) -> Optional[Dict[str, Any]]:
    """Get task by ID."""
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None