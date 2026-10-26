"""CSV minimization and deterministic privacy transformations."""
from __future__ import annotations

import csv
import hashlib
import hmac
import json
import math
import re
import secrets
import shutil
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any

from .contracts import Execution, RunConfig
from .runner import is_infrastructure, run_script

_SENSITIVE_NAME = re.compile(
    r"(?:^|[^a-z0-9])(name|first.?name|last.?name|email|e.?mail|phone|mobile|address|street|city|postal|zip|ssn|social.?security|token|secret|password|passwd|api.?key|access.?key|account|customer.?id|user.?id|birth|dob)(?:$|[^a-z0-9])",
    re.I,
)
_ARTIFACTS = ("specimen.csv", "repro.py", "report.json", "REPORT.md")


def _emit(callback, event_type: str, message: str, **fields: Any) -> None:
    if callback is not None:
        event = {"type": event_type, "message": message}
        event.update(fields)
        callback(event)


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(8192)
        handle.seek(0)
        delimiter = ","
        if sample:
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
            except csv.Error:
                pass
        reader = csv.reader(handle, delimiter=delimiter, strict=True)
        rows = list(reader)
    if not rows or not rows[0] or all(not cell for cell in rows[0]):
        raise ValueError("CSV has no header")
    header = rows[0]
    if any(not name for name in header) or len(set(header)) != len(header):
        raise ValueError("CSV header is empty or has duplicate columns")
    width = len(header)
    if any(len(row) != width for row in rows[1:]):
        raise ValueError("CSV contains rows with inconsistent column counts")
    return header, rows[1:]


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def _fingerprint(execution: Execution) -> str | None:
    if execution.timed_out or is_infrastructure(execution):
        return None
    if execution.returncode == 0 or not execution.fingerprint:
        return None
    return execution.fingerprint


def _replacement_char(char: str, offset: int) -> str | None:
    """Pick a nearby character of the same Unicode category and case shape."""
    if char.isdigit() and char.isascii():
        return str((int(char) + offset) % 10)
    if char.isascii() and char.isalpha():
        base = ord("A") if char.isupper() else ord("a")
        return chr(base + (ord(char) - base + offset) % 26)
    category = unicodedata.category(char)
    if category.startswith(("L", "N", "P", "S")):
        # Look locally first to retain scripts and uncommon Unicode classes.
        start = ord(char)
        for delta in range(offset, 8193):
            cp = start + delta
            if cp <= 0x10FFFF:
                candidate = chr(cp)
                if unicodedata.category(candidate) == category:
                    if category.startswith("L") and char.isupper() and not candidate.isupper():
                        continue
                    if category.startswith("L") and char.islower() and not candidate.islower():
                        continue
                    return candidate
            cp = start - delta
            if cp >= 0:
                candidate = chr(cp)
                if unicodedata.category(candidate) == category:
                    if category.startswith("L") and char.isupper() and not candidate.isupper():
                        continue
                    if category.startswith("L") and char.islower() and not candidate.islower():
                        continue
                    return candidate
    if category.startswith("P"):
        punctuation = "!#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
        candidates = [ch for ch in punctuation if unicodedata.category(ch) == category]
        if char in candidates and len(candidates) > 1:
            return candidates[(candidates.index(char) + offset) % len(candidates)]
    if category.startswith("S"):
        symbols = "#$%&*+<=>@^~"
        candidates = [ch for ch in symbols if unicodedata.category(ch) == category]
        if char in candidates and len(candidates) > 1:
            return candidates[(candidates.index(char) + offset) % len(candidates)]
    return None


