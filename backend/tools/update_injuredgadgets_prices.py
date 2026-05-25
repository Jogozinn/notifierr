from __future__ import annotations

import argparse
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import httpx

from backend.config import BASE_DIR, DEFAULT_REPAIR_VALUES_PATH


logger = logging.getLogger("injuredgadgets_updater")

DEFAULT_SOURCES_PATH = BASE_DIR / "data" / "injuredgadgets_sources.json"
DEFAULT_RAW_OUTPUT_PATH = BASE_DIR / "data" / "injuredgadgets_parts_raw.json"
DEFAULT_HTML_ROOT = BASE_DIR / "data" / "injuredgadgets_html"
DEFAULT_DELAY_SECONDS = 3.0
MAX_PAGES_PER_URL = 25

PART_CATEGORIES = [
    "screen_budget",
    "screen_safe",
    "screen_premium",
    "battery",
    "back_glass",
    "camera_lens",
    "charging_port",
]

SCREEN_ACCESSORY_TERMS = [
    "protector",
    "tempered glass",
    "privacy",
    "film",
    "adhesive",
    "gasket",
    "tape",
    "oca",
    "tool",
    "fixture",
    "mold",
    "screw",
    "cable",
    "flex",
    "case",
    "cover",
    "pack",
    "bundle",
]

GENERAL_ACCESSORY_TERMS = [
    "protector",
    "tempered glass",
    "privacy",
    "adhesive",
    "gasket",
    "tool",
    "screw",
    "case",
    "pack",
    "bundle",
]


@dataclass(frozen=True)
class Product:
    source_model: str
    name: str
    price: float
    product_url: str | None = None
    stock_text: str | None = None
    stock_count: int | None = None
    source_url: str | None = None
    intended_part_type: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_model": self.source_model,
            "name": self.name,
            "price": self.price,
            "stock_text": self.stock_text,
            "stock_count": self.stock_count,
            "product_url": self.product_url,
            "source_url": self.source_url,
            "intended_part_type": self.intended_part_type,
        }


