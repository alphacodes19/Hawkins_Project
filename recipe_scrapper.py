"""
hawkins_recipe_scraper.py
=========================
Scrapes all recipes from https://www.hawkinscookers.com/recipe_selector.aspx

HOW THE PAGE WORKS
------------------
• The full recipe list is embedded in the initial HTML page load (no JS needed).
• Each recipe title is a link that fires an ASP.NET __doPostBack() call.
• To fetch a recipe's full details we must:
    1. GET the page → extract __VIEWSTATE / __EVENTVALIDATION / __VIEWSTATEGENERATOR
    2. For each recipe link, POST those tokens back with the correct __EVENTTARGET
    3. Parse the recipe detail panel that appears in the response HTML

RESUMING AFTER A BLOCK (403 Forbidden)
---------------------------------------
If the site starts returning 403s partway through (rate limiting / bot
detection), this script:
  • retries each recipe up to MAX_RETRIES times with exponential backoff
  • takes a longer cooldown after 5 consecutive failures in a row
  • saves progress to hawkins_recipes_checkpoint.json after EVERY recipe
  • on the next run, automatically skips recipes already fetched successfully

So if you get blocked again, just re-run the script. It picks up where it
left off instead of starting over. If it gets blocked again quickly, wait
10-15 minutes before re-running — that usually means the IP-level throttle
window hasn't reset yet.

OUTPUT
------
• hawkins_recipes.csv              – one row per recipe (final)
• hawkins_recipes.json             – same data as JSON, with ingredients/method
• hawkins_recipes_checkpoint.json  – running progress file (safe to delete once done)

USAGE
-----
    pip install requests beautifulsoup4 lxml
    python hawkins_recipe_scraper.py
"""

import csv
import json
import random
import re
import time
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

# ── Configuration ────────────────────────────────────────────────────────────

BASE_URL      = "https://www.hawkinscookers.com/recipe_selector.aspx"
DELAY_SECONDS = 2.5          # base pause between requests (randomized +/- below)
DELAY_JITTER  = 1.5          # adds 0..1.5s random jitter so timing isn't robotic
OUTPUT_CSV    = "hawkins_recipes.csv"
OUTPUT_JSON   = "hawkins_recipes.json"
CHECKPOINT    = "hawkins_recipes_checkpoint.json"   # progress saved here after every recipe

MAX_RETRIES        = 4       # attempts per recipe on 403/429/5xx
RETRY_BASE_DELAY   = 8.0     # seconds; doubles each retry (8, 16, 32, 64...)
COOLDOWN_ON_BLOCK   = 45.0   # extra pause if we suspect we're being throttled

# Headers modeled on a real desktop Chrome session. A bare "compatible;
# HawkinsRecipeArchive/1.0" UA is an easy bot-detection signal — most WAFs key
# off User-Agent + missing Accept/Accept-Language/sec-fetch-* headers together.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_URL,
    "Origin": "https://www.hawkinscookers.com",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
}

# ── Data model ───────────────────────────────────────────────────────────────

@dataclass
class Recipe:
    name: str                          # e.g. "Aloo Matar"
    translation: str = ""              # e.g. "Pea and Potato Curry"
    cookware_note: str = ""            # e.g. "Futura Hard Anodised 3 Litre Handi"
    event_target: str = ""             # ASP.NET postback target string
    ingredients: list = field(default_factory=list)
    method: list     = field(default_factory=list)
    serves: str      = ""
    cooking_time: str = ""
    detail_fetched: bool = False
    error: str = ""


# ── Step 1: GET the main page ────────────────────────────────────────────────

def get_main_page(session: requests.Session) -> BeautifulSoup:
    print(f"[1/3] Fetching recipe list from {BASE_URL} …")
    resp = session.get(BASE_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "lxml")


# ── Step 2: Extract ASP.NET form tokens ──────────────────────────────────────

def extract_aspnet_tokens(soup: BeautifulSoup) -> dict:
    """Pull __VIEWSTATE, __VIEWSTATEGENERATOR and __EVENTVALIDATION from hidden inputs."""
    tokens = {}
    for field_name in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
        tag = soup.find("input", {"name": field_name})
        if tag:
            tokens[field_name] = tag.get("value", "")
    return tokens


# ── Step 3: Parse all recipe links from the list panel ───────────────────────

