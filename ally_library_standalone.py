#!/usr/bin/env python3
"""
ally_library_standalone.py — Ally Library Reference Report
===========================================================

Connects to your Ally and Blackboard instances, finds every course file
that has a library reference (publisher / textbook content added via Ally's
library integration), and produces a self-contained HTML report you can open
in any browser — no server required.

REQUIREMENTS
    Python 3.8 or newer
    One third-party package:   pip install requests

QUICK START
    Step 1 — Run the setup wizard (first time only):
        python ally_library_standalone.py --setup

    Step 2 — Test your credentials:
        python ally_library_standalone.py --check

    Step 3 — Generate the report:
        python ally_library_standalone.py --terms "Spring 2026"

    Step 4 — Open the HTML file in any browser.

USAGE
    python ally_library_standalone.py --terms "Spring 2026"
    python ally_library_standalone.py --terms "Spring 2026" --terms "Summer 2026"
    python ally_library_standalone.py --terms "Spring 2026" --out report.html
    python ally_library_standalone.py --terms "Spring 2026" --exclude-zero-students
    python ally_library_standalone.py --setup          # first-time config wizard
    python ally_library_standalone.py --check          # validate credentials

BLACKBOARD REST API ENTITLEMENTS REQUIRED
    Create a System Role with these three privileges:
        system.term.VIEW                 — list terms
        system.course.VIEW               — list courses by term
        course.unavailable-course.VIEW   — (optional) include unavailable/draft courses
    Assign that role to a dedicated user, then register a REST Integration in
    Blackboard (Admin Panel → REST API Integrations) linked to that user.
    The Key and Secret come from developer.blackboard.com, not from Blackboard itself.

ABOUT ally_admin_user_id
    This is the Blackboard username placed in the Ally JWT for audit-log purposes.
    It does NOT need to be a Blackboard administrator account — it just needs to be
    a user that Ally recognises (i.e. has logged into Ally at least once).
    The administrator role is asserted in the JWT itself, not derived from the
    account.  A dedicated read-only or service account works fine.
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import hashlib
import hmac
import io
import json
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ── Dependency check ───────────────────────────────────────────────────────────

try:
    import requests
except ImportError:
    print("=" * 60)
    print("  Missing dependency: 'requests'")
    print()
    print("  Install it with:")
    print("    pip install requests")
    print("=" * 60)
    sys.exit(1)

# ── Constants ──────────────────────────────────────────────────────────────────

VERSION         = "1.0.0"
CONFIG_FILENAME = "ally_library_config.json"
SCRIPT_DIR      = Path(__file__).parent
DEFAULT_CONFIG  = SCRIPT_DIR / CONFIG_FILENAME


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Config + setup wizard
# ══════════════════════════════════════════════════════════════════════════════

_FIELDS = [
    # (key, label, hint, default)
    #
    # ally_base_url / ally_client_id / ally_lti_key / ally_lti_secret all come
    # from the same place in Blackboard:
    #
    #   Administrator Panel → LTI Tool Providers → (find the Ally entry)
    #   → Edit Placement: Accessibility Report → Tool Provider Information
    #
    # The Tool Provider URL looks like:
    #   https://prod.ally.ac/api/v1/1111/lti/instructor
    #                               ^^^^
    #                           client ID is here
    #
    # "Tool Provider Key"    → ally_lti_key
    # "Tool Provider Secret" → ally_lti_secret

    ("ally_base_url",
     "Ally base URL",
     "The domain from the Tool Provider URL, e.g. https://prod.ally.ac",
     "https://prod.ally.ac"),

    ("ally_client_id",
     "Ally client ID",
     "The number between /v1/ and /lti/ in the Tool Provider URL\n"
     "  (e.g. https://prod.ally.ac/api/v1/>>>1111<<</lti/instructor)",
     ""),

    ("ally_lti_key",
     "Ally LTI key",
     "The 'Tool Provider Key' field on the Edit Placement screen in Blackboard",
     ""),

    ("ally_lti_secret",
     "Ally LTI secret",
     "The 'Tool Provider Secret' field on the Edit Placement screen — treat like a password",
     ""),

    ("ally_admin_user_id",
     "Ally admin user ID (for Ally audit log)",
     "A Blackboard username that Ally recognises (has logged into Ally before).\n"
     "  Does NOT have to be a Blackboard admin — any user Ally knows works.\n"
     "  Using a dedicated service/read-only account is recommended.",
     ""),

    ("bb_base_url",
     "Blackboard base URL",
     "e.g. https://blackboard.yourschool.edu",
     ""),

    ("bb_key",
     "Blackboard REST API key",
     "From developer.blackboard.com — shown when you create/view your application",
     ""),

    ("bb_secret",
     "Blackboard REST API secret",
     "From developer.blackboard.com — only shown once at creation, treat like a password",
     ""),
]

_REQUIRED = [f[0] for f in _FIELDS]


def _prompt(label: str, hint: str, default: str) -> str:
    if hint:
        print(f"  ℹ  {hint}")
    suffix = f" [{default}]" if default else ""
    val = input(f"  {label}{suffix}: ").strip()
    return val or default


def run_setup_wizard(config_path: Path) -> dict:
    """Interactive first-time setup wizard. Saves config and returns it."""
    print()
    print("╔═══════════════════════════════════════════════════════╗")
    print("║   Ally Library Report — First-time Setup Wizard       ║")
    print("╚═══════════════════════════════════════════════════════╝")
    print()
    print("You'll need credentials from two places in Blackboard:")
    print()
    print("  1. Ally LTI placement (for Ally credentials):")
    print("       Administrator Panel → LTI Tool Providers")
    print("       → click Edit on the Ally entry")
    print("       → Manage Placements → Edit Placement: Accessibility Report")
    print("       → Tool Provider Information section")
    print()
    print("  2. Blackboard REST API (four steps):")
    print("       a) Register an application at developer.blackboard.com")
    print("          (Key and Secret are shown here — copy them immediately)")
    print("       b) Create a System Role with the required entitlements")
    print("          (Admin Panel → Users → System Roles)")
    print("       c) Create a dedicated user account with that System Role")
    print("       d) Register the REST Integration in Blackboard")
    print("          (Admin Panel → REST API Integrations)")
    print()
    print("Press Enter to accept the default shown in [brackets].")
    print()

    cfg: Dict[str, Any] = {}

    print("── Ally settings ──────────────────────────────────────")
    for key, label, hint, default in _FIELDS[:5]:
        cfg[key] = _prompt(label, hint, default)
        print()

    print("── Blackboard settings ────────────────────────────────")
    for key, label, hint, default in _FIELDS[5:]:
        cfg[key] = _prompt(label, hint, default)
        print()

    print("── Default terms (optional) ───────────────────────────")
    print("  ℹ  Enter term names exactly as they appear in Blackboard,")
    print("     separated by commas.  These are used when you don't")
    print("     specify --terms on the command line.")
    raw = input("  Default terms (e.g. Spring 2026, Summer 2026): ").strip()
    cfg["default_terms"] = [t.strip() for t in raw.split(",") if t.strip()]
    print()

    ans = input(f"Save config to {config_path}? [Y/n]: ").strip().lower()
    if ans not in ("n", "no"):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        print(f"\n✓  Config saved to: {config_path}")
    else:
        print("  Config not saved. Run --setup again at any time.")

    return cfg


def load_config(config_path: Path) -> dict:
    """Load and validate the JSON config. Exits with a clear message on failure."""
    if not config_path.exists():
        print()
        print(f"  No config file found at: {config_path}")
        print()
        print("  Run the setup wizard first:")
        print(f"    python {Path(__file__).name} --setup")
        print()
        sys.exit(1)

    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"\n  ERROR: Could not parse config file: {e}")
        print(f"  Try running --setup to recreate it.")
        sys.exit(1)

    missing = [k for k in _REQUIRED if not cfg.get(k)]
    if missing:
        print(f"\n  ERROR: Config is missing required fields: {', '.join(missing)}")
        print(f"  Run --setup to reconfigure.")
        sys.exit(1)

    return cfg


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Data classes
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class AllyCfg:
    base_url:         str
    client_id:        str
    lti_key:          str
    lti_secret:       str
    admin_user_id:    str
    token_ttl_s:      int   = 3600
    timeout_s:        int   = 60
    max_retries:      int   = 6
    retry_backoff_s:  float = 1.6
    retry_jitter_s:   float = 0.25
    batch_size:       int   = 250
    # Ally API path templates
    csv_path:         str   = "/api/v1/{client_id}/reports/courses/{course_id}/csv"
    files_path:       str   = "/api/v1/{client_id}/reports/courses/{course_id}/files"
    report_path:      str   = "/api/v1/{client_id}/reports/courses/{course_id}"


@dataclass
class BbCfg:
    base_url: str
    key:      str
    secret:   str


def _cfgs_from_json(raw: dict) -> Tuple[AllyCfg, BbCfg]:
    ally = AllyCfg(
        base_url=raw["ally_base_url"].rstrip("/"),
        client_id=str(raw["ally_client_id"]),
        lti_key=raw["ally_lti_key"],
        lti_secret=raw["ally_lti_secret"],
        admin_user_id=raw["ally_admin_user_id"],
    )
    bb = BbCfg(
        base_url=raw["bb_base_url"].rstrip("/"),
        key=raw["bb_key"],
        secret=raw["bb_secret"],
    )
    return ally, bb


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — JWT helpers (HS256 — used to mint Ally LTI tokens)
# ══════════════════════════════════════════════════════════════════════════════

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_json(obj: Any) -> str:
    return _b64url(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _jwt_hs256(payload: Dict, secret: str, kid: Optional[str] = None) -> str:
    header: Dict[str, Any] = {"typ": "JWT", "alg": "HS256"}
    if kid:
        header["kid"] = kid
    h = _b64url_json(header)
    p = _b64url_json(payload)
    msg = f"{h}.{p}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url(sig)}"


def _mint_ally_token(cfg: AllyCfg) -> str:
    now = int(time.time())
    payload = {
        "iss": cfg.lti_key,
        "iat": now,
        "exp": now + cfg.token_ttl_s,
        "user": {
            "displayName": None,
            "email": None,
            "lmsSpecificData": {},
            "fileIds": None,
            "locale": "en-US",
            "roles": [{"r": "administrator"}],
            "externalUserId": cfg.admin_user_id,
            "sessionControl": None,
        },
    }
    return _jwt_hs256(payload, cfg.lti_secret, kid=cfg.lti_key)


class _TokenMgr:
    """Mints and auto-refreshes the Ally LTI JWT."""
    def __init__(self, cfg: AllyCfg):
        self._cfg = cfg
        self._token = _mint_ally_token(cfg)
        self._exp   = int(time.time()) + cfg.token_ttl_s

    def get(self, min_remaining_s: int = 300) -> str:
        if time.time() > self._exp - min_remaining_s:
            self._token = _mint_ally_token(self._cfg)
            self._exp   = int(time.time()) + self._cfg.token_ttl_s
        return self._token


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — HTTP session + retry
# ══════════════════════════════════════════════════════════════════════════════

_thread_local = threading.local()


def _session() -> requests.Session:
    """One requests.Session per thread for connection reuse and thread safety."""
    if not hasattr(_thread_local, "s"):
        _thread_local.s = requests.Session()
    return _thread_local.s


def _ally_hdrs(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json, text/csv, */*"}


def _http(cfg: AllyCfg, method: str, url: str, headers: dict,
          **kwargs) -> requests.Response:
    """HTTP request with automatic retry and rate-limit handling."""
    for attempt in range(cfg.max_retries + 1):
        try:
            r = _session().request(method, url, headers=headers,
                                   timeout=cfg.timeout_s, **kwargs)
            if r.status_code == 429:
                raw = r.headers.get("Retry-After", "")
                wait = (int(raw) + 2) if raw.isdigit() else 20 * (2 ** attempt)
                time.sleep(wait)
                continue
            if r.status_code in (500, 502, 503, 504):
                time.sleep(cfg.retry_backoff_s ** attempt
                           + random.random() * cfg.retry_jitter_s)
                continue
            r.raise_for_status()
            return r
        except requests.exceptions.RequestException:
            if attempt == cfg.max_retries:
                raise
            time.sleep(cfg.retry_backoff_s ** attempt
                       + random.random() * cfg.retry_jitter_s)
    raise RuntimeError("HTTP retry loop exhausted")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — Blackboard: auth, course discovery
# ══════════════════════════════════════════════════════════════════════════════

def _bb_token(bb: BbCfg) -> str:
    """Obtain a Blackboard OAuth2 access token."""
    resp = _session().post(
        f"{bb.base_url}/learn/api/public/v1/oauth2/token",
        auth=(bb.key, bb.secret),
        data={"grant_type": "client_credentials"},
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Blackboard authentication failed (HTTP {resp.status_code}).\n"
            f"  Double-check bb_key and bb_secret in your config file.\n"
            f"  Response: {resp.text[:200]}"
        )
    tok = resp.json().get("access_token")
    if not tok:
        raise RuntimeError("Blackboard returned no access_token — check your credentials.")
    return tok


def _bb_hdrs(tok: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {tok}", "Accept": "application/json"}


def _bb_check_quota(bb: BbCfg, tok: str) -> None:
    """
    Probe the Blackboard REST API rate-limit headers before starting.
    Exits cleanly if the quota is already exhausted (HTTP 429).
    Warns but continues if usage is >= 90%.
    """
    print("  Checking Blackboard API quota …", end="  ", flush=True)
    try:
        r = _session().get(
            f"{bb.base_url}/learn/api/public/v3/courses?limit=1",
            headers=_bb_hdrs(tok),
            timeout=15,
        )
    except Exception as e:
        print(f"⚠  probe failed ({e}) — continuing anyway")
        return

    if r.status_code == 429:
        raw   = r.headers.get("Retry-After", "")
        retry = f" (retry after {raw}s)" if raw else ""
        limit = r.headers.get("X-Rate-Limit-Limit", "?")
        used  = int(limit) - int(r.headers.get("X-Rate-Limit-Remaining", 0)) if limit != "?" else "?"
        print(f"✗  RATE LIMIT EXHAUSTED{retry}")
        print(f"     {used} of {limit} calls used this window.")
        print("     Wait for the quota window to reset, then re-run.")
        sys.exit(1)

    if r.status_code == 200:
        limit  = r.headers.get("X-Rate-Limit-Limit")
        remain = r.headers.get("X-Rate-Limit-Remaining")
        if limit and remain:
            used = int(limit) - int(remain)
            pct  = used / int(limit) * 100
            msg  = f"✓  {remain}/{limit} calls remaining  ({pct:.0f}% used)"
            if pct >= 90:
                msg += "  ⚠  WARNING: nearly exhausted"
            print(msg)
        else:
            print("✓  (no rate-limit headers returned by this instance)")
    else:
        print(f"⚠  unexpected HTTP {r.status_code} from quota probe — continuing anyway")



def get_courses_in_terms(bb: BbCfg, bb_tok: str,
                         term_names: List[str]) -> List[Dict]:
    """
    Discover all courses in the named terms via the Blackboard REST API.
    Returns a list of course dicts with bb_id, course_id, name, etc.
    """
    base = f"{bb.base_url}/learn/api/public/v1"
    hdrs = _bb_hdrs(bb_tok)

    # Resolve human-readable term names → Blackboard term IDs
    terms_resp = _session().get(f"{base}/terms", headers=hdrs, timeout=30)
    if terms_resp.status_code != 200:
        raise RuntimeError(
            f"Could not list Blackboard terms (HTTP {terms_resp.status_code}). "
            f"Check that your REST API integration has the correct entitlements."
        )
    all_terms    = terms_resp.json().get("results", [])
    name_to_id   = {t["name"].strip(): t["id"] for t in all_terms if t.get("name")}

    term_ids: List[str] = []
    for name in term_names:
        tid = name_to_id.get(name.strip())
        if tid:
            term_ids.append(tid)
        else:
            sample = list(name_to_id.keys())[:6]
            print(f"\n  ⚠  Term not found: '{name}'")
            print(f"     Available terms (sample): {', '.join(sample)}")

    if not term_ids:
        raise RuntimeError(
            f"No matching terms found. "
            f"Make sure the term name matches exactly as it appears in Blackboard."
        )

    # Paginate through courses for each matched term
    all_courses: List[Dict] = []
    for term_id in term_ids:
        url: Optional[str] = f"{base}/courses"
        params: Optional[Dict] = {
            "termId": term_id,
            "fields": "id,courseId,name,termId,hasChildren,parentId,availability",
            "limit": 100,
        }
        while url:
            r = _session().get(url, headers=hdrs, params=params, timeout=30)
            if r.status_code != 200:
                raise RuntimeError(
                    f"Course list request failed (HTTP {r.status_code}): {r.text[:200]}"
                )
            page = r.json()
            all_courses.extend(page.get("results", []))
            next_page = page.get("paging", {}).get("nextPage")
            if next_page:
                url = (f"{bb.base_url}{next_page}"
                       if next_page.startswith("/") else next_page)
                params = None
            else:
                url = None
            time.sleep(0.5)

    return [
        {
            "bb_id":      c["id"],
            "course_id":  c.get("courseId", ""),
            "name":       c.get("name", ""),
            "parent_id":  c.get("parentId"),           # set → cross-list child
            "has_children": bool(c.get("hasChildren")),
        }
        for c in all_courses
    ]


def _ally_student_count(cfg: AllyCfg, tok_mgr: _TokenMgr, bb_id: str) -> int:
    """Fetch the student enrollment count for one course from Ally."""
    url = (cfg.base_url
           + cfg.report_path.format(client_id=cfg.client_id, course_id=bb_id))
    try:
        r = _http(cfg, "GET", url, _ally_hdrs(tok_mgr.get()))
        js = r.json()
        if not isinstance(js, dict):
            return 0
        val = js.get("numberOfStudents") or js.get("students") or 0
        return int(float(str(val)))
    except Exception:
        return 0


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — Ally file fetching
# ══════════════════════════════════════════════════════════════════════════════

def _parse_csv_bytes(blob: bytes) -> List[Dict[str, str]]:
    """Decode and parse Ally's CSV export, auto-detecting the delimiter."""
    text = blob.decode("utf-8-sig", errors="replace")
    for delim in (",", "\t", ";", "|"):
        try:
            rows = list(csv.DictReader(io.StringIO(text), delimiter=delim))
            if rows and len(rows[0]) > 3:
                return rows
        except csv.Error:
            continue
    return list(csv.DictReader(io.StringIO(text)))