@dataclass
class FetchResult:
    products: list[Product]
    errors: dict[str, list[dict[str, Any]]]


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.make_html_folders:
        make_html_folders(args.html_root, load_json(args.sources))
        return 0

    sources = load_json(args.sources)
    current_repair_values = load_json(args.repair_values)
    if args.from_html:
        fetch_result = fetch_all_products_from_html(args.from_html)
    else:
        fetch_result = fetch_all_products(
            sources,
            delay_seconds=args.delay,
            use_browser=args.browser,
            headless=args.headless,
            max_pages_per_model=args.max_pages_per_model,
        )
    products = fetch_result.products
    source_models = [source["model"] for source in sources.get("models", [])]
    parts_by_model, audit_by_model = choose_all_parts(products, source_models)
    for model, errors in fetch_result.errors.items():
        audit_by_model.setdefault(model, {})
        audit_by_model[model]["_errors"] = errors
    next_repair_values = merge_repair_values(current_repair_values, sources, parts_by_model)
    raw_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "InjuredGadgets public category pages",
        "fetch_mode": "html" if args.from_html else "browser" if args.browser else "httpx",
        "sources_path": str(args.sources),
        "html_root": str(args.from_html) if args.from_html else None,
        "products": [product.as_dict() for product in products],
        "products_by_model": group_products_for_audit(products),
        "errors": fetch_result.errors,
        "audit": audit_by_model,
        "repair_values_preview": next_repair_values,
    }

    logger.info("Fetched %s products across %s models", len(products), len(parts_by_model))
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    args.raw_output.write_text(json.dumps(raw_payload, indent=2), encoding="utf-8")
    logger.info("Wrote raw product/audit output to %s", args.raw_output)

    if args.write:
        args.repair_values.write_text(json.dumps(next_repair_values, indent=2), encoding="utf-8")
        logger.info("Updated repair values at %s", args.repair_values)
    else:
        logger.info("Dry run complete; repair_values.json was not changed")
        print(json.dumps({"parts_by_model": parts_by_model, "audit": audit_by_model}, indent=2))

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update repair_values.json from InjuredGadgets category pages.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Fetch and audit prices without updating repair_values.json.")
    mode.add_argument("--write", action="store_true", help="Fetch prices and update repair_values.json.")
    mode.add_argument("--make-html-folders", action="store_true", help="Create local HTML folder placeholders and exit.")
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES_PATH)
    parser.add_argument("--repair-values", type=Path, default=DEFAULT_REPAIR_VALUES_PATH)
    parser.add_argument("--raw-output", type=Path, default=DEFAULT_RAW_OUTPUT_PATH)
    parser.add_argument("--from-html", type=Path, default=None, help="Import saved InjuredGadgets HTML files from this folder.")
    parser.add_argument("--html-root", type=Path, default=DEFAULT_HTML_ROOT, help="Folder created by --make-html-folders.")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS, help="Delay between requests in seconds.")
    parser.add_argument("--browser", action="store_true", help="Use headed Playwright browser mode for public pages.")
    parser.add_argument("--headless", action="store_true", help="Run Playwright browser mode headless. Default is headed.")
    parser.add_argument(
        "--max-pages-per-model",
        type=int,
        default=None,
        help="Optional safety limit across paginated pages per model.",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def make_html_folders(html_root: Path, sources: dict[str, Any]) -> None:
    html_root.mkdir(parents=True, exist_ok=True)
    filenames = ["screen.html", "battery.html", "charging_port.html", "back_glass.html", "camera_lens.html"]
    for source in sources.get("models", []):
        model = source["model"]
        model_dir = html_root / model
        model_dir.mkdir(parents=True, exist_ok=True)
        readme = model_dir / "README.txt"
        if not readme.exists():
            readme.write_text(
                "Save public InjuredGadgets result pages for this model here.\n"
                "Use the matching filename for each part type, for example screen.html or battery.html.\n",
                encoding="utf-8",
            )
        for filename in filenames:
            path = model_dir / filename
            if not path.exists():
                path.write_text(
                    "<!-- Save the InjuredGadgets public result page HTML for this part type here. -->\n",
                    encoding="utf-8",
                )
    logger.info("Created local HTML import folders under %s", html_root)


def fetch_all_products_from_html(html_root: Path) -> FetchResult:
    products: list[Product] = []
    errors: dict[str, list[dict[str, Any]]] = {}
    if not html_root.exists():
        return FetchResult(
            products=[],
            errors={
                "_global": [
                    {
                        "url": str(html_root),
                        "status_code": None,
                        "message": "HTML import root does not exist; run --make-html-folders first",
                    }
                ]
            },
        )

    html_files = sorted(path for path in html_root.rglob("*") if path.suffix.lower() in {".html", ".htm"})
    logger.info("Importing %s saved HTML files from %s", len(html_files), html_root)
    for html_file in html_files:
        model = infer_model_from_html_path(html_root, html_file)
        if not model:
            errors.setdefault("_global", []).append(
                {
                    "url": str(html_file),
                    "status_code": None,
                    "message": "could not infer model from HTML path",
                }
            )
            continue
        try:
            html = html_file.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            errors.setdefault(model, []).append(
                {"url": str(html_file), "status_code": None, "message": f"could not read file: {exc}"}
            )
            continue
        if not html.strip() or "Save the InjuredGadgets public result page HTML" in html:
            logger.info("Skipping placeholder HTML file %s", html_file)
            continue
        intended_part_type = infer_part_type_from_filename(html_file.name)
        file_products = parse_products_from_html(
            html,
            model,
            html_file.resolve().as_uri(),
            intended_part_type=intended_part_type,
        )
        logger.info("Parsed %s products from saved HTML %s", len(file_products), html_file)
        products.extend(file_products)
    return FetchResult(products=dedupe_products(products), errors=errors)


def infer_model_from_html_path(html_root: Path, html_file: Path) -> str | None:
    try:
        relative = html_file.relative_to(html_root)
    except ValueError:
        return None
    if len(relative.parts) < 2:
        return None
    return relative.parts[0]


def infer_part_type_from_filename(filename: str) -> str | None:
    normalized = normalize_name(Path(filename).stem).replace("-", "_")
    mapping = {
        "battery": "battery",
        "charging_port": "charging_port",
        "charge_port": "charging_port",
        "dock_connector": "charging_port",
        "back_glass": "back_glass",
        "rear_glass": "back_glass",
        "housing": "back_glass",
        "camera_lens": "camera_lens",
        "lens_glass": "camera_lens",
        "screen": "screen",
        "display": "screen",
    }
    for key, value in mapping.items():
        if key in normalized:
            return value
    return None


def fetch_all_products(
    sources: dict[str, Any],
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    use_browser: bool = False,
    headless: bool = False,
    max_pages_per_model: int | None = None,
) -> FetchResult:
    if use_browser:
        return fetch_all_products_browser(sources, delay_seconds, headless, max_pages_per_model)
    return fetch_all_products_httpx(sources, delay_seconds, max_pages_per_model)


def fetch_all_products_httpx(
    sources: dict[str, Any],
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    max_pages_per_model: int | None = None,
) -> FetchResult:
    products: list[Product] = []
    errors: dict[str, list[dict[str, Any]]] = {}
    with httpx.Client(
        timeout=25,
        follow_redirects=True,
        headers={"User-Agent": "Notifierr parts price updater/0.1 (manual local script)"},
    ) as client:
        first_request = True
        for source in sources.get("models", []):
            model = source["model"]
            pages_for_model = 0
            for url in source.get("urls", []):
                if not url:
                    continue
                if max_pages_per_model is not None and pages_for_model >= max_pages_per_model:
                    logger.info("Reached max page limit for %s", model)
                    break
                logger.info("Fetching %s from %s", model, url)
                for page_url, page_html, error in fetch_paginated_pages(client, url, delay_seconds, first_request):
                    first_request = False
                    if error:
                        errors.setdefault(model, []).append(error)
                        continue
                    pages_for_model += 1
                    page_products = parse_products_from_html(page_html, model, page_url)
                    logger.info("Parsed %s products from %s", len(page_products), page_url)
                    products.extend(page_products)
                    if max_pages_per_model is not None and pages_for_model >= max_pages_per_model:
                        logger.info("Reached max page limit for %s", model)
                        break
    return FetchResult(products=products, errors=errors)


def fetch_paginated_pages(
    client: httpx.Client,
    start_url: str,
    delay_seconds: float,
    first_request: bool = True,
) -> Iterable[tuple[str, str, dict[str, Any] | None]]:
    seen: set[str] = set()
    next_url: str | None = start_url
    page_count = 0

    while next_url and next_url not in seen and page_count < MAX_PAGES_PER_URL:
        if not first_request or page_count > 0:
            time.sleep(max(0, delay_seconds))
        seen.add(next_url)
        try:
            response = client.get(next_url)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            message = f"HTTP {status_code} for {next_url}"
            if status_code == 403:
                message = f"{message}; public page refused raw httpx fetch, retry manually with --browser"
            logger.warning(message)
            yield next_url, "", {"url": next_url, "status_code": status_code, "message": message}
            return
        except httpx.HTTPError as exc:
            message = f"{type(exc).__name__} for {next_url}: {exc}"
            logger.warning(message)
            yield next_url, "", {"url": next_url, "status_code": None, "message": message}
            return
        html = response.text
        yield next_url, html, None
        page_count += 1
        next_url = find_next_page_url(html, next_url)

    if page_count >= MAX_PAGES_PER_URL:
        logger.warning("Stopped pagination at max page limit for %s", start_url)


def parse_products_from_html(
    html: str,
    source_model: str,
    source_url: str,
    intended_part_type: str | None = None,
) -> list[Product]:
    soup = _soup(html)
    products = parse_json_ld_products(soup, source_model, source_url, intended_part_type)
    products.extend(parse_dom_products(soup, source_model, source_url, intended_part_type))
    return dedupe_products(products)


def parse_json_ld_products(
    soup: Any,
    source_model: str,
    source_url: str,
    intended_part_type: str | None = None,
) -> list[Product]:
    products: list[Product] = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or "")
        except json.JSONDecodeError:
            continue
        for item in _walk_json_ld(payload):
            if item.get("@type") != "Product":
                continue
            name = _clean_text(item.get("name"))
            offers = item.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            price = parse_price(offers.get("price") or offers.get("lowPrice"))
            if not name or price is None:
                continue
            products.append(
                Product(
                    source_model=source_model,
                    name=name,
                    price=price,
                    product_url=item.get("url") or offers.get("url"),
                    stock_text=_stock_text(str(item)),
                    stock_count=parse_stock_count(str(item)),
                    source_url=source_url,
                    intended_part_type=intended_part_type,
                )
            )
    return products


