"""Independent regression checks for publication and execution boundaries."""
import csv
import os
import signal
import time
from pathlib import Path

import pytest

from specimen.contracts import Execution, RunConfig
from specimen.core import reduce_csv
from specimen.runner import _fingerprint, run_script


def config_for(tmp_path, source="value\nretained\n", script=None, **kwargs):
    input_path, script_path = tmp_path / "source.csv", tmp_path / "source.py"
    input_path.write_text(source, encoding="utf-8")
    script_path.write_text(script or "import csv,sys\nrows=list(csv.DictReader(open(sys.argv[1])))\nif rows: raise ValueError()\n", encoding="utf-8")
    return RunConfig(input_path, script_path, tmp_path / "out", runner="local", **kwargs)


@pytest.mark.parametrize("artifact", ["specimen.csv", "repro.py", "report.json", "REPORT.md"])
def test_output_input_alias_never_deletes_original(tmp_path, artifact):
    config = config_for(tmp_path)
    config.output_dir.mkdir()
    (config.output_dir / artifact).symlink_to(config.input_path)
    original = config.input_path.read_bytes()
    report = reduce_csv(config)
    assert report["status"] != "verified"
    assert config.input_path.read_bytes() == original
    assert (config.output_dir / artifact).is_symlink()


def test_sensitive_substring_is_absent_from_every_public_file(tmp_path):
    private = "alice@private.test"
    config = config_for(tmp_path, f"email,note\n{private},copy:{private}\n")
    report = reduce_csv(config)
    for path in config.output_dir.iterdir():
        assert private not in path.read_text(encoding="utf-8")
    if report["status"] != "verified":
        assert not (config.output_dir / "specimen.csv").exists()


def test_sensitive_header_is_blocked_without_leaking_in_report(tmp_path):
    private = "SYNTHETIC-PERSON-981"
    config = config_for(tmp_path, f"name,{private}\n{private},ok\n")
    report = reduce_csv(config)
    assert report["status"] != "verified"
    for path in config.output_dir.iterdir():
        assert private not in path.read_text(encoding="utf-8")


def test_custom_exception_is_classified_from_actual_traceback(tmp_path):
    config = config_for(tmp_path, script="class Boom(Exception): pass\nraise Boom('synthetic private message')\n")
    result = run_script(config.script_path, config.input_path, "local")
    assert result.exception_type == "Boom"
    assert result.fingerprint == "Boom|line 2 in <module>"
    assert "private" not in result.fingerprint


def test_total_budget_caps_an_individual_execution(tmp_path):
    config = config_for(tmp_path, script="import time\ntime.sleep(.7)\nraise ValueError()\n", max_seconds=.05, timeout=1)
    started = time.monotonic()
    report = reduce_csv(config)
    elapsed = time.monotonic() - started
    assert elapsed < .5, f"50ms budget used {elapsed:.3f}s"
    assert report["status"] != "verified"
    assert report["runs"] <= 1


def test_normal_parent_exit_does_not_leave_a_background_child(tmp_path):
    config = config_for(tmp_path, script="import subprocess,sys,pathlib\np=subprocess.Popen([sys.executable,'-c','import time; time.sleep(15)'])\npathlib.Path(__file__).with_name('child.pid').write_text(str(p.pid))\nraise ValueError()\n")
    result = run_script(config.script_path, config.input_path, "local", .5)
    pid = int((tmp_path / "child.pid").read_text())
    alive = False
    try:
        deadline = time.monotonic() + .5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
                break
            time.sleep(.02)
        assert not alive, f"runner returned with child {pid} still alive"
        assert result.duration_ms < 1500
    finally:
        if alive:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_zero_row_failure_is_a_valid_minimal_result(tmp_path):
    config = config_for(tmp_path, source="value\n", script="raise ValueError()\n")
    report = reduce_csv(config)
    assert report["status"] == "verified"
    assert report["final_rows"] == 0
    assert report["minimized"] is True
    assert report["verification"]["final_runs"] == 2


def test_normalized_delimiter_still_replays_the_same_failure(tmp_path):
    config = config_for(tmp_path, "value;flag\none;yes\ntwo;no\n", "import csv,sys\ntext=open(sys.argv[1]).read()\nrows=list(csv.DictReader(text.splitlines(),delimiter=';' if ';' in text else ','))\nif any(r['flag']=='yes' for r in rows): raise ValueError()\n")
    baseline = run_script(config.script_path, config.input_path, "local")
    report = reduce_csv(config)
    assert report["status"] == "verified"
    assert report["final_rows"] == 1
    replay = run_script(config.output_dir / "repro.py", config.output_dir / "specimen.csv", "local")
    assert replay.fingerprint == baseline.fingerprint


def test_reduction_rejects_a_different_exception(tmp_path):
    config = config_for(tmp_path, "value\none\ntwo\n", "import csv,sys\nrows=list(csv.DictReader(open(sys.argv[1])))\nraise (ValueError if len(rows)==2 else TypeError)()\n")
    report = reduce_csv(config)
    assert report["status"] == "verified"
    assert report["final_rows"] == 2