def _extract_file_ids(rows: List[Dict]) -> List[str]:
    """Pull file IDs from Ally's per-course CSV export."""
    if not rows:
        return []
    cols  = list(rows[0].keys())
    lower = {c.lower().strip(): c for c in cols}
    id_col = None
    for candidate in ("file id", "file_id", "fileid", "id"):
        if candidate in lower:
            id_col = lower[candidate]
            break
    if not id_col:
        for c in cols:
            if "file" in c.lower() and "id" in c.lower():
                id_col = c
                break
    if not id_col:
        return []

    seen: set = set()
    ids: List[str] = []
    for row in rows:
        raw = (row.get(id_col) or "").strip()
        for pid in raw.replace(";", ",").split(","):
            pid = pid.strip()
            if pid and pid not in seen:
                seen.add(pid)
                ids.append(pid)
    return ids


def _chunked(seq: Sequence, n: int):
    for i in range(0, len(seq), n):
        yield list(seq[i: i + n])


def _flatten(obj: Any, prefix: str = "", out: Optional[Dict] = None) -> Dict:
    """Recursively flatten a nested dict into dot-notation keys."""
    if out is None:
        out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            _flatten(v, f"{prefix}.{k}" if prefix else k, out)
    elif isinstance(obj, list):
        out[prefix] = json.dumps(obj, ensure_ascii=False)
    else:
        out[prefix] = obj
    return out


