"""Flask web server for Bargin UI and API."""

import os
import json
import threading
import sys
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, Response

from bargin import __version__
from bargin.runner import run_sweep, start_task, finish_task, get_task


def create_app(get_db_path, config):
    """Create the Flask application."""
    
    # Determine the correct path to the web directory
    # The server.py is in bargin/web/, so we need to go up one level to bargin/
    # and then into web/
    import os
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    web_dir = os.path.join(base_dir, 'web')
    
    app = Flask(__name__, static_folder=web_dir, static_url_path='')

    from bargin.db import init_db
    
    # Get database path lazily
    def db_path():
        return get_db_path() if callable(get_db_path) else get_db_path

    init_db(db_path())

    def json_body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return None, (jsonify({"error": "JSON object required"}), 400)
        return data, None

    registry_dir = os.path.abspath(os.path.join(base_dir, os.pardir, 'registry'))
    
    @app.route('/')
    def index():
        """Serve the main HTML page."""
        return send_from_directory(app.static_folder, 'index.html')
    
    @app.route('/api/health')
    def health():
        """Health check endpoint."""
        from bargin.db import get_connection, get_zenrows_usage, get_llm_budget
        
        try:
            with get_connection(db_path()) as conn:
                conn.execute("SELECT 1")
            db_ok = True
        except Exception:
            db_ok = False
        
        zenrows_usage = get_zenrows_usage(db_path(), days=1)
        zenrows_usage["daily_limit"] = config.zenrows_daily_max_requests
        zenrows_usage["remaining"] = max(
            0, config.zenrows_daily_max_requests - zenrows_usage["credits"]
        )
        llm_budget = get_llm_budget(db_path(), config.llm_daily_budget_usd)
        return jsonify({
            "status": "ok" if db_ok else "degraded",
            "version": __version__,
            "database": db_path(),
            "config": {
                "locale": config.locale,
                "currency": config.currency,
                "timezone": config.timezone,
                "ntfy": bool(config.ntfy_topic),
                "anthropic": bool(config.anthropic_api_key),
                "ebay": bool(config.ebay_client_id),
                "reddit": bool(config.reddit_client_id),
                "zenrows": config.has_zenrows,
            },
            "zenrows_usage": zenrows_usage,
            "llm_budget": llm_budget,
        })
    
    @app.route('/api/products', methods=['GET'])
    def list_products():
        """Get all tracked products."""
        from bargin.db import get_products
        
        products = get_products(db_path())
        return jsonify(products)
    
    @app.route('/api/products', methods=['POST'])
    def add_product():
        """Add a new product to track."""
        from bargin.db import add_product, find_product_by_url, get_connection, log_activity, update_product_price
        from bargin.fetcher import Fetcher
        from bargin.extractor import extract_title, find_price_selectors, extract_price
        
        data, error = json_body()
        if error:
            return error
        url = data.get('url')
        requested_title = data.get('title')
        target_price = data.get('target_price')
        use_llm = bool(data.get('use_llm', False))
        use_zenrows = bool(data.get('use_zenrows', False))
        poll_now = bool(data.get('poll_now', True))
        
        if not url:
            return jsonify({"error": "URL required"}), 400

        from urllib.parse import urlparse
        parsed_url = urlparse(url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            return jsonify({"error": "A valid HTTP(S) URL is required"}), 400
        existing = find_product_by_url(db_path(), url)
        if existing:
            return jsonify({
                "product_id": existing["product_id"],
                "title": existing["title"],
                "url": existing["url"],
                "existing": True,
                "message": "Product is already tracked; no fetch was performed.",
            }), 200
        if target_price is not None:
            try:
                target_price = float(target_price)
                if target_price <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                return jsonify({"error": "target_price must be positive"}), 400
        
        fetcher = Fetcher(
            db_path(), config.zenrows_api_key,
            config.zenrows_daily_max_requests,
            config.zenrows_cooldown_hours,
        )
        html, status, error = fetcher.fetch(url, use_zenrows=use_zenrows)
        
        if status != 200:
            return jsonify({"error": f"Failed to fetch: {error}"}), 400
        
        title = (requested_title or extract_title(html)).strip()
        if use_llm and config.has_anthropic and not requested_title:
            try:
                from bargin.llm import LLMClient
                title = LLMClient(config.anthropic_api_key, db_path()).normalize_product_title(title)
            except Exception:
                pass
        
        # Detect retailer
        domain = parsed_url.netloc
        retailer = domain.replace("www.", "")
        
        # Find price selector
        candidates = find_price_selectors(html)
        selector = candidates[0] if candidates else None

        if use_llm and config.has_anthropic and not selector:
            try:
                from bargin.llm import LLMClient
                selector = LLMClient(config.anthropic_api_key, db_path()).extract_selector_from_html(html, url)
            except Exception:
                pass

        if not selector and not requested_title:
            return jsonify({
                "error": "This URL does not appear to be a single product page. "
                         "Use the individual product URL instead."
            }), 400
        
        # Extract current price
        current_price = None
        if selector:
            current_price = extract_price(html, selector)
        
        product_id = add_product(
            db_path(), title, url, retailer, selector, target_price, use_zenrows
        )

        if poll_now and current_price is not None:
            update_product_price(db_path(),
                                 _first_product_url_id(db_path(), product_id),
                                 current_price)
        
        return jsonify({
            "product_id": product_id,
            "title": title,
            "retailer": retailer,
            "selector": selector,
            "current_price": current_price
        })

    def _first_product_url_id(database_path, product_id):
        from bargin.db import get_connection
        with get_connection(database_path) as conn:
            row = conn.execute(
                "SELECT id FROM product_urls WHERE product_id = ? ORDER BY id LIMIT 1",
                (product_id,),
            ).fetchone()
        return row["id"] if row else None

    @app.route('/api/products/<int:product_id>', methods=['PUT'])
    def edit_product(product_id):
        """Edit the product name and its primary tracked URL."""
        from urllib.parse import urlparse
        from bargin.db import update_product

        data, error = json_body()
        if error:
            return error
        title = data.get("title")
        url = data.get("url")
        if title is None and url is None:
            return jsonify({"error": "title or url required"}), 400
        if url is not None:
            parsed_url = urlparse(url)
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
                return jsonify({"error": "A valid HTTP(S) URL is required"}), 400
            retailer = parsed_url.netloc.replace("www.", "")
        else:
            retailer = None
        try:
            update_product(db_path(), product_id, title=title, url=url, retailer=retailer)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"updated": True, "product_id": product_id})
    
    @app.route('/api/products/<int:product_id>', methods=['DELETE'])
    def delete_product(product_id):
        """Delete a product."""
        from bargin.db import get_connection
        
        with get_connection(db_path()) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
            conn.commit()
        
        return jsonify({"deleted": True})
    
    @app.route('/api/products/<int:url_id>/glitch', methods=['POST'])
    def toggle_glitch_watch(url_id):
        """Toggle glitch watch for a product URL."""
        from bargin.db import set_glitch_watch, get_connection
        
        data, error = json_body()
        if error:
            return error
        enabled = data.get('enabled', True)
        
        set_glitch_watch(db_path(), url_id, enabled)
        
        return jsonify({"glitch_watch": enabled})
    
    @app.route('/api/alerts', methods=['GET'])
    def list_alerts():
        """Get alerts."""
        from bargin.db import get_alerts
        
        unread_only = request.args.get('unread', 'false').lower() == 'true'
        try:
            limit = int(request.args.get('limit', 100))
            if limit < 1 or limit > 500:
                raise ValueError
        except ValueError:
            return jsonify({"error": "limit must be between 1 and 500"}), 400
        
        alerts = get_alerts(db_path(), unread_only=unread_only, limit=limit)
        return jsonify(alerts)
    
    @app.route('/api/alerts/<int:alert_id>/read', methods=['POST'])
    def mark_alert_read(alert_id):
        """Mark an alert as read."""
        from bargin.db import mark_alert_read
        
        mark_alert_read(db_path(), alert_id)
        return jsonify({"read": True})
    
    @app.route('/api/feeds', methods=['GET'])
    def list_feeds():
        """Get configured feeds."""
        from bargin.db import get_feeds
        
        feeds = get_feeds(db_path())
        return jsonify(feeds)
    
    @app.route('/api/feeds', methods=['POST'])
    def add_feed():
        """Add a new feed."""
        from bargin.db import add_feed as db_add_feed
        
        data, error = json_body()
        if error:
            return error
        url = data.get('url')
        name = data.get('name')
        
        if not url:
            return jsonify({"error": "URL required"}), 400
        
        # Normalize Reddit URLs
        if url.startswith("r/"):
            url = f"https://www.reddit.com/r/{url[2:]}/.rss"
        elif not url.startswith("http"):
            url = f"https://{url}"
        
        feed_id = db_add_feed(db_path(), url, name)
        return jsonify({"feed_id": feed_id})
    
    @app.route('/api/feeds/items', methods=['GET'])
    def list_feed_items():
        """Get feed items."""
        from bargin.db import get_feed_items
        
        feed_id = request.args.get('feed_id', type=int)
        try:
            limit = int(request.args.get('limit', 50))
            if limit < 1 or limit > 500:
                raise ValueError
        except ValueError:
            return jsonify({"error": "limit must be between 1 and 500"}), 400
        
        items = get_feed_items(db_path(), feed_id=feed_id, limit=limit)
        return jsonify(items)
    
    @app.route('/api/activity', methods=['GET'])
    def list_activity():
        """Get activity log."""
        from bargin.db import get_activity
        
        try:
            limit = int(request.args.get('limit', 200))
            if limit < 1 or limit > 500:
                raise ValueError
        except ValueError:
            return jsonify({"error": "limit must be between 1 and 500"}), 400
        activity = get_activity(db_path(), limit=limit)
        return jsonify(activity)
    
    @app.route('/api/costs', methods=['GET'])
    def get_costs():
        """Get LLM cost summary."""
        from bargin.db import get_llm_costs
        
        try:
            days = int(request.args.get('days', 7))
            if days < 1 or days > 3650:
                raise ValueError
        except ValueError:
            return jsonify({"error": "days must be between 1 and 3650"}), 400
        costs = get_llm_costs(db_path(), days=days)
        from bargin.db import get_llm_budget
        costs["llm_budget"] = get_llm_budget(db_path(), config.llm_daily_budget_usd)
        from bargin.db import get_zenrows_usage
        costs["zenrows"] = get_zenrows_usage(db_path(), days=days)
        costs["zenrows"]["daily_limit"] = config.zenrows_daily_max_requests
        costs["zenrows"]["remaining"] = max(
            0, config.zenrows_daily_max_requests - get_zenrows_usage(db_path(), 1)["credits"]
        )
        return jsonify(costs)
    
    @app.route('/api/search', methods=['GET'])
    def search():
        """Search for products."""
        query = request.args.get('q', '')
        
        results = []
        
        # Search eBay if configured
        if config.has_ebay:
            try:
                from bargin.search import ebay_search
                ebay_results = ebay_search(
                    query, config.ebay_client_id, config.ebay_client_secret,
                    config.ebay_marketplace,
                )
                results.extend(ebay_results[:10])
            except Exception:
                pass
        
        return jsonify(results)
    
    @app.route('/api/admin/retailers', methods=['GET'])
    def list_retailers():
        """Get configured retailers."""
        import yaml
        
        retailers = {}
        
        # Load curated retailers
        curated_path = os.path.join(registry_dir, "retailers.yaml")
        if os.path.exists(curated_path):
            with open(curated_path) as f:
                retailers['curated'] = yaml.safe_load(f) or {}
        
        # Load local retailers
        local_path = os.path.join(registry_dir, "local.yaml")
        if os.path.exists(local_path):
            with open(local_path) as f:
                retailers['local'] = yaml.safe_load(f) or {}
        
        return jsonify(retailers)
    
    @app.route('/api/admin/retailers', methods=['POST'])
    def save_retailer():
        """Save a retailer configuration."""
        import yaml
        
        data, error = json_body()
        if error:
            return error
        retailer_name = data.get('name')
        config_data = data.get('config')
        
        if not retailer_name or not config_data:
            return jsonify({"error": "Name and config required"}), 400
        
        # Load existing local retailers
        local_path = os.path.join(registry_dir, "local.yaml")
        os.makedirs(registry_dir, exist_ok=True)
        local = {}
        if os.path.exists(local_path):
            with open(local_path) as f:
                local = yaml.safe_load(f) or {}
        
        # Add/update retailer
        local[retailer_name] = config_data
        
        # Save back
        with open(local_path, 'w') as f:
            yaml.dump(local, f)
        
        return jsonify({"saved": True})
    
    @app.route('/api/admin/ua', methods=['GET'])
    def get_ua_overrides():
        """Get User-Agent overrides."""
        from bargin.db import get_connection
        
        with get_connection(db_path()) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT domain, ua_mode FROM ua_overrides")
            overrides = {row["domain"]: row["ua_mode"] for row in cursor.fetchall()}
        
        return jsonify(overrides)
    
    @app.route('/api/admin/ua', methods=['POST'])
    def set_ua_override():
        """Set User-Agent override for a domain."""
        from bargin.db import set_ua_override
        
        data, error = json_body()
        if error:
            return error
        domain = data.get('domain')
        mode = data.get('mode')
        
        if not domain or mode not in ['honest', 'browser']:
            return jsonify({"error": "Invalid domain or mode"}), 400
        
        set_ua_override(db_path(), domain, mode)
        return jsonify({"updated": True})
    
    @app.route('/api/admin/llm/calls', methods=['GET'])
    def list_llm_calls():
        """Get recent LLM calls."""
        from bargin.db import get_connection
        
        with get_connection(db_path()) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT capability, model, input_tokens, output_tokens, 
                       cost_usd, created_at
                FROM llm_calls
                ORDER BY created_at DESC
                LIMIT 50
            """)
            calls = [dict(row) for row in cursor.fetchall()]
        
        return jsonify(calls)
    
    @app.route('/api/run', methods=['POST'])
    def run_sweep():
        """Trigger a product or feed sweep asynchronously."""
        data = request.get_json(silent=True) or {}
        action = data.get("action", "products")
        if action not in {"products", "feeds"}:
            return jsonify({"error": "action must be products or feeds"}), 400

        task_id = start_task(db_path(), action)
        
        def run_in_thread():
            checked, errors = run_sweep(db_path(), config, action)
            finish_task(db_path(), task_id, checked, errors)
        
        thread = threading.Thread(target=run_in_thread, daemon=True)
        thread.start()
        
        return jsonify({"status": "started", "action": action, "task_id": task_id}), 202

    @app.route('/api/tasks/<int:task_id>', methods=['GET'])
    def get_task_status(task_id):
        """Get task status for polling."""
        task = get_task(db_path(), task_id)
        if not task:
            return jsonify({"error": "Task not found"}), 404
        return jsonify(task)

    return app
