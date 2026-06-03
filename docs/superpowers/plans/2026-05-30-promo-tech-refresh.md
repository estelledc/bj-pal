# Promo Tech Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refresh BJ-Pal promo assets into a stronger hackathon technology style with updated v3.1 proof points and `Jason · KeepL` author attribution.

**Architecture:** Keep the current single-file static HTML assets. Make scoped CSS/content edits in each promo file rather than introducing a build system or shared dependency.

**Tech Stack:** Static HTML/CSS, pytest content checks, local HTTP preview.

---

### Task 1: Promo Content Regression Test

**Files:**
- Create: `tests/test_promo_refresh.py`

- [ ] Add pytest checks that read the promo HTML/Markdown files, require `Jason · KeepL`, reject stale `1,102`/`1102`, and require current metrics such as `8,666`, `5,198`, `280/280`, and `0.1089`.
- [ ] Run `pytest tests/test_promo_refresh.py -q` and confirm it fails before implementation.

### Task 2: Primary Deck Refresh

**Files:**
- Modify: `promo/pitch-deck.html`

- [ ] Replace the global palette and decorative treatment with dark grid, glow, trace, and terminal-style CSS.
- [ ] Update data and eval slides to v3.1 claims.
- [ ] Update team slide and footer author text to `Jason · KeepL`.

### Task 3: Web Promo Refresh

**Files:**
- Modify: `promo/landing-page.html`
- Modify: `promo/one-pager.html`
- Modify: `promo/readme-hero.html`
- Modify: `promo/xhs-carousel.html`

- [ ] Apply the same technology palette and accent system.
- [ ] Replace stale data claims.
- [ ] Update author/watermark/team copy to `Jason · KeepL`.

### Task 4: Promo Docs Refresh

**Files:**
- Modify: `promo/architecture.md`
- Modify: `promo/README.md`

- [ ] Update documented promo tone and current metrics.
- [ ] Leave screenshot/PDF regeneration instructions intact, but note generated image/PDF files may need regeneration after HTML changes.

### Task 5: Verification

**Files:**
- Test: `tests/test_promo_refresh.py`

- [ ] Run `pytest tests/test_promo_refresh.py -q`.
- [ ] Open the refreshed assets locally or through the browser plugin.
- [ ] Inspect screenshots for visible content, no major overflow, and a coherent technology style.