def _download_ally_csv(cfg: AllyCfg, tok_mgr: _TokenMgr,
                       bb_id: str, max_wait_s: int = 300) -> List[Dict]:
    """
    Download Ally's per-course file CSV (polls until ready if Ally is
    generating it). Returns parsed rows.
    """
    url     = cfg.base_url + cfg.csv_path.format(client_id=cfg.client_id, course_id=bb_id)
    waited  = 0

    while waited <= max_wait_s:
        r = _http(cfg, "GET", url, _ally_hdrs(tok_mgr.get()))
        if r.status_code == 202:   # report still generating
            time.sleep(6)
            waited += 6
            continue

        blob = r.content
        # Some Ally instances return a JSON object containing a presigned S3 URL
        if blob and blob[:1] == b"{":
            try:
                signed = json.loads(blob.decode("utf-8", errors="replace")).get("url")
                if signed:
                    blob = _session().get(signed, timeout=cfg.timeout_s).content
            except Exception:
                pass
        return _parse_csv_bytes(blob)

    raise TimeoutError(f"Ally CSV timed out after {max_wait_s}s (course {bb_id})")


def _fetch_ally_files(cfg: AllyCfg, tok_mgr: _TokenMgr, bb_id: str) -> List[Dict]:
    """
    Full pipeline for one course:
      1. Download Ally's CSV to get file IDs
      2. POST those IDs to /files to get enriched records (scores, lib refs, …)
    Returns raw Ally file dicts.
    """
    rows     = _download_ally_csv(cfg, tok_mgr, bb_id)
    file_ids = _extract_file_ids(rows)
    if not file_ids:
        return []

    files_url = (cfg.base_url
                 + cfg.files_path.format(client_id=cfg.client_id, course_id=bb_id))
    all_files: List[Dict] = []

    for batch in _chunked(file_ids, cfg.batch_size):
        token = tok_mgr.get()
        hdrs  = {**_ally_hdrs(token), "Content-Type": "application/json"}

        # Ally's /files endpoint accepts two payload shapes; try both
        for payload in ([{"id": fid} for fid in batch], {"fileIds": batch}):
            try:
                r = _http(cfg, "POST", files_url, hdrs, json=payload)
                data   = r.json()
                files: List[Dict] = []
                if isinstance(data, list):
                    files = data
                elif isinstance(data, dict):
                    for key in ("filesReport", "files", "results", "data", "items"):
                        if isinstance(data.get(key), list):
                            files = data[key]
                            break
                    if not files and "id" in data:
                        files = [data]
                valid = [f for f in files
                         if isinstance(f, dict) and f.get("id") and f.get("name")]
                all_files.extend(valid)
                break        # succeeded — don't try the other payload shape
            except Exception:
                continue     # try next payload variant

    return all_files


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — Extract library rows from raw file records
# ══════════════════════════════════════════════════════════════════════════════

