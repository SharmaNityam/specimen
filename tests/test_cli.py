import json

from specimen import cli


def test_demo_cli_uses_trusted_fixture_and_explicit_local_runner(tmp_path, monkeypatch, capsys):
    root = tmp_path / "examples"
    folder = root / "relations"
    folder.mkdir(parents=True)
    (folder / "metadata.json").write_text(json.dumps({"id": "relations", "sensitive_columns": ["email"]}))
    (folder / "data.csv").write_text("email\na@example.test\n")
    (folder / "repro.py").write_text("raise ValueError()\n")
    monkeypatch.setattr(cli, "_examples_root", lambda: root)
    seen = {}

    def reduce(config, on_event=None):
        seen["config"] = config
        return {"status": "verified", "artifact_names": []}

    monkeypatch.setattr(cli, "reduce_csv", reduce)
    output = tmp_path / "output"
    result = cli.main(["demo", "relations", "--output", str(output)])
    assert result == 0
    config = seen["config"]
    assert config.runner == "local"
    assert config.input_path == folder / "data.csv"
    assert config.script_path == folder / "repro.py"
    assert config.output_dir == output
    assert config.sensitive_columns == ["email"]
    captured = capsys.readouterr()
    assert "trusted bundled fixture" in captured.err
    assert json.loads(captured.out)["status"] == "verified"


def test_cli_defaults_upload_reduction_to_docker_and_warns_on_local(tmp_path, monkeypatch, capsys):
    source = tmp_path / "input.csv"
    script = tmp_path / "repro.py"
    source.write_text("name\nAda\n")
    script.write_text("raise ValueError()\n")
    seen = {}

    def reduce(config, on_event=None):
        seen["runner"] = config.runner
        return {"status": "needs_review"}

    monkeypatch.setattr(cli, "reduce_csv", reduce)
    assert cli.main(["reduce", str(source), "--script", str(script)]) == 1
    assert seen["runner"] == "docker"
    cli.main(["reduce", str(source), "--script", str(script), "--runner", "local"])
    assert "without a sandbox" in capsys.readouterr().err


def test_cli_refuses_output_that_would_delete_an_input(tmp_path, monkeypatch, capsys):
    source = tmp_path / "specimen.csv"
    script = tmp_path / "repro.py"
    source.write_text("x\na\n")
    script.write_text("raise ValueError()\n")
    called = False

    def reduce(config, on_event=None):
        nonlocal called
        called = True
        return {"status": "verified"}

    monkeypatch.setattr(cli, "reduce_csv", reduce)
    assert cli.main(["reduce", str(source), "--script", str(script), "--output", str(tmp_path)]) == 2
    assert not called
    assert "does not overwrite" in capsys.readouterr().err

