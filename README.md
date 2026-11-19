# Nordic CCS Supply-Chain Monitor

**Public page (view only, no login):** https://chtolly.github.io/ccs-research-monitor/
**Review space (editors and approvers):** https://chtolly.github.io/ccs-research-monitor/review/

A web page that is updated **once a week** with new research, technical reports, news, projects, policy and events on CO₂ capture, transport and storage networks. It was built for the CCS supply-chain optimisation project at BI Norwegian Business School and its Swedish partners. It runs in the cloud, so nobody's laptop needs to be on.

Nothing goes on the public page by itself. Everything new, whether Claude found it or a person submitted it, waits in the review space until an approver approves it.

Maintained by Kshitij Chaudhary.

---

## Guide for editors (how to submit an item)

1. Open **https://chtolly.github.io/ccs-research-monitor/review/** and click **Log in with my email**.
2. Type your email address. You get an email with a 6-digit code from Cloudflare (`noreply@notify.cloudflare.com`). Type the code. You are back on the review page and stay logged in for 14 days.
   - No email? Check your spam folder. Some university mail filters hold these emails; ask your IT desk to allow `noreply@notify.cloudflare.com`.
   - "Not on the list"? Ask Kshitij or Erna to add you.
3. Click **Submit an item** and fill in the form:
   - **Link**: the page where people can read it (the original source if possible). If the item is already in the monitor, waiting for review or was rejected before, you see a warning straight away.
   - **Title**: exactly as on the page, in its original language. Add an English title if it is not in English.
   - **Source**, **publication date**, **content type** (news, research paper, technical report, or conference/course/call) and **topic**.
   - For events: the event or deadline date. For reports: tick **Paywalled** if the full report is not free.
   - **Short summary**: 2–3 neutral sentences, only facts from the page. **Why it matters** is optional.
4. Click **Submit for review**. The link is checked automatically. Your item now waits for an approver. **My submissions** shows whether it was approved.

Editors cannot publish anything themselves.

## Guide for approvers (how to review)

1. Log in the same way (see above). You start on **Waiting for review**.
2. Every Monday morning the agent adds the week's items; editors add items any time. Each card says who found it ("Found by Claude (weekly run …)" or "Submitted by …").
3. For each item, open the link and check it. Then:
   - **Approve**: it appears on the public page within a few minutes.
   - **Edit**: fix the title, summary, topic and so on, then **Save changes** or **Save and approve**.
   - **Reject**: optionally give a reason. It disappears and will never be suggested again.
   - **Related links** ("You might also be interested in"): they are approved together with the item. Untick any you don't want before approving.
   - Many good items? Tick them and click **Approve selected**.
4. Sometimes there is a card "New related links for a published item". These are suggestions for an item a person submitted earlier. Approve or reject them the same way.
5. **People**: add editors and approvers (email, name, role), change a role, or remove someone. A new person can log in at once; a removed person is locked out at once. There is always at least one approver, and you cannot remove yourself.
6. **Activity log**: who submitted, edited, approved, rejected or added people, and when.

The public page records who approved each item (by name, never by email), in `data/approved/`.

---

## Guide for the maintainer

### What happens every week

| When (Oslo time) | What happens | Where it runs | Cost |
|---|---|---|---|
| Monday from 05:30 | **Collect.** A script reads about 75 news feeds, news and report pages and event lists, plus the OpenAlex and Crossref research databases. It keeps only items that are new since last week and not already published, rejected or waiting for review, and saves them to `data/candidates.json`. **When it finishes, it starts Claude.** GitHub often starts this late; if the list is missing, Claude runs the collector itself in its own sandbox. | GitHub Actions ("1 · Collect candidates") | Free |
| right after | **Select and summarise.** Claude screens the candidates, runs up to 40 web searches, opens pages to check titles and dates, writes summaries, labels each item (news, paper, technical report, event; paywalled or not), finds 3–5 related links per item, and saves a draft in `data/inbox/`. Its instructions are in `ROUTINE.md`. Claude's own schedule (Monday 06:00 and 09:00 Oslo in winter, 07:00 and 10:00 in summer) also starts it; if this week's run is done, that run stops at once. | A Claude Code scheduled task ("routine") on Kshitij's Claude subscription | Subscription usage, no extra bill |
| ~15 min later | **Check and send to review.** A script checks every link (also the related links), drops dead links, made-up titles, repeats and anything rejected before, and sends the rest to the review queue. | GitHub Actions ("2 · Verify & publish") | Free |
| any time | **Approve / reject** in the review space. Each approval rebuilds the public page within a few minutes. | Cloudflare (review space) + GitHub Actions | Free |
| Monday 12:00, again 15:15 | **Safety check.** Emails you if this week's run did not reach the review queue, was made from an old list, or a source failed 2 weeks in a row. | GitHub Actions ("3 · Check this week's run") | Free |

