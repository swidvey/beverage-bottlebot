from __future__ import annotations

import json
import re
import urllib.parse

from bs4 import BeautifulSoup

try:
    from .base_scraper import (
        BaseScraper,
        SakeProduct,
        clean_text,
        dedupe,
        first_meta,
        jsonld_objects,
        run_cli,
    )
except ImportError:
    from base_scraper import (
        BaseScraper,
        SakeProduct,
        clean_text,
        dedupe,
        first_meta,
        jsonld_objects,
        run_cli,
    )


FIELD_MARKERS = {
    "food_pairing": ["Food Pairing", "Food pairings", "Pairing"],
    "serving_temperature": ["Recommended Serving Temperature", "Serving Temp", "Serving Temperature"],
    "alcohol": ["Alcohol Content", "Alcohol"],
    "rpr": ["RPR"],
    "smv": ["SMV"],
    "acidity": ["Acidity"],
    "category": ["Category"],
    "brewery": ["Brewery"],
    "region": ["Region"],
    "prefecture": ["Prefecture"],
    "taste_profile": ["Taste Profile"],
    "tasting_notes": ["Tasting Notes"],
}

TASTE_TAGS = {
    "balanced",
    "classic",
    "cloudy",
    "easygoing",
    "flavored",
    "fruity & elegant",
    "light & dry",
    "rich & savory",
    "smooth & dry",
    "sweet & juicy",
    "umami",
}

FOOD_TAG_PREFIXES = ("Sake for ",)
TEMP_TAG_RE = re.compile(r"^(Cold|Room Temp|Warm)(?:\s*\([^)]+\))?$", re.IGNORECASE)

SECTION_STOP_WORDS = {
    "Add to Cart",
    "Quantity",
    "Related Products",
    "You may also like",
    "Customer Reviews",
    "Reviews",
    "Sold Out",
    "Notify Me",
}