def _parse_name_and_notes(raw_text: str) -> tuple[str, str, str]:
    """
    Recipe anchor text looks like:
      "Aloo Matar (Pea and Potato Curry)"
    or
      "Dum Aloo (Potatoes in Thick Gravy) Futura Hard Anodised 3 Litre Handi (Saucepan)"

    Returns (name, translation, cookware_note).
    """
    raw_text = raw_text.strip()

    # Extract the first parenthesised group as the English translation
    translation = ""
    cookware_note = ""

    # Find italic text if available (the translation is often in <em>)
    # We'll rely on the title attribute of the <a> tag (already extracted) as fallback

    # Pattern: "RecipeName (Translation) Optional cookware note"
    match = re.match(r"^(.+?)\s*\(([^)]+)\)\s*(.*)$", raw_text)
    if match:
        name        = match.group(1).strip()
        translation = match.group(2).strip()
        cookware_note = match.group(3).strip()
    else:
        name = raw_text

    return name, translation, cookware_note


def extract_recipe_links(soup: BeautifulSoup) -> list[Recipe]:
    """
    All recipe <a> tags call javascript:__doPostBack('DataList$ctl01$dlst$ctlNN$ctl00','').
    We extract the event target from the href and the display text for the name.
    """
    recipes = []
    pattern = re.compile(r"__doPostBack\('([^']+)'")

    for a_tag in soup.find_all("a", href=re.compile(r"__doPostBack")):
        href = a_tag.get("href", "")
        m = pattern.search(href)
        if not m:
            continue

        event_target = m.group(1)
        raw_text     = a_tag.get_text(separator=" ", strip=True)
        name, translation, cookware_note = _parse_name_and_notes(raw_text)

        recipes.append(Recipe(
            name          = name,
            translation   = translation,
            cookware_note = cookware_note,
            event_target  = event_target,
        ))

    print(f"    → Found {len(recipes)} recipe links.")
    return recipes


# ── Checkpoint helpers ─────────────────────────────────────────────────────

def load_checkpoint() -> dict[str, dict]:
    """
    Returns {event_target: recipe_dict} for every recipe already fetched
    successfully in a previous run. Empty dict if no checkpoint exists.
    """
    path = Path(CHECKPOINT)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {r["event_target"]: r for r in data if r.get("detail_fetched")}
    except (json.JSONDecodeError, KeyError):
        print(f"    ⚠ Could not parse existing {CHECKPOINT}, starting fresh.")
        return {}