def _build_report_rows(course: Dict, raw_files: List[Dict],
                       bb_base_url: str) -> List[Dict]:
    """
    Flatten raw Ally file records and keep only those that have a library
    reference.  Returns clean dicts ready for the HTML report.
    """
    rows = []
    for f in raw_files:
        d = _flatten(f)

        lib_raw = d.get("libraryReference") or d.get("metadata.libraryReference") or ""
        library_reference = str(lib_raw).strip() if lib_raw else ""
        if not library_reference:
            continue   # not a library file — skip

        raw_score = d.get("score.value") or d.get("score")
        try:
            val = float(raw_score)
            score_pct = round(val * 100) if 0 <= val <= 1 else round(val, 1)
        except (ValueError, TypeError):
            score_pct = None

        size_mb = None
        for k, v in d.items():
            if "size" in k.lower() and v:
                try:
                    size_mb = round(float(v) / (1024 ** 2), 2)
                    break
                except (ValueError, TypeError):
                    pass

        xythos_id    = str(f.get("id") or "").strip().lstrip("_")
        ally_360_url = (
            f"{bb_base_url}/webapps/cmsadmin/execute/reports?xythos_id={xythos_id}"
            if xythos_id else ""
        )

        rows.append({
            "course_id":         course["course_id"],
            "course_name":       course["name"],
            "name":              f.get("name", ""),
            "type":              f.get("type", ""),
            "file_size_mb":      size_mb,
            "score_percent":     score_pct,
            "library_reference": library_reference,
            "ally_360_url":      ally_360_url,
        })
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — HTML report generation
# ══════════════════════════════════════════════════════════════════════════════

def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&#39;"))


def _parse_lib_ref(raw: str) -> Dict[str, str]:
    """Parse a JSON library reference string into a clean dict."""
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        if isinstance(d, dict):
            return {k: str(v).strip() for k, v in d.items() if v}
    except (json.JSONDecodeError, TypeError):
        pass
    return {"title": raw.strip()}   # not JSON — treat whole string as title


def _pub_key(ref: Dict) -> str:
    """Stable deduplication key for grouping the same publication."""
    return " | ".join([
        (ref.get("title") or "").lower().strip(),
        (ref.get("authors") or ref.get("author") or "").lower().strip(),
        (ref.get("publisher") or "").lower().strip(),
    ])


def _aggregate_publications(rows: List[Dict]) -> List[Dict]:
    """Group report rows by unique publication for the By Publication tab."""
    groups: Dict[str, Dict] = {}
    for r in rows:
        ref = _parse_lib_ref(r.get("library_reference", ""))
        key = _pub_key(ref)
        if key not in groups:
            groups[key] = {
                "title":     ref.get("title", ""),
                "authors":   ref.get("authors") or ref.get("author", ""),
                "publisher": ref.get("publisher", ""),
                "year":      ref.get("publicationDate") or ref.get("year", ""),
                "courses":   {},
                "file_count": 0,
            }
        g = groups[key]
        cid = r["course_id"]
        if cid:
            g["courses"][cid] = r["course_name"]
        g["file_count"] += 1

    return sorted(groups.values(),
                  key=lambda g: (-len(g["courses"]), g["title"].lower()))


_FONTS = ("https://fonts.googleapis.com/css2?"
          "family=DM+Mono:ital,wght@0,300;0,400;0,500;1,400"
          "&family=DM+Sans:wght@300;400;500;600&display=swap")


