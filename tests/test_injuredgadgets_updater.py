from backend.tools.update_injuredgadgets_prices import (
    Product,
    choose_parts_for_model,
    classify_product_name,
    fetch_all_products_from_html,
    fetch_paginated_pages,
    infer_part_type_from_filename,
    make_html_folders,
    merge_repair_values,
    parse_price,
    parse_stock_count,
)
import httpx


def test_screen_classification_tiers():
    assert classify_product_name("iPhone 14 Pro Incell LCD Screen Assembly") == "screen_budget"
    assert classify_product_name("iPhone 14 Pro Hard OLED Screen Replacement") == "screen_budget"
    assert classify_product_name("iPhone 14 Pro Soft OLED Screen Assembly") == "screen_safe"
    assert classify_product_name("iPhone 14 Pro Soft OLED QRT Screen Assembly") == "screen_safe"
    assert classify_product_name("iPhone 14 Pro Premium OLED Screen Assembly") == "screen_premium"
    assert classify_product_name("iPhone 14 Pro Premium Refurbished Screen") == "screen_premium"
    assert classify_product_name("iPhone 14 Pro Service Pack Display") == "screen_premium"


def test_accessories_are_ignored():
    assert classify_product_name("iPhone 14 Pro Tempered Glass Screen Protector") is None
    assert classify_product_name("iPhone 14 Pro Screen Adhesive Gasket") is None
    assert classify_product_name("iPhone 14 Pro Repair Tool Kit") is None
    assert classify_product_name("iPhone 14 Pro LCD Flex Cable") is None


def test_non_screen_part_classification():
    assert classify_product_name("iPhone 14 Pro Battery Replacement") == "battery"
    assert classify_product_name("iPhone 14 Pro Back Glass Replacement") == "back_glass"
    assert classify_product_name("iPhone 14 Pro Housing Assembly") == "back_glass"
    assert classify_product_name("iPhone 14 Pro Rear Cover") == "back_glass"
    assert classify_product_name("iPhone 14 Pro Camera Lens Glass") == "camera_lens"
    assert classify_product_name("iPhone 14 Pro Charging Port Flex Cable") == "charging_port"


def test_choose_parts_prefers_normal_soft_oled_over_qrt():
    products = [
        Product("iPhone 14 Pro", "iPhone 14 Pro Soft OLED QRT Screen", 95),
        Product("iPhone 14 Pro", "iPhone 14 Pro Soft OLED Screen", 110),
        Product("iPhone 14 Pro", "iPhone 14 Pro Incell LCD Screen", 42),
        Product("iPhone 14 Pro", "iPhone 14 Pro Battery", 18),
    ]

    parts, audit = choose_parts_for_model(products)

    assert parts["screen_budget"] == 42
    assert parts["screen_safe"] == 110
    assert parts["battery"] == 18
    assert "normal Soft OLED" in audit["screen_safe"]["reason"]


def test_choose_parts_uses_qrt_when_only_safe_screen_available():
    products = [
        Product("iPhone 14 Pro", "iPhone 14 Pro Soft OLED QRT Screen", 95),
    ]

    parts, audit = choose_parts_for_model(products)

    assert parts["screen_safe"] == 95
    assert "Soft OLED QRT" in audit["screen_safe"]["reason"]


def test_merge_repair_values_preserves_resale_and_risk_buffer():
    current = {
        "default": {"resale_value": 250, "risk_buffer": 50, "min_profit": 75},
        "iPhone 14 Pro": {
            "resale_value": 610,
            "risk_buffer": 85,
            "min_profit": 90,
            "screen_cost": 200,
            "battery_cost": 70,
        },
    }
    sources = {"models": [{"model": "iPhone 14 Pro", "urls": []}]}
    parts_by_model = {
        "iPhone 14 Pro": {
            "screen_budget": 44,
            "screen_safe": 105,
            "screen_premium": 155,
            "battery": 21,
        }
    }

    merged = merge_repair_values(current, sources, parts_by_model)

    assert merged["iPhone 14 Pro"]["resale_value"] == 610
    assert merged["iPhone 14 Pro"]["risk_buffer"] == 85
    assert merged["iPhone 14 Pro"]["min_profit"] == 90
    assert merged["iPhone 14 Pro"]["parts"]["screen_safe"] == 105
    assert merged["iPhone 14 Pro"]["screen_cost"] == 105
    assert merged["iPhone 14 Pro"]["battery_cost"] == 21


