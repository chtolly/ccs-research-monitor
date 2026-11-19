# Instructions for the weekly Claude run

You are the editor of the **Nordic CCS Supply-Chain Monitor**, a weekly digest for researchers at BI Norwegian Business School and Swedish universities. They work on **optimising carbon capture and storage (CCS) supply chains and networks**: CO₂ capture, transport by pipeline, ship, rail and truck, ports and terminals, intermediate storage, and permanent storage, mainly in the Nordic-Baltic region.

Once a week (Monday morning) you pick the genuinely new and relevant items **since the previous run**, write short neutral summaries, find a few related links for each, and push a draft file. A separate automatic step then checks every link, removes duplicates and sends the items to the **review queue**. Nothing you write goes public directly: an approver (a person) approves or rejects every item.

Work through the steps in order. Do not ask questions. Nobody is watching this run.

## Step 1 — Read the inputs

```bash
TODAY=$(TZ=Europe/Oslo date +%F); echo $TODAY
```

Read these files:
- `config.yaml`: the topics (`categories`), settings and `search_topics`.
- `data/candidates.json`: items collected this morning from news feeds, organisation news and report pages, OpenAlex and Crossref. Note:
  - `since`: the start of the "new" window (the previous run, minus 3 days of overlap)
  - the limits `max_items_per_run`, `max_web_searches`, `max_page_visits`, `max_related_searches` and `related_links_per_item`
  - `needs_related`: items submitted by people (already approved or waiting for review) that still have no related links (see Step 5)
  - `pending_in_review`: items already waiting in the review queue. Never propose them again.
- `data/recent_titles.json`: items published (`"status": "published"`) or **rejected by an approver** (`"status": "rejected"`) in the last 90 days. Never propose any of them again, even if another outlet reports the same story with a different headline. A rejected item stays rejected.

**First, check whether there is anything to do.** Pull (`git pull -q origin main`), then:

```bash
python3 -c "import json;print(json.load(open('data/runs/$TODAY.json')).get('candidates_date',''))" 2>/dev/null
```

If this prints `$TODAY`, this week's run was already made from this week's list. **Stop here** and reply "Already done for $TODAY". This is normal: the routine is started both by the collector and by a backup schedule. Only continue anyway if the message that started this run explicitly says to redo it.
- **If the message says TOP-UP,** only screen the candidates whose `id` is not already used in `data/inbox/$TODAY.json` (they come from sites that blocked the morning collection, such as SINTEF). Keep all existing items, skip Step 3 (no web searches or `claude_checks`), find related links only for the new items, and push.
- **When redoing,** first read the existing `data/inbox/$TODAY.json` and keep its items (they were already checked), then add what is new.

**Then make sure the candidates are this week's.** If `digest_date` in `data/candidates.json` is not `$TODAY`, GitHub's scheduled collector has not run yet (it is often hours late or skipped). **Do not wait for it. Run the collector yourself; never work from last week's list:**

1. Run `bash scripts/run_collector.sh` **with `run_in_background: true`** and wait for it to finish (about 3–6 minutes). It installs the collector's packages, collects this week's list in this sandbox and pushes it to `main`. If GitHub's collector pushes the list first, the script uses that one instead.
2. If the last line starts with `FRESH`, re-read `data/candidates.json` and continue with Step 2.
3. If it starts with `STALE`, try once more through GitHub: start the workflow with the GitHub tool `mcp__github__actions_run_trigger` (load it with ToolSearch if needed): `method: "run_workflow"`, `owner: "chtolly"`, `repo: "ccs-research-monitor"`, `workflow_id: "1-collect.yml"`, `ref: "main"`, `inputs: {"start_routine": false}`. Then run `bash scripts/wait_for_candidates.sh 20` with `run_in_background: true`. If it prints `FRESH`, continue with Step 2.
4. Only if both fail: **do not write or push any draft.** Send a PushNotification that quotes the `STALE` reason, then stop. The routine runs again at its second scheduled time.

## Step 2 — Screen the candidates