def build_html(rows: List[Dict], title: str) -> str:
    """Build a self-contained, interactive HTML report from report rows."""
    pubs       = _aggregate_publications(rows)
    n_courses  = len({r["course_id"] for r in rows if r["course_id"]})
    generated  = dt.datetime.now().strftime("%B %d, %Y %I:%M %p")
    title_safe = _esc(title)

    # Build JS data payloads
    js_rows = []
    for r in rows:
        ref = _parse_lib_ref(r.get("library_reference", ""))
        js_rows.append({
            "ci":  r["course_id"],
            "cn":  r["course_name"],
            "fn":  r["name"],
            "typ": r.get("type", ""),
            "sz":  r.get("file_size_mb", ""),
            "sc":  r.get("score_percent", ""),
            "lt":  ref.get("title", ""),
            "la":  ref.get("authors") or ref.get("author", ""),
            "lp":  ref.get("publisher", ""),
            "ly":  ref.get("publicationDate") or ref.get("year", ""),
        })

    js_pubs = [
        {
            "t":  p["title"],
            "a":  p["authors"],
            "p":  p["publisher"],
            "y":  p["year"],
            "nc": len(p["courses"]),
            "nf": p["file_count"],
            "courses": [{"id": cid, "name": cname}
                        for cid, cname in sorted(p["courses"].items())],
        }
        for p in pubs
    ]

    payload_json = json.dumps(js_rows, ensure_ascii=False).replace("</", r"<\/")
    pub_json     = json.dumps(js_pubs,  ensure_ascii=False).replace("</", r"<\/")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title_safe}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/clusterize.js/0.19.0/clusterize.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/clusterize.js/0.19.0/clusterize.min.js"></script>
<link href="{_FONTS}" rel="stylesheet">
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#0f1117;--surf:#181c26;--surf2:#1e2333;--bdr:#2a2f42;
  --txt:#eaeef8;--dim:#a8b2cc;--faint:#6b7a99;
  --accent:#4f7cff;--accent-dim:#2a3d7a;
  --lib:#a3e635;--lib-bg:#1a2b06;
  --r:6px;
  --sans:'DM Sans',system-ui,sans-serif;
  --mono:'DM Mono','Courier New',monospace;
}}
body{{background:var(--bg);color:var(--txt);font-family:var(--sans);font-size:14px;line-height:1.5;height:100vh;overflow:hidden;display:flex;flex-direction:column}}
.hdr{{background:var(--surf);border-bottom:1px solid var(--bdr);padding:14px 20px;display:flex;align-items:center;gap:16px;flex-wrap:wrap;flex-shrink:0}}
.hdr-title{{font-size:15px;font-weight:600;flex:1;min-width:200px}}
.hdr-sub{{font-size:11px;color:var(--faint)}}
.pills{{display:flex;gap:8px;flex-wrap:wrap}}
.pill{{background:var(--surf2);border:1px solid var(--bdr);border-radius:20px;padding:3px 10px;font-size:12px;color:var(--dim)}}
.pill b{{color:var(--txt)}}
.bar{{background:var(--surf);border-bottom:1px solid var(--bdr);padding:10px 20px;display:flex;align-items:center;gap:10px;flex-shrink:0;flex-wrap:wrap}}
.srch{{position:relative;flex:1;min-width:180px;max-width:360px}}
.srch input{{width:100%;background:var(--surf2);border:1px solid var(--bdr);border-radius:var(--r);color:var(--txt);padding:6px 10px 6px 30px;font-size:13px;outline:none}}
.srch input:focus{{border-color:var(--accent)}}
.srch-ico{{position:absolute;left:9px;top:50%;transform:translateY(-50%);color:var(--faint);font-size:13px;pointer-events:none}}
.cnt{{font-size:12px;color:var(--faint);margin-left:auto}}
.tab{{background:transparent;border:1px solid var(--bdr);border-radius:var(--r);color:var(--dim);padding:5px 12px;cursor:pointer;font-size:12px;transition:all .15s}}
.tab.on{{background:var(--accent-dim);border-color:var(--accent);color:var(--accent)}}
.dl-btn{{background:var(--surf2);border:1px solid var(--bdr);border-radius:var(--r);color:var(--dim);padding:5px 11px;cursor:pointer;font-size:12px;transition:all .15s;white-space:nowrap}}
.dl-btn:hover{{border-color:var(--accent);color:var(--txt)}}
.view{{flex:1;overflow:hidden;display:none;flex-direction:column}}
.view.on{{display:flex}}
.tw{{flex:1;overflow:hidden;display:flex;flex-direction:column}}
.sa-wrap{{overflow-y:scroll;scrollbar-gutter:stable;flex:1}}
.sa-wrap::-webkit-scrollbar{{width:8px}}
.sa-wrap::-webkit-scrollbar-thumb{{background:var(--bdr);border-radius:3px}}
.tb{{width:100%;border-collapse:collapse;table-layout:fixed}}
.th th{{position:sticky;top:0;z-index:2;background:var(--surf);border-bottom:2px solid var(--bdr);padding:8px 10px;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--faint);font-weight:500;white-space:nowrap;text-align:left}}
.sb{{background:transparent;border:none;color:var(--faint);cursor:pointer;font-size:11px;text-transform:uppercase;letter-spacing:.05em;display:flex;align-items:center;gap:4px;padding:0;white-space:nowrap}}
.sb.on{{color:var(--accent)}}.sa{{opacity:.5;font-size:10px}}.sb.on .sa{{opacity:1}}
.tb td{{padding:7px 10px;border-bottom:1px solid var(--bdr);vertical-align:middle;font-size:13px;overflow:hidden}}
.tb tr:hover td{{background:var(--surf2)}}
.c-typ{{width:70px}}.c-crs{{width:26%}}.c-fil{{width:22%}}.c-sz{{width:70px}}.c-pub{{width:24%}}.c-aut{{width:14%}}.c-yr{{width:60px}}
.chip{{display:inline-block;padding:2px 7px;border-radius:4px;font-size:11px;font-weight:500;font-family:var(--mono)}}
.chip.pdf{{background:#2b0d0d;color:#ef4444}}.chip.document{{background:#1a2b06;color:#a3e635}}.chip.presentation{{background:#2b1a00;color:#f97316}}.chip.image{{background:#1a1a2b;color:#818cf8}}.chip.other{{background:var(--surf2);color:var(--dim)}}
.cn{{display:block;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}}
.ci{{display:block;font-size:11px;color:var(--faint);font-family:var(--mono)}}
.fn{{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}}
.lt{{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--lib);max-width:100%}}
.la{{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--dim);font-size:12px;max-width:100%}}
.sz-b{{background:var(--surf2);border:1px solid var(--bdr);color:var(--dim);padding:1px 4px;border-radius:4px;font-size:11px;font-family:var(--mono);white-space:nowrap}}
.pub-scroll{{overflow-y:auto;flex:1;padding:16px 20px}}
.pub-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}}
.pub-card{{background:var(--surf);border:1px solid var(--bdr);border-radius:var(--r);padding:14px;display:flex;flex-direction:column;gap:6px}}
.pub-t{{font-weight:600;font-size:14px;color:var(--lib)}}
.pub-m{{font-size:12px;color:var(--dim)}}
.pub-s{{font-size:11px;color:var(--faint)}}
.pub-cs{{display:flex;flex-wrap:wrap;gap:4px;margin-top:4px}}
.pub-c{{background:var(--surf2);border:1px solid var(--bdr);border-radius:4px;padding:2px 6px;font-size:10px;font-family:var(--mono);color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:180px}}
.empty{{display:none;text-align:center;padding:60px 20px;color:var(--faint)}}
.empty.on{{display:block}}
.foot{{background:var(--surf);border-top:1px solid var(--bdr);padding:6px 20px;font-size:11px;color:var(--faint);flex-shrink:0;text-align:right}}
.clusterize-scroll{{height:100% !important;overflow-y:scroll !important}}
</style>
</head>
<body>

