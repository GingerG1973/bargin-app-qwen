from types import SimpleNamespace

from bargin.config import Config
from bargin.db import (
    add_feed,
    add_feed_item,
    add_product,
    claim_alert_condition,
    create_alert,
    get_connection,
    get_feed_items,
    init_db,
    get_products,
    get_zenrows_usage,
    reserve_zenrows_request,
)
from bargin.llm import LLMClient
from bargin.fetcher import Fetcher
from bargin.extractor import extract_structured_price, find_price_selectors, is_collection_page
from bargin.search import ebay_search
from bargin.web.server import create_app


def test_product_delete_cascades_and_alert_condition_rearms(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    product_id = add_product(db_path, "Kettle", "https://shop.test/kettle", "shop.test")

    with get_connection(db_path) as conn:
        url_id = conn.execute("SELECT id FROM product_urls").fetchone()["id"]
    create_alert(db_path, url_id, "target_reached", "Target")

    assert claim_alert_condition(db_path, url_id, "target_reached", True) is True
    assert claim_alert_condition(db_path, url_id, "target_reached", True) is False
    assert claim_alert_condition(db_path, url_id, "target_reached", False) is False
    assert claim_alert_condition(db_path, url_id, "target_reached", True) is True

    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
        assert conn.execute("SELECT COUNT(*) FROM product_urls").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM alert_conditions").fetchone()[0] == 0


def test_feed_items_are_deduplicated(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    feed_id = add_feed(db_path, "https://example.test/feed", "Example")

    first = add_feed_item(db_path, feed_id, "item-1", "A deal")
    second = add_feed_item(db_path, feed_id, "item-1", "A deal")

    assert first == second
    assert len(get_feed_items(db_path)) == 1


def test_product_edit_and_zenrows_preference(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    product_id = add_product(
        db_path, "Old name", "https://shop.test/old", "shop.test",
        use_zenrows=True,
    )
    app = create_app(lambda: db_path, Config(database_path=db_path))
    response = app.test_client().put(
        f"/api/products/{product_id}",
        json={"title": "New name", "url": "https://shop.test/new"},
    )

    assert response.status_code == 200
    product = get_products(db_path)[0]
    assert product["title"] == "New name"
    assert product["urls"][0]["url"] == "https://shop.test/new"
    assert product["urls"][0]["use_zenrows"] is True


def test_product_creation_polls_immediately_with_options(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    config = Config(database_path=db_path)

    class FakeFetcher:
        def __init__(self, *_args):
            pass

        def fetch(self, url, use_zenrows=False):
            assert use_zenrows is True
            return "<h1>Test kettle</h1><span class='price'>£19.99</span>", 200, ""

    monkeypatch.setattr("bargin.fetcher.Fetcher", FakeFetcher)
    app = create_app(lambda: db_path, config)
    response = app.test_client().post("/api/products", json={
        "url": "https://shop.test/kettle",
        "use_zenrows": True,
        "poll_now": True,
    })

    assert response.status_code == 200
    product = get_products(db_path)[0]
    assert product["urls"][0]["last_price"] == 19.99
    assert product["urls"][0]["use_zenrows"] is True


def test_duplicate_product_add_skips_network_fetch(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    config = Config(database_path=db_path)
    init_db(db_path)
    product_id = add_product(
        db_path, "Existing kettle", "https://shop.test/kettle?utm_source=mail",
        "shop.test", "__jsonld_product_offer__", 20,
    )
    calls = []

    class FakeFetcher:
        def __init__(self, *_args):
            pass

        def fetch(self, *args, **kwargs):
            calls.append((args, kwargs))
            return None, 500, "should not be called"

    monkeypatch.setattr("bargin.fetcher.Fetcher", FakeFetcher)
    app = create_app(lambda: db_path, config)
    response = app.test_client().post("/api/products", json={
        "url": "https://SHOP.TEST/kettle/?utm_campaign=duplicate",
    })

    assert response.status_code == 200
    assert response.get_json()["existing"] is True
    assert response.get_json()["product_id"] == product_id
    assert calls == []


def test_llm_records_input_and_output_tokens(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    recorded = {}

    def record(*args, **kwargs):
        recorded.update(kwargs)

    monkeypatch.setattr("bargin.db.record_llm_call", record)
    client = object.__new__(LLMClient)
    client.db_path = db_path
    client.model = "test-model"
    response = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=12, output_tokens=7),
        text="ok",
    )

    client._record_call("test", response)

    assert recorded["input_tokens"] == 12
    assert recorded["output_tokens"] == 7
    assert recorded["cost_usd"] == (12 * 3 + 7 * 15) / 1_000_000


def test_llm_call_is_blocked_before_network_when_budget_exhausted(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    client = object.__new__(LLMClient)
    client.db_path = db_path
    client.model = "test-model"
    client.simple_model = "test-simple"
    client.daily_budget_usd = 0.00001
    client.enabled = True

    class Messages:
        def create(self, **kwargs):
            raise AssertionError("Anthropic must not be called after budget rejection")

    client.client = SimpleNamespace(messages=Messages())

    try:
        client._call("test", 1000, [{"role": "user", "content": "x"}])
    except RuntimeError as exc:
        assert "budget" in str(exc)
    else:
        raise AssertionError("expected budget rejection")

    from bargin.db import get_llm_budget
    budget = get_llm_budget(db_path, 0.00001)
    assert budget["blocked"] == 1


def test_llm_call_reconciles_actual_usage(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    client = object.__new__(LLMClient)
    client.db_path = db_path
    client.model = "test-model"
    client.simple_model = "test-simple"
    client.daily_budget_usd = 1.0
    client.enabled = True
    response = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        content=[SimpleNamespace(text="done")],
    )
    client.client = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **kwargs: response)
    )

    assert client._call("test", 20, [{"role": "user", "content": "hello"}]) is response
    from bargin.db import get_llm_budget
    budget = get_llm_budget(db_path, 1.0)
    assert budget["spent"] == (10 * 3 + 5 * 15) / 1_000_000
    assert budget["reserved"] == 0


def test_selector_repair_uses_url_cooldown(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    client = object.__new__(LLMClient)
    client.db_path = db_path
    client.model = "test-model"
    client.simple_model = "test-simple"
    client.daily_budget_usd = 1.0
    client.enabled = True
    calls = []
    response = SimpleNamespace(content=[SimpleNamespace(text=".price")])
    client.client = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **kwargs: calls.append(kwargs) or response)
    )

    first = client.extract_selector_from_html("<div>£20</div>", "https://shop.test/item")
    second = client.extract_selector_from_html("<div>£21</div>", "https://shop.test/item")

    assert first == ".price"
    assert second == ".price"
    assert len(calls) == 1


def test_config_defaults_to_uk_locale(monkeypatch):
    monkeypatch.delenv("BARGIN_LOCALE", raising=False)
    monkeypatch.delenv("BARGIN_CURRENCY", raising=False)
    monkeypatch.delenv("BARGIN_TIMEZONE", raising=False)
    monkeypatch.delenv("BARGIN_EBAY_MARKETPLACE", raising=False)
    config = Config.load()
    assert config.locale == "en-GB"
    assert config.currency == "GBP"
    assert config.timezone == "Europe/London"
    assert config.ebay_marketplace == "EBAY_GB"


def test_ebay_search_uses_configured_marketplace(monkeypatch):
    seen = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"access_token": "token", "itemSummaries": []}

    def post(url, **kwargs):
        return Response()

    def get(url, **kwargs):
        seen.append(kwargs["headers"]["X-EBAY-C-MARKETPLACE-ID"])
        return Response()

    monkeypatch.setattr("bargin.search.requests.post", post)
    monkeypatch.setattr("bargin.search.requests.get", get)
    ebay_search("kettle", "id", "secret", "EBAY_GB")
    assert seen == ["EBAY_GB"]


def test_admin_retailers_and_api_validation_from_other_directory(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    config = Config(database_path=db_path)
    app = create_app(lambda: db_path, config)
    client = app.test_client()
    monkeypatch.chdir(tmp_path)

    response = client.get("/api/admin/retailers")
    assert response.status_code == 200
    assert "curated" in response.get_json()

    assert client.post("/api/products", json={"url": "ftp://invalid"}).status_code == 400
    assert client.get("/api/alerts?limit=not-a-number").status_code == 400
    assert client.post("/api/products", data="not-json", content_type="text/plain").status_code == 400


def test_fetcher_uses_zenrows_after_direct_403(monkeypatch):
    calls = []
    valid_key = "0" + "a" * 39

    class Response:
        def __init__(self, status_code, text=""):
            self.status_code = status_code
            self.text = text

    def get(url, **kwargs):
        calls.append((url, kwargs))
        if url == "https://shop.test/robots.txt":
            return Response(200, "User-agent: *\nAllow: /")
        if url == "https://api.zenrows.com/v1":
            return Response(200, "<html>rendered</html>")
        return Response(403)

    monkeypatch.setattr("bargin.fetcher.requests.get", get)
    html, status, error = Fetcher(zenrows_key=valid_key).fetch(
        "https://shop.test/item"
    )

    assert (html, status, error) == ("<html>rendered</html>", 200, "")
    assert any(url == "https://api.zenrows.com/v1" for url, _ in calls)


def test_fetcher_makes_one_paid_attempt_after_explicit_paid_failure(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    valid_key = "0" + "a" * 39
    calls = []

    class Response:
        def __init__(self, status_code, text=""):
            self.status_code = status_code
            self.text = text

    def get(url, **kwargs):
        calls.append(url)
        if url.endswith("/robots.txt"):
            return Response(200, "User-agent: *\nAllow: /")
        if url == "https://api.zenrows.com/v1":
            return Response(500, "provider error")
        return Response(403)

    monkeypatch.setattr("bargin.fetcher.requests.get", get)
    html, status, error = Fetcher(
        db_path, valid_key, zenrows_daily_limit=30
    ).fetch("https://shop.test/item", use_zenrows=True)

    assert html is None
    assert status == 403
    assert calls.count("https://api.zenrows.com/v1") == 1
    assert get_zenrows_usage(db_path)["credits"] == 1


def test_fetcher_respects_daily_zenrows_cap(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    valid_key = "0" + "a" * 39
    usage_id, _ = reserve_zenrows_request(db_path, "https://other.test/item", 1)
    assert usage_id is not None

    calls = []

    class Response:
        status_code = 403
        text = ""

    def get(url, **kwargs):
        calls.append(url)
        if url.endswith("/robots.txt"):
            return Response()
        return Response()

    monkeypatch.setattr("bargin.fetcher.requests.get", get)
    Fetcher(db_path, valid_key, zenrows_daily_limit=1).fetch(
        "https://shop.test/item"
    )

    assert "https://api.zenrows.com/v1" not in calls
    assert get_zenrows_usage(db_path)["credits"] == 1


def test_fetcher_explains_invalid_zenrows_key(monkeypatch):
    class Response:
        status_code = 403
        text = ""

    monkeypatch.setattr("bargin.fetcher.requests.get", lambda *args, **kwargs: Response())
    html, status, error = Fetcher(zenrows_key="invalid").fetch(
        "https://shop.test/item"
    )

    assert html is None
    assert status == 403
    assert "invalid format" in error


def test_structured_product_price_beats_container_numbers():
        html = """
        <div id="holder">£75</div>
        <script type="application/ld+json">
        {"@type":"Product","name":"Fridge","offers":{"@type":"Offer","price":"329.00","priceCurrency":"GBP"}}
        </script>
        """
        assert extract_structured_price(html) == 329.0
        assert find_price_selectors(html)[0] == "__jsonld_product_offer__"


def test_collection_page_does_not_return_first_product_price():
        html = """
        <script type="application/ld+json">
        {"@type":"ItemList","itemListElement":[
            {"item":{"@type":"Product","offers":{"price":"449.00"}}},
            {"item":{"@type":"Product","offers":{"price":"1499.00"}}}
        ]}
        </script>
        """
        assert extract_structured_price(html) is None
        assert is_collection_page(html) is True
        assert find_price_selectors(html) == []
