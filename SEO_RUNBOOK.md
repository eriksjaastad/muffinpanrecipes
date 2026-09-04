# SEO_RUNBOOK.md

A recurring operating procedure for the four SEO tools on this site: **Google Search
Console, GA4, Screaming Frog, and Ahrefs.**

Two things this is trying to fix. First, SEO work here has been episodic — a burst of
audits in June, another in August, then nothing until someone remembers. Second, and
worse: the August baseline was captured with no procedure attached, so nobody knew what
re-running it was supposed to look like. A baseline nobody re-runs is a file, not a
measurement.

It is written for muffinpanrecipes and deliberately kept portable — every site-specific
value is named in one place (**Site constants**, below) so another project can adopt the
procedure by changing that section and nothing else.

---

## Who can actually operate each tool

This is the part worth reading before anything else. The four tools do not have the same
access story, and treating them as if they do is how "run the audit" turns into a week of
back-and-forth.

| Tool | What it answers | Access route | Who runs it |
|---|---|---|---|
| **Screaming Frog** | What a crawler sees on our own HTML | Local CLI, headless | **Agent, directly and unattended.** No Erik. |
| **Google Search Console** | What Google actually *did* — impressions, position, indexing verdicts | Web UI, Erik's Google account | **Agent via Claude-in-Chrome**, in Erik's logged-in session |
| **GA4** | What humans did once they arrived | Web UI, Erik's Google account | **Agent via Claude-in-Chrome** |
| **Ahrefs Webmaster Tools** | Backlinks, and a second opinion on the crawl | Web UI, Erik's Ahrefs account | **Agent via Claude-in-Chrome** |

**The short version: exactly one of the four is fully automatable, and it is Screaming
Frog.** The other three are account-gated web apps with no credentials in Doppler. The
Chrome extension is what makes them reachable at all — it drives Erik's already-logged-in
browser, so the agent never sees or handles a password.

### Screaming Frog — the agent's own tool

Verified working 2026-09-04: a headless crawl of the live site returned 122 URLs in 21
seconds and exported CSV, with **columns identical to the August baseline**, so diffs
against that baseline are valid.

```
/Applications/Screaming Frog SEO Spider.app/Contents/MacOS/ScreamingFrogSEOSpiderLauncher
```

**It is the free tier.** The log says `Licence Status: Missing` and the config carries
`mCrawlTotalLimit=500`. What that costs us:

| Free-tier limit | Consequence here |
|---|---|
| 500 URLs per crawl | Fine — the site is at 122. **Watch the headroom.** |
| No `--save-crawl` | No `.seospider` files, so no built-in `--crawl-comparison`. |
| Configuration locked to defaults | Diffs are automatically comparable, which is a genuine upside. |
| API integrations (GSC/GA4/PageSpeed) unavailable | The crawl carries no traffic data — join it manually. |

The lost crawl-comparison feature is replaced by `scripts/seo_crawl_diff.py`, which diffs
the CSV exports instead. That is arguably the better artifact: a CSV diffs in git and
survives a Screaming Frog upgrade; a `.seospider` binary does neither.

Buy a licence (£199/yr) when either the crawl approaches 500 URLs or we want the crawl to
carry GSC/GA4 data. Not before.

### Search Console, GA4, and Ahrefs — the browser tools

All three are driven through the **Claude-in-Chrome extension**, which operates the live
browser in Erik's session. This means:

- Erik must be **logged in already**. The agent must never be asked to enter credentials,
  and must not be given them.
- Erik grants the extension **per-site permission once** for `search.google.com`,
  `analytics.google.com`, and `app.ahrefs.com`.
- The agent reads what is on screen and records it. It does not change settings, submit
  sitemaps, or request indexing without asking first.

**Ahrefs is the free Webmaster Tools tier**, which shapes what is worth asking of it:

| Available | Not available |
|---|---|
| Site Audit, 5,000 crawl credits/month per verified project | Keywords Explorer |
| Site Explorer, but only on **verified** properties | Any competitor's domain |
| Up to 1,000 backlinks and 1,000 keywords per view | API access of any kind |

So Ahrefs' real job here is **backlinks and a second crawler's opinion**. Competitor and
keyword research is not on the table at this tier — which matters, because keyword
targeting is the site's actual problem. Say so plainly in reports rather than
substituting a weaker metric that happens to be free.

### Site constants

Change these and only these when porting this runbook.

| | |
|---|---|
| Site | `https://muffinpanrecipes.com` |
| GA4 property | `549934218`, stream `G-05P73D3237` |
| GSC property | Domain property (DNS TXT verified) |
| Baseline crawl | `seo-audits/baseline-2026-08/` |
| Weekly output | `seo-audits/weekly/<YYYY-MM-DD>/` |

