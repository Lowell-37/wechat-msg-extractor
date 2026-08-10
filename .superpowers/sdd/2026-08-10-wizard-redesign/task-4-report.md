# Task 4 Report: Persistent Shell and Connection Step

## Status

Complete. The implementation uses the requested clean-efficiency visual language and
keeps the branch/worktree available for the remaining wizard tasks.

## Implementation

- Added a persistent 920px maximum-width shell with compact brand bar, truthful
  local-processing status, always-visible stepper, workspace boundary, and sticky
  action bar.
- Added composable stepper, status-message, and wizard-action components plus the
  new connection step template.
- Changed all full wizard routes to render `wizard.html`; fragment routes render the
  same step template inside `#wizard-workspace` and set `HX-Push-Url` to the
  canonical `/wizard/{step}` URL.
- Kept the current step-two and step-three content structurally unchanged while
  making each legacy body includable by the new shell. Step two remains one main
  region pending Task 5.
- Replaced connection result/error fragments with a complete `steps/connect.html`
  render driven by an escaped Jinja view model. No Python business HTML is built or
  concatenated.
- Cached discovered environment details in session state so errors preserve the
  client version, process, and data-path results. Errors show cause, recovery advice,
  and an explicit retry action.
- Successful connection marks the wizard connected and enables the next action while
  leaving `active_step` on the connection step.
- Placed manual key entry in a closed `details` disclosure with a visible label,
  password input, autocomplete disabled, and local-use guidance.
- Added non-color status icons/text, an `aria-live="polite"` update region, skip link,
  visible focus rings, mobile layout, and reduced-motion handling.

## TDD Evidence

### RED

```powershell
& 'C:\Users\qiuji\AppData\Local\Programs\Python\Python313\python.exe' -m pytest tests/test_wizard_templates.py -q -p no:cacheprovider --basetemp=.test-tmp-task4-red
```

Observed six expected failures: persistent shell selectors, accessibility controls,
truthful privacy copy, partial navigation header, full connection re-render, and
recoverable retained-info error UI were all absent.

### GREEN / Focused Regression

```text
tests/test_wizard_templates.py + tests/test_app_routes.py: 30 passed in 1.64s
```

### Full Verification

```text
Full pytest:   103 passed in 2.10s
Ruff:          All checks passed!
Jinja compile: 17 templates compiled
diff check:    exit 0; only expected LF-to-CRLF notices
```

All Python commands used the required Python 3.13 interpreter and explicit writable
pytest temp/cache locations.

## Visual QA

- Rendered the local shell at 1280x900 and 390x844.
- Confirmed the 920px desktop workspace, no horizontal overflow at 390px, collapsed
  advanced options, visible labeled key field after expansion, and disabled next
  action before connection.
- No browser console warnings or errors were emitted by local application assets.

## Concerns

- The sandbox could not load the existing external htmx CDN during visual QA, so
  htmx network interactions were verified through FastAPI integration tests rather
  than the browser. The task adds no new remote font or image dependencies.

## Fix Round 1: Persistent Stepper, Sticky Actions, and Live Announcements

### Root causes and implementation

- The stepper lives outside `#wizard-workspace`, but fragment and connection-action
  responses only replaced that workspace. Fragment responses now include an
  `hx-swap-oob="outerHTML"` stepper, and connection responses include the same OOB
  update. Connecting exposes the available step-two link without advancing; moving
  to step two marks step one completed/clickable and step two active.
- `.app-shell` used `overflow: hidden`, which made it a non-scrolling sticky
  containing block while the document body performed the actual scrolling. The
  overflow declaration was removed, so `.wizard-actions` remains sticky to the
  viewport scroll container.
- Connection actions replaced the live-region ancestor together with its populated
  content. A visually hidden `#wizard-announcer` now remains mounted in the shell;
  action responses update only its contents via `hx-swap-oob="innerHTML"`.

### RED

```powershell
& 'C:\Users\qiuji\AppData\Local\Programs\Python\Python313\python.exe' -m pytest tests/test_wizard_templates.py -q -p no:cacheprovider --basetemp=.test-tmp-task4-fix1-red
```

```text
4 failed, 6 passed in 1.76s
```

The failures independently showed no OOB stepper on navigation, no OOB stepper after
connection, no persistent announcer/OOB status payload, and the old
`overflow: hidden` sticky trap.

### GREEN / focused regressions

```powershell
& 'C:\Users\qiuji\AppData\Local\Programs\Python\Python313\python.exe' -m pytest tests/test_wizard_templates.py tests/test_app_routes.py tests/test_app_composition.py -q -p no:cacheprovider --basetemp=.test-tmp-task4-fix1-focused
```

```text
41 passed in 1.72s
```

### Full verification

```text
Full pytest:   107 passed in 2.15s
Ruff:          All checks passed!
Jinja compile: 17 templates compiled
diff check:    exit 0; only expected LF-to-CRLF notices
```
