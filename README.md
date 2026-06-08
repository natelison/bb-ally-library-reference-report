# Ally Library Reference Report

A standalone Python script that connects to your **Ally** and **Blackboard** instances, finds every course file tagged with a library reference (publisher/textbook content added via Ally's library integration), and generates a self-contained interactive HTML report — no server, no extra files, nothing to install beyond one Python package.

> **Built for Blackboard Learn Ultra + Ally.** Tested on Python 3.8–3.12, Windows and macOS.

---

## What It Does

Ally's built-in reporting doesn't expose library reference data through its standard UI. This script pulls that data directly from the Ally API, aggregates it across all courses in a term, and produces a polished HTML report with two views:

- **Files tab** — every library-referenced file, sortable by course, file name, publication title, author, or year; searchable across all fields
- **By Publication tab** — unique publications grouped with a count of how many courses use them and which course IDs

The report also includes a **↓ CSV** export button that downloads the current filtered view.

<!-- screenshot: Files tab -->
<!-- screenshot: By Publication tab -->

---

## Requirements

- Python **3.8** or newer
- One third-party package:

```
pip install requests
```

That's it. No pandas, no openpyxl, no browser drivers.

---

## Quick Start

### Step 1 — Run the setup wizard

```
python ally_library_standalone.py --setup
```

The wizard walks you through entering your credentials and saves them to `ally_library_config.json` in the same folder as the script.

<!-- screenshot: setup wizard terminal output -->

### Step 2 — Test your credentials

```
python ally_library_standalone.py --check
```

This mints an Ally JWT, gets a Blackboard OAuth token, and lists your Blackboard terms — without pulling any data. It's fast and safe to run any time you want to verify things are still working.

<!-- screenshot: --check output -->

### Step 3 — Generate the report

```
python ally_library_standalone.py --terms "Summer 2026"
```

The script prints a live progress bar as it processes each course, then writes a self-contained HTML file you can open in any browser.

<!-- screenshot: progress bar + completion box -->

### Step 4 — Open the report

Open `ally_library_report.html` from the same folder as the script in any modern browser. No web server needed.

---

## Where to Find Your Credentials

All three Ally credentials come from the same place in Blackboard:

**Administrator Panel → LTI Tool Providers → (Ally entry) → Manage Placements → Edit Placement: Accessibility Report → Tool Provider Information**

<!-- screenshot: Blackboard LTI Tool Provider Information screen -->

| Config field | Where it comes from |
|---|---|
| `ally_base_url` | The domain in the Tool Provider URL — usually `https://prod.ally.ac` |
| `ally_client_id` | The number between `/v1/` and `/lti/` in the Tool Provider URL |
| `ally_lti_key` | The **Tool Provider Key** field |
| `ally_lti_secret` | The **Tool Provider Secret** field |
| `ally_admin_user_id` | A Blackboard username that has logged into Ally at least once (see note below) |
| `bb_base_url` | Your Blackboard instance URL, e.g. `https://blackboard.yourschool.edu` |
| `bb_key` | Your REST API integration application key |
| `bb_secret` | Your REST API integration application secret |

The Blackboard REST API credentials come from:

**Administrator Panel → Building Blocks → REST API Integrations**

---

## Blackboard REST API Entitlements

The REST API integration account only needs these entitlements:

| Entitlement | Purpose |
|---|---|
| `system.term.VIEW` | List terms |
| `course.READ` | List courses by term |
| `course.ADMIN.VIEW` | *(Optional)* Include unavailable/draft courses |

No membership, user, or content entitlements are required.

---

## A Note on `ally_admin_user_id`

This field is the Blackboard username placed in the Ally JWT for **audit-log purposes only**. It does **not** need to be a Blackboard administrator account — it just needs to belong to a user that Ally has seen before (i.e. has logged into Ally at least once).

The administrator *role* is asserted inside the JWT itself, independently of whatever account the username belongs to. Using a dedicated read-only or service account is recommended over a personal admin account.

---

## Usage

```
python ally_library_standalone.py [options]
```

### Common examples

```bash
# Single term
python ally_library_standalone.py --terms "Spring 2026"

# Multiple terms combined into one report
python ally_library_standalone.py --terms "Spring 2026" --terms "Summer 2026"

# Custom output filename
python ally_library_standalone.py --terms "Spring 2026" --out reports/spring_library.html

# Custom report title
python ally_library_standalone.py --terms "Spring 2026" --title "Spring 2026 Library Content Audit"

# Skip cross-listed child courses (recommended if your school uses cross-listing)
python ally_library_standalone.py --terms "Spring 2026" --exclude-child-courses

# Skip courses with zero enrolled students
python ally_library_standalone.py --terms "Spring 2026" --exclude-zero-students

# Use a config file in a different location
python ally_library_standalone.py --terms "Spring 2026" --config /path/to/my_config.json
```

### All options

| Option | Description |
|---|---|
| `--terms TERM` | Term name to include. Repeatable for multiple terms. |
| `--out PATH` | Output HTML path (default: `ally_library_report.html` next to the script) |
| `--title TEXT` | Report title shown in the HTML header |
| `--exclude-child-courses` | Exclude cross-listed child/merged courses |
| `--exclude-zero-students` | Skip courses with no enrolled students (requires one extra Ally API call per course) |
| `--config PATH` | Path to a config JSON file (default: `ally_library_config.json` next to the script) |
| `--setup` | Run the first-time setup wizard |
| `--check` | Test credentials without generating a report |
| `--verbose` | Show per-course error details as they occur |

---

## Config File

The setup wizard creates `ally_library_config.json` automatically. You can also create or edit it manually:

```json
{
  "ally_base_url": "https://prod.ally.ac",
  "ally_client_id": "9299",
  "ally_lti_key": "your-lti-key",
  "ally_lti_secret": "your-lti-secret",
  "ally_admin_user_id": "your-blackboard-username",
  "bb_base_url": "https://blackboard.yourschool.edu",
  "bb_key": "your-app-key",
  "bb_secret": "your-app-secret",
  "default_terms": ["Summer 2026", "Spring 2026"]
}
```

`default_terms` is optional — if set, those terms are used when you run the script without `--terms`.

> ⚠️ **Keep this file private.** It contains API secrets. Add `ally_library_config.json` to your `.gitignore` if you store the script in a repository.

---

## Report Features

### Files tab
- Sortable columns: Type, Course, File, Publication, Author, Year
- Search filters all visible columns simultaneously
- File size badge, Ally accessibility score
- Long text truncated with ellipsis; full value shown in tooltip on hover

### By Publication tab
- Each unique publication shown as a card with author, publisher, and year
- Count of files and courses using it
- Course ID chips for quick reference

### CSV Export
- Click **↓ CSV** in the toolbar to download
- Exports the **current filtered view** — apply a search first to export a subset
- Filename is `ally_library_report.csv`, or `ally_library_report_filtered.csv` when a search is active
- UTF-8 with BOM so Excel opens it correctly without an import wizard

---

## Blackboard API Rate Limits

The script checks your Blackboard REST API quota immediately after authenticating and reports how many calls remain before starting. If the quota is exhausted it exits cleanly with a message telling you when to retry.

Blackboard's default REST API rate limit is **10,000 calls per 24-hour window**. A typical run for a single term uses approximately `1 + (number of courses / 100)` calls for discovery, well within the limit for most institutions.

---

## Troubleshooting

**Term not found**
The term name must match exactly as it appears in Blackboard, including capitalization and spacing. Run `--check` to see a sample of available term names after authenticating.

**`requests` not installed**
```
pip install requests
```

**`No library references found`**
This is a valid result — it means no files in the selected terms have a library reference attached in Ally. Verify that your institution has Ally's library integration enabled and that instructors have linked content through it.

**Config file errors**
Re-run `--setup` to recreate the config file from scratch.

---

## License

MIT
