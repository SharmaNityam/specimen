# 90–120 second demo storyboard

Use the local dashboard and bundled fixtures. The CSVs contain synthetic data. No Docker image or model endpoint is needed for these demos.

## Setup before recording

```sh
uv sync --extra dev
uv run specimen serve
```

Open `http://127.0.0.1:8765` and wait for the demo cards to load. Keep a terminal visible long enough to show the local server. Do not upload real customer data or enable the optional adviser during this walkthrough.

## Walkthrough

**0:00–0:20 — State the problem.** A reproducer can fail only for a tiny combination hidden in a large CSV. Manually deleting rows risks deleting the trigger. Specimen treats the script's observed failure as the property to preserve and records each check.

**0:20–0:45 — Start the Unicode demo.** Choose **Unicode ASCII export**. Point out the 1,200-row synthetic input and the failure description. Start the run. The evidence timeline records actual baseline and reduction events; it is not a simulated progress animation.

**0:45–1:05 — Inspect the specimen.** Show the before/after row previews, the reduced row count, pseudonymized synthetic values, and the final fingerprint. Explain that the fingerprint is the exception type plus the reproducer's line and function, and that the final version is replayed more than once.

**1:05–1:25 — Show a relationship.** Start **Repeated relationship pair**. The expected two-row result retains both copies of the synthetic pair, demonstrating why row reduction preserves a relationship rather than keeping a single isolated record.

**1:25–1:45 — Show the limit.** Start **Required sensitive value**. This 240-row synthetic case depends on a literal in a field marked sensitive. Replacement conflicts with the observed failure, so the run is blocked from a shareable ZIP. This is a useful result: the tool exposes the conflict instead of quietly publishing the triggering value.

**1:45–2:00 — Close with the boundary.** A verified result means the same observed fingerprint survived these checks under this script and environment. It does not establish the root cause, global minimum, or complete anonymization. Human review is still required before sharing.

If the three demos run more quickly than the time ranges, spend the remaining time inspecting the report's `verification`, `transformations`, `blockers`, and `sha256` fields. Do not add fake waits or synthetic progress events.

## Optional CLI close-up

To show the same failure replay outside the browser:

```sh
uv run specimen demo unicode --output ./demo-output
```

The CLI writes actual progress to stderr and a JSON report to stdout. The output directory contains the report and, when verified, the reviewed specimen and reproducer. The dashboard ZIP is enabled only when the report is verified and the archive privacy checks pass.
