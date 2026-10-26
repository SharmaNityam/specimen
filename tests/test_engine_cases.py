"""End-to-end cases for the checked-in synthetic CSV examples."""
import csv
import hashlib
import json
from pathlib import Path

import pytest

from specimen.contracts import RunConfig
from specimen.core import reduce_csv
from specimen.runner import run_script


EXAMPLES = Path(__file__).parents[1] / "examples"


def _read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _reduce(tmp_path: Path, example_id: str) -> tuple[dict, dict, Path, str]:
    folder = EXAMPLES / example_id
    metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    input_path = folder / "data.csv"
    before = hashlib.sha256(input_path.read_bytes()).hexdigest()
    output = tmp_path / example_id
    config = RunConfig(
        input_path=input_path,
        script_path=folder / "repro.py",
        output_dir=output,
        sensitive_columns=metadata["sensitive_columns"],
        runner="local",
        max_runs=150,
        timeout=1,
        max_seconds=30,
    )
    report = reduce_csv(config)
    return report, metadata, output, before


def _assert_sensitive_cells_changed(source: Path, specimen: Path, columns: list[str]) -> None:
    _, original_rows = _read_rows(source)
    _, output_rows = _read_rows(specimen)
    source_values = {row[column] for row in original_rows for column in columns if row[column]}
    output_values = {row[column] for row in output_rows for column in columns if row[column]}
    assert source_values.isdisjoint(output_values)


@pytest.mark.parametrize("example_id, exception_type", [
    ("unicode", "UnicodeEncodeError"),
    ("relations", "ValueError"),
])
def test_redacted_specimen_keeps_original_failure_and_relationships(
    tmp_path: Path, example_id: str, exception_type: str
) -> None:
    folder = EXAMPLES / example_id
    metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    source = folder / "data.csv"
    _, source_rows = _read_rows(source)
    assert len(source_rows) == metadata["rows"]
    baseline = run_script(folder / "repro.py", source, runner="local", timeout=1)
    assert baseline.exception_type == exception_type
    assert baseline.fingerprint

    report, metadata, output, original_hash = _reduce(tmp_path, example_id)
    assert report["status"] == "verified"
    assert report["baseline_fingerprint"] == baseline.fingerprint
    assert report["final_rows"] < report["original_rows"]
    assert report["verification"]["final_runs"] >= 2
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash

    specimen = output / "specimen.csv"
    assert specimen.is_file()
    _assert_sensitive_cells_changed(source, specimen, metadata["sensitive_columns"])
    replay = run_script(folder / "repro.py", specimen, runner="local", timeout=1)
    assert replay.exception_type == exception_type
    assert replay.fingerprint == baseline.fingerprint

    if example_id == "relations":
        _, reduced_rows = _read_rows(specimen)
        pairs = [(row["email"], row["customer_id"]) for row in reduced_rows]
        assert len(pairs) == 2
        assert pairs[0] == pairs[1]
        assert len({row["name"] for row in reduced_rows}) == 2


def test_required_sensitive_literal_blocks_export_without_leaking_value(tmp_path: Path) -> None:
    folder = EXAMPLES / "conflict"
    metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    source = folder / "data.csv"
    _, source_rows = _read_rows(source)
    assert len(source_rows) == metadata["rows"]
    baseline = run_script(folder / "repro.py", source, runner="local", timeout=1)
    assert baseline.exception_type == "ValueError"
    assert baseline.fingerprint

    report, metadata, output, original_hash = _reduce(tmp_path, "conflict")
    assert report["status"] != "verified"
    assert report["blockers"]
    assert not (output / "specimen.csv").exists()
    assert not (output / "repro.py").exists()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash

    # Reports and event-facing text must not repeat the sensitive trigger value.
    report_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (output / "report.json", output / "REPORT.md")
    )
    assert "Required Synthetic Name" not in report_text
    assert "required sentinel encountered" not in report_text
