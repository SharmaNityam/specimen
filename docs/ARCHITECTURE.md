# Architecture and evidence model

Specimen is a local CSV/Python reproducer workflow. The engine answers a narrow question: does a smaller row set, with selected values pseudonymized, still produce the same observed exception fingerprint under repeated runs? The output is an auditable specimen and evidence report. It is not a root-cause analyzer, a general-purpose anonymizer, or a hosted code-execution service.

## Data path

```text
CSV + reproducer
       │
       ▼
  Run baseline twice ── unstable / no failure / infrastructure → report, no shareable pair
       │ stable fingerprint
       ▼
  Bounded row deletion ── memoized candidate subsets
       │ complete row-deletion pass
       ▼
  Consistent pseudonyms for selected source values
       │
       ├─ same fingerprint twice ── privacy checks ── verified artifacts and ZIP
       └─ failure changes / budget ends / sensitive literal remains ── review or blocked report
```

`contracts.py` defines `RunConfig` and `Execution`. `runner.py` executes the script and derives a fingerprint. `core.py` owns the baseline checks, candidate reduction, value replacement, final verification, and JSON/Markdown evidence. `artifacts.py` builds a fixed-allowlist ZIP only when archive privacy checks pass. `cli.py` exposes the engine. `server.py` provides a loopback-only dashboard API and in-memory job polling. `web/` is vanilla HTML, CSS, and JavaScript. `model.py` is an optional policy adviser, called before reduction when explicitly enabled.

## Execution and fingerprint

The reproducer receives the candidate CSV path as its first argument. A failure fingerprint is `exception type | line N in function` for the final frame inside the reproducer. It does not include source paths, code excerpts, exception messages, stdout, or stderr. This reduces accidental disclosure in reports and progress events.

Only an observed nonzero Python execution with a recognized exception fingerprint can serve as the failure target. Timeouts and runner/infrastructure failures are not treated as preserved bugs. The baseline is executed twice; disagreement returns `unstable`, lack of a fingerprint returns `not_reproduced`, and infrastructure or classification uncertainty returns `needs_review`.

Fingerprint equality has a deliberately limited meaning: the same exception type and reproducer frame occurred again. It is not evidence that two runs have the same root cause. A script can raise the same exception at the same line for different underlying reasons. Specimen is designed for deterministic failures, not flaky or environment-sensitive behavior.

## Row reduction

The engine first tries delta-debugging style partitions and complements, then performs single-row deletion passes. Candidate subsets are keyed by their original row indices, so repeated probes use a memoized result. Execution count and elapsed-time budgets bound work. When the process exhausts its budget, the report says so instead of describing the candidate as fully minimized.

When the exhaustive passes complete, `minimized` means **1-minimal by row deletion for the observed fingerprint**: each single remaining row was necessary under this script and configuration. It does not mean globally minimal by row count, columns, bytes, semantic content, or another reduction order. A different script, runtime, environment, or failure property may produce another specimen.

## Pseudonymization and review

Columns can be selected explicitly or by a built-in sensitive-name heuristic. Missing explicitly requested headers are an error. The engine gathers non-empty values from selected columns and replaces matching occurrences consistently across the CSV, including copies outside those columns. It tries same-length, same-character-class Unicode replacements and then verifies the final candidate twice against the baseline fingerprint. A stable private key is used within a run so duplicate values keep their relationship; a later run can generate different replacement values.

This is **pseudonymization**, not anonymization. Same length and character class can retain recognizable structure. Unselected cells and their surrounding context remain. Rare combinations, script literals, or other values can still identify a person. Human review is required before sharing. `blockers` and archive checks are useful safeguards but cannot prove that a CSV contains no personal or confidential information.

The reducer writes `specimen.csv`, `repro.py`, `report.json`, and `REPORT.md` only for a verified result. The ZIP has a fixed list of those files plus `REPRODUCE.txt`. Before exporting, the archive builder checks the source sensitive values against each artifact and scans for email and common secret patterns. It also scans the reproducer. An unresolved value or suspicious match prevents a downloadable bundle. These checks are not comprehensive data-loss prevention and can produce false positives or miss unseen identifiers.

