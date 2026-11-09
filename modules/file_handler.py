"""
file_handler.py — Download, discovery, cache, and PDF compilation logic.

URL format (PapaCambridge):
  QP / MS  →  {sub}_{session}{year}_{type}_{component}.pdf   e.g. 9231_w24_ms_12.pdf
  ER / GT  →  {sub}_{session}{year}_{type}.pdf               e.g. 9231_w24_er.pdf
"""

import os
import json
import time

import fitz  # PyMuPDF
import requests

import sys

# ── Paths ──────────────────────────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    # Running as a bundled executable
    BUNDLE_DIR = sys._MEIPASS
    HOMEPATH  = BUNDLE_DIR
else:
    # Running as a script
    HOMEPATH  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# For writable paths (temp, cache), we should use the actual executable directory
# rather than the bundle directory, as BUNDLE_DIR is read-only.
EXECUTABLE_DIR = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else HOMEPATH
TEMPPATH  = os.path.join(EXECUTABLE_DIR, "temp")
CACHEPATH = os.path.join(EXECUTABLE_DIR, "component_cache.json")

# ── Constants ──────────────────────────────────────────────────────────────
BASE_URL = "https://pastpapers.papacambridge.com/directories/CAIE/CAIE-pastpapers/upload/"
FALLBACK_URL = "https://dynamicpapers.com/wp-content/uploads/2015/09/"

# Paper types that have NO component code (one file per session/year)
NO_COMPONENT_TYPES = {"er", "gt"}

# Session name → URL session code
SESSION_MAP = {
    "Feb/March": "m",
    "May/June":  "s",
    "Oct/Nov":   "w",
}


# ── Helpers ─────────────────────────────────────────────────────────────────

def _ensure_temp():
    os.makedirs(TEMPPATH, exist_ok=True)


def build_filename(sub_code: str, session_code: str, year_str: str,
                   paper_type: str, component: str | None = None) -> str:
    """Build the filename as used by PapaCambridge."""
    if component:
        return f"{sub_code}_{session_code}{year_str}_{paper_type}_{component}.pdf"
    return f"{sub_code}_{session_code}{year_str}_{paper_type}.pdf"


def build_url(filename: str) -> str:
    return BASE_URL + filename


def check_url_exists(url: str) -> bool:
    """Return True if the URL responds with HTTP 200 (PDF exists)."""
    try:
        r = requests.get(url, timeout=10, stream=True)
        r.close()
        return r.status_code == 200
    except Exception:
        return False


# ── Cache ──────────────────────────────────────────────────────────────────