def parse_dom_products(
    soup: Any,
    source_model: str,
    source_url: str,
    intended_part_type: str | None = None,
) -> list[Product]:
    products: list[Product] = []
    candidates = soup.select(
        ".product, .product-item, .product-grid-item, .grid__item, li.item, [data-product-id], [class*=product]"
    )
    if not candidates:
        candidates = soup.select("a[href]")

    for candidate in candidates:
        text = _clean_text(candidate.get_text(" "))
        price = parse_price(text)
        if price is None:
            continue
        name = _extract_product_name(candidate)
        if not name:
            name = text
        product_url = _extract_product_url(candidate, source_url)
        products.append(
            Product(
                source_model=source_model,
                name=name,
                price=price,
                product_url=product_url,
                stock_text=_stock_text(text),
                stock_count=parse_stock_count(text),
                source_url=source_url,
                intended_part_type=intended_part_type,
            )
        )
    return products


def fetch_all_products_browser(
    sources: dict[str, Any],
    delay_seconds: float,
    headless: bool,
    max_pages_per_model: int | None,
) -> FetchResult:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "playwright is required for --browser mode. Run: pip install -r requirements.txt; python -m playwright install chromium"
        ) from exc

    products: list[Product] = []
    errors: dict[str, list[dict[str, Any]]] = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            for source in sources.get("models", []):
                model = source["model"]
                pages_for_model = 0
                for start_url in source.get("urls", []):
                    if not start_url:
                        continue
                    next_url: str | None = start_url
                    seen: set[str] = set()
                    while next_url and next_url not in seen:
                        if max_pages_per_model is not None and pages_for_model >= max_pages_per_model:
                            logger.info("Reached max page limit for %s", model)
                            break
                        seen.add(next_url)
                        logger.info("Browser fetching %s from %s", model, next_url)
                        try:
                            response = page.goto(next_url, wait_until="domcontentloaded", timeout=45000)
                            status_code = response.status if response else None
                            if status_code and status_code >= 400:
                                message = f"browser received HTTP {status_code} for {next_url}"
                                logger.warning(message)
                                errors.setdefault(model, []).append(
                                    {"url": next_url, "status_code": status_code, "message": message}
                                )
                                break
                            wait_for_product_content(page, PlaywrightTimeoutError)
                            page.wait_for_timeout(int(max(0, delay_seconds) * 1000))
                            page_products = extract_products_from_browser_page(page, model, next_url)
                            logger.info("Browser parsed %s products from %s", len(page_products), next_url)
                            products.extend(page_products)
                            pages_for_model += 1
                            next_url = find_next_page_url_browser(page, next_url)
                            if next_url:
                                time.sleep(max(0, delay_seconds))
                        except Exception as exc:
                            message = f"{type(exc).__name__} for {next_url}: {exc}"
                            logger.warning(message)
                            errors.setdefault(model, []).append(
                                {"url": next_url, "status_code": None, "message": message}
                            )
                            break
        finally:
            browser.close()
    return FetchResult(products=dedupe_products(products), errors=errors)