The report includes original/final row counts, execution count, whether row reduction completed, the baseline and final fingerprints, selected transformation information, blockers, verification counts, artifact names, and the specimen SHA-256. Raw command output is kept internal to runner results and is not copied into the normal report/event text.

## Runners

**Docker runner.** The CLI defaults `reduce` to Docker. It uses `python:3.12-slim`, no network, a read-only container filesystem, read-only binds for script and CSV, a non-root UID, dropped capabilities, `no-new-privileges`, a small temporary filesystem, and memory/CPU/process limits. Docker availability includes a daemon check. For the dashboard, the image must already be present so its upload check can be quick and explicit.

Docker reduces exposure but does not make arbitrary code safe by definition. It relies on a functioning, patched Docker installation and host kernel. Use this for uploaded scripts, inspect inputs, and do not expose the local dashboard beyond loopback.

**Local runner.** `--runner local` executes the script using the current Python interpreter with the current user's permissions and no sandbox. It is intended for trusted scripts and the bundled synthetic fixtures only. The CLI warns when local mode is selected for supplied files. The dashboard demos intentionally use the checked-in trusted fixtures locally; uploads require Docker.

Both runners cap captured process output, impose per-execution timeouts, terminate process groups on timeout, and keep the output out of public evidence. The fingerprint classifier recognizes ordinary Python exception tracebacks; this project does not support arbitrary language runtimes or all possible exception formats.

## Local HTTP service

`specimen serve` binds to `127.0.0.1` by default. The API offers health and example metadata, asynchronous jobs, polling events, sanitized demo previews, and a ZIP download endpoint. Mutations enforce a same-origin check when an Origin header is present. Job count, upload size, CSV row/column counts, script size, and event history are bounded. Uploaded source files are removed after job completion; the archive bytes are held in memory for the local job lifetime. Finished jobs may be reclaimed when the queue fills.

Dashboard uploads require Docker with `python:3.12-slim` already pulled. The uploaded source rows are never returned as a preview. The dashboard shows the sanitized output's shape with its values hidden. The complete data and source script are inputs to local processing; a successful ZIP contains the minimized output and the tested `repro.py`, subject to archive checks. Review both before sharing.

The service is a single-user local workflow, not an authenticated multi-user or remote execution service. Do not bind it to a public or shared network.

## Optional policy adviser

When `--model-assist` is requested and the endpoint is configured, `model.py` reads the CSV locally to compute coarse per-column properties: position, whether values include non-ASCII characters, a maximum-length bucket, and a uniqueness-ratio bucket. It sends these properties and selected positions to an OpenAI-compatible chat-completions endpoint. It does not send column names or cell values. The endpoint may receive information about data shape, so enable this only if that disclosure is acceptable.

The adviser returns a constrained JSON proposal for sensitive positions and one of a small set of strategy labels. Specimen validates its schema and indices and merges its selected positions additively with caller choices. It is called once before reduction; it is not called within the row-deletion loop, does not execute tools or code, and cannot mark a run verified. Authentication is optional and uses `SPECIMEN_MODEL_API_KEY`. The base URL and model are configured through `SPECIMEN_MODEL_BASE_URL` and `SPECIMEN_MODEL_NAME`. The endpoint accepts HTTPS, or HTTP only for loopback addresses. Retry handling is bounded and excludes authentication/credit failures from retries.

## Result states

| Status | Meaning |
| --- | --- |
| `verified` | Baseline was stable, row reduction completed, pseudonymization preserved the observed fingerprint twice, and artifacts were written. ZIP availability also depends on archive privacy checks. |
| `unstable` | The repeated baseline fingerprints disagreed. |
| `not_reproduced` | The supplied input did not produce a target failure fingerprint. |
| `budget_exhausted` | A required baseline, candidate, or verification run could not finish within configured limits. |
| `needs_review` | Classification, pseudonymization, or final verification could not safely establish a shareable result. |
| `error` | Input or configuration processing failed. Exception text is excluded from the report. |

`verified` reports reproducible evidence under the tested setup. It is not a guarantee of correct root cause, globally smallest data, complete anonymization, or safety in every environment.
