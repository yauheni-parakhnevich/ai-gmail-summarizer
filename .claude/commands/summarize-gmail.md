---
description: Summarize unread Gmail job emails, scrape vacancies, match against CV, generate report
argument-hint: <profile-name>
allowed-tools: [Read, Write, Glob, Grep, Bash, WebFetch, "mcp__claude_ai_Gmail__*"]
---

# Gmail Job Summarizer

You are running a multi-step pipeline that processes unread Gmail emails, identifies job-related ones, scrapes vacancy pages, matches them against a candidate's CV, and produces an HTML report. Follow each step carefully and report progress as you go.

**Profile name**: `$ARGUMENTS`
**Profile directory**: `profiles/$ARGUMENTS/`

---

## Step 0: Load Profile Configuration

1. Read `profiles/$ARGUMENTS/config.yaml` and extract:
   - `recipient_email` (required)
   - `min_fit_percentage` (default: 30)
   - `linkedin_email`, `linkedin_password` (optional, for scraper fallback)
   - `xing_email`, `xing_password` (optional, for scraper fallback)
2. Read `profiles/$ARGUMENTS/cv.md` — this is the candidate's CV. Store its full content for later matching.
3. Read `profiles/$ARGUMENTS/matcher_instructions.md` if it exists — these are custom rules for scoring vacancies.
4. **If the profile directory or cv.md does not exist, tell the user what's missing and stop.**

---

## Step 1: Connect to Gmail & Fetch Unread Emails

1. Call `mcp__claude_ai_Gmail__authenticate` to authenticate with Gmail.
2. Once authenticated, search for unread primary emails using query: `is:unread category:primary` (limit to 100 results).
3. For each email, retrieve: message ID, subject, sender, date, and body content (HTML and/or plain text).
4. Report: "Found N unread emails."
5. **If no unread emails, report "No unread emails found." and stop.**

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

For each job-related email, examine the full body content and extract URLs that point to **specific job vacancy pages**.

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

For each vacancy URL, fetch the page and extract structured job details.

### For LinkedIn or Xing URLs (domain contains `linkedin.com` or `xing.com`):

Use the existing Python scraper via Bash — it handles Playwright browser sessions and login:

```bash
uv run python -c "
import json, sys
from gmail_summarizer.scraper import scrape_vacancy
from gmail_summarizer.config import load_config
config = load_config('PROFILE_NAME')
v = scrape_vacancy(config, 'URL_HERE')
if v:
    print(json.dumps({'title': v.title, 'company': v.company, 'location': v.location, 'description': v.description, 'requirements': v.requirements, 'salary': v.salary}))
else:
    print('null')
"
```

Replace `PROFILE_NAME` with `$ARGUMENTS` and `URL_HERE` with the actual URL. Parse the JSON output.

### For all other URLs:

Use the `WebFetch` tool with the URL and this prompt:
> Extract the following job posting details from this page. Return them clearly labeled:
> - Job title
> - Company name
> - Location
> - Job description (2-3 sentence summary)
> - Key requirements (list)
> - Salary (if mentioned, otherwise "Not specified")

Parse the WebFetch response to extract the structured fields. If the result is empty or clearly not a job page, note it as failed and continue.

Report progress for each URL: "Scraped: [title] at [company]" or "Failed to scrape: [url]"

**If no vacancies were successfully scraped, report and stop.**

---

## Step 5: Match Vacancies Against CV

You are a career matching expert. For each scraped vacancy, compare it against the candidate's CV and evaluate the fit.

For each vacancy, produce:
- `fit_percentage`: integer 0-100 representing how well the candidate fits
- `summary`: 2-3 sentence explanation of why this is or isn't a good fit
- `key_matches`: list of skills/experiences from the CV that match the requirements
- `gaps`: list of requirements the candidate doesn't meet

**If matcher_instructions.md was loaded, apply those rules.** Typical rules include:
- "Rank Higher" — boost fit % for roles matching these criteria
- "Rank Lower" — reduce fit % for roles matching these criteria
- "Skip" — exclude these types of roles entirely (set fit to 0)

**Filter out vacancies with fit_percentage below `min_fit_percentage` from config.**
Sort remaining matches by fit_percentage descending.

Report: "N vacancies above threshold (min_fit_percentage%), M filtered out."

**Do NOT stop here even if 0 vacancies are above threshold — always proceed to generate and send the report.**

---

## Step 6: Generate & Save HTML Report

**Always generate a report, even if there are no matches above threshold.** If there are no matches, the report should summarize what was found (total emails, job emails, links scraped) and list skipped/below-threshold vacancies for reference.

Generate an HTML report with this exact structure:

```html
<html>
<body style="font-family:Arial,sans-serif;color:#333;max-width:900px;margin:auto;">
    <h2 style="color:#1a73e8;">Job Opportunities Report — YYYY-MM-DD</h2>
    <p>Found <strong>N</strong> matching vacancies, ranked by fit percentage.</p>
    <table style="width:100%;border-collapse:collapse;">
        <thead>
            <tr style="background:#f5f5f5;">
                <th style="padding:12px;text-align:left;">#</th>
                <th style="padding:12px;text-align:left;">Position</th>
                <th style="padding:12px;text-align:center;">Fit</th>
                <th style="padding:12px;text-align:left;">Summary</th>
                <th style="padding:12px;text-align:left;">Details</th>
            </tr>
        </thead>
        <tbody>
            <!-- For each match, generate a row: -->
            <tr>
                <td style="padding:12px;border-bottom:1px solid #eee;">1</td>
                <td style="padding:12px;border-bottom:1px solid #eee;">
                    <a href="URL" style="color:#1a73e8;text-decoration:none;">
                        <strong>Job Title</strong>
                    </a><br>
                    <span style="color:#666;">Company &middot; Location</span>
                </td>
                <td style="padding:12px;border-bottom:1px solid #eee;text-align:center;">
                    <span style="background:COLOR;color:#fff;padding:4px 10px;border-radius:12px;font-weight:bold;">
                        XX%
                    </span>
                </td>
                <td style="padding:12px;border-bottom:1px solid #eee;">Summary text</td>
                <td style="padding:12px;border-bottom:1px solid #eee;font-size:0.9em;">
                    <strong>Matches:</strong> skill1, skill2<br>
                    <strong>Gaps:</strong> gap1, gap2
                </td>
            </tr>
        </tbody>
    </table>
    <p style="color:#999;font-size:0.85em;margin-top:24px;">
        Generated by AI Gmail Summarizer
    </p>
</body>
</html>
```

**Fit badge colors:**
- Green `#34a853` for fit >= 70%
- Yellow `#fbbc04` for fit >= 50%
- Red `#ea4335` for fit < 50%

**Save the report** to `profiles/$ARGUMENTS/report_YYYY-MM-DD.html` using the Write tool.

**Print a console summary** like:
```
Job Opportunities Report — YYYY-MM-DD
  1. [85%] Senior Developer at Acme Corp
  2. [72%] Backend Engineer at StartupXYZ
  3. [55%] Full Stack Dev at BigCo
```

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
uv run python tools/gmail_mark_read.py --profile PROFILE_NAME --ids ID1,ID2,ID3,...
```

Replace `PROFILE_NAME` with `$ARGUMENTS` and provide a comma-separated list of all job-related email message IDs collected in Step 2.

---

## Error Handling

- If any step fails, report the error clearly and continue to the next item where possible (e.g., if one URL fails to scrape, continue with the rest).
- Always save the HTML report locally even if sending fails.
- If the Gmail MCP tools are not what you expect after authentication, describe what tools are available and adapt accordingly.