class TippsySakeScraper(BaseScraper):
    site_name = "tippsy_sake"
    base_url = "https://www.tippsysake.com"

    def discover_urls(self, limit: int | None = None) -> list[str]:
        product_urls = [
            url
            for url in self.sitemap_urls()
            if self._looks_like_product_url(url)
        ]
        if product_urls:
            return product_urls[:limit] if limit is not None else product_urls

        collection_urls = self._discover_from_collections(limit=limit)
        return collection_urls[:limit] if limit is not None else collection_urls

    def parse_product(self, url: str) -> SakeProduct:
        response = self.fetch(url)
        soup = BeautifulSoup(response.text, "html.parser")
        jsonld = self._product_jsonld(soup)
        lines = self._page_lines(soup)
        tags = self._shopify_tags(response.text)

        source_url = url
        final_url = response.url
        name = self._name(soup, jsonld, lines)
        brand = self._brand(jsonld)
        field_values = {
            field: self._field_from_html(soup, markers) or self._field_from_lines(lines, markers)
            for field, markers in FIELD_MARKERS.items()
        }
        field_values["tasting_notes"] = (
            self._icon_list_after_heading(soup, "Tasting Notes")
            or field_values.get("tasting_notes")
        )
        field_values["food_pairing"] = (
            self._icon_list_after_heading(soup, "Food Pairing")
            or self._food_pairing_from_tags(tags)
            or field_values.get("food_pairing")
        )
        field_values["serving_temperature"] = (
            self._serving_temperature_from_tags(tags)
            or field_values.get("serving_temperature")
        )
        field_values["taste_profile"] = (
            self._taste_profile_from_tags(tags)
            or field_values.get("taste_profile")
        )
        description = self._description(soup, jsonld, lines, name)
        image_url = self._image_url(soup, jsonld, final_url)

        brewery = field_values.get("brewery")
        if not brand and brewery:
            brand = brewery

        return SakeProduct(
            source_site=self.site_name,
            source_url=source_url,
            final_url=final_url,
            name=name,
            brand=brand,
            brewery=brewery,
            category=field_values.get("category"),
            taste_profile=field_values.get("taste_profile"),
            tasting_notes=field_values.get("tasting_notes"),
            food_pairing=field_values.get("food_pairing"),
            serving_temperature=field_values.get("serving_temperature"),
            alcohol=field_values.get("alcohol"),
            rpr=field_values.get("rpr"),
            smv=field_values.get("smv"),
            acidity=field_values.get("acidity"),
            region=field_values.get("region"),
            prefecture=field_values.get("prefecture"),
            description=description,
            price=self._price(soup, jsonld),
            image_url=image_url,
            raw_jsonld=json.dumps(jsonld, ensure_ascii=False) if jsonld else None,
        )

    def _discover_from_collections(self, limit: int | None = None) -> list[str]:
        found: list[str] = []
        pages_to_try = 3 if limit is None else max(1, min(5, (limit // 24) + 2))
        collection_paths = ["/collections/sake", "/collections/all", "/collections/shop-all"]
        for path in collection_paths:
            for page in range(1, pages_to_try + 1):
                url = urllib.parse.urljoin(self.base_url, f"{path}?page={page}")
                try:
                    response = self.fetch(url)
                except Exception:
                    continue
                soup = BeautifulSoup(response.text, "html.parser")
                for anchor in soup.select('a[href*="/products/"]'):
                    href = anchor.get("href")
                    if not href:
                        continue
                    absolute = urllib.parse.urljoin(response.url, href.split("?")[0])
                    if self._looks_like_product_url(absolute):
                        found.append(absolute)
                found = dedupe(found)
                if limit is not None and len(found) >= limit:
                    return found[:limit]
        return dedupe(found)

    def _looks_like_product_url(self, url: str) -> bool:
        parsed = urllib.parse.urlparse(url)
        return "/products/" in parsed.path and parsed.netloc.endswith(("tippsysake.com", "palateproject.co"))

    def _product_jsonld(self, soup: BeautifulSoup) -> dict:
        for obj in jsonld_objects(soup):
            value = obj.get("@type")
            types = value if isinstance(value, list) else [value]
            if "Product" in types:
                return obj
        return {}

    def _page_lines(self, soup: BeautifulSoup) -> list[str]:
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        lines = [clean_text(line) for line in soup.get_text("\n", strip=True).splitlines()]
        return [line for line in lines if line]

    def _name(self, soup: BeautifulSoup, jsonld: dict, lines: list[str]) -> str | None:
        candidates = [
            jsonld.get("name"),
            first_meta(soup, 'meta[property="og:title"]', 'meta[name="twitter:title"]'),
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
        ]
        for candidate in candidates:
            cleaned = clean_text(str(candidate)) if candidate else None
            if cleaned:
                return cleaned.replace(" - Tippsy Sake", "").replace(" - Palate Project", "")
        return lines[0] if lines else None

    def _brand(self, jsonld: dict) -> str | None:
        brand = jsonld.get("brand") if jsonld else None
        if isinstance(brand, dict):
            return clean_text(str(brand.get("name"))) if brand.get("name") else None
        return clean_text(str(brand)) if brand else None

    def _price(self, soup: BeautifulSoup, jsonld: dict) -> str | None:
        offers = jsonld.get("offers") if jsonld else None
        if isinstance(offers, dict) and offers.get("price"):
            currency = offers.get("priceCurrency") or "USD"
            return clean_text(f"{currency} {offers['price']}")
        amount = first_meta(soup, 'meta[property="product:price:amount"]', 'meta[property="og:price:amount"]')
        currency = first_meta(soup, 'meta[property="product:price:currency"]', 'meta[property="og:price:currency"]')
        if amount and currency:
            return f"{currency} {amount}"
        return amount

    def _image_url(self, soup: BeautifulSoup, jsonld: dict, final_url: str) -> str | None:
        image = jsonld.get("image") if jsonld else None
        if isinstance(image, list):
            image = image[0] if image else None
        elif isinstance(image, dict):
            image = image.get("url")
        image = image or first_meta(soup, 'meta[property="og:image"]', 'meta[name="twitter:image"]')
        return urllib.parse.urljoin(final_url, image) if image else None

    def _description(
        self,
        soup: BeautifulSoup,
        jsonld: dict,
        lines: list[str],
        name: str | None,
    ) -> str | None:
        json_description = clean_text(str(jsonld.get("description"))) if jsonld.get("description") else None
        if json_description:
            return json_description

        meta_description = first_meta(soup, 'meta[name="description"]', 'meta[property="og:description"]')
        if meta_description:
            return meta_description

        if "More Tasting Info" in lines:
            start = lines.index("More Tasting Info") + 1
            collected: list[str] = []
            for line in lines[start:]:
                if line == name or line in SECTION_STOP_WORDS or line in marker_values():
                    break
                if not self._is_noise(line):
                    collected.append(line)
            if collected:
                return clean_text(" ".join(collected))
        return None

    def _field_from_html(self, soup: BeautifulSoup, markers: list[str]) -> str | None:
        marker_set = {marker.lower() for marker in markers}
        for span in soup.select("dt span, h3"):
            label = clean_text(span.get_text(" ", strip=True))
            if not label or label.lower() not in marker_set:
                continue

            dl = span.find_parent("dl")
            if dl:
                dd = dl.find("dd")
                if dd:
                    return self._clean_field_value(dd.get_text(" ", strip=True))

            block = span.find_parent("div")
            if block:
                values = [
                    self._clean_field_value(node.get_text(" ", strip=True))
                    for node in block.select("li p, dd, a, span")
                ]
                values = [value for value in values if value and value.lower() != label.lower()]
                if values:
                    return clean_text(", ".join(dedupe(values)))
        return None

    def _icon_list_after_heading(self, soup: BeautifulSoup, heading: str) -> str | None:
        for h3 in soup.find_all("h3"):
            if clean_text(h3.get_text(" ", strip=True)) != heading:
                continue
            block = h3.find_parent("div")
            if not block:
                continue
            values = [
                self._clean_field_value(node.get_text(" ", strip=True))
                for node in block.select("li p")
            ]
            values = [value for value in values if value]
            if values:
                return clean_text(", ".join(dedupe(values)))
        return None

    def _shopify_tags(self, html: str) -> list[str]:
        match = re.search(r"Categories:\s*(\[[^\]]+\])", html)
        if not match:
            return []
        payload = match.group(1).encode("utf-8").decode("unicode_escape")
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return []
        return [clean_text(str(tag)) for tag in parsed if clean_text(str(tag))]

    def _taste_profile_from_tags(self, tags: list[str]) -> str | None:
        matches = [tag for tag in tags if tag.lower() in TASTE_TAGS]
        return clean_text(", ".join(dedupe(matches))) if matches else None

    def _food_pairing_from_tags(self, tags: list[str]) -> str | None:
        matches: list[str] = []
        for tag in tags:
            for prefix in FOOD_TAG_PREFIXES:
                if tag.startswith(prefix):
                    matches.append(tag.removeprefix(prefix))
        return clean_text(", ".join(dedupe(matches))) if matches else None

    def _serving_temperature_from_tags(self, tags: list[str]) -> str | None:
        matches = [tag for tag in tags if TEMP_TAG_RE.match(tag)]
        return clean_text(", ".join(dedupe(matches))) if matches else None

    def _field_from_lines(self, lines: list[str], markers: list[str]) -> str | None:
        for marker in markers:
            for index, line in enumerate(lines):
                if line == marker:
                    value = self._value_after_marker(lines, index)
                    if value:
                        return value
                if line.startswith(f"{marker}:"):
                    return clean_text(line.split(":", 1)[1])
        return None

    def _value_after_marker(self, lines: list[str], marker_index: int) -> str | None:
        collected: list[str] = []
        for line in lines[marker_index + 1 : marker_index + 8]:
            if line in marker_values() or line in SECTION_STOP_WORDS:
                break
            if self._is_noise(line):
                continue
            collected.append(line)
            if len(collected) >= 3:
                break
        return clean_text(", ".join(collected))

    def _is_noise(self, line: str) -> bool:
        return bool(
            re.fullmatch(r"[$\d.,]+", line)
            or re.fullmatch(r"\d+\s*(ml|oz)", line, flags=re.IGNORECASE)
            or line.lower() in {"sale", "regular price", "unit price", "default title"}
        )

    def _clean_field_value(self, value: str | None) -> str | None:
        cleaned = clean_text(value)
        if not cleaned:
            return None
        return (
            cleaned.replace("\u00b1", "+/-")
            .replace("\u2028", " ")
            .replace("\u2019", "'")
        )


def marker_values() -> set[str]:
    values: set[str] = set()
    for markers in FIELD_MARKERS.values():
        values.update(markers)
    return values


if __name__ == "__main__":
    run_cli(TippsySakeScraper)
