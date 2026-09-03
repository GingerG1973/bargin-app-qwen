"""Database layer for Bargin using SQLite."""

import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
import json


@contextmanager
def get_connection(db_path: str):
    """Get a database connection with row factory."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: str) -> None:
    """Initialize the database schema."""
    with get_connection(db_path) as conn:
        conn.executescript("""
            -- Products table
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            -- Product URLs (one product can have multiple retailer URLs)
            CREATE TABLE IF NOT EXISTS product_urls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                url TEXT NOT NULL UNIQUE,
                retailer TEXT NOT NULL,
                selector TEXT,
                target_price REAL,
                glitch_watch BOOLEAN DEFAULT FALSE,
                last_checked TIMESTAMP,
                last_price REAL,
                last_status TEXT,
                error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
            );
            
            -- Price history
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_url_id INTEGER NOT NULL,
                price REAL NOT NULL,
                currency TEXT DEFAULT 'GBP',
                in_stock BOOLEAN DEFAULT TRUE,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (product_url_id) REFERENCES product_urls(id) ON DELETE CASCADE
            );
            
            -- Alerts
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_url_id INTEGER NOT NULL,
                alert_type TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT,
                price REAL,
                old_price REAL,
                sent_push BOOLEAN DEFAULT FALSE,
                read BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (product_url_id) REFERENCES product_urls(id) ON DELETE CASCADE
            );
            
            -- Deal feeds
            CREATE TABLE IF NOT EXISTS feeds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                name TEXT,
                feed_type TEXT DEFAULT 'rss',
                poll_interval_minutes INTEGER DEFAULT 30,
                last_polled TIMESTAMP,
                enabled BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            -- Feed items (posts from feeds)
            CREATE TABLE IF NOT EXISTS feed_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feed_id INTEGER NOT NULL,
                item_id TEXT NOT NULL,
                title TEXT NOT NULL,
                link TEXT,
                summary TEXT,
                published_at TIMESTAMP,
                triaged BOOLEAN DEFAULT FALSE,
                triage_result TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (feed_id) REFERENCES feeds(id) ON DELETE CASCADE,
                UNIQUE(feed_id, item_id)
            );
            
            -- Activity log
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                entity_type TEXT,
                entity_id INTEGER,
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            -- LLM cost tracking
            CREATE TABLE IF NOT EXISTS llm_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                capability TEXT NOT NULL,
                model TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                cost_usd REAL,
                request_data TEXT,
                response_data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            -- ZenRows usage tracking
            CREATE TABLE IF NOT EXISTS zenrows_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                success BOOLEAN,
                cost_credits INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            -- User agent overrides per domain
            CREATE TABLE IF NOT EXISTS ua_overrides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL UNIQUE,
                ua_mode TEXT NOT NULL DEFAULT 'honest',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            -- Indexes for common queries
            CREATE INDEX IF NOT EXISTS idx_product_urls_product_id ON product_urls(product_id);
            CREATE INDEX IF NOT EXISTS idx_product_urls_url ON product_urls(url);
            CREATE INDEX IF NOT EXISTS idx_price_history_product_url_id ON price_history(product_url_id);
            CREATE INDEX IF NOT EXISTS idx_price_history_recorded_at ON price_history(recorded_at);
            CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON alerts(created_at);
            CREATE INDEX IF NOT EXISTS idx_alerts_read ON alerts(read);
            CREATE INDEX IF NOT EXISTS idx_feed_items_created_at ON feed_items(created_at);
            CREATE INDEX IF NOT EXISTS idx_activity_log_created_at ON activity_log(created_at);
            CREATE INDEX IF NOT EXISTS idx_llm_calls_created_at ON llm_calls(created_at);
        """)
        conn.commit()


def add_product(db_path: str, title: str, url: str, retailer: str, 
                selector: Optional[str] = None, target_price: Optional[float] = None) -> int:
    """Add a new product to track. Returns product ID."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        
        # Check if URL already exists
        cursor.execute("SELECT id, product_id FROM product_urls WHERE url = ?", (url,))
        existing = cursor.fetchone()
        
        if existing:
            return existing["product_id"]
        
        # Create product
        cursor.execute("INSERT INTO products (title) VALUES (?)", (title,))
        product_id = cursor.lastrowid
        
        # Create product URL entry
        cursor.execute("""
            INSERT INTO product_urls (product_id, url, retailer, selector, target_price)
            VALUES (?, ?, ?, ?, ?)
        """, (product_id, url, retailer, selector, target_price))
        
        log_activity(conn, "product_added", "product", product_id, {"url": url, "retailer": retailer})
        conn.commit()
        
        return product_id