def _pseudonym(value: str, key: bytes, attempt: int, strategy: str) -> str | None:
    """Create a keyed, consistent same-length replacement for one source value."""
    chars: list[str] = []
    value_tag = hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    for index, char in enumerate(value):
        if char.isspace():
            chars.append(char)
            continue
        message = f"{value_tag}:{index}:{unicodedata.category(char)}:{attempt}:{strategy}".encode("utf-8")
        digest = hmac.new(key, message, hashlib.sha256).digest()
        choice = int.from_bytes(digest[:4], "big")
        if strategy == "generic":
            if char.isalpha():
                alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if char.isupper() else "abcdefghijklmnopqrstuvwxyz"
                replacement = alphabet[choice % len(alphabet)]
                if replacement == char:
                    replacement = alphabet[(choice + 1) % len(alphabet)]
            elif char.isdigit():
                alphabet = "0123456789"
                replacement = alphabet[choice % len(alphabet)]
                if replacement == char:
                    replacement = alphabet[(choice + 1) % len(alphabet)]
            else:
                replacement = char
        elif strategy == "length":
            if char.isalpha():
                alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if char.isupper() else "abcdefghijklmnopqrstuvwxyz"
                replacement = alphabet[choice % len(alphabet)]
                if replacement == char:
                    replacement = alphabet[(choice + 1) % len(alphabet)]
            elif char.isdigit():
                alphabet = "0123456789"
                replacement = alphabet[choice % len(alphabet)]
                if replacement == char:
                    replacement = alphabet[(choice + 1) % len(alphabet)]
            else:
                alphabet = "abcdefghijklmnopqrstuvwxyz"
                replacement = alphabet[choice % len(alphabet)]
                if replacement == char:
                    replacement = alphabet[(choice + 1) % len(alphabet)]
        else:
            offset = 1 + (choice % 3) + attempt
            replacement = _replacement_char(char, offset)
        if replacement is None:
            return None
        chars.append(replacement)
    result = "".join(chars)
    if result == value:
        # Last-resort deterministic choices for punctuation-only values.
        choices = ["_", "~", ".", "+", "!", "-", ":", "="]
        for i, char in enumerate(value):
            if unicodedata.category(char).startswith("P"):
                replacement = next((candidate for candidate in choices
                                    if candidate != char and unicodedata.category(candidate) == unicodedata.category(char)), None)
                if replacement:
                    result = value[:i] + replacement + value[i + 1:]
                    break
    return result if result != value and len(result) == len(value) else None


def _replace_sensitive_substrings(value: str, mapping: dict[str, str]) -> str:
    """Replace complete sensitive values wherever they occur in an exported cell."""
    for source in sorted(mapping, key=lambda item: (-len(item), item)):
        value = value.replace(source, mapping[source])
    return value


def _contains_sensitive_substring(values: list[str], text: str) -> bool:
    return any(value and value in text for value in values)


def _scrub_metadata(value: Any, sensitive_values: list[str]) -> Any:
    if isinstance(value, str):
        for sensitive in sorted(sensitive_values, key=lambda item: (-len(item), item)):
            if sensitive:
                value = value.replace(sensitive, "[redacted]")
        return value
    if isinstance(value, list):
        return [_scrub_metadata(item, sensitive_values) for item in value]
    if isinstance(value, dict):
        return {key: _scrub_metadata(item, sensitive_values) for key, item in value.items()}
    return value