def save_checkpoint(recipes: list[Recipe]) -> None:
    Path(CHECKPOINT).write_text(
        json.dumps([asdict(r) for r in recipes], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── Step 4: POST to fetch each recipe detail ──────────────────────────────────

def fetch_recipe_detail(
    session: requests.Session,
    recipe: Recipe,
    tokens: dict,
) -> None:
    """
    Submits the ASP.NET form postback for one recipe and parses the detail panel.
    Modifies the Recipe object in-place.

    Retries on 403 (Forbidden) / 429 (Too Many Requests) / 5xx with exponential
    backoff, since these usually mean we tripped a rate limit or WAF rule rather
    than a permanent failure. A persistent 403 across several retries means the
    cooldown wasn't long enough — the caller should pause longer and resume later.
    """
    payload = {
        "__EVENTTARGET":         recipe.event_target,
        "__EVENTARGUMENT":       "",
        "__LASTFOCUS":           "",
        **tokens,
    }

    last_exc: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.post(BASE_URL, data=payload, headers=HEADERS, timeout=30)

            if resp.status_code in (403, 429) or resp.status_code >= 500:
                wait = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                print(f"\n        ⚠ HTTP {resp.status_code} on attempt {attempt}/{MAX_RETRIES}, "
                      f"backing off {wait:.0f}s …", end="", flush=True)
                time.sleep(wait)
                continue

            resp.raise_for_status()

        except requests.RequestException as exc:
            last_exc = exc
            wait = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            time.sleep(wait)
            continue

        # ── Success: parse the response ────────────────────────────────────
        detail_soup = BeautifulSoup(resp.text, "lxml")

        new_tokens = extract_aspnet_tokens(detail_soup)
        if new_tokens:
            tokens.update(new_tokens)

        detail_div = (
            detail_soup.find("div", id=re.compile(r"recipe", re.I))
            or detail_soup.find("div", class_=re.compile(r"recipe", re.I))
            or detail_soup.find("td", id=re.compile(r"recipe", re.I))
        )

        if not detail_div:
            for table in detail_soup.find_all("table"):
                text = table.get_text()
                if "Ingredients" in text or "Method" in text or "ingredients" in text:
                    detail_div = table
                    break

        if not detail_div:
            recipe.error = "detail panel not found in postback response"
            return

        full_text = detail_div.get_text(separator="\n", strip=True)

        serves_m = re.search(r"Serves?\s*[:\-]?\s*(\d[\d\s\-to]*)", full_text, re.I)
        if serves_m:
            recipe.serves = serves_m.group(1).strip()

        time_m = re.search(r"(Cooking\s+time|Time)\s*[:\-]?\s*([^\n]+)", full_text, re.I)
        if time_m:
            recipe.cooking_time = time_m.group(2).strip()

        lines = [l.strip() for l in full_text.splitlines() if l.strip()]
        ingr_start = next((i for i, l in enumerate(lines) if re.search(r"ingredient", l, re.I)), None)
        method_start = next((i for i, l in enumerate(lines) if re.search(r"method|preparation|directions", l, re.I)), None)

        if ingr_start is not None:
            end = method_start if method_start and method_start > ingr_start else len(lines)
            recipe.ingredients = lines[ingr_start + 1 : end]

        if method_start is not None:
            recipe.method = lines[method_start + 1 :]

        recipe.detail_fetched = True
        return

    # All retries exhausted
    recipe.error = f"failed after {MAX_RETRIES} attempts" + (f": {last_exc}" if last_exc else " (HTTP block)")


# ── Step 5: Save results ──────────────────────────────────────────────────────

def save_csv(recipes: list[Recipe], path: str) -> None:
    fieldnames = [
        "name", "translation", "cookware_note",
        "serves", "cooking_time",
        "detail_fetched", "error", "event_target",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in recipes:
            row = asdict(r)
            # Flatten list fields to a semicolon-separated string for CSV
            row["ingredients"] = " | ".join(r.ingredients)
            row["method"]      = " | ".join(r.method)
            writer.writerow({k: row[k] for k in fieldnames})
    print(f"    → CSV saved to {path}")


def save_json(recipes: list[Recipe], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in recipes], f, ensure_ascii=False, indent=2)
    print(f"    → JSON saved to {path}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    session = requests.Session()

    # 1. Load the page and find all recipe links
    soup   = get_main_page(session)
    tokens = extract_aspnet_tokens(soup)

    if not tokens.get("__VIEWSTATE"):
        sys.exit("ERROR: Could not extract ASP.NET form tokens. The page structure may have changed.")

    recipes = extract_recipe_links(soup)

    if not recipes:
        sys.exit("ERROR: No recipe links found. The page structure may have changed.")

    # 1b. Resume from checkpoint if one exists — skip recipes already fetched
    #     successfully so a previous block doesn't cost you the earlier progress.
    done = load_checkpoint()
    if done:
        print(f"    → Found checkpoint with {len(done)} previously-fetched recipes; resuming.")
        for r in recipes:
            if r.event_target in done:
                cached = done[r.event_target]
                r.ingredients     = cached.get("ingredients", [])
                r.method          = cached.get("method", [])
                r.serves          = cached.get("serves", "")
                r.cooking_time    = cached.get("cooking_time", "")
                r.detail_fetched  = True

    remaining = [r for r in recipes if not r.detail_fetched]
    total = len(recipes)
    print(f"\n[2/3] {total - len(remaining)} already done, {len(remaining)} remaining "
          f"(≈{len(remaining) * (DELAY_SECONDS + DELAY_JITTER/2) / 60:.1f} mins) …")

    consecutive_failures = 0

    for idx, recipe in enumerate(remaining, start=1):
        print(f"    [{idx:>4}/{len(remaining)}] {recipe.name[:60]}", end="  ", flush=True)
        fetch_recipe_detail(session, recipe, tokens)

        if recipe.detail_fetched:
            print("✓")
            consecutive_failures = 0
        else:
            print(f"✗  ({recipe.error})")
            consecutive_failures += 1

        # Save progress after every single recipe so nothing is ever lost again
        save_checkpoint(recipes)

        # If several in a row failed, the site is likely still throttling us —
        # take a longer cooldown rather than hammering it and getting IP-banned.
        if consecutive_failures >= 5:
            print(f"        ⚠ {consecutive_failures} failures in a row — "
                  f"cooling down {COOLDOWN_ON_BLOCK:.0f}s before continuing …")
            time.sleep(COOLDOWN_ON_BLOCK)
            consecutive_failures = 0

        time.sleep(DELAY_SECONDS + random.uniform(0, DELAY_JITTER))

    # 3. Save final outputs
    print(f"\n[3/3] Saving results …")
    save_csv(recipes, OUTPUT_CSV)
    save_json(recipes, OUTPUT_JSON)

    fetched = sum(1 for r in recipes if r.detail_fetched)
    failed  = total - fetched
    print(f"\nDone.  {fetched} recipes saved,  {failed} failed.")
    if failed:
        print(f"       Failed recipes still appear in the CSV/JSON with detail_fetched=False.")
        print(f"       Just re-run the script — it will resume from {CHECKPOINT} "
              f"and only retry the {failed} that are still missing.")


if __name__ == "__main__":
    main()