def get_products(db_path: str) -> List[Dict[str, Any]]:
    """Get all tracked products with their URLs."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT p.id, p.title, p.created_at,
                   pu.id as url_id, pu.url, pu.retailer, pu.selector,
                   pu.target_price, pu.glitch_watch, pu.last_checked,
                   pu.last_price, pu.last_status, pu.error
            FROM products p
            LEFT JOIN product_urls pu ON p.id = pu.product_id
            ORDER BY p.updated_at DESC
        """)
        
        products = {}
        for row in cursor.fetchall():
            pid = row["id"]
            if pid not in products:
                products[pid] = {
                    "id": pid,
                    "title": row["title"],
                    "created_at": row["created_at"],
                    "urls": []
                }
            
            if row["url_id"]:
                products[pid]["urls"].append({
                    "id": row["url_id"],
                    "url": row["url"],
                    "retailer": row["retailer"],
                    "selector": row["selector"],
                    "target_price": row["target_price"],
                    "glitch_watch": bool(row["glitch_watch"]),
                    "last_checked": row["last_checked"],
                    "last_price": row["last_price"],
                    "last_status": row["last_status"],
                    "error": row["error"]
                })
        
        return list(products.values())


def update_product_price(db_path: str, url_id: int, price: float, 
                         in_stock: bool = True, currency: str = "GBP") -> None:
    """Update the price for a product URL and record history."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        
        # Get current price
        cursor.execute("SELECT last_price FROM product_urls WHERE id = ?", (url_id,))
        row = cursor.fetchone()
        old_price = row["last_price"] if row else None
        
        # Update product URL
        cursor.execute("""
            UPDATE product_urls 
            SET last_price = ?, last_checked = ?, last_status = 'ok', error = NULL
            WHERE id = ?
        """, (price, datetime.utcnow(), url_id))
        
        # Record price history
        cursor.execute("""
            INSERT INTO price_history (product_url_id, price, currency, in_stock)
            VALUES (?, ?, ?, ?)
        """, (url_id, price, currency, in_stock))
        
        # Update product timestamp
        cursor.execute("""
            UPDATE products SET updated_at = ?
            WHERE id = (SELECT product_id FROM product_urls WHERE id = ?)
        """, (datetime.utcnow(), url_id))
        
        log_activity(conn, "price_update", "product_url", url_id, 
                    {"price": price, "old_price": old_price, "in_stock": in_stock})
        conn.commit()


def create_alert(db_path: str, url_id: int, alert_type: str, title: str,
                 message: Optional[str] = None, price: Optional[float] = None,
                 old_price: Optional[float] = None) -> int:
    """Create an alert. Returns alert ID."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO alerts (product_url_id, alert_type, title, message, price, old_price)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (url_id, alert_type, title, message, price, old_price))
        
        alert_id = cursor.lastrowid
        log_activity(conn, "alert_created", "alert", alert_id, 
                    {"type": alert_type, "title": title})
        conn.commit()
        
        return alert_id


def get_alerts(db_path: str, unread_only: bool = False, limit: int = 100) -> List[Dict[str, Any]]:
    """Get alerts, optionally filtered to unread only."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        
        query = """
            SELECT a.*, pu.url, pu.retailer, p.title as product_title
            FROM alerts a
            JOIN product_urls pu ON a.product_url_id = pu.id
            JOIN products p ON pu.product_id = p.id
        """
        
        if unread_only:
            query += " WHERE a.read = FALSE"
        
        query += " ORDER BY a.created_at DESC LIMIT ?"
        
        cursor.execute(query, (limit,))
        
        return [dict(row) for row in cursor.fetchall()]


def mark_alert_read(db_path: str, alert_id: int) -> None:
    """Mark an alert as read."""
    with get_connection(db_path) as conn:
        conn.execute("UPDATE alerts SET read = TRUE WHERE id = ?", (alert_id,))
        conn.commit()


def add_feed(db_path: str, url: str, name: Optional[str] = None,
             feed_type: str = "rss", poll_interval: int = 30) -> int:
    """Add a deal feed. Returns feed ID."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO feeds (url, name, feed_type, poll_interval_minutes)
            VALUES (?, ?, ?, ?)
        """, (url, name, feed_type, poll_interval))
        conn.commit()
        
        cursor.execute("SELECT id FROM feeds WHERE url = ?", (url,))
        row = cursor.fetchone()
        return row["id"] if row else 0


def get_feeds(db_path: str) -> List[Dict[str, Any]]:
    """Get all configured feeds."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM feeds ORDER BY created_at DESC")
        return [dict(row) for row in cursor.fetchall()]