def test_parse_price_and_stock_count():
    assert parse_price("$1,234.56") == 1234.56
    assert parse_price("Sale $49.99 12 in stock") == 49.99
    assert parse_stock_count("Stock: 17 available") == 17
    assert parse_stock_count("22 in stock") == 22


def test_httpx_403_is_returned_as_page_error():
    class ClientStub:
        def get(self, url):
            request = httpx.Request("GET", url)
            response = httpx.Response(403, request=request)
            raise httpx.HTTPStatusError("forbidden", request=request, response=response)

    pages = list(fetch_paginated_pages(ClientStub(), "https://example.com/parts", delay_seconds=0))

    assert len(pages) == 1
    page_url, html, error = pages[0]
    assert page_url == "https://example.com/parts"
    assert html == ""
    assert error["status_code"] == 403
    assert "--browser" in error["message"]


def test_part_type_inference_from_filenames():
    assert infer_part_type_from_filename("screen.html") == "screen"
    assert infer_part_type_from_filename("battery.html") == "battery"
    assert infer_part_type_from_filename("charging_port.html") == "charging_port"
    assert infer_part_type_from_filename("back_glass.html") == "back_glass"
    assert infer_part_type_from_filename("camera_lens.html") == "camera_lens"
    assert infer_part_type_from_filename("unknown.html") is None


def test_recursive_html_folder_import_uses_model_and_filename_hint(tmp_path):
    model_dir = tmp_path / "iPhone 14 Pro"
    model_dir.mkdir()
    (model_dir / "screen.html").write_text(
        """
        <html><body>
          <div class="product-card">
            <a href="/screen-safe">iPhone 14 Pro Soft OLED Screen Assembly</a>
            <span class="price">$109.99</span>
            <span class="stock">Stock: 12 available</span>
          </div>
          <div class="product-card">
            <a href="/screen-premium">iPhone 14 Pro Premium OLED Screen Assembly</a>
            <span class="price">$159.99</span>
          </div>
          <div class="product-card">
            <a href="/protector">iPhone 14 Pro Tempered Glass Screen Protector</a>
            <span class="price">$4.99</span>
          </div>
        </body></html>
        """,
        encoding="utf-8",
    )
    (model_dir / "battery.html").write_text(
        """
        <html><body>
          <div class="product-card">
            <a href="/battery">Replacement Power Cell Assembly</a>
            <span class="price">$18.50</span>
          </div>
        </body></html>
        """,
        encoding="utf-8",
    )

    result = fetch_all_products_from_html(tmp_path)
    parts, audit = choose_parts_for_model(result.products)

    assert result.errors == {}
    assert len(result.products) == 4
    assert parts["screen_safe"] == 109.99
    assert parts["screen_premium"] == 159.99
    assert parts["battery"] == 18.50
    assert audit["battery"]["chosen"]["intended_part_type"] == "battery"
    assert "screen_budget" not in parts


def test_make_html_folders_creates_placeholders(tmp_path):
    sources = {"models": [{"model": "iPhone 15 Pro", "urls": []}]}

    make_html_folders(tmp_path, sources)

    assert (tmp_path / "iPhone 15 Pro" / "screen.html").exists()
    assert (tmp_path / "iPhone 15 Pro" / "battery.html").exists()
    assert (tmp_path / "iPhone 15 Pro" / "charging_port.html").exists()
    assert (tmp_path / "iPhone 15 Pro" / "back_glass.html").exists()
    assert (tmp_path / "iPhone 15 Pro" / "camera_lens.html").exists()