The GitHub schedules use `timezone: Europe/Oslo`, so they stay at the same Oslo time in summer and winter. Claude's own schedule is in UTC (`0 5,8 * * 1`), so it runs an hour later in summer; that is only the backup.

### Sources

All sources are in `config.yaml` (edit it on github.com with the ✏️ pencil, then **Commit changes**):
- `feeds`: RSS feeds. `type: news` or `type: report` (technical reports), `filter: true` = keep only entries that mention a keyword, `paywalled: true` = reports there are usually not free.
- `watch_pages`: news and report pages without a feed. New links are passed to Claude. `filter: true` = only links whose text mentions a keyword (for general pages such as DNV or the Port of Rotterdam).
- `event_pages`: conference and course lists. `claude_checks`: pages Claude opens itself because they block the collector.
- `search_topics`: what Claude searches for. `keywords`: the keyword filter and paper searches.
- Sources tested and found unusable are listed in comments, with the reason.

**To add a news source:** add a line under `feeds:` with the site's RSS address (many WordPress sites have `https://site.com/feed/`).

### Limits per weekly run (`config.yaml → settings`)

`max_items_per_run: 60`, `max_web_searches: 40`, `max_page_visits: 120`, `max_related_searches: 20`, `related_links_per_item: 5`, `max_candidates_for_claude: 400`.

### Roles: adding and removing people

Do it on the **People** tab of the review space (approvers only). Nothing else is needed: Cloudflare sends a login code to any email address, and the review space only lets in people on that list. Every page and action checks the role again on the server, so an editor can never approve.

### Cost

- **GitHub:** free (public repository: Actions and Pages are free).
- **Cloudflare (review space):** free plan. The Zero Trust free plan (the email-code login) allows up to 50 people; Cloudflare asks for a card on file but does not charge for the free plan. The Workers and database free limits (100,000 requests a day) are far above what this uses; going over them does not cost money, requests just fail until the next day.
- **Claude:** no separate bill. The weekly run counts toward the Claude subscription's usage, like a normal Claude Code session. One weekly run is bigger than one old daily run, but there is one run instead of seven, so overall usage goes down. See usage at https://claude.ai/settings/usage. If the limit is reached, that week's run is skipped and the safety check emails you.
- **If it ever moved off the subscription** to a paid Anthropic API key: roughly $4–8 per weekly run on Claude Opus (about $17–35 a month), about 60% less on Sonnet (estimates from November 2026).

### Common tasks

