# car-alerts

A small used-car alert bot. Runs on GitHub Actions cron every hour, scrapes
a few listing sites, diffs against the previous run, and pushes an
[ntfy](https://ntfy.sh) notification for every new match and every meaningful
price drop. No server, no database — state is a JSON file committed back to
this repo.

```
New: 2023 Audi S3 Premium Plus – $36,713
26,049 mi · Fishers, IN (~160 mi) · Alderman Automotive · No accidents · Cars.com
```

---

## Source status — read this first

Scraping these sites is not uniformly possible. Here is the honest state of
each one, verified against live traffic:

| Source | Status | How it works |
|---|---|---|
| **Cars.com** | ⚠️ Works, but intermittent from CI | Server-rendered HTML, all filters pushed server-side. Cloudflare refuses GitHub Actions IPs some runs — see below. |
| **Autotrader** | ✅ Working, better than expected | Private JSON API the SRP itself calls. Full history flags. |
| **CarMax** | ✅ Working | Private JSON API. Best history data of the three — real fleet/rental flags. |
| **CarGurus** | ❌ Skipped by design | Remix app, listings render client-side. Logs a warning and moves on. |

### Two things that will surprise you

**1. `requests` cannot fetch these sites — that's why `curl_cffi` is in
`requirements.txt`.** Cars.com (Cloudflare) and CarMax (Akamai) fingerprint the
*TLS handshake*, not the User-Agent. Plain `requests` gets a hard `403` on
every request no matter what headers you set. `curl_cffi` replays a real Chrome
fingerprint. It is a pip-only wheel — no browser binary, unlike
Selenium/Playwright. This is the one dependency substitution in the brief and
it is load-bearing.

**2. Do not add a User-Agent header.** This is counter-intuitive and it will
bite whoever touches `http.py` next. `curl_cffi` sends a UA that matches the
TLS fingerprint of the browser it is impersonating. Setting your own UA on top
desyncs the two, and *that mismatch is itself a detection signal* — spoofing a
"Chrome 131" UA made Autotrader serve a challenge page on every request, while
the identical request using curl_cffi's own UA returned JSON.

So the bot rotates the **impersonation profile**, not the User-Agent string;
rotating the profile rotates the UA coherently. Profiles are also not
interchangeable — `chrome`, `chrome136`, `chrome145` and `chrome150` get real
responses, while `chrome131`, `chrome133a`, `chrome142`, `chrome146`, `edge`
and `safari180` are challenged. The working set is pinned in
`IMPERSONATE_PROFILES`.

### Two different failure modes, often confused

Earlier runs looked like one flaky problem. They were two, and only one is
about IP reputation.

**1. A bad impersonation profile — fixed.** A bot-challenge page arrives as
`200 text/html`, *not* an error status, so nothing retried it and the source
just returned zero listings for the run. `chrome136` was in the profile list
on the strength of a single lucky trial. Re-measured over 3 trials each:

| profile | Autotrader | CarMax | | profile | Autotrader | CarMax |
|---|---|---|---|---|---|---|
| `chrome` | 3/3 | 3/3 | | `chrome136` | 0/3 | 0/3 |
| `chrome142` | 3/3 | 3/3 | | `chrome133a` | 0/3 | 0/3 |
| `chrome145` | 3/3 | 3/3 | | `chrome146` | 0/3 | 0/3 |
| `chrome150` | 3/3 | 3/3 | | `chrome120` | 3/3 | 0/3 |

So roughly one run in four silently lost Autotrader entirely. Two fixes: the
list is pinned to the four verified-good profiles, and `get_json()` now treats
an HTML body as a soft block and retries under a fresh fingerprint. That
self-healing is the important half — profile behaviour varies by IP and over
time, and a CI run immediately after the fix caught `chrome142` being
challenged, rotated, and recovered all 73 listings.

**2. Cars.com refuses GitHub Actions IPs — genuinely unfixed.** This one is
real IP reputation, and it survives a 45-minute cooldown. Cloudflare scores
Actions' datacenter ranges as hostile. Raising retries made it *worse* — more
requests got the whole range refused. One CI run did get 18 listings through
after the session warm-up landed, so it is not hopeless, just unreliable.

What the bot does about it:

- **Warm-up.** Lands on `https://www.cars.com/` first for clearance cookies,
  then hits the SRP with a `Referer`, so it reads as a second page view rather
  than a cold-start scrape. This is what produced the one successful run.
- **Circuit breaker.** Once a host hard-blocks, it is skipped for the rest of
  the run instead of being retried once per target — same information, a third
  of the requests, minutes faster.
- **Modest retries (3).** Tuned down from 5 after measuring that more was worse.

**Why this is survivable:** Autotrader is reliable from CI and returns ~73
listings to cars.com's ~18, with heavy overlap. Dedupe is by VIN, so a car
cars.com would have surfaced usually arrives via Autotrader in the same run —
and if not, on one of the next twenty-odd runs that day.

If you want cars.com reliable rather than best-effort, the only real fix is a
non-datacenter egress IP: run the scan from a machine at home on `cron`, or
put a residential proxy in front of it. Both are more moving parts than this
brief asked for, so the bot degrades gracefully instead.

### Notification links

Tapping a notification opens the listing itself — `Click` is set to the
vehicle page, and all three URL shapes were verified to resolve:

| Source | URL shape |
|---|---|
| Cars.com | `cars.com/vehicledetail/<uuid>/` (taken from the card's own href) |
| Autotrader | `autotrader.com/cars-for-sale/vehicle/<id>` (constructed) |
| CarMax | `carmax.com/car/<stock>` (constructed) |

When the same VIN is on two sites, one `Click` is not enough, so the
notification also carries a **button per site** via ntfy's `Actions` header
(capped at 3, which is ntfy's limit). Single-source listings get no buttons —
tapping already does the job.

### How CarMax works (the `uri` trick)

Worth writing down, because it is not guessable and it took tracing the SPA
bundle to find. CarMax's endpoint is:

```
GET https://www.carmax.com/cars/api/search/run?uri=<route>&skip=0&take=100
```

Every attempt to pass `make=` / `model=` / `makes[]=` / `facets=` as top-level
params is **silently ignored** — you get the full ~59k nationwide inventory
back with a `200` and no error. The facets come from the **`uri`** param, which
carries the SRP route the SPA would be sitting on, URL-encoded, *including its
own nested query string*:

```
uri=%2Fcars%2Faudi%2Fs3%3Fyear%3D2022-2027%26price%3D0-38500%26mileage%3D0-45000
```

Verified behaviour:

| `uri` | `totalCount` |
|---|---|
| `/cars/all` | 59,256 |
| `/cars/audi` | 1,770 |
| `/cars/audi/s3` | 6 |
| `/cars/audi/s3?year=2022-2027` | 1 |

Range facets are `min-max` strings (`year`, `price`, `mileage`). Nationwide is
already the default, so there is no need to ask for it. `skip` and `take` stay
*outside* the `uri`, as ordinary top-level params.

If CarMax ever changes this, the source notices: `_looks_filtered()` checks
that the response is actually mostly Audis and skips the source with a loud
warning rather than returning 100 Dodge Durangos. There is also a
`carmax_query_overrides` escape hatch in `config.py` for pasting a raw query
string from DevTools.

---

## Setup

### 1. Push this repo

```bash
gh repo create <you>/car-alerts --public --source=. --push
```

**Public on purpose.** GitHub Actions is unlimited and free on public repos,
but private repos draw on a shared 2,000 min/month pool across your whole
account. Hourly runs cost ~1,440 min/month — 72% of that pool — and when it is
exhausted *every* private repo's Actions stop, not just this one. Nothing here
is sensitive: it is scraper code plus public car listings. The ntfy topic is an
Actions secret, never committed, and GitHub masks secrets in public logs.

If you do keep it private, drop the schedule back to `0 */2 * * *` (~720
min/month) or raise your spending limit above the $0 default.

> **Commit authorship:** use your GitHub noreply address
> (`<id>+<user>@users.noreply.github.com`) rather than a real email. On a
> public repo, commit emails are scraped. Note that force-pushing a rewrite is
> *not* enough on its own — GitHub keeps the orphaned commits fetchable by SHA
> indefinitely, so the old email stays readable. The only reliable fix is to
> push the cleaned history to a genuinely new repo.

### 2. Set the secrets

**Settings → Secrets and variables → Actions → New repository secret**

| Secret | Required | Notes |
|---|---|---|
| `NTFY_TOPIC` | yes | Your topic, e.g. `<my-topic>`. Anyone who knows a public ntfy topic can read it — pick something unguessable. |
| `NTFY_URL` | no | Defaults to `https://ntfy.sh`. Set this for a self-hosted server. |
| `NTFY_TOKEN` | no | Bearer token for a protected topic. |

Then subscribe to the same topic in the ntfy app.

### 3. Seed the state, so you don't get 40 pings at once

**Actions → Car scan → Run workflow → mode: `seed`.**

That records every current match without notifying. From then on you only hear
about genuinely new cars. The cron starts working on its own after that.

---

## Known issue: the schedule does not fire on a brand-new repo

If you have just created this repo and the cron never runs, it is almost
certainly not your config. Measured on 2026-09-22:

| Check | Result |
|---|---|
| Workflow state | `active`, on the default branch, valid cron |
| Manual `workflow_dispatch` | works every time |
| GitHub status | all operational, no incidents |
| Another repo on the same account (established) | scheduled runs firing normally |
| This repo, `*/5` cron, 5 slots | **0 runs** |
| This repo, minimal probe workflow, 6 slots | **0 runs** |

The minimal probe — no dispatch inputs, no concurrency group, no permissions
block — failed identically, which rules the config out. GitHub throttles cron
activation on freshly created repositories; it generally starts working within
a day without intervention.

`.github/workflows/cron-probe.yml` is a temporary canary for exactly this. Once
you see it appear under scheduled runs, registration is live — **delete the
probe at that point**, or it will run every 5 minutes forever.

If you need it working sooner, the options are: add a `gh workflow run` step to
a workflow in an *established* repo that already fires on schedule; drive
`workflow_dispatch` from an external cron service; or run `python -m
carbot.main` from a local `cron`/`launchd` job. The local option has a real
side benefit — a residential IP is not blocked by cars.com the way GitHub
Actions runners are, so that source becomes reliable rather than best-effort.

## Running it locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export PYTHONPATH=src
```

```bash
# See what matches right now. Sends nothing, writes nothing.
python -m carbot.main --dry-run

# Same, but also show what got filtered out and why — use this to tune filters.
python -m carbot.main --dry-run --show-rejected

# One source at a time, for debugging.
python -m carbot.main --dry-run --source cars.com
python -m carbot.main --dry-run --source autotrader

# Populate state without notifying (first run).
python -m carbot.main --seed

# For real.
python -m carbot.main
```

| Flag | Effect |
|---|---|
| `--dry-run` | Print what would be sent. No notifications, no state write. |
| `--seed` | Write state, suppress all notifications. |
| `--source NAME` | Limit to one source. Repeatable. |
| `--show-rejected` | Also print filtered-out listings and the reason. |
| `--state PATH` | Use a different state file. |
| `-v` | Debug logging. |

> **Note:** cars.com rate-limits repeated runs from one IP. Back-to-back dry
> runs — local or in CI — will start returning `403`; the bot backs off, logs,
> and skips rather than crashing. Wait a few minutes between manual runs.

---

## Changing your filters

Everything lives at the top of **`src/carbot/config.py`**.

**Price, mileage, exclusions:**

```python
CONFIG = {
    "max_price": 38_500,
    "max_mileage": 45_000,
    "exclude_flags": ["accident", "salvage", "frame_damage", "fleet_use"],
    "price_drop_threshold": 500,     # notify on a drop of >= this
    "high_priority_under": 36_000,   # ntfy priority=high below this
    ...
}
```

**Adding a car** — append to `TARGETS`. The year floor is per model:

```python
{
    "key": "rs3",
    "label": "Audi RS3",
    "make": "Audi", "model": "RS3",
    "year_min": 2022,
    "cars_com_slug": "audi-rs3",      # from a cars.com URL: models[]=audi-rs3
    "autotrader_code": "AUDRS3",      # see note below
    "carmax_path": "audi/rs3",
    "body_any_of": None,              # e.g. ["sportback", "hatchback"]
}
```

Set any per-source id to `None` to skip that source for that car.

*Finding an Autotrader model code:* the codes are irregular (`S3` is `AUDS3`
but `S4` is just `S4`). Get the real list with:

```bash
curl -s 'https://www.autotrader.com/rest/lsc/listing?zip=45217&numRecords=1&makeCode=AUDI' \
  | python3 -c "import json,sys; [print(o['value'], '=', o['label']) for o in json.load(sys.stdin)['filters']['modelCode']['options']]"
```

**About `strict_history_filter`** (default `True`): cars.com can filter
server-side for *no accidents + clean title + personal use*. That is stricter
than your brief — it also drops listings with **no history data at all**, not
just ones flagged bad. Set it to `False` to keep unknowns and rely only on
explicitly-bad flags.

**S5 Sportback:** no site exposes it as a distinct model, and the two working
sources *name it differently*:

- cars.com writes **"Sportback"** into the trim text.
- Autotrader never writes "Sportback" at all — it reports a **"Hatchback"**
  body style. (The S5 Coupe is `Coupe`, the Cabriolet is `Convertible`.)

So `body_any_of: ["sportback", "hatchback"]` matches either, and the target is
accepted if *any* token appears in the model/trim/body text. Requiring only
`"sportback"` silently drops every Autotrader Sportback — which is exactly the
bug the tests in `TestAutotraderBodyStyleQuirk` now pin down.

Verified against the NHTSA VIN decoder: all five S5s this returns decode as
4-door Hatchback/Liftback, series "Sportback". No Coupes leak through.

---

## When a source's HTML changes

Symptom: a source that used to return listings now logs
`0 listings` or `failed to parse`, and the tests still pass (they run against
saved fixtures, so they will not catch a live layout change).

1. **Confirm it's parsing, not blocking.** Run
   `python -m carbot.main --dry-run --source cars.com -v`.
   `HTTP 403 ... backing off` is a block — wait, or the site has tightened up.
   `0 listings` with a `200` is a layout change.

2. **Grab the live page.**

   ```bash
   python -c "
   from curl_cffi import requests as cr
   url='https://www.cars.com/shopping/results/?stock_type=used&makes[]=audi&models[]=audi-s3&zip=45217&maximum_distance=all'
   open('/tmp/live.html','w').write(cr.get(url, impersonate='chrome').text)"
   ```

3. **Find the new shape.** For cars.com the contract is: every result is a
   `<fuse-card>` containing an `<a data-vin>` whose `data-*` attributes hold the
   record (`data-price`, `data-mileage`, `data-year`, `data-trim`, …). If
   `data-vin` is gone, find what replaced it:

   ```bash
   python -c "
   from bs4 import BeautifulSoup
   s=BeautifulSoup(open('/tmp/live.html').read(),'lxml')
   print(len(s.select('fuse-card')), 'fuse-cards;', len(s.select('[data-vin]')), 'with data-vin')"
   ```

4. **Refresh the fixture and fix the parser.** Replace
   `tests/fixtures/cars_com_srp.html` with a handful of real cards from the
   live page, update `src/carbot/sources/cars_com.py`, and run `pytest`. The
   fixture tests assert on dealer/location/price/flags, so they will tell you
   when the parser is right again.

For the JSON sources the same loop applies, but check `requestParams` in the
Autotrader response — it echoes back only the params the server actually
honoured, which is how the working param names were found in the first place.

**A broken source never breaks the run.** Each one is wrapped so that a block,
a parse failure, or an outright crash logs a warning and the others carry on.

---

## Tests

```bash
PYTHONPATH=src:tests python -m pytest -q
```

109 tests, no network access — they run against saved fixtures captured from
real responses:

- `test_filters.py` — per-model year floors, price/mileage caps, excluded
  flags, the S5-Sportback body rule, cross-source VIN dedupe.
- `test_parsers.py` — cars.com HTML and Autotrader JSON parsing, including the
  "don't mistake the star rating for the dealer name" case.
- `test_carmax.py` — the `uri` contract, stock-number URLs, status mapping,
  and prior-use handling. The fixture holds a real Fleet car and a real prior-
  theft car, so the exclusion logic is tested against genuine payloads.
- `test_diff.py` — new vs. known vs. gone, the $500 price-drop threshold,
  `first_seen` preservation, notification text and priority, state round-trip.

The fixtures are real captured payloads, including Autotrader's
`bodyStyles: [{"code": "HATCH", "name": "Hatchback"}]` shape — a hand-written
fixture using plain strings hid a real crash until it was corrected.

---

## Layout

```
src/carbot/
  config.py              filters + targets — the file you edit
  models.py              the normalized Listing record
  filters.py             hard filters + VIN dedupe (pure, unit-tested)
  http.py                curl_cffi client: UA rotation, delays, 403/429 backoff
  geo.py                 haversine distance from ZIP 45217
  notify.py              ntfy formatting and POST
  state.py               load/save state/listings.json
  main.py                orchestration, diff logic, CLI
  sources/
    base.py              the interface every source implements
    cars_com.py          ✅ HTML
    autotrader.py        ✅ JSON API
    carmax.py            ✅ JSON API via the `uri` facet param
    cargurus.py          ❌ client-rendered, skips
state/listings.json      committed state
.github/workflows/scan.yml
```

`filters.py` and `geo.py` are additions to the layout in the brief — the filter
logic needed to live somewhere unit-testable rather than inside `main.py`.

## How the diff works

- **New VIN** → notification. Priority `high` under $36k, `default` otherwise.
- **Known VIN, price dropped ≥ $500** → price-drop notification.
- **VIN gone** → record kept, `status` set to `gone`, `last_seen` frozen at the
  last run that actually saw it. Never notified.
- Same VIN on two sites → one record holding both URLs, the union of the flags,
  and the lower of the two prices.
