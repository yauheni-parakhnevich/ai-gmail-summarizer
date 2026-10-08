---
description: Summarize unread Gmail job emails, scrape vacancies, match against CV, generate report
argument-hint: <profile-name>
allowed-tools: [Read, Write, Glob, Grep, Bash, WebFetch]
---

# Gmail Job Summarizer

You are running a multi-step pipeline that processes unread Gmail emails, identifies job-related ones, scrapes vacancy pages, matches them against a candidate's CV, and produces an HTML report. Follow each step carefully and report progress as you go.

**IMPORTANT**: All mailbox operations (fetch, mark as read) MUST use the profile's own credentials (Gmail OAuth and/or IMAP) via the standalone Python tools. NEVER use MCP Gmail tools — they may be connected to a different account.

**Profile name**: `$ARGUMENTS`
**Profile directory**: `profiles/$ARGUMENTS/`

---

## Step 0: Load Profile Configuration

1. Read `profiles/$ARGUMENTS/config.yaml` and extract:
   - `recipient_email` (required)
   - `min_fit_percentage` (default: 30)
   - `accounts` (optional list of email accounts: `type: gmail` and/or `type: imap` entries; if absent, the profile has a single Gmail account)
   - `linkedin_email`, `linkedin_password` (optional, for scraper fallback)
   - `xing_email`, `xing_password` (optional, for scraper fallback)
2. Read `profiles/$ARGUMENTS/cv.md` — this is the candidate's CV. Store its full content for later matching.
3. Read `profiles/$ARGUMENTS/matcher_instructions.md` if it exists — these are custom rules for scoring vacancies.
4. **If the profile directory or cv.md does not exist, tell the user what's missing and stop.**

---

## Step 1: Fetch Unread Emails Using Profile Credentials