**Run the weekly update now:** on GitHub, **Actions → "1 · Collect candidates" → Run workflow** (tick "redo" if this week's run is already done). It starts Claude automatically; the items reach the review space 20–40 minutes later.

**Change the day or time:**
- Collector: `cron: "30 5 * * 1"` in `.github/workflows/1-collect.yml` (Oslo time; `1` = Monday).
- Claude's backup run: at https://claude.ai/code/routines, open the task, **Edit**, change the schedule (UTC).
- Safety check: `.github/workflows/3-freshness.yml`. Keep it after Claude's backup run.

**Change the Claude model:** at https://claude.ai/code/routines → the task → **Edit** → model. It uses Claude Opus 5.5. Sonnet uses less of the allowance.

**Pause it:** turn the routine's toggle off at https://claude.ai/code/routines, and on GitHub disable the three workflows (**Actions** → workflow → **⋯ → Disable workflow**). The public page stays online.

### One-time setup of the review space (Cloudflare)

Done once by the maintainer; the steps are here in case it has to be redone.

1. Create a free Cloudflare account at https://dash.cloudflare.com/sign-up. In the dashboard open **Zero Trust**, choose a team name, pick the **Free** plan (card on file, $0).
2. In a terminal: `cd editor && npm install && npx wrangler login` (a browser tab asks you to allow access).
3. `npx wrangler d1 create ccs-review`, put the printed `database_id` into `editor/api/wrangler.jsonc`, then `npx wrangler d1 execute ccs-review --remote --file api/schema.sql -c api/wrangler.jsonc`.
4. Add the first approver: `npx wrangler d1 execute ccs-review --remote -c api/wrangler.jsonc --command "INSERT INTO people (email,name,role,added_by,added_at) VALUES ('you@bi.no','Your Name','approver','setup',datetime('now'))"`.
5. Secrets (each command asks for the value; nothing is shown or saved in files):
   - one long random value for `SESSION_SECRET`, set in **both** Workers: `npx wrangler secret put SESSION_SECRET -c api/wrangler.jsonc` and `… -c login/wrangler.jsonc`
   - one long random value for `INGEST_SECRET` in the API Worker, and the **same** value as the GitHub repository secret `REVIEW_INGEST_KEY`
   - `GITHUB_TOKEN` in the API Worker: a fine-grained GitHub token for this repository only, with **Contents: read and write**.
6. `npm run deploy`. Put the two `workers.dev` addresses into `config.yaml → review` (`api_url`, `login_url`).
7. In Cloudflare: **Workers & Pages → ccs-review-login → Access → Protect this Worker behind Access → All traffic**, with a policy that allows everyone to log in by one-time PIN (the people list in the review space decides who gets in).

### Handing it over to someone else

1. **GitHub:** **Settings → General → Transfer ownership**, or add the person under **Settings → Collaborators**. After a transfer the link changes to `https://<new-owner>.github.io/ccs-research-monitor/`; update `base_url` in `config.yaml`, `REVIEW_URL` in `editor/login/wrangler.jsonc` and `SITE_URL`/`ALLOWED_ORIGINS` in `editor/api/wrangler.jsonc`.
2. **Cloudflare:** add the new maintainer as a member of the Cloudflare account, or redo the one-time setup in their account.
3. **Claude:** the new maintainer needs a Claude Pro, Max, Team or Enterprise plan. They connect GitHub at https://claude.ai/code, install the Claude GitHub app on the repository, and create a scheduled task at https://claude.ai/code/routines with the repository, the schedule `0 5,8 * * 1` (UTC), and this prompt: *"You are the weekly editor of the Nordic CCS Supply-Chain Monitor. Follow the instructions in ROUTINE.md in this repository exactly."* The old maintainer turns their task off.
4. **Email alerts:** GitHub emails whoever last edited the schedule line in each workflow file; the new maintainer should make a small edit to those files.

### Troubleshooting

| Symptom | Where to look |
|---|---|
| Email "3 · Check this week's run failed" | The message says which step did not run. Check **Actions** on GitHub and the run history at claude.ai/code/routines (usage limit? GitHub app disconnected?). |
| Workflow 2 fails with "review queue is not configured" | The secret `REVIEW_INGEST_KEY` or `config.yaml → review → api_url` is missing. The draft is kept and sent on the next run. |
| Approving says "Could not save to GitHub" | The `GITHUB_TOKEN` in the API Worker has expired or lost access. Create a new one (setup step 5). Nothing was changed. |
| An item looks wrong on the public page | Fix it in `data/approved/<id>.json` on github.com, or delete that file to take it off the page. |
| What did the agent drop and why? | `data/runs/<date>.json` lists every item sent to review and every item dropped, with the reason. |
| A source stopped working | The collector logs each source as ✓ or ✗ in the "1 · Collect candidates" run. |

### Technical notes

- `scripts/collect.py` collects candidates (no AI). `scripts/build.py` checks drafts, sends them to the review queue and builds the site from `data/approved/` only. `scripts/common.py` holds shared helpers.
- `data/approved/<id>.json`: one file per public item, with `submitted_by`, `submitted_at`, `approved_by`, `approved_at`. `data/rejected/<id>.json`: rejected items (never suggested again). `data/runs/`: one record per agent run (the daily runs before 19 Nov 2026 are there too). `data/history.json` and `data/recent_titles.json` are rebuilt from approved and rejected items and used to avoid repeats. The 27 items published before the review step were migrated as approved (`scripts/migrate_to_review.py`).
- The review space: `site/review/` (static page on GitHub Pages) talks to two Cloudflare Workers in `editor/`: `login` (behind Cloudflare Access; signs a 14-day session) and `api` (pending queue, people and activity log in a private D1 database; writes approvals to the repository through the GitHub contents API). Pending items are never in the public repository.
- The public page is static HTML in `_site/`, deployed by GitHub Actions to GitHub Pages. Search and the topic filter run in the browser over `items.json`; the related-links dialog is a native `<dialog>` (keyboard, Esc, close button, full-width on phones). Search engines are asked not to index it.
- Local test of everything: see `editor/test/` (`api_test.py` runs 50 checks against a local `wrangler dev`, with `mock_github.py` standing in for GitHub).