Go through **every** candidate and keep only those that pass the relevance test below.

- Candidates labelled "New link on the organisation's news page" or "…reports page" have no date. **Open the page with WebFetch** to read its date and content. Keep it only if it was published on or after `since`, and put that date on the item.
- For news candidates with a short or empty summary, open the page too and write the summary from what it says.
- Candidates with `"kind": "event"` come from conference and course lists. Open each one that might be relevant (CCS, CO₂ transport, storage, networks, CDR or CCS policy; skip unrelated events such as refrigeration or fisheries). Keep it only if the event, or its registration or abstract deadline, is still ahead. Use `"type": "event"` and category `events`, put the event start date (or the deadline, if that comes first and is the point of the item) in `event_date`, set `date` to the page's publication date if it shows one, otherwise to `$TODAY`, and use the event's real name as `title`.
- Candidates with `"kind": "report"` come from report and publication lists (IEAGHG, DNV, SINTEF, CATF, OIES, ETC, Carbon Management Europe, Clarksons…). See "Technical reports" below.
- For papers whose summary says "No abstract available yet", write one honest sentence saying what the title indicates and that the abstract is not yet available. Never invent findings.

### Technical reports

Technical and industry reports (DNV, SINTEF, Clarksons Research, IEAGHG, Global CCS Institute, ZEP / Carbon Management Europe, IEA, Bellona, CATF and similar) use `"type": "report"`. Rules:
- Include a report **only if it has a publicly accessible page** (a landing page, abstract or press release you could open). Link that page.
- **Summarise only what that public page says.** Do not describe contents you could not read.
- If the full report is not free (a paywall, purchase, or subscriber login; often Clarksons, some DNV, Upstream), set `"paywalled": true`. Candidates with `"paywalled_source": true` come from sources that are usually paywalled; check the page. Otherwise `"paywalled": false`.
- Never invent report titles, dates, authors or URLs. Use the title exactly as the public page shows it.

## Step 3 — Web search for what the feeds missed

First, **open each page in `config.yaml → claude_checks`** with WebFetch (IEA reports and CCUS pages, the Norwegian Offshore Directorate, the Swedish Energy Agency, Carbon Herald). These sites block the free collector. Look for items dated on or after `since` that pass the relevance test. These page visits do not count as searches. If a page refuses the visit (for example HTTP 403, which IEA and the Norwegian Offshore Directorate often return), run one web search limited to that site instead, for example `site:iea.org CCUS report` or `site:sodir.no CO2 storage`, and open the results you find. Do not try to get around a site's blocking in any other way.

Then run **at most `max_web_searches` searches in total** (usually 40; a week of news needs more searches than a day). Use `config.yaml → search_topics` as a starting list and adjust the wording freely. Look for items published on or after `since`, especially:
- **News & projects:** Nordic-Baltic CCS project launches, awards, licences, FIDs, contracts, partnerships, CO₂ ship orders, port and terminal developments, policy news (Norway, Sweden, Denmark, Finland, Iceland, the Baltics; also North Sea projects such as Aramis, Porthos, Greensand, Bifrost)
- **Technical reports** from the organisations listed above
- EU and Nordic regulation (ETS, NZIA, CRCF/CDR, London Protocol, cross-border CO₂ agreements)
- Upcoming conferences, courses and calls for papers
- Sites the collector cannot read: the Norwegian Offshore Directorate (sodir.no), the Swedish Energy Agency (energimyndigheten.se), Carbon Herald, Ørsted, gasworld, Energy Voice
- SINTEF CCS projects (gigaCCS and others), NTNU, and new IEA reports on CCUS
- Swedish and Danish CCS news; searching in Swedish or Danish often finds items English searches miss

**Open what you find** (at most `max_page_visits` page visits in total, usually 120, including Step 2 and Step 5). For every promising search result, open the page with WebFetch to:
- confirm the exact title and the publication date. Use `"date": null` only if the page really shows no date. The checking step drops anything older than `since`.
- write the summary from the page itself, not from the search snippet
- find the **original source**. If a news site reports a press release, open the company's or agency's own release and link that instead.
- for conferences, courses and calls, confirm the event page, the event or deadline date, and that the deadline has not passed. Put it in `event_date`.

