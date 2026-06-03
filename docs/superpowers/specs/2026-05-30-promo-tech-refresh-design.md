# Promo Tech Refresh Design

## Goal

Refresh the existing BJ-Pal promo package from a paper/editorial visual system into a stronger hackathon technology style, while updating stale v3.0/v3.1 proof points and adding KeepL as a co-author.

## Scope

Modify only static promo assets and promo documentation:

- `promo/pitch-deck.html`
- `promo/landing-page.html`
- `promo/one-pager.html`
- `promo/readme-hero.html`
- `promo/xhs-carousel.html`
- `promo/architecture.md`
- `promo/README.md`

Generated screenshots and PDFs are not regenerated in this pass unless explicitly requested later.

## Visual Direction

Use a dark technical palette with cyan, blue, and green accents. Replace the current paper-grain / serif-heavy feeling with a command-center style: grid backgrounds, scan lines, glow rules, terminal labels, trace panels, metric tiles, and pipeline language. Keep the static single-file HTML structure so the assets remain easy to open locally.

## Content Direction

Bring promo copy up to the latest project state:

- UGC: `8,666` aspects
- Signal coverage: `5,198 POI 信号网`
- Evaluation: `L3 280/280`
- Calibration: `ECE 0.1089`
- Algorithms: `ToT / OPTW / Kemeny+Borda`
- Observability: `plan_tracer / OTel / tool_call_log`

All author/team spots should show `Jason · KeepL`.

## Verification

Add a focused pytest check for promo content consistency:

- co-author text appears in the primary HTML assets
- stale `1,102` / `1102` promo claims are gone
- latest metric claims appear in the refreshed assets
- technology-theme CSS tokens appear in the HTML
