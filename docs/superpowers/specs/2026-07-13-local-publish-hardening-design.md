# Local Publish Hardening Design

## Goal

Make unattended publication local, isolated, resource-bounded, and fail-closed while retaining VPS publication only as an explicit manual emergency path.

## Scope

This design fixes production-critical behavior:

- automatic workflows must never depend on VPS;
- automatic workflows must never attach to the user's primary browser/profile;
- one run must not overlap another run;
- ambiguous platform outcomes must never become success;
- draft and publish idempotency must remain separate;
- state must survive interruption without silently enabling duplicate submission;
- browser and dependency behavior must be reproducible from a clean checkout.

Out of scope: UI redesign, content-variant copy, theme behavior, platform selector expansion unrelated to result verification, and deletion of manual VPS worker features.

## Architecture

### One automatic entrypoint

`scripts/monitor_publish.py` remains the queue scanner and automatic entrypoint. It selects pending articles, acquires a repository-local run lock, and invokes the local publish pipeline. It must not call `require_vps_ready`, SSH, SCP, remote CDP, or `--remote vps`.

Each article is submitted through at most two local mode groups: a WeChat API draft group and one browser-platform publish group containing all still-pending browser platforms. This preserves one browser process/context for browser platforms while keeping the established WeChat draft workflow. WeChat must not cause browser startup when it is the only pending platform.

`publish.py --remote vps` remains available only when explicitly supplied by a human. `publish` mode defaults to `local`.

### Browser ownership and lifecycle

Browser platforms use one `PlaywrightEngine` with repository-local profile `.ordo/automation-profile`. The engine always passes that profile through `user_data_dir`; it never attaches to an existing CDP target and never resolves `DevToolsActivePort` in standalone mode.

Automatic/headless runs require an existing initialized profile. Missing or unusable login state returns a blocking error. They never switch to headed mode. A separate explicit bootstrap command starts the same isolated profile in headed mode for login preparation.

Platforms run sequentially. At most one publishing page remains active at a time; after a platform result is collected, its page is closed. The shared context and browser close in a `finally` block. Failure to create the shared engine aborts browser publication; no per-platform browser fallback is allowed.

### Result contract

Platform outcomes use these classes:

- terminal success: `published`, `scheduled`, `draft_saved`;
- terminal skip: `skipped_existing`;
- retry later: `limit_reached`;
- ambiguous: `submitted_unverified`, `unknown`;
- failure: `failed`, including login, environment, selector, and content failures.

Process return code 0 means terminal success or intentional skip only. `limit_reached`, `submitted_unverified`, `unknown`, and `failed` produce nonzero run status while retaining their typed outcome for reporting.

Verification requires article-specific evidence: a returned published URL/platform ID, or an exact normalized title match in the correct management list. Merely reaching a management page is not evidence. Navigation errors cannot be swallowed into success.

If submission may have happened but verification fails, record `submitted_unverified`. A later run performs verification-only reconciliation before any new submission. If reconciliation cannot establish success or absence, the run stops for that platform and requests human review; it does not submit again.

### Durable idempotency state

One state module owns `.ordo/publish-state.json`. The key is:

```text
article identity -> platform -> mode
```

Article identity uses frontmatter `article_id`. Content hash is allowed only for legacy Markdown without `article_id`.

State writes use a temporary file in the same directory, flush, `fsync`, and `os.replace`. Invalid JSON is a blocking error; it is never treated as empty state. The old misplaced `/Users/wizard/work_2025/.ordo/publish-state.json` is not read automatically because cross-project ownership is ambiguous.

The monitor's historical `.ordo/auto_publish_state.json` may be read once for migration/compatibility, but new completion decisions come from the unified state and structured publish records. Raw subprocess return codes are not sufficient evidence.

### Concurrency

The automatic entrypoint acquires `.ordo/publish.lock` with a non-blocking OS file lock before queue selection. A second run exits as `skipped_overlap` without opening files, starting browsers, or publishing.

The lock covers queue selection, platform execution, state updates, records, and summary. The daemon catches per-scan exceptions, reports them, then continues at the next interval. Each platform execution has a bounded timeout.

### Reporting

Every automatic run reports separate platform lists:

- succeeded;
- skipped;
- rate-limited;
- submitted but unverified;
- failed.

Empty queue and overlapping run are explicit no-op results. Notifications use the same structured summary. Unknown and rate-limited outcomes never appear under succeeded/skipped.

### Runtime dependencies

Declare one browser automation runtime in project dependencies and install documentation. Unit tests inject a fake engine and must never launch system Chrome. Real-browser smoke tests remain opt-in and require an explicit environment flag.

## Error Handling

- Missing initialized profile: block browser platforms before content mutation.
- Login expired: stop affected platform; do not wait five minutes in headless mode.
- Selector missing: fail as `platform_changed`.
- Rate limit: record `limit_reached`, continue other platforms when configured, retry next scheduled run.
- Submit clicked but result ambiguous: record `submitted_unverified`; next run reconciles before submit.
- State corrupt/unwritable: block entire run before submission.
- Shared browser startup failure: block browser platforms; never fall back to active Chrome, CDP, or multiple engines.
- Notification failure: log separately; do not change publication outcome.

## Testing Strategy

### Unit tests

- publish mode defaults to local; explicit VPS still works;
- monitor contains no automatic VPS preflight or remote command;
- WeChat-only pipeline does not construct a browser engine;
- shared engine failure is blocking;
- automatic missing-profile behavior remains headless and fails closed;
- draft success does not suppress later publish;
- rate limit and unknown never return success;
- management-page navigation without article match returns unknown/failure;
- state path is repository-local;
- state writes are atomic and corrupt state blocks;
- concurrent monitor lock allows exactly one runner;
- submitted-unverified reconciliation happens before resubmission;
- summaries keep success, skip, rate-limit, unknown, and failure separate.

### Integration tests

- one article across mocked WeChat plus five browser adapters creates one browser context;
- platform pages close sequentially;
- interruption after submit produces `submitted_unverified` and safe recovery;
- retry after one platform failure skips only terminal outcomes for the same mode.

### Manual verification

- explicit headed bootstrap uses only `.ordo/automation-profile`;
- primary Chrome remains open and unaffected during a draft run;
- five-platform draft run uses one isolated browser process;
- local scheduled run handles empty queue, partial failure, rate limit, and notification;
- explicit manual `--remote vps` still reaches the existing worker path.

## Acceptance Criteria

1. No automatic code path performs SSH/VPS work.
2. No automatic code path attaches to or launches the primary browser profile.
3. One local automatic run uses at most one browser process/context and one active publishing page.
4. Overlapping runs cannot both submit.
5. Ambiguous, limited, or unverified outcomes cannot become terminal success.
6. Draft state cannot suppress publish state.
7. State corruption blocks publication instead of resetting dedupe.
8. Clean dependency installation can import and run the local engine.
9. Full unit suite performs no real browser launch and passes.
10. Manual VPS emergency mode remains available only through explicit CLI selection.