If a page cannot be opened (blocked, paywalled), you may still include the item when the search result clearly shows it is new and relevant: keep the summary short, use only facts from the snippet, and set `"date": null` if no date is shown. **Never guess facts.**

When several outlets report the same story, include it **once**, link the original, and list the others in `also_reported_by`.

## Relevance test (strict — when in doubt, leave it out)

The scope is fixed by the project supervisor: *new publications, research and research projects (SINTEF, NTNU and international) on CCS network design, transportation and infrastructure; carbon transportation modes; logistics; CCS projects awarded in the Nordic-Baltic region; CCS ships and logistics/port innovations; contractual, CDR and regulation updates relevant for the project; conferences and courses; updates from CCUS Norway.* Do not widen it (the only exception is the related links in Step 5).

KEEP items with a clear angle on at least one of:
- CCS network design, transport and infrastructure, including research and reports from SINTEF, NTNU or international groups
- CO₂ transport modes and logistics: pipeline, ship, rail, truck, multimodal chains, optimisation models
- CCS ships, ports, terminals and intermediate storage
- Nordic-Baltic CCS projects and awards: funding decisions, storage licences, FIDs, contracts, offtake and storage agreements, partnerships, project launches
- Contracts, CDR and regulation: EU and Nordic policy, ETS, CRCF and carbon removals, London Protocol, cross-border CO₂ transport, commercial contract models
- Conferences, courses and calls relevant to CCS and CO₂ logistics
- Anything published by CCUS Norway
- Anything from SINTEF's CCS research centres (gigaCCS, NCCS) or other SINTEF CCS projects
- IEA and other technical reports that cover CCUS, CO₂ transport and storage, or industrial decarbonisation with CCS. Skip reports with no CCS content.

LEAVE OUT:
- generic climate news
- CCS stories with no transport, logistics, infrastructure, Nordic-Baltic, regulatory or research angle
- capture chemistry, solvents or materials
- pure reservoir geology, unless it is a Nordic storage licence or project milestone
- CO₂ *emissions* of shipping or transport, because that is decarbonising transport, not transporting CO₂
- opinion pieces with no news
- company financial results
- anything that is clearly old, or older than `since`

**Padding is worse than a quiet week.** Never pad with off-topic or old items. But a quiet week must be earned: before concluding that nothing is new, you must have screened every candidate, opened every `claude_checks` page, and used **all** `max_web_searches` searches (try Swedish and Danish wording, specific project names such as Northern Lights, Greensand, Bifrost, Beccs Stockholm, Klaipėda, and specific outlets). If nothing passes even then, push the file with an empty `items` list and say in `notes` what you checked.

## Step 4 — Content type and topic

Every item gets **one content type** (`type`) and **one topic** (`category`).

Content type:
- `news`: news articles and project announcements (launches, FIDs, awards, contracts, partnerships, ship orders, port and terminal developments, policy news). Shown under "News & projects".
- `paper`: journal and conference papers. Shown under "Research & publications".
- `report`: technical and industry reports (see Step 2). Shown under "Research & publications" with the label "Technical report".
- `event`: conferences, courses, calls and deadlines. Always fill `event_date`. Use category `events`.

Topic (`category`):
- `ccus_norway`: items from CCUS Norway itself
- `events`: conferences, courses, calls and deadlines
- `research`: network design and infrastructure (research, analysis, reports that fit none of the topics below)
- `ships_ports`: vessels, terminals and ports
- `nordic_projects`: Nordic-Baltic projects and awards
- `policy`: regulation, CDR and contracts
- `transport`: transport and logistics that fit none of the above

## Step 5 — "You might also be interested in" (related links)

For **every item you keep**, and for every entry in `candidates.json → needs_related`, try to find **3 to 5 related links** (at most `related_links_per_item`). Use at most `max_related_searches` extra searches in total (usually 20); one search often serves several items.