def wait_for_product_content(page: Any, timeout_error_type: Any) -> None:
    selectors = [
        ".product",
        ".product-item",
        ".product-grid-item",
        ".grid__item",
        "[data-product-id]",
        "[class*=product]",
    ]
    for selector in selectors:
        try:
            page.wait_for_selector(selector, timeout=8000)
            return
        except timeout_error_type:
            continue
    logger.warning("No standard product selector appeared; extracting from visible links as fallback")


def extract_products_from_browser_page(page: Any, source_model: str, source_url: str) -> list[Product]:
    records = page.evaluate(
        """
        () => {
          const selectors = [
            '.product',
            '.product-item',
            '.product-grid-item',
            '.grid__item',
            'li.item',
            '[data-product-id]',
            '[class*=product]'
          ];
          let cards = [];
          for (const selector of selectors) {
            cards = Array.from(document.querySelectorAll(selector));
            if (cards.length) break;
          }
          if (!cards.length) cards = Array.from(document.querySelectorAll('a[href]'));

          const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
          const textFor = (root, selectors) => {
            for (const selector of selectors) {
              const node = root.querySelector && root.querySelector(selector);
              const text = clean(node && node.textContent);
              if (text) return text;
            }
            return '';
          };

          return cards.map((card) => {
            const anchor = card.matches && card.matches('a[href]') ? card : card.querySelector && card.querySelector('a[href]');
            const name = textFor(card, ['.product-title', '.product-name', '.card-title', '.title', 'h1', 'h2', 'h3', 'a']) || clean(card.textContent);
            const price = textFor(card, ['.price', '.product-price', '[class*=price]', '[data-price]']) || clean(card.textContent);
            const stock = textFor(card, ['.stock', '.availability', '[class*=stock]', '[class*=availability]']) || clean(card.textContent);
            return {
              name,
              priceText: price,
              stockText: stock,
              productUrl: anchor ? anchor.href : null,
              text: clean(card.textContent)
            };
          });
        }
        """
    )
    products: list[Product] = []
    for record in records:
        name = _clean_text(record.get("name"))
        price = parse_price(record.get("priceText") or record.get("text"))
        if not name or price is None:
            continue
        stock_text = _stock_text(record.get("stockText") or record.get("text"))
        products.append(
            Product(
                source_model=source_model,
                name=name,
                price=price,
                stock_text=stock_text,
                stock_count=parse_stock_count(stock_text or ""),
                product_url=record.get("productUrl"),
                source_url=source_url,
                intended_part_type=None,
            )
        )
    return dedupe_products(products)


