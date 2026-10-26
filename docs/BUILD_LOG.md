# Build and verification log

Built for Openfire on October 26, 2026. The product scope and interfaces were recorded in [PLAN.md](PLAN.md) before implementation.

## Development approach

Three inexpensive GPT-6 Luna agents initially owned independent work: the reduction engine, browser interface, and synthetic fixtures/tests. Subsequent bounded assignments covered CLI/server integration, the optional model adviser, and documentation. A GPT-6 Astra agent independently checked correctness and execution/privacy boundaries. The parent integrated results and verified the actual browser, Docker runner, package, and downloaded artifact. Source ownership reduced conflicting edits in the shared checkout.

Model calls are deliberately outside the reduction loop. Ordinary code finds row deletions, tries consistent pseudonyms, runs the reproducer, caches candidate results, and checks the final artifact. An optional open-model adviser can propose column positions and a constrained strategy order using aggregate metadata and whitelisted operation hints; its suggestions are untrusted and executable checks remain decisive.

## Findings that changed the implementation

- A sensitive value can be copied into an unselected notes field. Replacement and final publication checks now consider sensitive substrings across all cells and serialized CSV, not just selected columns.
- Sanitization can make a previously necessary row redundant. The final sanitized rows receive another deletion pass before the tool claims row-deletion 1-minimality.
- A script can modify the candidate it receives. Verification recreates pristine input bytes for each execution and publication uses the checked candidate data.
- Arbitrary nonzero exits are not evidence. Custom exception names, multiline messages, changed exception locations, infrastructure errors, and unstable baselines receive independent checks.
- A normal parent exit can leave children running. The trusted local runner cleans up its process group; Docker timeouts explicitly remove the named container.
- A per-execution timeout alone can overrun the job budget. Each execution receives the remaining job time as an upper bound, with bounded cleanup afterward.
- Docker mounts directly from macOS Documents timed out in this environment, while temporary paths worked. The Docker runner now stages both script and CSV, mounts only those copies read-only, and removes the container before cleaning them up.
- A malformed Origin header caused an HTTP 500. Origin parsing now rejects malformed or mismatched origins, including scheme mismatches. Streamed multipart uploads also have a total byte cap independent of Content-Length.
- An artifact privacy check can fail after the core run. The server changes that result to needs_review, removes candidate files, and withholds both the download and uploaded preview.

## Completed verification

- Python suite: **73 passed**, including independent adversarial cases. One upstream Starlette/httpx test-client deprecation warning remains.
- Dashboard JavaScript syntax check passed.
- Browser: Unicode demo reduced 1,200 rows to one; the conflict example disabled download and showed needs_review.
- Browser: a real CSV/script upload used Docker and produced a verified one-row result. Uploaded original values stayed hidden.
- Browser: 390px viewport had no horizontal document overflow.
- Actual Docker checks covered nonroot UID, read-only writes, resource limits, timeout/container cleanup, and equivalent local/container fingerprints.
- Wheel built successfully with bundled examples and web assets. An isolated installation outside the source checkout ran the Unicode demo successfully.
- Downloaded ZIP had exactly five allowlisted files. Its CSV hash matched the report, and running the bundled script against that CSV independently raised UnicodeEncodeError.

These checks establish the tested behavior, not universal anonymization or proof of the same underlying bug. See [ARCHITECTURE.md](ARCHITECTURE.md) and the README limitations.

## Alan and cost status

Alan Pro was available in the desktop app. A read-only review was requested using Alan Code with DeepSeek V4.1 Flash. Startup attempts encountered a catalog timeout and an opencode server startup timeout; retries were attempted. No Alan review findings or API integration are claimed unless a completed response is recorded later.

No new paid service was purchased. The application adviser remains configurable and unconfigured by default; unit tests mock its transport and make no model calls. Actual account/model charges were not available, so no dollar-cost claim is made.
