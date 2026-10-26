# Specimen implementation plan

Specimen makes a failing CSV smaller and replaces sensitive values, while repeatedly checking that the same observed Python failure survives. The primary artifact is a reproducible dataset plus evidence, not an AI explanation.

## Product contract

Inputs: CSV, Python script receiving its input path as argv[1], sensitive column names. Outputs: specimen.csv, repro.py, report.json, REPORT.md, and a ZIP containing only reviewed public artifacts. CLI supports user files; a local dashboard supports bundled demos and Docker-backed uploads.

The tool must detect unstable baselines, reject unrelated exceptions, distinguish timeouts from preserved failures, consistently replace repeated identifiers, report unresolved sensitive values, avoid mutating originals, and refuse a shareable bundle when sanitization and failure preservation conflict. It makes no guarantee of complete anonymization or globally smallest input.

## Architecture

- contracts.py: RunConfig, Execution, event callback.
- runner.py: explicit trusted host mode and restricted Docker mode; subprocess limits and exception fingerprints.
- core.py: repeat baseline, memoized bounded delta reduction, deterministic consistent sanitization, property-preserving attempts, repeat final verification, evidence report.
- cli.py: reduce, demo, serve commands.
- server.py: loopback service, background jobs, polling events, limits and artifact download.
- web/: dependency-light responsive dashboard with actual events and downloadable results.
- model.py: optional OpenAI-compatible policy adviser; schema and structural context only, validated declarative output, bounded requests, no model in the reduction loop.

## Parallel ownership

1. Cheap implementation agent: core.py and runner.py, core-focused unit tests.
2. Cheap UI agent: web/index.html, app.js, style.css.
3. Cheap fixture/test agent: examples/, independent tests/test_engine_cases.py, fixture metadata.
4. Parent: integration contracts, CLI/server, optional provider, packaging, docs, Git.
5. Stronger verification agent: review integrated implementation and independent adversarial testing; implementation agents fix findings.

## Shared interface

core.reduce_csv(config: RunConfig, on_event: EventCallback | None = None) -> dict.
runner.run_script(script: Path, csv_path: Path, runner: str = 'docker', timeout: float = 3.0) -> Execution.
Events are dicts with type, message, optional runs/rows/elapsed. Event messages must not expose raw private values. Report records original/final row counts, execution counts, reduction completeness, baseline fingerprint, transformations, privacy blockers, SHA256, artifact filenames and verification evidence.

## Checks and milestones

1. Core and deterministic fixtures; baseline + row reduction + sanitization.
2. CLI and dashboard end-to-end.
3. Optional open model adviser and documented Alan usage if access permits.
4. Adversarial tests: changed error, flaky failure, hanging child, malformed inputs, budgets, sanitization conflict, duplicate relationships, archive leaks, paths and network boundaries.
5. Browser run and download; installation from fresh environment; Docker verification if daemon available.
6. Detailed README, architecture/thought process, limitations, demo script and regular commits; push to supplied GitHub repository.

## Cost and persistence

No model calls inside the deletion search. Cache execution results. Use cheap builders; stronger verification only after integration. Alan Pro and API credits are separate until verified. No additional paid service. Retry transient network failures with bounded exponential backoff, never auth/credit errors. Commit milestones; do not claim unattended reconnection after the execution session ends.

## Scope limits

CSV only, Python only, deterministic script failures only, standard-library demo scripts. No arbitrary public code-execution service, hosted customer-data storage, automatic GitHub issue publishing, or universal privacy guarantees. Local host execution is opt-in and not a security sandbox; uploaded scripts use Docker without network, with restricted mounts/resources.