> The old `react-my-burger` GA4 property is **not** this site. Ignore it.

---

## The cadence

| When | What | Who | Effort |
|---|---|---|---|
| **Weekly**, Sunday after the publish cron | Crawl + diff | Agent, unattended | ~2 min |
| **Biweekly** | Browser pass across GSC, GA4, Ahrefs | Agent in Erik's session | ~20 min |
| **Quarterly** | Full re-baseline and written report | Agent + Erik | ~1 hr |

The weekly job is the load-bearing one, because this site publishes itself. A recipe goes
live every Sunday with no human in the loop, and the crawl is the only thing that would
notice if that publish broke an existing page.

---

## Procedure 1 — the weekly crawl diff

Fully automated. Run it after Sunday's publish completes.

```bash
./scripts/seo_weekly_crawl.sh
```

It crawls the live site headless, writes `seo-audits/weekly/<date>/internal_all.csv`, and
diffs against the previous weekly crawl. `scripts/seo_crawl_diff.py` **exits non-zero when
it finds a regression**, so this can be wired to something that fails loudly.

To measure against the original baseline instead of last week:

```bash
./scripts/seo_weekly_crawl.sh --against baseline
```

### Reading the output

The diff is written to say as little as possible, because a weekly report that lists
everything gets skimmed by week three and ignored by week six.

| Section | What it means |
|---|---|
| **Regressions** | The only section that should ever stop you. A page that was 200 and indexable is no longer. Investigate before anything else. |
| **URLs added** | Expected: one recipe page plus its images, every Sunday. Anything else needs explaining. |
| **URLs removed** | Never normal. A published recipe should not vanish. |
| **Thin internal linking** | Pages under 2 unique inlinks. Structural, slow-moving, tracked as a trend. |
| **On-page gaps** | Missing title/description, or under 200 words. |

### When to escalate

- **A regression appears** → check `RUNBOOK.md` for a matching known incident *before*
  improvising. The 2026-04-14 storage-prefix contamination presented exactly as pages
  reading wrong, and guessing cost hours.
- **The crawl hits 500 URLs** → the export is silently truncated and the diff is
  worthless. The script warns. Erik needs to decide on a licence.
- **Screaming Frog is missing or won't launch** → fall back to the escalation template.

### What to do with findings

**Findings become cards. They do not become same-session fixes.** The portfolio rule
applies: stay on the card you are on. A crawl that surfaces six problems should produce
six cards and zero commits, unless one of them is a live regression.

---

## Procedure 2 — the biweekly browser pass

Run through Claude-in-Chrome with Erik present. Fifteen to twenty minutes.

Order matters. Search Console first, because it is the only tool reporting on Google's
actual behaviour; everything else is a proxy for it.

### 1. Search Console — Performance (Search results)

Set the range to the **last 28 days**, compare to the previous period. Record:

- Total impressions, clicks, average position, CTR — and the direction of each.
- Top queries. **The question that matters: do any of them contain "muffin pan" or
  "muffin tin"?** That is the niche the site is built to own, and the only demand signal
  that means anything here.
- Any page whose average position moved more than ~10 places, either way.

### 2. Search Console — Indexing → Pages

- The count of indexed vs not-indexed pages.
- Any **new** exclusion reason. Known and settled, do not re-litigate:
  - *"Page with redirect"* on the `www.` and `http://` homepage variants is correct
    behaviour. Leave it.
  - *"Crawled – currently not indexed"* on a thin seed recipe is a content problem, not
    a technical one. It belongs to card #6389.

### 3. Search Console — Recipe rich results

Confirm the count of valid Recipe items tracks the count of published recipes. Five
non-critical warnings are known and carded.

