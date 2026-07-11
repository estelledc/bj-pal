#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$ROOT_DIR/_site"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR/pitch-png"

cp "$ROOT_DIR/promo/landing-page.html" "$OUT_DIR/index.html"
cp "$ROOT_DIR/promo/404.html" "$OUT_DIR/404.html"
cp "$ROOT_DIR/promo/robots.txt" "$OUT_DIR/robots.txt"
cp "$ROOT_DIR/promo/sitemap.xml" "$OUT_DIR/sitemap.xml"
cp "$ROOT_DIR/promo/favicon.png" "$OUT_DIR/favicon.png"
cp "$ROOT_DIR/promo/pitch-png/01.png" "$OUT_DIR/pitch-png/01.png"
cp "$ROOT_DIR/promo/pitch-png/08.png" "$OUT_DIR/pitch-png/08.png"
cp "$ROOT_DIR/promo/pitch-deck.pdf" "$OUT_DIR/pitch-deck.pdf"
cp "$ROOT_DIR/promo/one-pager.pdf" "$OUT_DIR/one-pager.pdf"

touch "$OUT_DIR/.nojekyll"

printf 'BJ-Pal showcase built at %s\n' "$OUT_DIR"