def test_multiline_exception_message_cannot_hide_changed_type(tmp_path):
    config = config_for(tmp_path, "value\none\ntwo\n", "import csv,sys\nrows=list(csv.DictReader(open(sys.argv[1])))\nraise (ValueError if len(rows)==2 else TypeError)('message\\nCustomerRecord')\n")
    report = reduce_csv(config)
    assert report["status"] == "verified"
    assert report["baseline_fingerprint"] == "ValueError|line 3 in <module>"
    assert report["final_rows"] == 2


def test_minimality_claim_applies_to_final_sanitized_rows(tmp_path):
    config = config_for(tmp_path, "name\nAA\nAA\n", "import csv,sys\nrows=list(csv.DictReader(open(sys.argv[1])))\nif len(rows)>=2 or any(ord(r['name'][0])>65 for r in rows): raise ValueError()\n")
    report = reduce_csv(config)
    assert report["status"] == "verified"
    if report["minimized"]:
        with (config.output_dir / "specimen.csv").open(newline="") as handle:
            data = list(csv.reader(handle))
        for index in range(1, len(data)):
            candidate = tmp_path / "deletion.csv"
            with candidate.open("w", newline="") as handle:
                csv.writer(handle).writerows(data[:index] + data[index+1:])
            execution = run_script(config.script_path, candidate, "local")
            assert execution.fingerprint != report["baseline_fingerprint"]


def test_sigterm_resistant_child_is_cleaned_after_timeout(tmp_path):
    config = config_for(tmp_path, script="import subprocess,sys,pathlib,time\np=subprocess.Popen([sys.executable,'-c','import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(15)'])\npathlib.Path(__file__).with_name('child.pid').write_text(str(p.pid))\ntime.sleep(15)\n")
    execution = run_script(config.script_path, config.input_path, "local", .3)
    pid = int((tmp_path / "child.pid").read_text())
    alive = False
    try:
        deadline = time.monotonic() + .5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
                break
            time.sleep(.02)
        assert execution.timed_out and execution.fingerprint is None
        assert not alive
    finally:
        if alive:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_large_output_is_drained_and_bounded_without_deadlock(tmp_path):
    config = config_for(tmp_path, script="import sys\nsys.stdout.write('x' * 2_000_000)\nsys.stderr.write('x' * 2_000_000)\nraise ValueError()\n")
    started = time.monotonic()
    execution = run_script(config.script_path, config.input_path, "local", 1)
    assert time.monotonic() - started < 1.5
    assert not execution.timed_out
    assert len(execution.stdout.encode()) <= 65536
    assert len(execution.stderr.encode()) <= 65536


def test_repeated_baseline_uses_independent_input_bytes(tmp_path):
    config = config_for(tmp_path, "value\none\n", "import csv,sys,pathlib\nrows=list(csv.DictReader(open(sys.argv[1])))\npathlib.Path(sys.argv[1]).write_text('value\\n')\nif rows: raise ValueError()\n")
    original = config.input_path.read_bytes()
    report = reduce_csv(config)
    assert report["status"] == "verified"
    assert report["final_rows"] == 1
    assert config.input_path.read_bytes() == original
    assert (config.output_dir / "specimen.csv").read_bytes() == original


def test_same_traceback_has_equal_local_and_docker_fingerprints(tmp_path):
    script = tmp_path / "source.py"
    local = f'Traceback (most recent call last):\n  File "{script}", line 7, in evaluate\nValueError: private\n'
    docker = 'Traceback (most recent call last):\n  File "/work/script.py", line 7, in evaluate\nValueError: private\n'
    assert _fingerprint(local, script) == _fingerprint(docker, Path("/work/script.py"))


def test_docker_restrictions_and_timeout_cleanup_are_requested(tmp_path, monkeypatch):
    config = config_for(tmp_path)
    calls = []
    monkeypatch.setattr("specimen.runner.shutil.which", lambda _: "/fake/docker")
    def execute(command, script_path, timeout, *args, **kwargs):
        calls.append(command)
        return Execution(-9, None, "TimeoutError", None, timed_out=True)
    monkeypatch.setattr("specimen.runner._execute", execute)
    monkeypatch.setattr("specimen.runner.subprocess.run", lambda command, **kwargs: calls.append(command))
    result = run_script(config.script_path, config.input_path, "docker", .1)
    command = calls[0]
    for option, value in [("--network", "none"), ("--user", "65534:65534"), ("--cap-drop", "ALL"), ("--security-opt", "no-new-privileges"), ("--memory", "256m"), ("--pids-limit", "64")]:
        assert command[command.index(option)+1] == value
    assert "--read-only" in command
    assert len([arg for arg in command if arg.endswith(",readonly")]) == 2
    name = command[command.index("--name")+1]
    assert calls[1] == ["/fake/docker", "rm", "-f", name]
    assert result.timed_out and result.fingerprint is None