> **Never add `aggregateRating`.** No traffic means no genuine ratings, and fabricated
> review markup is a manual-action risk. This has been decided twice (cards #6344, #6345).
> Do not re-propose it.

### 4. GA4 — property 549934218

Reports → Acquisition → Traffic acquisition, last 28 days:

- Organic Search sessions, and the trend.
- Engagement rate on organic. **A rise in sessions with a collapse in engagement is worse
  than no rise at all** — it usually means we started ranking for something we shouldn't.

### 5. Ahrefs Webmaster Tools

- **Site Audit**: run it, note the Health Score and any *new* issue category. It uses
  crawl credits, so once a fortnight, not on a whim.
- **Backlinks**: referring domains count. On a site this young the honest expected value
  is near zero, and recording a zero is a real data point — do not dress it up.

### 6. Write it down

Append to the report log. Numbers with dates, and one sentence on what changed. Numbers
without dates are how "I thought we had metrics" happens.

---

## Procedure 3 — the quarterly re-baseline

Due **December 2026** (four months after the August baseline, per
`seo-audits/baseline-2026-08/README.md`).

1. `./scripts/seo_weekly_crawl.sh --against baseline`
2. Full Ahrefs Site Audit; export the PDF alongside the August one.
3. GSC Performance over the **full period since the baseline**, not 28 days.
4. Write `seo-audits/report-<date>.md`: what changed, what caused it, what is next.

**One thing to resolve at the first quarterly.** The two tools disagreed at baseline —
Screaming Frog found 109 URLs on 22 Aug, Ahrefs found 206 on 24 Aug. Two days apart, so it
is either growth or a crawl-scope difference, and until that is settled neither number is
a reference count. The 2026-09-04 crawl found 118, which makes plain growth look like the
weaker explanation.

---

## What only Erik can do

Everything here needs Erik's own account. When the agent hits one of these, it stops and
asks — it does not work around it.

| Task | Why it can't be delegated |
|---|---|
| Grant the Chrome extension per-site permission | One-time, per site, in the extension |
| Submit a sitemap, or click "Validate Fix" / "Request Indexing" | Writes to Google's queue on his behalf |
| Buy or install a Screaming Frog licence | Payment |
| Verify a new property in GSC or Ahrefs | Domain ownership |
| Change GA4 configuration | Account-level, and easy to break silently |

### The escalation template

When the agent cannot reach a tool, it should say exactly this much — no more, and with
the numbers already filled in:

> **Blocked on:** [tool]
> **Need you to:** [the specific click path, e.g. "Search Console → Indexing → Pages →
> export the 'Why pages aren't indexed' table as CSV"]
> **Where to put it:** `seo-audits/weekly/<date>/`
> **Why:** [the one question this answers]
> **Meanwhile:** [what has already been done without it]

The last line is the point. A blocked tool blocks one step, not the pass.

---

## Standing rules

- **One crawl configuration, forever.** Change it in `scripts/seo_weekly_crawl.sh` and
  note in `seo-audits/README.md` that runs before and after are not comparable. A silent
  config change makes every past crawl worthless.
- **Never fabricate a metric.** If a number can't be reached, the report says it can't be
  reached. This is the same rule that killed `aggregateRating` and per-recipe nutrition.
- **Findings become cards.** See "What to do with findings".
- **Technical SEO is not the bottleneck here, and reports should stop implying it is.**
  Canonicals, sitemap, JSON-LD, breadcrumbs, and WebP are all in good shape. The site
  ranks around position 87 with zero clicks because it has no authority and no keyword
  targeting. A green crawl is table stakes, not progress.

---

## Worked example — 2026-09-04

The first real run, baseline vs. today.

```
## Crawl size
  all URLs     109 ->  118  (+9)
  HTML pages    36 ->   37  (+1)

## Status codes (all URLs)
  200                           109 ->  118  (+9)

## Indexability (HTML pages)
  Indexable                      36 ->   37  (+1)

## Regressions
  none

## Thin internal linking
  5 -> 1  (-4)
      https://muffinpanrecipes.com/recipes  (1 inlinks)
```

Read as the procedure says to read it:

- **Regressions: none.** Every URL 200, every HTML page indexable. Nothing the August–September work shipped has broken a page.
- **+9 URLs, +1 HTML page** — one recipe (`lemon-ricotta-polenta-cups`) plus eight image
  variants. Exactly the expected weekly shape.
- **Thin internal linking 5 → 1.** The internal-linking work (PRs #68, #70) did what it
  was supposed to do, and this is the first hard evidence of it. The one remaining page is
  `/recipes` — the hub itself, linked from the homepage only.
- **On-page gaps** put numbers on two open cards: the ten seed recipes crawl at **92–101
  words** each (#6389), and the homepage renders **19 words** of body text to a
  non-JS crawler (#6821).

That last pair is the useful part. Both cards existed before this run; neither had a
number attached, and "thin" is much easier to deprioritise than "94 words".

---

## Porting this to another project

The procedures are stack-agnostic — Screaming Frog crawls HTML over HTTP and does not care
what served it. To adopt:

1. Copy `SEO_RUNBOOK.md`, `scripts/seo_crawl_diff.py`, `scripts/seo_weekly_crawl.sh`, and
   `tests/test_seo_crawl_diff.py`.
2. Rewrite **Site constants**. Change `SITE` in `seo_weekly_crawl.sh`.
3. Capture a baseline crawl before changing anything, and commit it. The August baseline
   here is worth more than any single fix that followed it.
4. Delete the site-specific carve-outs — the `aggregateRating` decision, the seed-recipe
   thinness, the `react-my-burger` warning. Keep the *rules*; drop this site's history.
5. Re-check the free-tier limits. A larger site will blow the 500-URL cap immediately, and
   the runbook's central claim — that one of the four tools is fully automatable — stops
   being true the moment it does.