def add_feed_item(db_path: str, feed_id: int, item_id: str, title: str,
                  link: Optional[str] = None, summary: Optional[str] = None,
                  published_at: Optional[datetime] = None) -> int:
    """Add a feed item. Returns item ID or existing ID."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        
        # Check for duplicate
        cursor.execute("""
            SELECT id FROM feed_items WHERE feed_id = ? AND item_id = ?
        """, (feed_id, item_id))
        existing = cursor.fetchone()
        
        if existing:
            return existing["id"]
        
        cursor.execute("""
            INSERT INTO feed_items (feed_id, item_id, title, link, summary, published_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (feed_id, item_id, title, link, summary, published_at))
        
        conn.commit()
        return cursor.lastrowid


def get_feed_items(db_path: str, feed_id: Optional[int] = None, 
                   limit: int = 50) -> List[Dict[str, Any]]:
    """Get feed items, optionally filtered by feed."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        
        if feed_id:
            cursor.execute("""
                SELECT fi.*, f.name as feed_name
                FROM feed_items fi
                JOIN feeds f ON fi.feed_id = f.id
                WHERE fi.feed_id = ?
                ORDER BY fi.created_at DESC
                LIMIT ?
            """, (feed_id, limit))
        else:
            cursor.execute("""
                SELECT fi.*, f.name as feed_name
                FROM feed_items fi
                JOIN feeds f ON fi.feed_id = f.id
                ORDER BY fi.created_at DESC
                LIMIT ?
            """, (limit,))
        
        return [dict(row) for row in cursor.fetchall()]


def log_activity(conn: sqlite3.Connection, event_type: str, 
                 entity_type: Optional[str] = None,
                 entity_id: Optional[int] = None,
                 details: Optional[Dict] = None) -> None:
    """Log an activity event."""
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO activity_log (event_type, entity_type, entity_id, details)
        VALUES (?, ?, ?, ?)
    """, (event_type, entity_type, entity_id, json.dumps(details) if details else None))


def get_activity(db_path: str, limit: int = 200) -> List[Dict[str, Any]]:
    """Get recent activity log entries."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM activity_log
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]


def record_llm_call(db_path: str, capability: str, model: str,
                    input_tokens: int, output_tokens: int, cost_usd: float,
                    request_data: Optional[Dict] = None,
                    response_data: Optional[Dict] = None) -> None:
    """Record an LLM API call for cost tracking."""
    with get_connection(db_path) as conn:
        conn.execute("""
            INSERT INTO llm_calls (capability, model, input_tokens, output_tokens, 
                                   cost_usd, request_data, response_data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (capability, model, input_tokens, output_tokens, cost_usd,
              json.dumps(request_data) if request_data else None,
              json.dumps(response_data) if response_data else None))
        conn.commit()


def get_llm_costs(db_path: str, days: int = 7) -> Dict[str, Any]:
    """Get LLM cost summary for the specified period."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        
        since = datetime.utcnow() - timedelta(days=days)
        
        cursor.execute("""
            SELECT 
                SUM(cost_usd) as total_cost,
                SUM(input_tokens) as total_input_tokens,
                SUM(output_tokens) as total_output_tokens,
                COUNT(*) as total_calls
            FROM llm_calls
            WHERE created_at >= ?
        """, (since,))
        
        row = cursor.fetchone()
        
        # Get breakdown by capability
        cursor.execute("""
            SELECT capability, SUM(cost_usd) as cost, COUNT(*) as calls
            FROM llm_calls
            WHERE created_at >= ?
            GROUP BY capability
        """, (since,))
        
        by_capability = {r["capability"]: {"cost": r["cost"], "calls": r["calls"]} 
                        for r in cursor.fetchall()}
        
        return {
            "total_cost": row["total_cost"] or 0,
            "total_input_tokens": row["total_input_tokens"] or 0,
            "total_output_tokens": row["total_output_tokens"] or 0,
            "total_calls": row["total_calls"] or 0,
            "by_capability": by_capability
        }


def set_glitch_watch(db_path: str, url_id: int, enabled: bool) -> None:
    """Enable or disable glitch watch for a product URL."""
    with get_connection(db_path) as conn:
        conn.execute("""
            UPDATE product_urls SET glitch_watch = ? WHERE id = ?
        """, (enabled, url_id))
        conn.commit()


def set_ua_override(db_path: str, domain: str, mode: str) -> None:
    """Set user agent override for a domain."""
    with get_connection(db_path) as conn:
        now = datetime.utcnow()
        conn.execute("""
            INSERT INTO ua_overrides (domain, ua_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
                ua_mode = excluded.ua_mode,
                updated_at = excluded.updated_at
        """, (domain, mode, now, now))
        conn.commit()


def get_ua_override(db_path: str, domain: str) -> Optional[str]:
    """Get user agent override for a domain."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT ua_mode FROM ua_overrides WHERE domain = ?", (domain,))
        row = cursor.fetchone()
        return row["ua_mode"] if row else None
