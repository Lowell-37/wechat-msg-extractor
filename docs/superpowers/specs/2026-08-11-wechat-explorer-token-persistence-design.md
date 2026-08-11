# WechatExplorer Token Persistence Design

## Goal

Persist the WechatExplorer Bearer Token across project restarts without storing plaintext in configuration, environment files, logs, Git, or browser state. The encrypted credential must be usable by other Windows users on the same machine.

## Storage and Access

Use Windows DPAPI with `CRYPTPROTECT_LOCAL_MACHINE`. Save only the encrypted blob under Git-ignored `local/secrets/wechat_explorer_token.dpapi`. Machine-scope DPAPI intentionally permits any local Windows account that can read the blob to decrypt it; the file is therefore limited to local authenticated users and never exposed over HTTP.

The token resolution order is explicit constructor input, `WECHATEXPLORER_API_TOKEN`, then the DPAPI store. Existing environment-variable deployments remain compatible and can temporarily override the persisted value.

## Credential Lifecycle

Provide a local PowerShell input flow that reads the Token without echoing it and passes it directly to a small Python credential command over the child process environment. The command encrypts the value, atomically replaces the credential blob, clears its environment copy, and never prints the Token. Saving a new credential replaces the prior encrypted blob only after encryption succeeds.

After storage, validate the credential against the loopback-only WechatExplorer API. A failed validation leaves the encrypted credential available for replacement but does not start the project as connected. Users can rerun the secure input flow to rotate or repair it.

## Error Handling and Security

Empty values are rejected. Non-Windows systems report that persistent storage is unavailable and continue supporting explicit/environment tokens. DPAPI or file errors become safe domain errors without ciphertext or plaintext in messages. The Reader client must not include credentials in URLs, logs, exceptions, or response fragments.

The Token already posted in conversation must be treated as exposed. The final setup uses a newly generated Token from WechatExplorer API Center rather than copying the posted value into a tool call.

## Verification

Tests cover DPAPI round trips through an injected protector, machine-scope selection, atomic replacement, missing/corrupt blobs, explicit and environment precedence, persisted-token fallback, validation failures without secret leakage, and Git exclusion. Final verification includes full pytest, Ruff, a live health/authenticated API check through the secure local input flow, service restart, and a connection-page smoke test.
