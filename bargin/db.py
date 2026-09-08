"""Database layer for Bargin using SQLite."""

import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
import json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode


def normalize_url(url: str) -> str:
    """Normalize harmless URL differences for duplicate detection."""
    parts = urlsplit(url.strip())
    query = urlencode([
        (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith(("utm_", "fbclid", "gclid"))
    ])
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def find_product_by_url(db_path: str, url: str) -> Optional[Dict[str, Any]]:
    """Find an existing product URL using normalized URL comparison."""
    wanted = normalize_url(url)
    with get_connection(db_path) as conn:
        rows = conn.execute("""
            SELECT p.id AS product_id, p.title, pu.id AS url_id, pu.url
            FROM products p JOIN product_urls pu ON pu.product_id = p.id
        """).fetchall()
    for row in rows:
        if normalize_url(row["url"]) == wanted:
            return dict(row)
    return None


@contextmanager
def get_connection(db_path: str):
    """Get a database connection with row factory."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
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
                use_zenrows BOOLEAN DEFAULT FALSE,
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

            -- Tracks whether a threshold condition is currently active.
            CREATE TABLE IF NOT EXISTS alert_conditions (
                product_url_id INTEGER NOT NULL,
                alert_type TEXT NOT NULL,
                active BOOLEAN DEFAULT FALSE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (product_url_id, alert_type),
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
            
            -- Task tracking for async API
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TIMESTAMP NOT NULL,
                finished_at TIMESTAMP,
                result_json TEXT,
                error TEXT,
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
                estimated_cost_usd REAL DEFAULT 0,
                reserved_cost_usd REAL DEFAULT 0,
                status TEXT DEFAULT 'success',
                error TEXT,
                latency_ms INTEGER,
                request_data TEXT,
                response_data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS llm_cache (
                capability TEXT NOT NULL,
                cache_key TEXT NOT NULL,
                result TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (capability, cache_key)
            );
            
            -- ZenRows usage tracking
            CREATE TABLE IF NOT EXISTS zenrows_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                success BOOLEAN,
                status_code INTEGER,
                reason TEXT,
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
            CREATE INDEX IF NOT EXISTS idx_zenrows_usage_created_at ON zenrows_usage(created_at);
            CREATE INDEX IF NOT EXISTS idx_zenrows_usage_url ON zenrows_usage(url);
        """)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(product_urls)")}
        if "use_zenrows" not in columns:
            conn.execute("ALTER TABLE product_urls ADD COLUMN use_zenrows BOOLEAN DEFAULT FALSE")
        usage_columns = {row["name"] for row in conn.execute("PRAGMA table_info(zenrows_usage)")}
        if "status_code" not in usage_columns:
            conn.execute("ALTER TABLE zenrows_usage ADD COLUMN status_code INTEGER")
        if "reason" not in usage_columns:
            conn.execute("ALTER TABLE zenrows_usage ADD COLUMN reason TEXT")
        llm_columns = {row["name"] for row in conn.execute("PRAGMA table_info(llm_calls)")}
        for column, definition in {
            "estimated_cost_usd": "REAL DEFAULT 0",
            "reserved_cost_usd": "REAL DEFAULT 0",
            "status": "TEXT DEFAULT 'success'",
            "error": "TEXT",
            "latency_ms": "INTEGER",
        }.items():
            if column not in llm_columns:
                conn.execute(f"ALTER TABLE llm_calls ADD COLUMN {column} {definition}")
        conn.commit()