Fetch unread emails from **all** accounts configured for the profile (Gmail via the profile's own OAuth credentials, IMAP accounts such as Migadu via the credentials in `config.yaml`):

```bash
uv run python tools/mail_fetch.py --profile PROFILE_NAME
```

Replace `PROFILE_NAME` with `$ARGUMENTS`. This outputs one merged JSON array of emails with fields: `id`, `subject`, `sender`, `date`, `body_text`, `body_html`, `account`. The `id` is prefixed with the account it came from (`gmail:<msgid>` or `<imap-email>:<uid>`) — **keep the full prefixed id**, Step 8 needs it to mark the right mailbox.

If one account fails to fetch, a warning is printed to stderr and the other accounts are still returned — report the warning but continue.

Parse the JSON output. Report: "Found N unread emails." (per account if more than one).
**If no unread emails (empty array), report "No unread emails found." and stop.**

---

## Step 2: Classify Emails

Examine each email and identify which are **job-related**. You are an email classifier.

**Include emails that:**
- Directly advertise jobs (e.g. "new position", "we're hiring", "Stellenangebot", "neue Stelle", "Jobangebot")
- Suggest candidate fit (e.g. "You may be a fit", "based on your profile", "Passt zu Ihrem Profil", "passende Stellen")
- Come from recruiters, job boards, or hiring platforms (LinkedIn, Indeed, Glassdoor, Xing, jobs.ch, etc.)
- Contain job alerts, saved search results, or talent community updates (e.g. "Jobalarm", "Jobbenachrichtigung", "neue Stellenangebote")

**Exclude:**
- Newsletters, marketing, social media notifications, and non-job correspondence

Use the email's subject, sender, and first ~300 characters of body text to classify.

Report: "Identified N job-related emails out of M total."
**If none are job-related, report and stop.**

---

## Step 3: Extract Vacancy Links

For each job-related email, examine the full body content (body_html first, fall back to body_text) and extract URLs that point to **specific job vacancy pages**.

**Include links that:**
- Point to a specific job posting (e.g., `/vacancies/detail/...`, `/jobs/view/...`)
- Are "Apply now", "View job", "See position" type links
- Lead to a specific role on job boards (LinkedIn, Indeed, Glassdoor, jobs.ch, etc.)

**Exclude links that:**
- Are unsubscribe, preferences, or email management links
- Lead to generic homepages, feeds, notifications, or search result pages
- Are social media profile links, privacy policy, or terms of service links

**After collecting links:**
1. Strip these tracking query parameters: `utm_source`, `utm_medium`, `utm_campaign`, `utm_content`, `utm_term`, `trk`, `midToken`, `midSig`, `lipi`, `lgCta`, `lgTemp`, `uid`, `pid`, `hash`, `profile-id`, `reference-date`
2. Deduplicate by scheme + host + path (ignore query params for dedup)

Report: "Found N unique vacancy links."
**If no links found, report and stop.**

---

## Step 4: Scrape Vacancy Pages

For each vacancy URL, fetch the page and dump the raw cleaned text. **Use the standalone batch tool in `--raw` mode** — it reuses one Playwright browser session across all LinkedIn/Xing URLs (much faster than one process per URL), handles `requests`-based jobs.ch, decodes HTML entities, and writes a single JSON array of raw page texts. No Claude API call is made here — extraction of structured fields happens inside Step 5.

### Full job descriptions are mandatory — never score from email one-liners

Every vacancy must be scored from its **full posting page**, not from the alert email's title/snippet. Email snippets are only a last-resort fallback for a URL whose scrape failed (or an Indeed job skipped by the title pre-filter below), and must then carry a `note` (see below) so the report shows the score is a guess.

### Indeed: scrape with the visible Chrome window

Indeed (`*.indeed.com`) sits behind Cloudflare, which blocks `requests` and headless browsers. The scraper handles it: Indeed URLs (`/rc/clk`, `/pagead/clk`, `/viewjob`) go to a **visible real-Chrome window** (`channel="chrome"`), with the session saved to `credentials/indeed_state.json`. **Include Indeed URLs in the scraper batch like any other URL.**

- If Cloudflare shows a "Security Check" checkbox, the scraper prints `[cloudflare] Security check ...` and waits up to 120s. **Tell the user to click the checkbox in the Chrome window.** Never click it yourself or automate it. Once passed, the `cf_clearance` cookie is reused for the rest of the batch and saved for later runs.
- Run the scrape command in the foreground with a long timeout (≥ 600000 ms) so there is time for the user to click.
- Treat an Indeed result as **failed** if it is `ok: false`, or if `page_text` is under ~800 chars or contains "Security Check" / "Blocked - Indeed.com" / "Zusätzliche Verifizierung". Only for those failed URLs, use the snippet fallback below.

#### Title pre-filter (Indeed only): don't scrape clearly off-profile jobs

Indeed alerts (especially location-only alerts) mix in many jobs unrelated to the candidate. Before scraping, look at each Indeed job's **title + company** from the alert email block. If it is **90%+ certain to be off-profile** — a different profession that the CV cannot plausibly fit (e.g. household help, doctor/nurse, sales rep, UX writer, supply chain planner, hostess, purchasing, IT developer for a finance CV) — **do not scrape it** and leave it out of the URL file.

- Be conservative: if the title could be in or near the candidate's field (e.g. "Sachbearbeiter", "Administration", "Finanzen", "Steuer", "Treuhand", "Assistant" with a finance/admin angle), **scrape it**. Only obvious mismatches are skipped.
- This applies to Indeed only. jobs.ch, LinkedIn, and Xing URLs are always scraped.
- Skipped jobs still go into the report so the recipient can see what was filtered. For each one, add an entry `{url, ok: true, page_text: <alert block text without the URL line>, note: "not scraped — title clearly off-profile"}` to the scraped JSON for Step 5. Score it from the title alone (0–10) and keep the `note`.
- Report: "Skipped N off-profile Indeed jobs by title: <title list>."

#### Snippet fallback (failed Indeed scrapes only)

Extract the job from the alert email's `body_text`: jobs are blank-line-separated blocks of the form

```
<title>
<company> - <city>, <canton>
[optional salary line / "Easily apply"]
<1-2 snippet lines>
<age, e.g. "3 days ago">
<https://ch.indeed.com/... click-URL>
```

For each failed URL's block, create an entry `{url, ok: true, page_text: <block text without the URL line>, note: "from Indeed alert email snippet (full page could not be scraped)"}` and replace the failed entry in the scraped JSON for Step 5. The `note` must be preserved through matching so the report shows the score is snippet-based.

### Batch all URLs in one call

1. Write the unique vacancy URLs (one per line, **including Indeed**, minus Indeed jobs skipped by the title pre-filter) to a temp file, e.g. `/tmp/<profile>_urls.txt`.
2. Run:

```bash
uv run python tools/scrape_vacancies.py \
    --profile PROFILE_NAME \
    --urls-file /tmp/PROFILE_NAME_urls.txt \
    --output /tmp/PROFILE_NAME_scraped.json \
    --raw \
    --progress
```

Replace `PROFILE_NAME` with `$ARGUMENTS`. Per-URL progress prints to stderr; the JSON output goes to the `--output` file. Each entry has `{url, ok, page_text?, error?}`. Failed entries (`ok: false`) include an `error` field — skip them when matching. Closed/withdrawn listings are also marked `ok: false`.

You can also pass URLs via `--json-file links.json --json-key linkedin`, `--url <single>`, or stdin (`--urls-file -`).

### Fallback: non-Playwright sites only

If you only have a few non-LinkedIn/Xing URLs and want to avoid spinning up the project scraper, use the `WebFetch` tool with this prompt:
> Extract the following job posting details from this page. Return them clearly labeled:
> - Job title
> - Company name
> - Location
> - Full job description: responsibilities/tasks, verbatim or near-verbatim, not a summary
> - All requirements (list: education, experience, skills, languages, workload %)
> - Salary (if mentioned, otherwise "Not specified")

If the result is empty or clearly not a job page, note it as failed and continue.

Report progress for each URL: "Scraped: [title] at [company]" or "Failed to scrape: [url]"

**If no vacancies were successfully scraped, report and stop.**

---

## Step 5: Extract + Match Vacancies Against CV

**You are both the extractor and the matcher.** No external API call, no ANTHROPIC_API_KEY. Use the CV from Step 0 and the rules from `matcher_instructions.md` (if it exists). If you also collected extra vacancies via WebFetch (Step 4 fallback), include them here too.

1. Read `/tmp/PROFILE_NAME_scraped.json` (replace `PROFILE_NAME` with `$ARGUMENTS`). Each entry is either raw (`{url, ok, page_text}`) or already structured (`{url, ok, title, company, location, description, requirements, salary}` from WebFetch). Merge both sources.
2. For each entry where `ok` is true:
   - If only `page_text` is present, extract `title`, `company`, `location`, `description` (2-3 sentence summary), `requirements` (list), `salary` (or null) by reading the text yourself.
     - **A posting can list multiple locations.** Job boards — especially Google Careers, LinkedIn, and large multinationals — frequently advertise one role across several cities (e.g. `Zürich, Switzerland; New York, NY, USA`). Capture **every** location the posting lists in the `location` field; never record just the first one.
   - Then evaluate fit against the CV. **Apply `matcher_instructions.md` rules** — they may boost, penalize, or exclude certain vacancies and OVERRIDE the default scoring.
     - **Location rules are evaluated against the MOST FAVORABLE listed location, never a single arbitrary one.** Before applying any location-based rule (e.g. a "different country" / "wrong location" skip or penalty), check **all** of the posting's locations. If *at least one* of them is acceptable for the candidate (their home country / commuting region per the profile), the role counts as **in-region**: do **not** skip it, zero it, or down-rank it for location, and do **not** list location as a gap. Only apply a location skip/penalty when **none** of the listed locations are acceptable. A multi-location role that includes the candidate's region must never be penalized merely because it *also* lists a foreign office.
3. Produce one match object per scored vacancy:
   ```json
   {
     "url": "...",
     "title": "...",
     "company": "...",
     "location": "...",
     "fit_percentage": 0-100 integer,
     "summary": "2-3 sentence why this is or isn't a good fit",
     "key_matches": ["skill/experience that matches", "..."],
     "gaps": ["requirement not met", "..."]
   }
   ```
   Preserve any `note` field from the input entry on the output.
4. Skip entries where `ok` is false.
5. Sort the list by `fit_percentage` descending.
6. Write the JSON array (UTF-8, 2-space indent, `ensure_ascii=false`) to `/tmp/PROFILE_NAME_matches.json` using the `Write` tool.
7. Filter entries with `fit_percentage >= min_fit_percentage`. Report: "N vacancies above threshold (min_fit_percentage%), M filtered out."

### Scoring rubric (default — `matcher_instructions.md` overrides this)

- **fit_percentage**: integer 0-100. Higher = stronger fit. Weigh required skills/experience, role seniority, location/language, work-load (%) compatibility.
- **key_matches**: concrete CV evidence that maps to vacancy requirements (skills, tools, years, certifications, language).
- **gaps**: vacancy requirements the CV does not satisfy (missing skill, lower seniority, wrong location, language mismatch). Only cite location as a gap when *none* of the posting's listed locations are acceptable for the candidate — see Step 5, item 2 on multi-location postings.
- **summary**: 2-3 sentences. Lead with the verdict, then the strongest match and the most important gap.

### Tips for batching

- If there are many vacancies (>10), score them in batches mentally but write **one** final JSON array. Do not stream partial files.
- Quote German job titles verbatim. Do not translate.
- Keep `key_matches` / `gaps` lists short (3-6 items each).

**Do NOT stop here even if 0 vacancies are above threshold — always proceed to generate and send the report.**

---

## Step 6: Generate & Save HTML Report

Build the HTML report directly from the matches JSON using the standalone tool — it renders the above-threshold table (green ≥70%, yellow ≥50%, red <50% badges), the below-threshold list, and writes to `profiles/<profile>/report_<date>.html`.

```bash
uv run python tools/build_report.py \
    --profile PROFILE_NAME \
    --matches-file /tmp/PROFILE_NAME_matches.json \
    --date YYYY-MM-DD \
    --print-summary
```

Replace `PROFILE_NAME` with `$ARGUMENTS` and `YYYY-MM-DD` with today's date. The tool reads `min_fit_percentage` from the profile config; override with `--min-fit N` if needed. It prints the output path to stdout and a `[fit%] title at company` summary to stderr.

**Always run this step**, even when no matches are above threshold — the tool emits a clear "No matches above threshold." cell and still lists below-threshold vacancies for reference.

---

## Step 7: Send Report via SMTP

**Always send the report**, even if there are no matches above threshold. The recipient should know the pipeline ran and what was found.

Send the HTML report using the standalone send tool:

```bash
uv run python tools/gmail_send.py --to RECIPIENT_EMAIL --subject "Job Opportunities Report — YYYY-MM-DD" --html-file profiles/PROFILE_NAME/report_YYYY-MM-DD.html
```

Replace `PROFILE_NAME` with `$ARGUMENTS`, `RECIPIENT_EMAIL` with the `recipient_email` from config, and `YYYY-MM-DD` with today's date. The sender address is read from SENDER_EMAIL in .env.

If sending fails, inform the user and note the report is saved locally.

---

## Step 8: Mark Processed Emails as Read

Mark all processed job-related emails as read using the standalone tool:

```bash
uv run python tools/mail_mark_read.py --profile PROFILE_NAME --ids ID1,ID2,ID3,...
```

Replace `PROFILE_NAME` with `$ARGUMENTS` and provide a comma-separated list of all job-related email message IDs collected in Step 2 — pass the **prefixed ids exactly as returned by Step 1** (`gmail:<msgid>` / `<imap-email>:<uid>`); the tool routes each id to the right account (Gmail API vs IMAP `\Seen`).

---

## Error Handling

- If any step fails, report the error clearly and continue to the next item where possible (e.g., if one URL fails to scrape, continue with the rest).
- Always save the HTML report locally even if sending fails.
