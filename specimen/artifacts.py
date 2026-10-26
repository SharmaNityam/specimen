"""Privacy checks and the deliberately small public artifact archive."""
from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path
from typing import Iterable

PUBLIC_FILES = ("specimen.csv", "repro.py", "report.json", "REPORT.md")
_SENSITIVE_HEADER = re.compile(
    r"(?:^|[^a-z0-9])(name|first.?name|last.?name|email|e.?mail|phone|mobile|address|street|city|postal|zip|ssn|social.?security|token|secret|password|passwd|api.?key|access.?key|account|customer.?id|user.?id|birth|dob)(?:$|[^a-z0-9])",
    re.I,
)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_SECRET = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}|Bearer\s+[A-Za-z0-9._~+/=-]{16,})\b",
    re.I,
)
_LITERAL_SECRET = re.compile(r"\b(?:api[_-]?key|password|passwd|secret|token)\s*[:=]\s*['\"][^'\"]{8,}['\"]", re.I)


def _read_source(source: Path) -> list[list[str]]:
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(8192)
        handle.seek(0)
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter if sample else ","
        except csv.Error:
            delimiter = ","
        return list(csv.reader(handle, delimiter=delimiter, strict=True))


def _source_sensitive_info(source: Path, explicit: Iterable[str]) -> tuple[set[str], set[int]]:
    rows = _read_source(source)
    if not rows:
        return set(), set()
    names = {name.casefold() for name in explicit}
    indexes = {i for i, name in enumerate(rows[0]) if _SENSITIVE_HEADER.search(name) or name.casefold() in names}
    values = {row[index] for row in rows[1:] for index in indexes if index < len(row) and len(row[index]) >= 3}
    return values, indexes


def build_artifact_zip(output_dir: Path, source_csv: Path, source_script: Path,
                       sensitive_columns: Iterable[str] = ()) -> bytes | None:
    """Return a ZIP of approved files, or ``None`` when a privacy scan blocks it.

    The engine's privacy-checked ``repro.py`` is included with the reviewed
    specimen. The original input and script paths are inspection sources only;
    the archive has a fixed allowlist and excludes inputs, caches, and mappings.
    """
    output_dir, source_csv, source_script = Path(output_dir), Path(source_csv), Path(source_script)
    try:
        values, sensitive_indexes = _source_sensitive_info(source_csv, sensitive_columns)
        script = source_script.read_text(encoding="utf-8", errors="replace")
        contents: dict[str, bytes] = {}
        for name in PUBLIC_FILES:
            path = output_dir / name
            if not path.is_file():
                return None
            contents[name] = path.read_bytes()
    except (OSError, UnicodeError, csv.Error):
        return None

    # Scan both supplied source code and every file about to leave the process.
    # Comparing values here is internal only; no matched value is returned.
    scanned = [("source script", script)] + [
        (name, blob.decode("utf-8", "replace")) for name, blob in contents.items()
    ]
    for name, text in scanned:
        if any(value in text for value in values):
            return None
        # Transformed email cells may retain their email shape. Exact original
        # values are already checked above; email literals in code/evidence are
        # still suspicious and should prevent a shareable bundle.
        email_secret = name != "specimen.csv" and _EMAIL.search(text)
        if email_secret or _SECRET.search(text) or _LITERAL_SECRET.search(text):
            return None
    try:
        specimen_rows = list(csv.reader(io.StringIO(contents["specimen.csv"].decode("utf-8"), newline=""), strict=True))
    except (UnicodeError, csv.Error):
        return None
    # A replacement may keep the email-shaped format inside a sensitive
    # column. The same shape in a non-sensitive column is a likely copied leak.
    for row in specimen_rows[1:]:
        for index, cell in enumerate(row):
            if index in sensitive_indexes:
                continue
            if _EMAIL.search(cell) or _SECRET.search(cell) or _LITERAL_SECRET.search(cell):
                return None
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, blob in contents.items():
            bundle.writestr(name, blob)
        bundle.writestr(
            "REPRODUCE.txt",
            "Run the reviewed specimen with a compatible Python interpreter:\n"
            "  python repro.py specimen.csv\n"
            "The archive contains a minimized, sanitized CSV and execution evidence.\n",
        )
    return archive.getvalue()