def find_next_page_url_browser(page: Any, current_url: str) -> str | None:
    current_host = urlparse(current_url).netloc
    next_url = page.evaluate(
        """
        () => {
          const anchors = Array.from(document.querySelectorAll('a[href]'));
          for (const anchor of anchors) {
            const label = (anchor.textContent || '').replace(/\\s+/g, ' ').trim().toLowerCase();
            const rel = (anchor.getAttribute('rel') || '').toLowerCase();
            const classes = (anchor.getAttribute('class') || '').toLowerCase();
            const aria = (anchor.getAttribute('aria-label') || '').toLowerCase();
            if (rel.includes('next') || classes.includes('next') || aria.includes('next') || label === 'next' || label === '>' || label === '»') {
              return anchor.href;
            }
          }
          return null;
        }
        """
    )
    if next_url and urlparse(next_url).netloc == current_host:
        return next_url
    return None


def find_next_page_url(html: str, current_url: str) -> str | None:
    soup = _soup(html)
    selectors = ['a[rel="next"]', "a.next", ".pagination a", "a"]
    current_host = urlparse(current_url).netloc

    for selector in selectors:
        for anchor in soup.select(selector):
            label = _clean_text(anchor.get_text(" ")).lower()
            rel = " ".join(anchor.get("rel") or []).lower()
            if "next" not in label and "next" not in rel and label not in {">", ">", "»"}:
                continue
            href = anchor.get("href")
            if not href:
                continue
            next_url = urljoin(current_url, href)
            if urlparse(next_url).netloc == current_host:
                return next_url
    return None


def dedupe_products(products: list[Product]) -> list[Product]:
    seen: set[tuple[str, str, float]] = set()
    deduped: list[Product] = []
    for product in products:
        key = (product.source_model, product.product_url or product.name.lower(), product.price)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(product)
    return deduped


def classify_product_name(name: str) -> str | None:
    normalized = normalize_name(name)
    if not normalized or is_ignored_product(normalized):
        return None

    if is_screen(normalized):
        if any(term in normalized for term in ("service pack", "premium refurbished", "premium oled")):
            return "screen_premium"
        if "soft oled" in normalized:
            return "screen_safe"
        if any(term in normalized for term in ("incell", "in-cell", "lcd", "hard oled")):
            return "screen_budget"
        return None

    if "battery" in normalized:
        return "battery"
    if (
        "back glass" in normalized
        or "rear glass" in normalized
        or "housing" in normalized
        or "back cover" in normalized
        or "rear cover" in normalized
    ):
        return "back_glass"
    if "camera lens" in normalized or "lens glass" in normalized:
        return "camera_lens"
    if ("charging port" in normalized or "charge port" in normalized or "dock connector" in normalized) and not _has_any(
        normalized, ["tester", "test cable"]
    ):
        return "charging_port"

    return None


