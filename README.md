# Nordic CCS Supply-Chain Monitor

**Link to share:** https://chtolly.github.io/ccs-research-monitor/

A web page that updates itself every morning with new research, projects, policy and events on CO₂ capture, transport and storage networks. It was built for the CCS supply-chain optimisation project at BI Norwegian Business School and its Swedish partners. It runs in the cloud, so nobody's laptop needs to be on.

Maintained by Kshitij Chaudhary.

---

## How it works (three steps every morning)

| Time (Oslo, winter / summer) | What happens | Where it runs | Cost |
|---|---|---|---|
| 06:30 / 07:30 | **Collect.** A script reads about 25 news feeds and organisation pages, plus the OpenAlex and Crossref research databases. It keeps only items that are new and match the keywords, and saves them to `data/candidates.json`. | GitHub Actions (workflow "1 · Collect candidates") | Free |
| 07:00 / 08:00 | **Select and summarise.** Claude reads the candidates, runs up to 15 web searches for news the feeds missed, drops anything off-topic or already published, writes the summaries, and saves a draft in `data/inbox/`. Its instructions are in `ROUTINE.md`. | A Claude Code scheduled task ("routine") on Kshitij's Claude subscription | Uses the subscription's normal usage. No API key, no extra bill |
| ~07:15 / 08:15 | **Check and publish.** A script checks every link (dead or unverifiable links are removed), removes duplicates, saves the day's digest, and rebuilds the website and RSS feed. | GitHub Actions ("2 · Verify & publish") | Free |
| 08:30 / 09:30 | **Safety check.** If today's update is missing, this step fails and GitHub sends an email. | GitHub Actions ("3 · Check today's update") | Free |

Claude's schedule is fixed in UTC, so the update comes an hour later in summer.

**If something fails,** the website keeps showing the previous day's digest. GitHub then emails whoever last edited the workflow files.

**Quality rules built in:**
- Every item must come from a page that was actually retrieved.
- Items from feeds and databases must match the collected title and link exactly.
- Items found by web search must open, and the page must contain the claimed title. Their publication date is read from the page itself, and anything older than the window is dropped.
- Papers without an abstract yet get an honest one-line description instead of invented findings.
- Nothing already published in the last few months is repeated.
- If nothing relevant is new, the page says "No significant new items today".

## Files you might want to edit

You can edit any of these on github.com: open the file, click the ✏️ pencil, then **Commit changes**.

| File | What it controls |
|---|---|
| `config.yaml` | **Sources** (`feeds`, `watch_pages`), **keywords** (`keywords: core`), **paper searches** (`paper_queries`), Claude's **search topics** (`search_topics`), **limits** (`max_web_searches`, `max_items_per_day`), category names, and the page title and "maintained by" text. Each section has comments explaining it. |
| `ROUTINE.md` | Claude's instructions: what counts as relevant, how to write summaries, and the format of the draft. Edit it in plain English. Changes apply from the next morning. |

**To add a news source:** add a line under `feeds:` with the site's RSS address. Many WordPress sites have one at `https://site.com/feed/`. Use `filter: false` if the source is only about CCS, or `filter: true` to keep only entries that mention a keyword.

## Common tasks

**Run an update now (outside the schedule)**
1. On GitHub, go to **Actions → "1 · Collect candidates" → Run workflow**.
2. A minute later, go to https://claude.ai/code/routines, open **"CCS research monitor – daily digest"** and click **Run now**.
3. The website updates about 10–20 minutes later.

**Change the time**
- **Claude:** at https://claude.ai/code/routines, open the task, click **Edit**, and change the schedule. Times are in UTC: 06:00 UTC is 07:00 Oslo in winter and 08:00 in summer.
- **Collector:** change `cron: "30 5 * * *"` in `.github/workflows/1-collect.yml`. Keep it about 30 minutes before Claude's time.
- **Safety check:** change `cron: "30 7 * * *"` in `.github/workflows/3-freshness.yml`.

**Change the Claude model**
At https://claude.ai/code/routines, open the task, click **Edit**, and pick another model in the model selector. It currently uses Claude Opus 5.5. Sonnet uses less of the subscription allowance, and Opus is a bit stricter about relevance.

**Pause it**
- At https://claude.ai/code/routines, turn the task's toggle **off**.
- On GitHub, go to **Actions**, click each of the three workflows, then **⋯ → Disable workflow**.

The website stays online, showing the last digest. Turn everything back on the same way.

**Shut it down completely**
Delete the routine at claude.ai/code/routines. Then on GitHub, go to **Settings → Pages** and unpublish, or delete the repository.

## Cost

- **GitHub:** free. Public repositories get free Actions and Pages.
- **Claude:** no separate bill. Each morning's run counts toward the Claude subscription's usage, just like a normal Claude Code session. You can see usage at https://claude.ai/settings/usage. If the usage limit is reached, that morning's run is skipped. The page then keeps the previous digest, and the safety check emails you.
- **Alternative:** if someone later wants it off the personal subscription, the Claude step can be moved to the Anthropic API with a paid API key. That would cost roughly $20–40 a month on Opus or $12–22 on Sonnet (estimates from November 2026). Only step 2 would change.

## Handing it over to someone else

The routine belongs to one person's Claude account, and the repository to one GitHub account. To hand over:

1. **GitHub:** go to **Settings → General → Transfer ownership** and transfer the repository to the new person or to a BI GitHub organisation. Or add them under **Settings → Collaborators**. The link changes to `https://<new-owner>.github.io/ccs-research-monitor/` after a transfer. Update `base_url` in `config.yaml` to match.
2. **Claude:** the new maintainer needs a Claude Pro, Max, Team or Enterprise plan.
   1. They connect GitHub at https://claude.ai/code and install the Claude GitHub app on the repository.
   2. They create a scheduled task at https://claude.ai/code/routines with:
      - the repository
      - the schedule `0 6 * * *` (UTC)
      - the model of their choice
      - this prompt: *"Follow the instructions in ROUTINE.md in this repository exactly."*
   3. The old maintainer then turns their own task off.
3. **Email alerts:** GitHub emails whoever last edited the schedule line in each workflow file. The new maintainer should make a small edit to those files, for example a comment, so the alerts go to them.

## Troubleshooting

| Symptom | Where to look |
|---|---|
| Email "3 · Check today's update failed" | The error message says which step didn't run. Check **Actions** on GitHub and the run history at claude.ai/code/routines. Common causes are a reached usage limit, a disconnected GitHub app, or a temporary outage. |
| An item looks wrong | Each day's full record, including which items were dropped and why, is in `data/digests/<date>.json`. Tighten the rules in `ROUTINE.md` if needed. |
| A source stopped working | The collector logs each source as ✓ or ✗ in the "1 · Collect candidates" run on GitHub Actions. |

## Technical notes

- `scripts/collect.py` is the collector (Python, no AI). `scripts/build.py` does the verification and builds the site. `scripts/common.py` holds shared helpers.
- `data/history.json` lists every published item (used to avoid repeats). `data/digests/` holds one file per day. `data/inbox/` holds Claude's raw drafts. `data/state.json` records the last published date.
- The site is static HTML in `_site/`, deployed by GitHub Actions to GitHub Pages. Search and the category filter run in the browser over `items.json`. Search engines are asked not to index it (`noindex` and `robots.txt`).
- To test locally: `python -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python scripts/collect.py && .venv/bin/python scripts/build.py`.