def reserve_llm_budget(db_path: str, capability: str, model: str,
                       estimated_cost_usd: float, daily_budget_usd: float) -> tuple[Optional[int], str]:
    """Reserve estimated LLM spend before making a remote call."""
    with get_connection(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if daily_budget_usd > 0:
            used = conn.execute("""
                SELECT COALESCE(SUM(CASE WHEN status = 'reserved'
                    THEN reserved_cost_usd ELSE cost_usd END), 0) AS used
                FROM llm_calls WHERE date(created_at) = date('now')
            """).fetchone()["used"]
            if used + estimated_cost_usd > daily_budget_usd:
                conn.execute("""
                    INSERT INTO llm_calls (capability, model, cost_usd,
                        estimated_cost_usd, reserved_cost_usd, status, error)
                    VALUES (?, ?, 0, ?, 0, 'blocked', ?)
                """, (capability, model, estimated_cost_usd, "daily budget reached"))
                conn.commit()
                return None, "daily LLM budget reached"
        cursor = conn.execute("""
            INSERT INTO llm_calls (capability, model, cost_usd,
                estimated_cost_usd, reserved_cost_usd, status)
            VALUES (?, ?, 0, ?, ?, 'reserved')
        """, (capability, model, estimated_cost_usd, estimated_cost_usd))
        conn.commit()
        return cursor.lastrowid, "reserved"


def get_llm_cache(db_path: str, capability: str, cache_key: str,
                  max_age_hours: Optional[int] = None) -> Optional[str]:
    """Return a cached LLM result for an identical input."""
    with get_connection(db_path) as conn:
        if max_age_hours is None:
            row = conn.execute(
                "SELECT result FROM llm_cache WHERE capability = ? AND cache_key = ?",
                (capability, cache_key),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT result FROM llm_cache WHERE capability = ? AND cache_key = ? "
                "AND created_at >= datetime('now', ?)",
                (capability, cache_key, f"-{max_age_hours} hours"),
            ).fetchone()
        return row["result"] if row else None


def set_llm_cache(db_path: str, capability: str, cache_key: str, result: str) -> None:
    """Cache an LLM result without storing the original prompt."""
    with get_connection(db_path) as conn:
        conn.execute("""
            INSERT INTO llm_cache (capability, cache_key, result)
            VALUES (?, ?, ?)
            ON CONFLICT(capability, cache_key) DO UPDATE SET
                result = excluded.result, created_at = CURRENT_TIMESTAMP
        """, (capability, cache_key, result))
        conn.commit()


def finish_llm_call(db_path: str, call_id: int, status: str,
                    input_tokens: int = 0, output_tokens: int = 0,
                    cost_usd: float = 0.0, error: Optional[str] = None,
                    latency_ms: Optional[int] = None,
                    response_data: Optional[Dict] = None) -> None:
    """Finalize a reserved LLM call with actual usage and status."""
    with get_connection(db_path) as conn:
        conn.execute("""
            UPDATE llm_calls SET status = ?, input_tokens = ?, output_tokens = ?,
                cost_usd = ?, reserved_cost_usd = 0, error = ?, latency_ms = ?,
                response_data = ? WHERE id = ?
        """, (status, input_tokens, output_tokens, cost_usd, error, latency_ms,
              json.dumps(response_data) if response_data else None, call_id))
        conn.commit()


def get_llm_budget(db_path: str, daily_budget_usd: float) -> Dict[str, Any]:
    """Return today's actual, reserved, blocked, and remaining LLM budget."""
    with get_connection(db_path) as conn:
        row = conn.execute("""
            SELECT COALESCE(SUM(cost_usd), 0) AS spent,
                   COALESCE(SUM(reserved_cost_usd), 0) AS reserved,
                   SUM(CASE WHEN status = 'blocked' THEN 1 ELSE 0 END) AS blocked,
                   SUM(CASE WHEN status IN ('error', 'timeout') THEN 1 ELSE 0 END) AS failed
            FROM llm_calls WHERE date(created_at) = date('now')
        """).fetchone()
    committed = row["spent"] or 0
    reserved = row["reserved"] or 0
    return {
        "spent": committed,
        "reserved": reserved,
        "blocked": row["blocked"] or 0,
        "failed": row["failed"] or 0,
        "budget": daily_budget_usd,
        "remaining": None if daily_budget_usd <= 0 else max(0, daily_budget_usd - committed - reserved),
    }


def reserve_zenrows_request(db_path: str, url: str, daily_limit: int,
                            cooldown_hours: int = 24,
                            force: bool = False) -> tuple[Optional[int], str]:
    """Atomically reserve one ZenRows credit, enforcing cap and cooldown."""
    with get_connection(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        today = datetime.utcnow().date().isoformat()
        used = conn.execute(
            "SELECT COALESCE(SUM(cost_credits), 0) AS used FROM zenrows_usage "
            "WHERE date(created_at) = ?", (today,)
        ).fetchone()["used"]
        if daily_limit >= 0 and used >= daily_limit:
            return None, "daily ZenRows limit reached"

        if not force:
            recent = conn.execute(
                "SELECT id FROM zenrows_usage WHERE url = ? AND success = 1 "
                "AND created_at >= datetime('now', ?) ORDER BY id DESC LIMIT 1",
                (url, f"-{cooldown_hours} hours"),
            ).fetchone()
            if recent:
                return None, "ZenRows URL cooldown active"

        cursor = conn.execute(
            "INSERT INTO zenrows_usage (url, success, reason) VALUES (?, NULL, ?)",
            (url, "reserved"),
        )
        conn.commit()
        return cursor.lastrowid, "reserved"


def finish_zenrows_request(db_path: str, usage_id: int, success: bool,
                           status_code: int = 0, reason: str = "") -> None:
    """Record the result of a reserved ZenRows request."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE zenrows_usage SET success = ?, status_code = ?, reason = ? WHERE id = ?",
            (success, status_code, reason, usage_id),
        )
        conn.commit()


def get_zenrows_usage(db_path: str, days: int = 1) -> Dict[str, Any]:
    """Return ZenRows usage totals and recent request details."""
    with get_connection(db_path) as conn:
        since = datetime.utcnow() - timedelta(days=days)
        row = conn.execute("""
            SELECT COALESCE(SUM(cost_credits), 0) AS credits,
                   COUNT(*) AS attempts,
                   SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS successes,
                   SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failures
            FROM zenrows_usage WHERE created_at >= ?
        """, (since,)).fetchone()
        return {
            "credits": row["credits"] or 0,
            "attempts": row["attempts"] or 0,
            "successes": row["successes"] or 0,
            "failures": row["failures"] or 0,
        }


def add_product(db_path: str, title: str, url: str, retailer: str, 
                selector: Optional[str] = None, target_price: Optional[float] = None,
                use_zenrows: bool = False) -> int:
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
            INSERT INTO product_urls (product_id, url, retailer, selector, target_price, use_zenrows)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (product_id, url, retailer, selector, target_price, use_zenrows))
        
        log_activity(conn, "product_added", "product", product_id, {"url": url, "retailer": retailer})
        conn.commit()
        
        return product_id


def update_product(db_path: str, product_id: int, title: Optional[str] = None,
                   url: Optional[str] = None, retailer: Optional[str] = None) -> None:
    """Update a product name and/or its tracked URL."""
    with get_connection(db_path) as conn:
        product = conn.execute(
            "SELECT id FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if not product:
            raise ValueError("product not found")

        if title is not None:
            title = title.strip()
            if not title:
                raise ValueError("title must not be empty")
            conn.execute(
                "UPDATE products SET title = ?, updated_at = ? WHERE id = ?",
                (title, datetime.utcnow(), product_id),
            )

        if url is not None or retailer is not None:
            row = conn.execute(
                "SELECT id FROM product_urls WHERE product_id = ? ORDER BY id LIMIT 1",
                (product_id,),
            ).fetchone()
            if not row:
                raise ValueError("product has no tracked URL")
            allowed = {"url", "retailer"}
            data = {"url": url, "retailer": retailer}
            fields = [f"{k} = ?" for k in data.keys() if k in allowed and data[k] is not None]
            values = [data[k] for k in allowed if k in data and data[k] is not None]
            values.append(row["id"])
            conn.execute(
                f"UPDATE product_urls SET {', '.join(fields)} WHERE id = ?",
                values,
            )
            conn.execute(
                "UPDATE products SET updated_at = ? WHERE id = ?",
                (datetime.utcnow(), product_id),
            )
        conn.commit()


def get_products(db_path: str) -> List[Dict[str, Any]]:
    """Get all tracked products with their URLs."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT p.id, p.title, p.created_at,
                   pu.id as url_id, pu.url, pu.retailer, pu.selector,
                   pu.target_price, pu.use_zenrows, pu.glitch_watch, pu.last_checked,
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
                    "use_zenrows": bool(row["use_zenrows"]),
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


def claim_alert_condition(db_path: str, url_id: int, alert_type: str,
                          active: bool) -> bool:
    """Return whether an alert should be emitted for a condition transition."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT active FROM alert_conditions WHERE product_url_id = ? AND alert_type = ?",
            (url_id, alert_type),
        ).fetchone()
        was_active = bool(row["active"]) if row else False
        conn.execute("""
            INSERT INTO alert_conditions (product_url_id, alert_type, active, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(product_url_id, alert_type) DO UPDATE SET
                active = excluded.active, updated_at = excluded.updated_at
        """, (url_id, alert_type, active, datetime.utcnow()))
        conn.commit()
        return active and not was_active


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


def mark_alert_sent(db_path: str, alert_id: int) -> None:
    """Mark an alert as successfully delivered by push notification."""
    with get_connection(db_path) as conn:
        conn.execute("UPDATE alerts SET sent_push = TRUE WHERE id = ?", (alert_id,))
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


def get_feed_item(db_path: str, feed_id: int, item_id: str) -> Optional[Dict[str, Any]]:
    """Return a feed item by its feed-local identifier."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM feed_items WHERE feed_id = ? AND item_id = ?",
            (feed_id, item_id),
        ).fetchone()
        return dict(row) if row else None


def mark_feed_item_triaged(db_path: str, item_id: int, result: Dict[str, Any]) -> None:
    """Persist a feed item's triage result."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE feed_items SET triaged = TRUE, triage_result = ? WHERE id = ?",
            (json.dumps(result), item_id),
        )
        conn.commit()


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