def _write_report(output: Path, report: dict[str, Any]) -> None:
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Specimen report", "",
        f"Status: **{report['status']}**", "",
        f"Rows: {report['original_rows']} original; {report['final_rows']} retained.",
        f"Executions: {report['runs']}.",
        f"Reduction 1-minimal by row deletion: {'yes' if report['minimized'] else 'no'}.",
        f"Baseline fingerprint: `{report['baseline_fingerprint'] or 'none'}`.",
        f"Privacy blockers: {len(report['blockers'])}.",
        f"Artifacts: {', '.join(report['artifact_names']) or 'none'}.",
        "", "This report contains execution metadata only; raw program output is not included.", "",
        "Pseudonyms preserve selected character classes and string lengths for this run; they do not guarantee complete anonymization.", "",
    ]
    (output / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def _script_has_sensitive_literals(script_path: Path, values: list[str]) -> bool:
    try:
        source = script_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return True
    if any(value and value in source for value in values):
        return True
    pattern = r"(?i)\b(?:api[_-]?key|access[_-]?key|token|secret|password|passwd)\b\s*=\s*(['\"])([^'\"]{4,})\1"
    return bool(re.search(pattern, source))


def reduce_csv(config: RunConfig, on_event=None) -> dict[str, Any]:
    """Reduce a failing CSV, sanitize sensitive cells, and write evidence artifacts."""
    started = time.monotonic()
    output = Path(config.output_dir)
    input_resolved = Path(config.input_path).resolve()
    script_resolved = Path(config.script_path).resolve()
    output_resolved = output.resolve()
    collision_names = [name for name in _ARTIFACTS
                       if (output_resolved / name).resolve() in {input_resolved, script_resolved}]
    if collision_names:
        report = {
            "status": "error", "original_rows": 0, "final_rows": 0,
            "runs": 0, "baseline_fingerprint": None, "minimized": False,
            "transformations": {"sensitive_columns": 0, "values_changed": 0, "strategy": None},
            "policy": {"applied": False, "suggested_sensitive_indices": [], "strategies": []},
            "blockers": ["output artifact path collides with an input file"],
            "verification": {}, "artifact_names": [], "sha256": None,
        }
        return report
    output.mkdir(parents=True, exist_ok=True)
    for name in _ARTIFACTS:
        try:
            (output / name).unlink()
        except FileNotFoundError:
            pass
    report: dict[str, Any] = {
        "status": "error", "original_rows": 0, "final_rows": 0,
        "runs": 0, "baseline_fingerprint": None, "minimized": False,
        "transformations": {"sensitive_columns": 0, "values_changed": 0, "strategy": None},
        "policy": {"applied": False, "suggested_sensitive_indices": [], "strategies": []},
        "blockers": [], "verification": {}, "artifact_names": [], "sha256": None,
    }

    def finish() -> dict[str, Any]:
        return _finish(output, report, on_event=on_event, started=started)

    try:
        input_path = input_resolved
        script_path = script_resolved
        header, original = _read_csv(input_path)
        report["original_rows"] = len(original)
        report["final_rows"] = len(original)
        sensitive = {i for i, name in enumerate(header) if _SENSITIVE_NAME.search(name)}
        names = {name.casefold(): i for i, name in enumerate(header)}
        missing_columns = [name for name in config.sensitive_columns if name.casefold() not in names]
        sensitive.update(names[name.casefold()] for name in config.sensitive_columns if name.casefold() in names)
        policy = config.policy if isinstance(config.policy, dict) else None
        if policy is None:
            raise ValueError("policy must be an object")
        suggested_indices = policy.get("suggested_sensitive_indices", policy.get("sensitive_column_indices", []))
        if (not isinstance(suggested_indices, list)
                or any(not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(header)
                       for index in suggested_indices)):
            raise ValueError("invalid suggested sensitive column indices")
        suggested_indices = sorted(set(suggested_indices))
        sensitive.update(suggested_indices)
        strategies = policy.get("strategies", ["generic", "unicode", "length"])
        allowed_strategies = {"generic", "unicode", "length"}
        if (not isinstance(strategies, list)
                or any(not isinstance(strategy, str) or strategy not in allowed_strategies for strategy in strategies)):
            raise ValueError("invalid sanitization strategy suggestion")
        strategies = list(dict.fromkeys(strategies)) or ["generic", "unicode", "length"]
        report["policy"] = {
            "applied": bool(suggested_indices or "strategies" in policy),
            "suggested_sensitive_indices": suggested_indices,
            "strategies": strategies,
        }
        if report["policy"]["applied"]:
            _emit(on_event, "progress", "Applied validated privacy policy suggestions",
                  rows=len(original), runs=report["runs"])
        report["transformations"]["sensitive_columns"] = len(sensitive)
        if missing_columns:
            report["blockers"].append("configured sensitive column is missing")
            report["status"] = "error"
            raise ValueError("configured sensitive column is missing")
        raw_values = sorted({row[index] for row in original for index in sensitive if row[index]}, key=lambda s: (len(s), s))
        report["_sensitive_values"] = raw_values
        if any(_contains_sensitive_substring(raw_values, name) for name in header):
            report["status"] = "needs_review"
            report["blockers"].append("a sensitive source value occurs in a CSV header")
            return finish()
        budget_deadline = started + max(0.0, float(config.max_seconds))
        cache: dict[tuple[int, ...], tuple[bool, str | None]] = {}

        def can_run() -> bool:
            return report["runs"] < max(0, int(config.max_runs)) and time.monotonic() < budget_deadline

        def execute_csv(path: Path) -> Execution | None:
            remaining = budget_deadline - time.monotonic()
            if report["runs"] >= max(0, int(config.max_runs)) or remaining <= 0:
                return None
            report["runs"] += 1
            return run_script(script_path, path, config.runner, min(max(0.0001, float(config.timeout)), remaining))

        _emit(on_event, "progress", "Checking baseline stability", rows=len(original), runs=report["runs"])
        with tempfile.TemporaryDirectory(prefix="specimen-baseline-") as baseline_dir:
            baseline_copy = Path(baseline_dir) / "input.csv"
            baseline_execs = []
            for _ in range(2):
                if not can_run():
                    baseline_execs.append(None)
                    continue
                shutil.copyfile(input_path, baseline_copy)
                baseline_execs.append(execute_csv(baseline_copy))
        if any(execution is None for execution in baseline_execs):
            report["status"] = "budget_exhausted"
            report["blockers"].append("execution budget does not allow repeated baseline verification")
            return finish()
        _emit(on_event, "progress", "Repeated baseline executions finished",
              rows=len(original), runs=report["runs"])
        first, second = baseline_execs  # type: ignore[misc]
        fp1, fp2 = _fingerprint(first), _fingerprint(second)
        report["verification"]["baseline_runs"] = 2
        if is_infrastructure(first) or is_infrastructure(second):
            report["status"] = "needs_review"
            report["blockers"].append("baseline execution could not be classified safely")
            return finish()
        if fp1 != fp2:
            report["status"] = "unstable"
            report["blockers"].append("repeated baseline executions did not produce the same failure")
            return finish()
        if fp1 is None:
            report["status"] = "not_reproduced"
            return finish()
        baseline_fp = fp1
        report["baseline_fingerprint"] = baseline_fp

        # Candidate subsets are keyed by original row indices, making repeated
        # ddmin probes free and keeping all candidate CSVs outside the output dir.
        with tempfile.TemporaryDirectory(prefix="specimen-") as temp_dir:
            temp_root = Path(temp_dir)

            def preserves(indices: tuple[int, ...]) -> bool | None:
                if indices in cache:
                    return cache[indices][0]
                if not can_run():
                    return None
                candidate_path = temp_root / f"candidate-{len(cache)}.csv"
                _write_csv(candidate_path, header, [original[index] for index in indices])
                execution = execute_csv(candidate_path)
                if execution is None:
                    return None
                result = _fingerprint(execution) == baseline_fp
                cache[indices] = (result, execution.fingerprint)
                _emit(on_event, "progress", "Testing a reduced row set", rows=len(indices), runs=report["runs"])
                return result

            current = list(range(len(original)))
            granularity = 2
            exhaustive = True
            while len(current) >= 2:
                chunk_size = math.ceil(len(current) / granularity)
                chunks = [current[i:i + chunk_size] for i in range(0, len(current), chunk_size)]
                changed = False
                for chunk in chunks:
                    complement = tuple(index for index in current if index not in set(chunk))
                    if preserves(complement) is True:
                        current = list(complement)
                        _emit(on_event, "progress", "Accepted row reduction", rows=len(current), runs=report["runs"])
                        granularity = max(granularity - 1, 2)
                        changed = True
                        break
                if changed:
                    continue
                for chunk in chunks:
                    subset = tuple(chunk)
                    if preserves(subset) is True:
                        current = list(subset)
                        _emit(on_event, "progress", "Accepted failing row subset", rows=len(current), runs=report["runs"])
                        granularity = max(granularity - 1, 2)
                        changed = True
                        break
                if changed:
                    continue
                if not can_run():
                    exhaustive = False
                    break
                if granularity >= len(current):
                    break
                granularity = min(len(current), granularity * 2)
            if exhaustive:
                changed = True
                while changed:
                    changed = False
                    for index in list(current):
                        candidate = tuple(row_index for row_index in current if row_index != index)
                        result = preserves(candidate)
                        if result is None:
                            exhaustive = False
                            changed = False
                            break
                        if result:
                            current = list(candidate)
                            _emit(on_event, "progress", "Confirmed one-row reduction", rows=len(current), runs=report["runs"])
                            changed = True
                            break
            report["minimized"] = exhaustive
            report["final_rows"] = len(current)
            if exhaustive:
                _emit(on_event, "progress", "Row reduction is 1-minimal", rows=len(current), runs=report["runs"])
            if not exhaustive:
                report["blockers"].append("reduction stopped at the configured execution budget")
                report["status"] = "budget_exhausted"
                return finish()

            reduced_rows = [original[index] for index in current]
            # Unique source identifiers map consistently across duplicate cells.
            # Replace every sensitive value observed in the source, even when a
            # copy appears in a retained non-sensitive column.
            values = raw_values
            report["transformations"]["values_changed"] = len(values)
            verified_rows: list[list[str]] | None = None
            used_strategy = None
            last_reason = "sanitized candidate did not preserve the baseline failure"
            pseudonym_key = secrets.token_bytes(32)
            for strategy in strategies:
                for attempt in range(8):
                    _emit(on_event, "progress", f"Trying {strategy} sanitization", rows=len(reduced_rows), runs=report["runs"])
                    mapping: dict[str, str] = {}
                    impossible = False
                    used_replacements: set[str] = set()
                    for value in values:
                        replacement = _pseudonym(value, pseudonym_key, attempt, strategy)
                        if replacement is None or replacement == value:
                            impossible = True
                            break
                        if replacement in used_replacements:
                            replacement = _pseudonym(value, pseudonym_key, attempt + 8, strategy)
                        if replacement is None or replacement == value or replacement in used_replacements:
                            impossible = True
                            break
                        mapping[value] = replacement
                        used_replacements.add(replacement)
                    if impossible or any(_contains_sensitive_substring(values, value) for value in mapping.values()):
                        last_reason = "a sensitive value has no safe same-length replacement"
                        continue
                    candidate_rows = [[_replace_sensitive_substrings(cell, mapping) for cell in row]
                                      for row in reduced_rows]
                    if any(_contains_sensitive_substring(values, cell)
                           for row in candidate_rows for cell in row):
                        last_reason = "a sensitive source substring remains after transformation"
                        continue
                    candidate_path = temp_root / f"sanitized-{strategy}-{attempt}.csv"
                    _write_csv(candidate_path, header, candidate_rows)
                    execution = execute_csv(candidate_path)
                    if execution is None:
                        exhaustive = False
                        report["minimized"] = False
                        last_reason = "budget ended before sanitized verification"
                        break
                    _emit(on_event, "progress", "Sanitized candidate checked", rows=len(candidate_rows), runs=report["runs"])
                    if _fingerprint(execution) != baseline_fp:
                        last_reason = "sanitized candidate did not preserve the baseline failure"
                        continue
                    # Sanitization can change which rows trigger the failure, so
                    # establish 1-minimality again on the sanitized candidate.
                    working_rows = list(candidate_rows)
                    minimality_runs = 0
                    budget_stopped = False
                    changed = True
                    while changed:
                        changed = False
                        for row_index in range(len(working_rows)):
                            trial_rows = [row for i, row in enumerate(working_rows) if i != row_index]
                            _write_csv(candidate_path, header, trial_rows)
                            trial = execute_csv(candidate_path)
                            if trial is None:
                                exhaustive = False
                                budget_stopped = True
                                last_reason = "budget ended while checking sanitized row minimality"
                                break
                            minimality_runs += 1
                            if _fingerprint(trial) == baseline_fp:
                                working_rows = trial_rows
                                changed = True
                                _emit(on_event, "progress", "Accepted sanitized row reduction",
                                      rows=len(working_rows), runs=report["runs"])
                                break
                        if budget_stopped:
                            break
                    if budget_stopped:
                        report["minimized"] = False
                        break
                    report["verification"]["sanitized_minimality_runs"] = minimality_runs
                    # Each execution gets a pristine candidate because tested
                    # scripts may mutate their input path.
                    final_ok = True
                    for _ in range(2):
                        _write_csv(candidate_path, header, working_rows)
                        execution = execute_csv(candidate_path)
                        if execution is None:
                            exhaustive = False
                            report["minimized"] = False
                            last_reason = "budget ended before repeated final verification"
                            final_ok = False
                            break
                        if _fingerprint(execution) != baseline_fp:
                            last_reason = "repeated sanitized verification was unstable"
                            final_ok = False
                            break
                    if not final_ok:
                        if not exhaustive:
                            break
                        continue
                    verified_rows = working_rows
                    used_strategy = f"{strategy}; same-length replacement; variant {attempt + 1}"
                    report["verification"]["final_runs"] = 2
                    report["verification"]["final_fingerprint"] = baseline_fp
                    report["final_rows"] = len(working_rows)
                    report["minimized"] = True
                    _emit(on_event, "progress", "Repeated final verification passed",
                          rows=len(working_rows), runs=report["runs"])
                    break
                if verified_rows is not None or not exhaustive:
                    break

            if verified_rows is None:
                report["status"] = "budget_exhausted" if not exhaustive else "needs_review"
                report["blockers"].append(last_reason)
                return finish()
            if len(set(mapping.values())) != len(mapping):
                report["blockers"].append("sensitive value mapping was not one-to-one")
                report["status"] = "needs_review"
                return finish()
            # Confirm every source-sensitive substring is absent from all cells.
            for old in values:
                if any(old in cell for row in verified_rows for cell in row):
                    report["status"] = "needs_review"
                    report["blockers"].append("a sensitive source value remains in the candidate")
                    return finish()

            if _script_has_sensitive_literals(script_path, raw_values):
                report["status"] = "needs_review"
                report["blockers"].append("the source script contains a sensitive literal and cannot be shared safely")
                return finish()

            specimen_path = output / "specimen.csv"
            _write_csv(specimen_path, header, verified_rows)
            report["status"] = "verified"
            report["final_rows"] = len(verified_rows)
            report["transformations"]["strategy"] = used_strategy
            report["sha256"] = hashlib.sha256(specimen_path.read_bytes()).hexdigest()
            report["artifact_names"] = ["specimen.csv", "repro.py", "report.json", "REPORT.md"]
            (output / "repro.py").write_text(script_path.read_text(encoding="utf-8"), encoding="utf-8")
            return finish()
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        # Deliberately exclude exception text, which can contain filenames or data.
        if report["status"] != "error" or not report["blockers"]:
            report["blockers"].append(f"input could not be processed ({type(exc).__name__})")
        report["status"] = "error"
        return finish()


def _finish(output: Path, report: dict[str, Any], on_event=None, started: float | None = None) -> dict[str, Any]:
    sensitive_values = report.pop("_sensitive_values", [])
    report = _scrub_metadata(report, sensitive_values)
    # Avoid stale files even on an early return.
    if report["status"] != "verified":
        for name in ("specimen.csv", "repro.py"):
            try:
                (output / name).unlink()
            except FileNotFoundError:
                pass
    report["artifact_names"] = [name for name in _ARTIFACTS
                                if (output / name).exists() or name in {"report.json", "REPORT.md"}]
    _write_report(output, report)
    report["artifact_names"] = [name for name in _ARTIFACTS if (output / name).exists()]
    # Persist the final artifact list after report generation.
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if on_event is not None:
        event_type = "complete" if report["status"] == "verified" else ("error" if report["status"] == "error" else "warning")
        if event_type == "complete":
            message = "Sanitized specimen verified"
        elif report["blockers"]:
            message = "Privacy or verification checks blocked specimen output"
        else:
            message = f"Run finished with status {report['status']}"
        event = {"type": event_type, "message": message,
                 "rows": report["final_rows"], "runs": report["runs"]}
        if started is not None:
            event["elapsed"] = round(time.monotonic() - started, 3)
        on_event(event)
    return report
