import csv
import json
import os
import sys
import time
from pathlib import Path

from specimen.contracts import RunConfig
from specimen.core import reduce_csv
from specimen.runner import _fingerprint, run_script


def _config(tmp_path: Path, script: str, content: str, **kwargs) -> RunConfig:
    csv_path = tmp_path / "input.csv"
    csv_path.write_text(content, encoding="utf-8")
    script_path = tmp_path / "script.py"
    script_path.write_text(script, encoding="utf-8")
    return RunConfig(input_path=csv_path, script_path=script_path,
                     output_dir=tmp_path / "out", runner="local", **kwargs)


def test_fingerprint_omits_exception_message_and_data(tmp_path):
    script = tmp_path / "script.py"
    script.write_text("raise ValueError('private-value')\n", encoding="utf-8")
    execution = run_script(script, tmp_path / "data.csv", runner="local")
    assert execution.exception_type == "ValueError"
    assert execution.location == "line 1 in <module>"
    assert "private-value" not in execution.fingerprint
    assert "private-value" not in execution.location


def test_fingerprint_supports_custom_exception_classes(tmp_path):
    script = tmp_path / "script.py"
    script.write_text("class Boom(Exception): pass\nraise Boom('private')\n", encoding="utf-8")
    execution = run_script(script, tmp_path / "data.csv", runner="local")
    assert execution.exception_type == "Boom"
    assert execution.fingerprint == "Boom|line 2 in <module>"


def test_exception_fingerprint_ignores_multiline_message_words(tmp_path):
    script = tmp_path / "script.py"
    script.write_text(
        """import csv, sys
rows = list(csv.DictReader(open(sys.argv[1], newline='')))
if len(rows) == 2:
    raise ValueError('message\\nCustomerRecord')
raise TypeError('message\\nCustomerRecord')
""", encoding="utf-8")
    csv_path = tmp_path / "data.csv"
    csv_path.write_text("value\none\ntwo\n", encoding="utf-8")
    execution = run_script(script, csv_path, runner="local")
    assert execution.exception_type == "ValueError"
    assert execution.fingerprint.startswith("ValueError|")


def test_reducer_rejects_error_change_with_multiline_message(tmp_path):
    config = _config(
        tmp_path,
        """import csv, sys
rows = list(csv.DictReader(open(sys.argv[1], newline='')))
if len(rows) == 2:
    raise ValueError('message\\nCustomerRecord')
raise TypeError('message\\nCustomerRecord')
""",
        "value\none\ntwo\n",
        max_runs=60, max_seconds=30,
    )
    report = reduce_csv(config)
    assert report["status"] == "verified"
    assert report["baseline_fingerprint"].startswith("ValueError|")
    assert report["final_rows"] == 2


def test_rechecks_minimality_after_sanitization_changes_failure_predicate(tmp_path):
    config = _config(
        tmp_path,
        """import csv, sys
rows = list(csv.DictReader(open(sys.argv[1], newline='')))
if len(rows) >= 2 or any(ord(row['name'][0]) > 65 for row in rows):
    raise ValueError('stable')
""",
        "name\nAA\nAA\n",
        max_runs=80, max_seconds=30,
    )
    report = reduce_csv(config)
    assert report["status"] == "verified"
    assert report["minimized"] is True
    assert report["final_rows"] == 1