def load_cache() -> dict:
    if os.path.exists(CACHEPATH):
        try:
            with open(CACHEPATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cache(cache: dict) -> None:
    try:
        with open(CACHEPATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        print(f"[Cache] Save error: {e}")


def clear_cache() -> None:
    if os.path.exists(CACHEPATH):
        os.remove(CACHEPATH)


# ── Download ───────────────────────────────────────────────────────────────


def _download_from_url(url: str, path: str, filename: str, log_cb=None) -> bool:
    """Internal helper to download a file from a specific URL."""
    try:
        r = requests.get(url, timeout=30, stream=True)
        if r.status_code == 200:
            # Check Content-Type but be lenient
            content_type = r.headers.get('Content-Type', '').lower()
            
            # If it's definitely not a PDF (like text/html), reject it
            if 'text/html' in content_type or 'application/json' in content_type:
                return False
                
            # Download a small chunk to check for %PDF header
            chunk = next(r.iter_content(chunk_size=1024), b"")
            if not chunk.startswith(b"%PDF"):
                return False
            
            with open(path, "wb") as f:
                f.write(chunk)
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True
    except Exception:
        pass
    return False

def download_paper(sub_code: str, session_code: str, year_str: str,
                    paper_type: str, component: str | None = None,
                    log_cb=None) -> tuple[bool, str]:
    """
    Download one PDF to /temp/. 
    Returns (Success, Source). Source can be 'PapaCambridge', 'DynamicPapers', or 'None'.
    """
    _ensure_temp()
    filename = build_filename(sub_code, session_code, year_str, paper_type, component)
    path     = os.path.join(TEMPPATH, filename)

    # Try PapaCambridge
    url_pc = BASE_URL + filename
    if log_cb:
        log_cb(f"Trying PapaCambridge: {filename}...")
    if _download_from_url(url_pc, path, filename, log_cb):
        if log_cb:
            log_cb(f"  ✓ Found on PapaCambridge")
        return True, "PapaCambridge"

    # Try DynamicPapers
    url_dp = FALLBACK_URL + filename
    if log_cb:
        log_cb(f"Trying DynamicPapers: {filename}...")
    if _download_from_url(url_dp, path, filename, log_cb):
        if log_cb:
            log_cb(f"  ✓ Found on DynamicPapers")
        return True, "DynamicPapers"

    if log_cb:
        log_cb(f"  ✗ Not found anywhere: {filename}")
    return False, "None"



# ── Compile ────────────────────────────────────────────────────────────────

def compile_pdf(save_path: str,
                  files_to_merge: list[str] = None,
                  sort_by: str = "year",
                  sort_order: str = "desc",
                  delete_blanks: bool = False,
                  delete_additional: bool = False,
                  delete_formulae: bool = False,
                  log_cb=None) -> bool:
    """
    Merge specific PDFs into a single file at save_path.
    Sorts files based on sort_by ('year', 'session', 'component', 'interleaved') and sort_order ('asc', 'desc').
    Returns True if at least one file was compiled successfully.
    """
    _ensure_temp()
    
    if files_to_merge is None:
        files = [
            f for f in os.listdir(TEMPPATH)
            if f.lower().endswith(".pdf") and f != ".gitignore"
        ]
    else:
        files = files_to_merge

    if not files:
        return False

    def get_sort_key(filename):
        try:
            name = filename.replace(".pdf", "")
            parts = name.split("_")
            if len(parts) < 2:
                return (0, "", "")
            
            session_year = parts[1]
            session = session_year[0]
            year = int(session_year[1:]) if session_year[1:].isdigit() else 0
            component = parts[3] if len(parts) > 3 else ""
            
            type_map = {"qp": 0, "ms": 1, "gt": 2, "er": 3}
            ptype = parts[2] if len(parts) > 2 else "unknown"
            type_val = type_map.get(ptype, 4)

            if sort_by == "year":
                return (year, session, component)
            elif sort_by == "session":
                return (session, year, component)
            elif sort_by == "component":
                return (component, year, session)
            elif sort_by == "interleaved":
                return (component, type_val, year, session)
            
            return (year, session, component)
        except Exception:
            return (0, "", "")

    sorted_files = sorted(files, key=get_sort_key, reverse=(sort_order == "desc"))

    # Ensure output directory exists
    out_dir = os.path.dirname(save_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    # Handle blank_pdf path for bundled executable
    blank_pdf_path = os.path.join(HOMEPATH, "assets", "blank.pdf")
    if not os.path.exists(blank_pdf_path):
        blank_pdf_path = os.path.join(EXECUTABLE_DIR, "assets", "blank.pdf")

    try:
        out = fitz.open(blank_pdf_path)
        status = False

        for filename in sorted_files:
            try:
                full_path = os.path.join(TEMPPATH, filename)
                if not os.path.exists(full_path):
                    continue
                f = fitz.open(full_path)
                out.insert_file(f)
                f.close()
                status = True
                if log_cb:
                    log_cb(f"  + Compiled: {filename}")
            except Exception as e:
                if log_cb:
                    log_cb(f"  ! Error compiling {filename}: {e}")

        # Pages to strip (always remove the initial blank placeholder)
        pages_to_remove: set[int] = {0}

        if delete_blanks or delete_additional or delete_formulae:
            for page in out:
                text = page.get_text("text")
                if delete_blanks and "BLANK PAGE" in text:
                    pages_to_remove.add(page.number)
                if delete_additional and "Additional Page" in text:
                    pages_to_remove.add(page.number)
                if delete_formulae:
                    if "The Periodic Table of Elements" in text:
                        pages_to_remove.add(page.number)
                    if ("Important values, constants and standards" in text
                            and "are printed in the question paper" not in text):
                        pages_to_remove.add(page.number)
                    if "Stefan\u2013Boltzmann constant" in text:
                        pages_to_remove.add(page.number)
                    if "Mathematical Formulae" in text or "Formula List" in text:
                        pages_to_remove.add(page.number)

        if status:
            out.delete_pages(sorted(pages_to_remove))
            out.save(save_path)
        
        out.close()
        return status
    except Exception as e:
        if log_cb:
            log_cb(f"Critical compilation error: {e}")
        return False



# ── Temp management ────────────────────────────────────────────────────────

def clear_temp_files(log_cb=None) -> None:
    _ensure_temp()
    for f in os.listdir(TEMPPATH):
        if f != ".gitignore":
            try:
                os.remove(os.path.join(TEMPPATH, f))
                if log_cb:
                    log_cb(f"Removed: {f}")
            except Exception as e:
                if log_cb:
                    log_cb(f"Could not remove {f}: {e}")
