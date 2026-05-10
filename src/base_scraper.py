from __future__ import annotations

import argparse
import csv
import html
import json
import time
import urllib.parse
import urllib.robotparser
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

import requests
from bs4 import BeautifulSoup


DEFAULT_USER_AGENT = (
    "beverage-bottlebot/0.1 "
    "(dataset research; polite scraper; contact: local-user)"
)


@dataclass(slots=True)
class SakeProduct:
    source_site: str
    source_url: str
    final_url: str
    name: str | None = None
    brand: str | None = None
    brewery: str | None = None
    category: str | None = None
    taste_profile: str | None = None
    tasting_notes: str | None = None
    food_pairing: str | None = None
    serving_temperature: str | None = None
    alcohol: str | None = None
    rpr: str | None = None
    smv: str | None = None
    acidity: str | None = None
    region: str | None = None
    prefecture: str | None = None
    description: str | None = None
    price: str | None = None
    image_url: str | None = None
    raw_jsonld: str | None = None


class BaseScraper:
    """Small shared toolkit for polite product scraping and export."""

    site_name = "generic"
    base_url = ""

    def __init__(
        self,
        *,
        delay_seconds: float = 1.0,
        timeout_seconds: float = 30.0,
        user_agent: str = DEFAULT_USER_AGENT,
        respect_robots: bool = True,
    ) -> None:
        self.delay_seconds = delay_seconds
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent
        self.respect_robots = respect_robots
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self._last_request_at = 0.0
        self._robots: urllib.robotparser.RobotFileParser | None = None

    @property
    def netloc(self) -> str:
        return urllib.parse.urlparse(self.base_url).netloc

    def fetch(self, url: str) -> requests.Response:
        if self.respect_robots and not self.can_fetch(url):
            raise PermissionError(f"robots.txt disallows fetching {url}")

        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)

        response = self.session.get(url, timeout=self.timeout_seconds)
        self._last_request_at = time.monotonic()
        response.raise_for_status()
        return response

    def can_fetch(self, url: str) -> bool:
        if self._robots is None:
            self._robots = urllib.robotparser.RobotFileParser()
            self._robots.set_url(urllib.parse.urljoin(self.base_url, "/robots.txt"))
            try:
                self._robots.read()
            except Exception:
                return True
        return self._robots.can_fetch(self.user_agent, url)

    def discover_urls(self, limit: int | None = None) -> list[str]:
        raise NotImplementedError

    def parse_product(self, url: str) -> SakeProduct:
        raise NotImplementedError

    def scrape(self, urls: Sequence[str] | None = None, limit: int | None = None) -> list[SakeProduct]:
        product_urls = list(urls or self.discover_urls(limit=limit))
        if limit is not None:
            product_urls = product_urls[:limit]

        rows: list[SakeProduct] = []
        for url in product_urls:
            try:
                rows.append(self.parse_product(url))
            except Exception as exc:
                rows.append(
                    SakeProduct(
                        source_site=self.site_name,
                        source_url=url,
                        final_url=url,
                        name=None,
                        description=f"SCRAPE_ERROR: {type(exc).__name__}: {exc}",
                    )
                )
        return rows

    def export(self, rows: Sequence[SakeProduct], output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.suffix.lower() in {".xlsx", ".xlsm"}:
            return self._export_xlsx(rows, output_path)
        return self._export_csv(rows, output_path)

    def _export_xlsx(self, rows: Sequence[SakeProduct], output_path: Path) -> Path:
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = self.site_name[:31]

        headers = list(asdict(rows[0]).keys()) if rows else list(SakeProduct.__dataclass_fields__)
        sheet.append(headers)
        for row in rows:
            sheet.append([asdict(row).get(header) for header in headers])

        for column in sheet.columns:
            max_length = max(len(str(cell.value or "")) for cell in column)
            sheet.column_dimensions[column[0].column_letter].width = min(max(max_length + 2, 12), 55)

        workbook.save(output_path)
        return output_path

    def _export_csv(self, rows: Sequence[SakeProduct], output_path: Path) -> Path:
        headers = list(asdict(rows[0]).keys()) if rows else list(SakeProduct.__dataclass_fields__)
        with output_path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                writer.writerow(asdict(row))
        return output_path

    def sitemap_urls(self, sitemap_url: str | None = None) -> list[str]:
        sitemap_url = sitemap_url or urllib.parse.urljoin(self.base_url, "/sitemap.xml")
        seen: set[str] = set()
        urls: list[str] = []
        queue = [sitemap_url]

        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)

            try:
                response = self.fetch(current)
            except Exception:
                continue

            try:
                root = ET.fromstring(response.content)
            except ET.ParseError:
                continue

            namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            child_sitemaps = [
                node.text.strip()
                for node in root.findall(".//sm:sitemap/sm:loc", namespace)
                if node.text and node.text.strip()
            ]
            if child_sitemaps:
                queue.extend(child_sitemaps)
                continue

            urls.extend(
                node.text.strip()
                for node in root.findall(".//sm:url/sm:loc", namespace)
                if node.text and node.text.strip()
            )

        return dedupe(urls)


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = html.unescape(value)
    cleaned = " ".join(cleaned.replace("\xa0", " ").split())
    return cleaned or None


def dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            output.append(value)
    return output


def jsonld_objects(soup: BeautifulSoup) -> list[dict]:
    objects: list[dict] = []
    for script in soup.select('script[type="application/ld+json"]'):
        payload = clean_text(script.string)
        if not payload:
            continue
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            objects.extend(item for item in parsed if isinstance(item, dict))
        elif isinstance(parsed, dict):
            graph = parsed.get("@graph")
            if isinstance(graph, list):
                objects.extend(item for item in graph if isinstance(item, dict))
            objects.append(parsed)
    return objects


def first_meta(soup: BeautifulSoup, *selectors: str) -> str | None:
    for selector in selectors:
        tag = soup.select_one(selector)
        if tag:
            return clean_text(tag.get("content") or tag.get_text(" ", strip=True))
    return None


def build_cli(scraper_cls: type[BaseScraper]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"Scrape {scraper_cls.site_name} sake products.")
    parser.add_argument("--output", default=f"data/{scraper_cls.site_name}.xlsx")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--url", action="append", dest="urls", help="Specific product URL to scrape.")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between requests.")
    parser.add_argument("--ignore-robots", action="store_true", help="Skip robots.txt checks.")
    return parser


def run_cli(scraper_cls: type[BaseScraper]) -> None:
    parser = build_cli(scraper_cls)
    args = parser.parse_args()
    scraper = scraper_cls(delay_seconds=args.delay, respect_robots=not args.ignore_robots)
    rows = scraper.scrape(urls=args.urls, limit=args.limit)
    output = scraper.export(rows, Path(args.output))
    print(f"Wrote {len(rows)} rows to {output}")