def classify_product(product: Product) -> str | None:
    base_category = classify_product_name(product.name)
    hint = product.intended_part_type
    if not hint:
        return base_category
    normalized = normalize_name(product.name)
    if is_ignored_product(normalized):
        return None
    if hint == "screen":
        return base_category if base_category and base_category.startswith("screen_") else None
    if hint in PART_CATEGORIES:
        if base_category is None:
            return hint
        return hint if base_category == hint else None
    return base_category


def product_hint_matches_category(product: Product, category: str) -> bool:
    hint = product.intended_part_type
    if not hint:
        return False
    if hint == "screen":
        return category.startswith("screen_")
    return hint == category


def choose_all_parts(
    products: list[Product],
    model_names: list[str] | None = None,
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, Any]]]:
    grouped: dict[str, list[Product]] = {}
    for product in products:
        grouped.setdefault(product.source_model, []).append(product)

    parts_by_model: dict[str, dict[str, float]] = {}
    audit_by_model: dict[str, dict[str, Any]] = {}
    ordered_models = list(model_names or [])
    for model in grouped:
        if model not in ordered_models:
            ordered_models.append(model)

    for model in ordered_models:
        model_products = grouped.get(model, [])
        parts, audit = choose_parts_for_model(model_products)
        parts_by_model[model] = parts
        audit_by_model[model] = audit
    return parts_by_model, audit_by_model


def group_products_for_audit(products: list[Product]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for product in products:
        grouped.setdefault(product.source_model, []).append(product.as_dict())
    return grouped


def choose_parts_for_model(products: list[Product]) -> tuple[dict[str, float], dict[str, Any]]:
    by_category: dict[str, list[Product]] = {category: [] for category in PART_CATEGORIES}
    for product in products:
        category = classify_product(product)
        if category:
            by_category[category].append(product)

    parts: dict[str, float] = {}
    audit: dict[str, Any] = {}
    for category, candidates in by_category.items():
        chosen = choose_product_for_category(category, candidates)
        if not chosen:
            audit[category] = {"chosen": None, "reason": "no matching public product found"}
            continue
        parts[category] = chosen.price
        audit[category] = {
            "chosen": chosen.as_dict(),
            "chosen_price": chosen.price,
            "reason": choice_reason(category, chosen, candidates),
            "candidate_count": len(candidates),
        }
    return parts, audit


def choose_product_for_category(category: str, candidates: list[Product]) -> Product | None:
    available = [candidate for candidate in candidates if candidate.price > 0]
    if not available:
        return None
    preferred = [candidate for candidate in available if product_hint_matches_category(candidate, category)]
    if preferred:
        available = preferred
    if category == "screen_safe":
        normal_soft_oled = [candidate for candidate in available if "soft oled qrt" not in normalize_name(candidate.name)]
        return min(normal_soft_oled or available, key=lambda product: product.price)
    return min(available, key=lambda product: product.price)


def choice_reason(category: str, chosen: Product, candidates: list[Product]) -> str:
    if category == "screen_safe" and "soft oled qrt" in normalize_name(chosen.name):
        return "selected lowest Soft OLED QRT because no normal Soft OLED candidate was available"
    if category == "screen_safe":
        return "selected lowest normal Soft OLED screen candidate"
    if category.startswith("screen"):
        return f"selected lowest priced {category.replace('_', ' ')} screen candidate"
    return f"selected lowest priced {category.replace('_', ' ')} candidate"


def merge_repair_values(
    current: dict[str, Any],
    sources: dict[str, Any],
    parts_by_model: dict[str, dict[str, float]],
) -> dict[str, Any]:
    default_min_profit = current.get("default", {}).get("min_profit", 75)
    ordered_models = ["default"] + [source["model"] for source in sources.get("models", [])]
    for model in current:
        if model not in ordered_models:
            ordered_models.append(model)

    merged: dict[str, Any] = {}
    for model in ordered_models:
        existing = current.get(model, {})
        existing_parts = normalize_parts(existing)
        fetched_parts = parts_by_model.get(model, {})
        parts = {**existing_parts, **fetched_parts}
        entry = {
            "resale_value": existing.get("resale_value", 0),
            "risk_buffer": existing.get("risk_buffer", current.get("default", {}).get("risk_buffer", 50)),
            "min_profit": existing.get("min_profit", default_min_profit),
            "estimated_parts_cost": existing.get("estimated_parts_cost", parts.get("screen_safe") or parts.get("screen_budget") or 0),
            "parts": parts,
        }
        entry.update(legacy_part_keys(parts, existing))
        merged[model] = entry
    return merged


def normalize_parts(entry: dict[str, Any]) -> dict[str, float]:
    parts = {key: float(value) for key, value in (entry.get("parts") or {}).items() if value is not None}
    legacy_map = {
        "screen_budget": "screen_cost",
        "screen_safe": "screen_cost",
        "screen_premium": "screen_cost",
        "battery": "battery_cost",
        "back_glass": "back_glass_cost",
        "camera_lens": "camera_lens_cost",
        "charging_port": "charging_port_cost",
    }
    for part_key, legacy_key in legacy_map.items():
        if part_key not in parts and entry.get(legacy_key) is not None:
            parts[part_key] = float(entry[legacy_key])
    return parts


def legacy_part_keys(parts: dict[str, float], existing: dict[str, Any]) -> dict[str, float]:
    return {
        "screen_cost": float(parts.get("screen_safe") or parts.get("screen_budget") or existing.get("screen_cost") or 0),
        "battery_cost": float(parts.get("battery") or existing.get("battery_cost") or 0),
        "back_glass_cost": float(parts.get("back_glass") or existing.get("back_glass_cost") or 0),
        "camera_lens_cost": float(parts.get("camera_lens") or existing.get("camera_lens_cost") or 0),
        "charging_port_cost": float(parts.get("charging_port") or existing.get("charging_port_cost") or 0),
    }


def is_ignored_product(normalized_name: str) -> bool:
    if "service pack" in normalized_name and is_screen(normalized_name):
        return False
    if "charging port" in normalized_name or "charge port" in normalized_name or "dock connector" in normalized_name:
        ignored_terms = [term for term in GENERAL_ACCESSORY_TERMS if term not in {"pack", "bundle"}]
        return _has_any(normalized_name, ignored_terms)
    if is_screen(normalized_name):
        return _has_any(normalized_name, SCREEN_ACCESSORY_TERMS)
    return _has_any(normalized_name, GENERAL_ACCESSORY_TERMS + ["cable", "flex"])


def is_screen(normalized_name: str) -> bool:
    return _has_any(normalized_name, ["screen", "display", "lcd", "oled", "digitizer"])


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.lower()).strip()


