# beverage-bottlebot

A modular scraping and data pipeline for collecting tasting profiles and product data across sake, wine, and beer platforms.

## Current scraper

The first working target is Tippsy Sake / Palate Project. The scraper discovers product URLs from the sitemap, follows redirects, extracts sake metadata, and writes one spreadsheet per website.

Install dependencies:

```bash
pip install -r requirements.txt
```

Scrape a small sample:

```bash
python -m src.tippsy_sake --limit 25 --output data/tippsy_sake.xlsx
```

Scrape specific products:

```bash
python -m src.tippsy_sake --url https://www.tippsysake.com/products/denshin-ine --output data/tippsy_sake.xlsx
```

Output columns include name, brand, brewery, category, taste profile, tasting notes, food pairing, serving temperature, alcohol, RPR, SMV, acidity, region, prefecture, description, price, image URL, source URL, and final URL.