def test_reduces_to_minimal_failing_row_and_replaces_email(tmp_path):
    config = _config(
        tmp_path,
        """import csv, sys
with open(sys.argv[1], newline='') as f:
    rows = list(csv.DictReader(f))
for row in rows:
    if row['keep'] == 'yes':
        raise ValueError('boom')
""",
        "email,keep\nalice@example.com,yes\nbob@example.com,no\ncarol@example.com,no\n",
        max_runs=100, max_seconds=30,
    )
    report = reduce_csv(config)
    assert report["status"] == "verified"
    assert report["final_rows"] == 1
    assert report["minimized"] is True
    assert report["runs"] >= 5
    assert report["verification"]["final_runs"] == 2
    specimen = (config.output_dir / "specimen.csv").read_text(encoding="utf-8")
    assert "alice@example.com" not in specimen
    assert "bob@example.com" not in specimen
    assert (config.output_dir / "repro.py").exists()
    saved = json.loads((config.output_dir / "report.json").read_text(encoding="utf-8"))
    assert saved["sha256"] == report["sha256"]
    with (config.output_dir / "specimen.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1


def test_refuses_artifact_when_sensitive_value_is_required_for_failure(tmp_path):
    config = _config(
        tmp_path,
        """import csv, sys
with open(sys.argv[1], newline='') as f:
    rows = list(csv.DictReader(f))
if any(row['token'] == 'secret-token' for row in rows):
    raise ValueError('boom')
""",
        "token,keep\nsecret-token,x\nother-token,y\n",
        max_runs=60, max_seconds=30,
    )
    report = reduce_csv(config)
    assert report["status"] == "needs_review"
    assert report["blockers"]
    assert not (config.output_dir / "specimen.csv").exists()
    assert not (config.output_dir / "repro.py").exists()


def test_rejects_flaky_baseline_and_does_not_export_candidate(tmp_path):
    counter = tmp_path / "counter.txt"
    script = tmp_path / "script.py"
    script.write_text(
        """import pathlib, sys
p = pathlib.Path(__file__).with_name('counter.txt')
n = int(p.read_text()) if p.exists() else 0
p.write_text(str(n + 1))
if n % 2 == 0:
    raise ValueError('first')
else:
    raise TypeError('second')
""", encoding="utf-8")
    config = RunConfig(input_path=tmp_path / "input.csv", script_path=script,
                       output_dir=tmp_path / "out", runner="local")
    config.input_path.write_text("value\na\n", encoding="utf-8")
    report = reduce_csv(config)
    assert report["status"] == "unstable"
    assert report["runs"] == 2
    assert not (config.output_dir / "specimen.csv").exists()


def test_budget_must_allow_two_baseline_runs(tmp_path):
    config = _config(tmp_path, "raise ValueError('x')\n", "x\na\n", max_runs=1)
    report = reduce_csv(config)
    assert report["status"] == "budget_exhausted"
    assert report["runs"] == 1
    assert report["baseline_fingerprint"] is None
    assert not (config.output_dir / "specimen.csv").exists()


def test_local_runner_cannot_overwrite_original_csv(tmp_path):
    source = "value\nkeep\n"
    config = _config(
        tmp_path,
        """import pathlib, sys
pathlib.Path(sys.argv[1]).write_text('destroyed')
raise ValueError('stable')
""",
        source,
        max_runs=40, max_seconds=30,
    )
    report = reduce_csv(config)
    assert report["status"] == "verified"
    assert config.input_path.read_text(encoding="utf-8") == source


def test_sensitive_substrings_are_replaced_in_all_cells(tmp_path):
    sensitive = "alice@private.test"
    config = _config(
        tmp_path,
        """import csv, sys
rows = list(csv.DictReader(open(sys.argv[1], newline='')))
if rows:
    raise ValueError('stable')
""",
        f"email,note\n{sensitive},copy:{sensitive}\n",
        max_runs=50, max_seconds=30,
    )
    report = reduce_csv(config)
    assert report["status"] == "verified"
    for artifact in ("specimen.csv", "report.json", "REPORT.md", "repro.py"):
        assert sensitive not in (config.output_dir / artifact).read_text(encoding="utf-8")


def test_max_seconds_caps_individual_run_timeout(tmp_path):
    config = _config(
        tmp_path,
        "import time\ntime.sleep(.5)\nraise ValueError('slow')\n",
        "value\na\n",
        max_runs=20, max_seconds=.05, timeout=1,
    )
    started = time.monotonic()
    report = reduce_csv(config)
    assert report["status"] == "budget_exhausted"
    assert report["runs"] == 1
    assert time.monotonic() - started < .35


def test_final_verification_rewrites_mutated_candidate(tmp_path):
    source = (
        "import pathlib, sys\n"
        "p = pathlib.Path(sys.argv[1])\n"
        "data = p.read_text()\n"
        "p.write_text('damaged')\n"
        "if 'keep' in data:\n"
        "    raise ValueError('stable')\n"
    )
    config = _config(tmp_path, source, "value\nkeep\n", max_runs=50, max_seconds=30)
    report = reduce_csv(config)
    assert report["status"] == "verified"
    with (config.output_dir / "specimen.csv").open(newline="", encoding="utf-8") as handle:
        assert list(csv.DictReader(handle)) == [{"value": "keep"}]


def test_policy_sensitive_indices_are_additive_and_strategies_apply(tmp_path):
    config = _config(
        tmp_path,
        """import csv, sys
rows = list(csv.DictReader(open(sys.argv[1], newline='')))
if rows:
    raise ValueError('stable')
""",
        "id,reference\nabc-123,case:abc-123\n",
        max_runs=50, max_seconds=30,
    )
    config.sensitive_columns = ["id"]
    config.policy = {"sensitive_column_indices": [1], "strategies": ["length", "unicode"]}
    report = reduce_csv(config)
    assert report["status"] == "verified"
    assert report["policy"] == {
        "applied": True, "suggested_sensitive_indices": [1], "strategies": ["length", "unicode"]
    }
    assert "abc-123" not in (config.output_dir / "specimen.csv").read_text(encoding="utf-8")


def test_local_runner_kills_child_processes_after_script_exits(tmp_path):
    script = tmp_path / "script.py"
    source = (
        "import pathlib, subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(15)'])\n"
        "pathlib.Path(__file__).with_name('child.pid').write_text(str(child.pid))\n"
        "raise ValueError('parent exits')\n"
    )
    script.write_text(source, encoding="utf-8")
    execution = run_script(script, tmp_path / "input.csv", runner="local", timeout=2)
    assert execution.fingerprint is not None
    child_pid = int((tmp_path / "child.pid").read_text())
    for _ in range(30):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(.05)
    else:
        raise AssertionError("script child process survived runner cleanup")