def parse_price(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value)
    match = re.search(r"\$\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)", text)
    if not match:
        match = re.search(r"\b([0-9]+(?:,[0-9]{3})*\.[0-9]{1,2})\b", text)
    if not match:
        return None
    return round(float(match.group(1).replace(",", "")), 2)


def parse_stock_count(text: str) -> int | None:
    match = re.search(r"(?:stock|available|in stock)\D{0,12}(\d+)|(\d+)\s+(?:in stock|available)", text, re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1) or match.group(2))


def _stock_text(text: str) -> str | None:
    cleaned = _clean_text(text)
    patterns = [
        r"(?:stock|available|availability|in stock)[^|]{0,80}",
        r"\d+\s+(?:in stock|available)",
        r"out of stock",
    ]
    for pattern in patterns:
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if match:
            return _clean_text(match.group(0))
    return None


def _walk_json_ld(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, dict):
        yield payload
        graph = payload.get("@graph")
        if graph:
            yield from _walk_json_ld(graph)
    elif isinstance(payload, list):
        for item in payload:
            yield from _walk_json_ld(item)


def _extract_product_name(candidate: Any) -> str:
    selectors = [".product-title", ".product-name", ".card-title", ".title", "h1", "h2", "h3", "a"]
    for selector in selectors:
        node = candidate.select_one(selector) if hasattr(candidate, "select_one") else None
        if node:
            text = _clean_text(node.get_text(" "))
            if text:
                return text
    return ""


def _extract_product_url(candidate: Any, source_url: str) -> str | None:
    anchor = candidate if getattr(candidate, "name", None) == "a" else candidate.select_one("a[href]")
    if not anchor:
        return None
    href = anchor.get("href")
    return urljoin(source_url, href) if href else None


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _has_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def _soup(html: str) -> Any:
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise RuntimeError("beautifulsoup4 is required for the InjuredGadgets updater") from exc
    return BeautifulSoup(html, "html.parser")


if __name__ == "__main__":
    raise SystemExit(main())
