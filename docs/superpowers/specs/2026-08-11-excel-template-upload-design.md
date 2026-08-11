# Excel Template Upload Design

## Goal

Replace the fixed `excel.template_path` workflow with a browser file picker. A user can upload an `.xlsx` workbook, validate it, and make the server-side copy the persistent default template for later sessions and restarts.

## User Flow

The connection step shows the active template name and an “选择 Excel 模板” control. Selecting a file submits it without leaving the wizard. On success, the page shows the saved template name and available Sheet count, clears cached catalog and preview state, and requires the user to continue with the refreshed workbook. On failure, it shows an actionable error and keeps the previous template active.

## Server Design

Add a multipart upload endpoint owned by the action router. It accepts only `.xlsx`, enforces a conservative size limit, saves to a temporary file, and opens the workbook through the existing Excel boundary before activation. A valid workbook must contain at least one visible worksheet.

After validation, atomically move the file into a Git-ignored local template directory using a server-generated filename. Persist that path under `excel.template_path` in the local `config.yaml`, preserving unrelated settings. Update the in-memory application configuration only after both the template and configuration writes succeed. Replacing a template must not delete the prior file until activation succeeds.

## State and Security

Uploads are treated as untrusted input. Never use the browser filename as a filesystem path, never allow macro-enabled formats, and never render workbook values as HTML during upload. The selected template becomes a machine-wide default because this is a local single-user application. Existing sessions invalidate catalog, selection, and preview data that were derived from the old workbook.

## Error Handling and Tests

Cover successful upload and restart persistence, invalid extension, oversized input, corrupt workbook, workbook without visible Sheets, atomic failure preservation, path traversal filenames, and wizard cache invalidation. Existing configured templates remain supported when no upload has occurred. Full pytest, Ruff, Jinja compilation, and a browser-level upload check are required before completion.