<div class="hdr">
  <div>
    <div class="hdr-title">{title_safe}</div>
    <div class="hdr-sub">Generated {generated}</div>
  </div>
  <div class="pills">
    <span class="pill"><b>{len(rows):,}</b> files</span>
    <span class="pill"><b>{len(pubs):,}</b> publications</span>
    <span class="pill"><b>{n_courses:,}</b> courses</span>
  </div>
</div>

<div class="bar">
  <div class="srch">
    <span class="srch-ico">🔍</span>
    <input type="text" id="q" placeholder="Search courses, files, publications…">
  </div>
  <button class="tab on" data-v="files">Files</button>
  <button class="tab" data-v="pubs">By Publication</button>
  <button class="dl-btn" onclick="downloadCSV()" title="Download current view as CSV (matches active search filter)">↓ CSV</button>
  <span class="cnt" id="cnt"></span>
</div>

<div class="view on" id="view-files">
  <div class="tw">
    <div class="sa-wrap" id="scrollArea">
      <table class="tb">
        <thead class="th"><tr>
          <th class="c-typ"><button class="sb" data-c="type">Type<span class="sa">↕</span></button></th>
          <th class="c-crs"><button class="sb" data-c="course">Course<span class="sa">↕</span></button></th>
          <th class="c-fil"><button class="sb" data-c="file">File<span class="sa">↕</span></button></th>
          <th class="c-sz">Size</th>
          <th class="c-pub"><button class="sb" data-c="pub">Publication<span class="sa">↕</span></button></th>
          <th class="c-aut"><button class="sb" data-c="aut">Author<span class="sa">↕</span></button></th>
          <th class="c-yr"><button class="sb" data-c="yr">Year<span class="sa">↕</span></button></th>
        </tr></thead>
        <tbody id="tableBody"></tbody>
      </table>
    </div>
  </div>
  <div class="empty" id="fe">No matching files found.</div>
</div>

<div class="view" id="view-pubs">
  <div class="pub-scroll"><div class="pub-grid" id="pubList"></div></div>
  <div class="empty" id="pe">No matching publications found.</div>
</div>

<div class="foot">Ally Library Reference Report · ally_library_standalone.py v{VERSION}</div>

<script>
const P={payload_json};
const PB={pub_json};
let q="",sc="course",sd="asc",cl=null,av="files";