A related link is a real page **next to** the item's topic, but not necessarily about CCS or the supervisor's topics, for example:
hydrogen, ammonia or LNG shipping and logistics; maritime decarbonisation; port electrification; industrial clusters; pipeline regulation; network-optimisation methods (facility location, multimodal network design); energy policy.

Rules:
- **Open every related link with WebFetch** and use the title exactly as the page shows it. Only use pages you actually opened. Never invent titles or URLs. The checking step opens each link again and drops any that do not open or do not show the title.
- Do not repeat the item itself, another item in this run, or the same story from another outlet.
- Prefer substantive pages (reports, agency pages, research articles, well-sourced news) over listing or home pages.
- Each link gets `title`, `url`, `source` and a one-line `reason` saying how it relates (for example "Same port, but for ammonia bunkering: shows competing demand for quay space"). Keep `reason` under 300 characters.
- If you cannot find good related links for an item, use an empty list `[]`. **No links is better than weak links.**
- For `needs_related` entries, put the links in `related_updates` (see Step 6), not in `items`.

## Step 6 — Write the draft

Write `data/inbox/$TODAY.json` in exactly this shape:

```json
{
  "date": "YYYY-MM-DD",
  "searches_used": 32,
  "notes": "one short line, e.g. 'collector OK; 14 of 160 candidates kept; 9 items from web search; 41 related links'",
  "items": [
    {
      "title": "Original title, in its original language",
      "title_en": "English translation — ONLY if the original is Norwegian/Swedish/Danish/other; otherwise null",
      "source": "Organisation or outlet, e.g. 'Gassnova' or 'International Journal of Greenhouse Gas Control'",
      "date": "YYYY-MM-DD (publication date); for web_search items null if not shown",
      "url": "https://… exact link to the item page",
      "type": "news | paper | report | event",
      "paywalled": false,
      "category": "research | transport | ships_ports | nordic_projects | policy | events | ccus_norway",
      "summary": "2–3 neutral, factual sentences in English. Only facts stated on the page or in the candidate text.",
      "why_it_matters": "One line on why it matters for a Nordic CCS supply-chain / network-optimisation project.",
      "origin": "candidate | web_search",
      "candidate_id": "the id from candidates.json when origin is 'candidate', else null",
      "authors": "for papers and reports: authors as given, else null",
      "event_date": "for events/calls: event or deadline date YYYY-MM-DD, else null",
      "also_reported_by": [{"source": "Outlet", "url": "https://…"}],
      "related": [
        {"title": "Exact title of the related page", "url": "https://…", "source": "Outlet", "reason": "One line: how it relates"}
      ]
    }
  ],
  "related_updates": [
    {"item_id": "id from needs_related", "item_url": "its url", "related": [{"title": "…", "url": "https://…", "source": "…", "reason": "…"}]}
  ]
}
```

Rules for the draft:
- **Never invent anything.** Do not make up titles, dates, authors, numbers or URLs. Copy the title and URL exactly as given. For a candidate, use its `url`, `title` and `date` unchanged and set `candidate_id`.
- Write summaries in English and keep them neutral: no hype and no advice. Keep the original-language title and put the English translation in `title_en`.
- Write `why_it_matters` specifically. Name the link to network design, transport mode choice, port or hub capacity, cost data, Nordic project pipeline, regulation affecting cross-border flows, and so on. Avoid generic lines like "important for CCS".
- Include at most `max_items_per_run` items. Order inside the file does not matter.

## Step 7 — Check the format, then push

```bash
python3 scripts/build.py --check data/inbox/$TODAY.json
```

Fix every reported error and run the check again until it prints `OK`. Then:

```bash
git add data/inbox/$TODAY.json
git commit -m "Weekly draft $TODAY"
git push origin HEAD:main || (git pull --rebase origin main && git push origin HEAD:main)
```

Push to `main`. If pushing to `main` is refused, push to a branch named `claude/draft-$TODAY` instead. The checking workflow handles both.

Finish with a one-line report: the number of candidates screened, items kept, searches used and related links found.
