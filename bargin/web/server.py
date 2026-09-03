"""Flask web server for Bargin UI and API."""

import os
import json
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, Response

from bargin import __version__


def create_app(get_db_path, config):
    """Create the Flask application."""
    
    # Determine the correct path to the web directory
    # The server.py is in bargin/web/, so we need to go up one level to bargin/
    # and then into web/
    import os
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    web_dir = os.path.join(base_dir, 'web')
    
    app = Flask(__name__, static_folder=web_dir, static_url_path='')
    
    # Get database path lazily
    def db_path():
        return get_db_path() if callable(get_db_path) else get_db_path
    
    @app.route('/')
    def index():
        """Serve the main HTML page."""
        return send_from_directory(app.static_folder, 'index.html')
    
    @app.route('/api/health')
    def health():
        """Health check endpoint."""
        from bargin.db import get_connection
        
        try:
            with get_connection(db_path()) as conn:
                conn.execute("SELECT 1")
            db_ok = True
        except Exception:
            db_ok = False
        
        return jsonify({
            "status": "ok" if db_ok else "degraded",
            "version": __version__,
            "database": db_path(),
            "config": {
                "ntfy": bool(config.ntfy_topic),
                "anthropic": bool(config.anthropic_api_key),
                "ebay": bool(config.ebay_client_id),
                "reddit": bool(config.reddit_client_id),
                "zenrows": bool(config.zenrows_api_key),
            }
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
        from bargin.db import add_product, get_connection, log_activity
        from bargin.fetcher import Fetcher
        from bargin.extractor import extract_title, find_price_selectors, extract_price
        
        data = request.json
        url = data.get('url')
        target_price = data.get('target_price')
        
        if not url:
            return jsonify({"error": "URL required"}), 400
        
        fetcher = Fetcher(db_path(), config.zenrows_api_key)
        html, status, error = fetcher.fetch(url)
        
        if status != 200:
            return jsonify({"error": f"Failed to fetch: {error}"}), 400
        
        title = extract_title(html)
        
        # Detect retailer
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
        retailer = domain.replace("www.", "")
        
        # Find price selector
        candidates = find_price_selectors(html)
        selector = candidates[0] if candidates else None
        
        # Extract current price
        current_price = None
        if selector:
            current_price = extract_price(html, selector)
        
        product_id = add_product(
            db_path(), title, url, retailer, selector, target_price
        )
        
        return jsonify({
            "product_id": product_id,
            "title": title,
            "retailer": retailer,
            "selector": selector,
            "current_price": current_price
        })
    
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
        
        data = request.json
        enabled = data.get('enabled', True)
        
        set_glitch_watch(db_path(), url_id, enabled)
        
        return jsonify({"glitch_watch": enabled})
    
    @app.route('/api/alerts', methods=['GET'])
    def list_alerts():
        """Get alerts."""
        from bargin.db import get_alerts
        
        unread_only = request.args.get('unread', 'false').lower() == 'true'
        limit = int(request.args.get('limit', 100))
        
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
        
        data = request.json
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
        limit = int(request.args.get('limit', 50))
        
        items = get_feed_items(db_path(), feed_id=feed_id, limit=limit)
        return jsonify(items)
    
    @app.route('/api/activity', methods=['GET'])
    def list_activity():
        """Get activity log."""
        from bargin.db import get_activity
        
        limit = int(request.args.get('limit', 200))
        activity = get_activity(db_path(), limit=limit)
        return jsonify(activity)
    
    @app.route('/api/costs', methods=['GET'])
    def get_costs():
        """Get LLM cost summary."""
        from bargin.db import get_llm_costs
        
        days = int(request.args.get('days', 7))
        costs = get_llm_costs(db_path(), days=days)
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
                ebay_results = ebay_search(query, config.ebay_client_id, config.ebay_client_secret)
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
        curated_path = "registry/retailers.yaml"
        if os.path.exists(curated_path):
            with open(curated_path) as f:
                retailers['curated'] = yaml.safe_load(f) or {}
        
        # Load local retailers
        local_path = "registry/local.yaml"
        if os.path.exists(local_path):
            with open(local_path) as f:
                retailers['local'] = yaml.safe_load(f) or {}
        
        return jsonify(retailers)
    
    @app.route('/api/admin/retailers', methods=['POST'])
    def save_retailer():
        """Save a retailer configuration."""
        import yaml
        
        data = request.json
        retailer_name = data.get('name')
        config_data = data.get('config')
        
        if not retailer_name or not config_data:
            return jsonify({"error": "Name and config required"}), 400
        
        # Load existing local retailers
        local_path = "registry/local.yaml"
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
        
        data = request.json
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
        """Trigger a price check sweep."""
        # This would normally trigger background work
        # For now, just acknowledge
        return jsonify({"status": "started"})
    
    return app