function chip(t){{const c=["pdf","document","presentation","image"].includes(t)?t:"other";return`<span class="chip ${{c}}">${{t}}</span>`;}}
function sv(r,c){{
  switch(c){{
    case"type":return r.typ.toLowerCase();case"course":return r.cn.toLowerCase();
case"file":return r.fn.toLowerCase();
    case"pub":return(r.lt||"").toLowerCase();case"aut":return(r.la||"").toLowerCase();
    case"yr":return r.ly||"";default:return"";
  }}
}}
function filt(){{
  const s=q.toLowerCase();
  return P.filter(r=>!s||[r.ci,r.cn,r.fn,r.lt,r.la,r.lp].some(v=>(v||"").toLowerCase().includes(s)))
    .sort((a,b)=>{{let c=sv(a,sc)<sv(b,sc)?-1:sv(a,sc)>sv(b,sc)?1:0;if(c)return sd==="asc"?c:-c;return a.cn.toLowerCase()<b.cn.toLowerCase()?-1:1;}});
}}
function row(r){{
  const sz=r.sz?`<span class="sz-b">${{parseFloat(r.sz).toFixed(2)}} MB</span>`:`<span style="color:var(--faint)">—</span>`;
  return`<tr>
    <td class="c-typ">${{chip(r.typ)}}</td>
    <td class="c-crs"><span class="cn" title="${{r.cn}}">${{r.cn}}</span><span class="ci">${{r.ci}}</span></td>
    <td class="c-fil"><span class="fn" title="${{r.fn}}">${{r.fn}}</span></td>
    <td class="c-sz">${{sz}}</td>
    <td class="c-pub"><span class="lt" title="${{r.lt}}">${{r.lt||"—"}}</span></td>
    <td class="c-aut"><span class="la" title="${{r.la}}">${{r.la||"—"}}</span></td>
    <td class="c-yr" style="font-family:var(--mono);font-size:12px;color:var(--dim)">${{r.ly||"—"}}</td>
  </tr>`;
}}
function renderFiles(){{
  const rows=filt();
  document.getElementById("cnt").textContent=rows.length.toLocaleString()+" / "+P.length.toLocaleString()+" files";
  const em=document.getElementById("fe");
  if(!rows.length){{if(cl)cl.update(["<tr><td colspan='7'></td></tr>"]);em.classList.add("on");return;}}
  em.classList.remove("on");
  const h=rows.map(row);
  if(cl)cl.update(h);
  else cl=new Clusterize({{rows:h,scrollId:"scrollArea",contentId:"tableBody",rows_in_block:40,blocks_in_cluster:4,tag:"tr"}});
}}
function renderPubs(){{
  const s=q.toLowerCase();
  const fs=PB.filter(p=>!s||[p.t,p.a,p.p,p.y].some(v=>(v||"").toLowerCase().includes(s))||p.courses.some(c=>c.id.toLowerCase().includes(s)||c.name.toLowerCase().includes(s)));
  document.getElementById("cnt").textContent=fs.length.toLocaleString()+" / "+PB.length.toLocaleString()+" publications";
  const el=document.getElementById("pubList"),em=document.getElementById("pe");
  if(!fs.length){{el.innerHTML="";em.classList.add("on");return;}}
  em.classList.remove("on");
  el.innerHTML=fs.map(p=>{{
    const m=[p.a,p.p,p.y].filter(Boolean).join(" · ");
    const cs=p.courses.map(c=>`<span class="pub-c" title="${{c.name}}">${{c.id}}</span>`).join("");
    return`<div class="pub-card"><div class="pub-t">${{p.t||"(untitled)"}}</div><div class="pub-m">${{m||"No additional metadata"}}</div><div class="pub-s">${{p.nf}} file(s) across ${{p.nc}} course(s)</div><div class="pub-cs">${{cs}}</div></div>`;
  }}).join("");
}}
function downloadCSV(){{
  const rows=filt();
  const hdrs=['Course ID','Course Name','File Name','Type','Size (MB)','Score (%)','Publication Title','Author','Publisher','Year'];
  const esc=v=>('"'+String(v==null?'':v).replace(/"/g,'""')+'"');
  const lines=[hdrs.map(esc).join(',')];
  for(const r of rows){{
    lines.push([r.ci,r.cn,r.fn,r.typ,r.sz||'',r.sc||'',r.lt||'',r.la||'',r.lp||'',r.ly||''].map(esc).join(','));
  }}
  const blob=new Blob(['\\uFEFF'+lines.join('\\n')],{{type:'text/csv;charset=utf-8;'}});
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');
  a.href=url;
  // filename reflects any active search
  const suffix=q?'_filtered':'';
  a.download='ally_library_report'+suffix+'.csv';
  document.body.appendChild(a);a.click();document.body.removeChild(a);
  URL.revokeObjectURL(url);
}}

document.getElementById("q").addEventListener("input",e=>{{q=e.target.value.trim();av==="files"?renderFiles():renderPubs();}});
document.querySelectorAll(".sb").forEach(b=>b.addEventListener("click",()=>{{
  const col=b.dataset.c;
  sd=sc===col?(sd==="asc"?"desc":"asc"):(b.dataset.dir||"asc");sc=col;
  document.querySelectorAll(".sb").forEach(x=>{{x.classList.remove("on");x.querySelector(".sa").textContent="↕";}});
  b.classList.add("on");b.querySelector(".sa").textContent=sd==="asc"?"↑":"↓";
  renderFiles();
}}));
document.querySelectorAll(".tab").forEach(b=>b.addEventListener("click",()=>{{
  av=b.dataset.v;
  document.querySelectorAll(".tab").forEach(x=>x.classList.remove("on"));b.classList.add("on");
  document.querySelectorAll(".view").forEach(v=>v.classList.remove("on"));
  document.getElementById("view-"+av).classList.add("on");
  av==="files"?renderFiles():renderPubs();
}}));
const ib=document.querySelector('.sb[data-c="course"]');
if(ib){{ib.classList.add("on");ib.querySelector(".sa").textContent="↑";}}
renderFiles();
</script>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — Main pipeline
# ══════════════════════════════════════════════════════════════════════════════

def _fmt_now() -> str:
    n = dt.datetime.now()
    h = str(n.hour % 12 or 12)
    return f"{n.month}/{n.day}/{n.year} {h}:{n.strftime('%M %p')}"


def _progress(done: int, total: int, label: str) -> None:
    w   = 22
    pct = done / total if total else 0
    bar = "█" * int(w * pct) + "░" * (w - int(w * pct))
    print(f"\r  [{bar}] {done}/{total}  {label:<48}", end="", flush=True)


def run_check(cfg_raw: dict) -> None:
    """Test all credentials without generating a report."""
    print()
    print("── Credential Check ──────────────────────────────────")
    ally_cfg, bb_cfg = _cfgs_from_json(cfg_raw)

    # 1. Ally JWT minting
    print("  Minting Ally LTI token …", end="  ", flush=True)
    try:
        tok = _mint_ally_token(ally_cfg)
        print(f"✓  ({len(tok)} chars)")
    except Exception as e:
        print(f"✗  FAILED\n     {e}")
        return

    # 2. Blackboard OAuth token
    print("  Requesting Blackboard OAuth2 token …", end="  ", flush=True)
    try:
        bb_tok = _bb_token(bb_cfg)
        print(f"✓  ({len(bb_tok)} chars)")
    except Exception as e:
        print(f"✗  FAILED\n     {e}")
        return

    # 3. Blackboard API quota
    _bb_check_quota(bb_cfg, bb_tok)

    # 4. Blackboard terms (validates the token actually works)
    print("  Listing Blackboard terms …", end="  ", flush=True)
    try:
        r = _session().get(
            f"{bb_cfg.base_url}/learn/api/public/v1/terms",
            headers=_bb_hdrs(bb_tok), timeout=20,
        )
        result = r.json()
        all_terms = result.get("results", [])
        sample    = [t["name"] for t in all_terms[:5] if t.get("name")]
        print(f"✓  ({len(all_terms)} terms found)")
        if sample:
            print(f"     Sample: {', '.join(sample)}")
    except Exception as e:
        print(f"✗  FAILED\n     {e}")
        return

    print()
    print("  All checks passed! You're ready to generate reports.")
    print(f"\n  Try:  python {Path(__file__).name} --terms \"{sample[0] if sample else 'Spring 2026'}\"")
    print()


def run(
    cfg_raw:               dict,
    terms:                 List[str],
    out_path:              Path,
    title:                 str,
    exclude_child_courses: bool = False,
    exclude_zero_students: bool = False,
    verbose:               bool = False,
) -> None:
    """End-to-end pipeline: fetch data → filter → build HTML → write file."""
    ally_cfg, bb_cfg = _cfgs_from_json(cfg_raw)
    tok_mgr = _TokenMgr(ally_cfg)

    # ── Auth ──────────────────────────────────────────────────────────────────
    print("\n  Authenticating with Blackboard …", end="  ", flush=True)
    try:
        bb_tok = _bb_token(bb_cfg)
        print("✓")
    except RuntimeError as e:
        print(f"✗\n\n  {e}\n")
        sys.exit(1)

    _bb_check_quota(bb_cfg, bb_tok)

    # ── Course discovery ──────────────────────────────────────────────────────
    print(f"  Discovering courses in: {', '.join(terms)} …", end="  ", flush=True)
    try:
        courses = get_courses_in_terms(bb_cfg, bb_tok, terms)
        print(f"✓  ({len(courses):,} courses)")
    except RuntimeError as e:
        print(f"✗\n\n  {e}\n")
        sys.exit(1)

    # ── Filters ───────────────────────────────────────────────────────────────
    if exclude_child_courses:
        before  = len(courses)
        courses = [c for c in courses if not c["parent_id"]]
        n_drop  = before - len(courses)
        if n_drop:
            print(f"  Excluded {n_drop:,} child/cross-listed courses "
                  f"({len(courses):,} remain)")

    if exclude_zero_students and courses:
        print(f"  Fetching student counts from Ally ({len(courses):,} courses) …",
              end="  ", flush=True)
        counts: Dict[str, int] = {}
        def _get_count(c: Dict) -> Tuple[str, int]:
            return c["bb_id"], _ally_student_count(ally_cfg, tok_mgr, c["bb_id"])
        with ThreadPoolExecutor(max_workers=6) as pool:
            for bb_id, n in pool.map(_get_count, courses):
                counts[bb_id] = n
        before  = len(courses)
        courses = [c for c in courses if counts.get(c["bb_id"], 0) > 0]
        print(f"✓  (excluded {before - len(courses):,} zero-student courses, "
              f"{len(courses):,} remain)")

    if not courses:
        print("\n  No courses remain after filtering. Nothing to report.\n")
        return

    # ── Ally file fetching ────────────────────────────────────────────────────
    print(f"\n  Fetching Ally file data for {len(courses):,} courses …")
    all_rows: List[Dict] = []
    errors:   List[str]  = []
    total                = len(courses)
    completed            = 0
    lock                 = threading.Lock()

    def _process(c: Dict) -> Tuple[Dict, List[Dict]]:
        raw   = _fetch_ally_files(ally_cfg, tok_mgr, c["bb_id"])
        rows  = _build_report_rows(c, raw, bb_cfg.base_url)
        return c, rows

    _progress(0, total, "starting…")

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_process, c): c for c in courses}
        for future in as_completed(futures):
            c = futures[future]
            try:
                _, rows = future.result()
                with lock:
                    all_rows.extend(rows)
            except Exception as e:
                with lock:
                    errors.append(f"{c['course_id']}: {e}")
                if verbose:
                    print(f"\n  ⚠  {c['course_id']}: {e}")
            completed += 1
            _progress(completed, total, c["course_id"])

    print()   # end the progress-bar line

    # ── Summary ───────────────────────────────────────────────────────────────
    lib_course_count = len({r["course_id"] for r in all_rows})
    print(f"\n  ✓  {len(all_rows):,} library files found across "
          f"{lib_course_count:,} courses")

    if errors:
        print(f"  ⚠  {len(errors)} course(s) had errors")
        if not verbose:
            print("     (re-run with --verbose to see details)")
        else:
            for e in errors:
                print(f"     • {e}")

    if not all_rows:
        print("\n  No library references found in the selected terms.")
        print("  Nothing to write.\n")
        return

    pubs = _aggregate_publications(all_rows)
    print(f"     {len(pubs):,} unique publications identified")

    # ── Write HTML ────────────────────────────────────────────────────────────
    print(f"\n  Building HTML report …", end="  ", flush=True)
    html = build_html(all_rows, title)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8", newline="\n")
    size_kb = out_path.stat().st_size // 1024
    print(f"✓")

    print()
    path_str = str(out_path)
    meta_str = f"{size_kb} KB  ·  Open in any browser — no server needed."
    w = max(len(path_str), len(meta_str)) + 4
    print(f"  ┌{'─' * w}┐")
    print(f"  │  {path_str:<{w - 2}}│")
    print(f"  │  {meta_str:<{w - 2}}│")
    print(f"  └{'─' * w}┘")
    print()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 10 — CLI entry point
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate an Ally library reference HTML report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # ── Setup / check (no config needed) ──────────────────────────────────────
    ap.add_argument("--setup", action="store_true",
                    help="Run the first-time setup wizard to create your config file")
    ap.add_argument("--check", action="store_true",
                    help="Test your credentials without generating a report")

    # ── Config ────────────────────────────────────────────────────────────────
    ap.add_argument("--config", default=str(DEFAULT_CONFIG), metavar="PATH",
                    help=f"Path to your config JSON file "
                         f"(default: {CONFIG_FILENAME} next to this script)")

    # ── Report content ────────────────────────────────────────────────────────
    ap.add_argument("--terms", action="append", default=None, metavar="TERM",
                    help="Term to include. Repeat for multiple: "
                         "--terms \"Spring 2026\" --terms \"Summer 2026\"")
    ap.add_argument("--out", default="", metavar="PATH",
                    help="Output HTML filename "
                         "(default: ally_library_report.html next to this script)")
    ap.add_argument("--title", default="", metavar="TEXT",
                    help="Report title shown in the HTML header")

    # ── Filters ───────────────────────────────────────────────────────────────
    ap.add_argument("--exclude-child-courses", action="store_true",
                    help="Exclude cross-listed child / merged courses "
                         "(recommended if your school uses cross-listing)")
    ap.add_argument("--exclude-zero-students", action="store_true",
                    help="Skip courses with no enrolled students "
                         "(slower — requires one extra Ally API call per course)")
    # ── Debug ─────────────────────────────────────────────────────────────────
    ap.add_argument("--verbose", action="store_true",
                    help="Show per-course error details as they happen")

    args = ap.parse_args()

    # ── Setup wizard ──────────────────────────────────────────────────────────
    if args.setup:
        cfg = run_setup_wizard(Path(args.config))
        print()
        print("Next steps:")
        print(f"  1. Test credentials:  python {Path(__file__).name} --check")
        print(f"  2. Generate a report: python {Path(__file__).name} --terms \"<Term Name>\"")
        print()
        return

    # ── Load config ───────────────────────────────────────────────────────────
    cfg_raw = load_config(Path(args.config))

    # ── Credential check ──────────────────────────────────────────────────────
    if args.check:
        run_check(cfg_raw)
        return

    # ── Resolve terms ─────────────────────────────────────────────────────────
    terms = args.terms or cfg_raw.get("default_terms") or []
    if not terms:
        print()
        print("  ERROR: No terms specified.")
        print()
        print("  Options:")
        print(f"    1. Pass --terms \"Spring 2026\" on the command line")
        print(f"    2. Set default_terms in your config file (run --setup)")
        print()
        sys.exit(1)

    # ── Resolve output path ───────────────────────────────────────────────────
    out_path = (Path(args.out) if args.out
                else SCRIPT_DIR / "ally_library_report.html")

    # ── Resolve title ─────────────────────────────────────────────────────────
    title = args.title or f"Ally Library Reference Report — {', '.join(terms)}"

    # ── Banner ────────────────────────────────────────────────────────────────
    print()
    print("═" * 60)
    print(f"  Ally Library Reference Report  v{VERSION}")
    print(f"  {_fmt_now()}   Terms: {', '.join(terms)}")
    print("═" * 60)

    run(
        cfg_raw               = cfg_raw,
        terms                 = terms,
        out_path              = out_path,
        title                 = title,
        exclude_child_courses = args.exclude_child_courses,
        exclude_zero_students = args.exclude_zero_students,
        verbose               = args.verbose,
    )


if __name__ == "__main__":
    main()
