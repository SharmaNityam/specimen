import io
import time
import zipfile

from fastapi.testclient import TestClient

from specimen.server import create_app


def _wait(client: TestClient, job_id: str, timeout: float = 25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["status"] in {"complete", "error"}:
            return job
        time.sleep(0.025)
    raise AssertionError("job did not finish within the test timeout")


def test_health_and_examples_contract(tmp_path):
    app = create_app(storage_root=tmp_path, docker_probe=lambda: False)
    with TestClient(app) as client:
        health = client.get("/api/health").json()
        assert health["status"] == "ok"
        assert health["docker_available"] is False
        assert set(health["model"]) == {"configured", "model"}
        examples = client.get("/api/examples").json()
        assert {item["id"] for item in examples} >= {"unicode", "relations", "conflict"}


def test_demo_poll_and_download_contains_only_public_evidence(tmp_path):
    app = create_app(storage_root=tmp_path, docker_probe=lambda: False)
    with TestClient(app) as client:
        created = client.post("/api/jobs", json={"example_id": "unicode"})
        assert created.status_code == 200
        job = _wait(client, created.json()["id"])
        assert job["status"] == "complete"
        assert job["report"]["status"] == "verified"
        assert job["preview"]["before"]
        assert job["preview"]["after"]
        assert job["download_available"] is True
        response = client.get(f"/api/jobs/{job['id']}/download")
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert set(archive.namelist()) == {
                "specimen.csv", "repro.py", "report.json", "REPORT.md", "REPRODUCE.txt"
            }
            contents = {name: archive.read(name).decode("utf-8") for name in archive.namelist()}
        assert "Māra Synthetic" not in contents["specimen.csv"]
        assert "mara617@example.test" not in "\n".join(contents.values())


def test_download_is_blocked_for_unverified_result(tmp_path, monkeypatch):
    def needs_review(config, on_event=None):
        config.output_dir.mkdir(parents=True, exist_ok=True)
        return {"status": "needs_review", "artifact_names": [], "blockers": ["review required"]}

    monkeypatch.setattr("specimen.server.reduce_csv", needs_review)
    app = create_app(storage_root=tmp_path, docker_probe=lambda: False)
    with TestClient(app) as client:
        created = client.post("/api/jobs", json={"example_id": "unicode"}).json()
        job = _wait(client, created["id"])
        assert job["report"]["status"] == "needs_review"
        assert job["download_available"] is False
        assert client.get(f"/api/jobs/{job['id']}/download").status_code == 409


def test_upload_without_docker_traversal_and_cross_origin_are_rejected(tmp_path):
    app = create_app(storage_root=tmp_path, docker_probe=lambda: False)
    with TestClient(app) as client:
        upload = client.post("/api/jobs/upload", files={
            "file": ("x.csv", b"name,email\nprivate,private@example.test\n"),
            "script": ("x.py", b"raise ValueError()\n"),
        })
        assert upload.status_code == 503
        assert client.get("/api/jobs/../../etc/passwd").status_code == 404
        cross = client.post("/api/jobs", json={"example_id": "unicode"},
                            headers={"Origin": "https://evil.example"})
        assert cross.status_code == 403


def test_upload_job_never_returns_the_original_preview(tmp_path, monkeypatch):
    import json

    def safe_reduction(config, on_event=None):
        config.output_dir.mkdir(parents=True, exist_ok=True)
        (config.output_dir / "specimen.csv").write_text("name,email,note\nPerson A,person@example.test,safe note\n")
        (config.output_dir / "repro.py").write_text("raise ValueError()\n")
        report = {"status": "verified", "artifact_names": ["specimen.csv", "repro.py", "report.json", "REPORT.md"]}
        (config.output_dir / "report.json").write_text(json.dumps(report))
        (config.output_dir / "REPORT.md").write_text("Verified sanitized specimen.\n")
        return report

    from specimen import server
    monkeypatch.setattr(server, "reduce_csv", safe_reduction)
    app = create_app(storage_root=tmp_path, docker_probe=lambda: True)
    with TestClient(app) as client:
        malformed = client.post("/api/jobs/upload", files={
            "file": ("x.csv", b"name,email\nprivate,private@example.test\n"),
            "script": ("x.py", b"raise ValueError()\n"),
        }, data={"sensitive_columns": "email"})
        assert malformed.status_code == 200
        job = _wait(client, malformed.json()["id"])
        assert job["status"] == "complete"
        assert "before" not in job["preview"]
        assert "private" not in repr(job["preview"])
        assert job["download_available"] is True

        def leaked_reduction(config, on_event=None):
            config.output_dir.mkdir(parents=True, exist_ok=True)
            (config.output_dir / "specimen.csv").write_text(
                "name,email,note\nPerson A,person@example.test,copied:other@private.test\n"
            )
            (config.output_dir / "repro.py").write_text("raise ValueError()\n")
            report = {"status": "verified", "artifact_names": ["specimen.csv", "repro.py", "report.json", "REPORT.md"]}
            (config.output_dir / "report.json").write_text(json.dumps(report))
            (config.output_dir / "REPORT.md").write_text("Verified sanitized specimen.\n")
            return report

        monkeypatch.setattr(server, "reduce_csv", leaked_reduction)
        leaked = client.post("/api/jobs/upload", files={
            "file": ("x.csv", b"name,email,note\nAlice,alice@private.test,copy\n"),
            "script": ("x.py", b"raise ValueError()\n"),
        }, data={"sensitive_columns": "email"})
        assert leaked.status_code == 200
        blocked = _wait(client, leaked.json()["id"])
        assert blocked["preview"] == {"after": []}
        assert blocked["download_available"] is False


def test_upload_size_limits_are_enforced_before_enqueue(tmp_path):
    app = create_app(storage_root=tmp_path, docker_probe=lambda: True)
    with TestClient(app) as client:
        oversized_csv = client.post("/api/jobs/upload", files={
            "file": ("x.csv", b"x" * (5 * 1024 * 1024 + 1)),
            "script": ("x.py", b"raise ValueError()\n"),
        })
        assert oversized_csv.status_code == 413
        oversized_script = client.post("/api/jobs/upload", files={
            "file": ("x.csv", b"x\n"),
            "script": ("x.py", b"x" * (100 * 1024 + 1)),
        })
        assert oversized_script.status_code == 413
        too_many_columns = client.post("/api/jobs/upload", files={
            "file": ("x.csv", (",".join(f"c{i}" for i in range(101)) + "\n").encode()),
            "script": ("x.py", b"raise ValueError()\n"),
        })
        assert too_many_columns.status_code == 413
        too_many_rows = client.post("/api/jobs/upload", files={
            "file": ("x.csv", ("x\n" + "v\n" * 10_001).encode()),
            "script": ("x.py", b"raise ValueError()\n"),
        })
        assert too_many_rows.status_code == 413

