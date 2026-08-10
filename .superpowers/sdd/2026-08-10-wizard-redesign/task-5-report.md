# Task 5 Report: Unified Select-Data Step and Returnable Navigation

## Status

Complete. Step two is now one vertical workspace with cached group search, date
filters, native-radio group rows, per-row Sheet selection, a one-line summary, and
sticky Back/Next actions. Preview/export markup remains the existing partial.

## Implementation

- Added `templates/steps/select.html` and
  `templates/components/chatroom_rows.html` for the unified one-column selection
  experience. The order is search, date range, group list, summary, and actions;
  there is no aside or right-hand summary.
- Rows expose a native radio, group name, last-message timestamp, message count,
  and a Sheet select. Selecting or editing values revalidates in place and does
  not navigate.
- Added cache-only `/api/catalog` filtering. Entering step two populates the Task 2
  session cache once when needed; subsequent searches never query the database or
  workbook.
- Added `/api/selection` with exact cached group ID/name validation, chronological
  date validation, and cached Sheet validation. HTTP 422 responses re-render the
  whole select step, retain submitted dates and Sheet values, announce the error,
  and disable Next.
- Valid selections update both typed wizard state and compatibility session keys.
  Refresh restores group, dates, Sheet, summary, and Next state. A selection that
  no longer exactly matches the cache is not considered ready.
- Updated `/api/preview` to use the validated Task 2 preview orchestration for the
  new group-ID contract while retaining the legacy group-name contract. Success
  returns the existing preview partial inside the wizard fragment, updates the
  stepper out of band, and sets `HX-Push-Url: /wizard/3`.
- Step-two Back and stepper links request partials. `popstate` fetches the matching
  partial without pushing another history entry, and swapped views focus their
  heading (falling back to the workspace only when no heading exists).
- Added responsive row/date/action styling and visible selected/invalid states.

## TDD Evidence

### RED

Exact Python 3.13 runs established the missing behavior before implementation:

```text
tests/test_select_step.py: 11 failed
tests/test_wizard_state.py: collection failed because has_complete_selection did not exist
```

The failures covered the legacy split layout, missing catalog/selection routes,
missing state retention, absent preview push URL, and absent browser-history logic.

Two additional regression cycles were run during hardening:

```text
stale cached group refresh: 1 failed, then passed
invalid submitted Sheet retention: 1 failed, then passed
```

### Focused GREEN

```text
tests/test_select_step.py
tests/test_wizard_state.py
tests/test_catalog_service.py
tests/test_app_composition.py

35 passed in 1.22s
```

## Final Verification

All commands used
`C:\Users\qiuji\AppData\Local\Programs\Python\Python313\python.exe`.

```text
Full pytest:   120 passed in 2.16s
Ruff:          All checks passed!
Jinja compile: 19 templates compiled
```

## Concerns

- Browser behavior is protected by route and JavaScript contract tests here; the
  full desktop/390px live-browser acceptance run remains intentionally scheduled
  for Task 7.
- Initial catalog construction still crosses a synchronous route boundary. The
  redesign plan explicitly moves catalog and preview blocking work behind
  `asyncio.to_thread` in Task 7; all search requests after construction are already
  cache-only in this task.
