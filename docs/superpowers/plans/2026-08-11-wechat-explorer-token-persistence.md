# WechatExplorer Token Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist the WechatExplorer API Token as a machine-scope Windows DPAPI blob and reuse it across service restarts.

**Architecture:** A dedicated credential store owns DPAPI encryption and atomic file replacement. The WechatExplorer client resolves explicit input, environment input, then the encrypted store. A CLI command accepts a Token through a short-lived child-process environment, validates it against the loopback API, and never prints the secret.

**Tech Stack:** Python 3.11–3.13, Windows CryptProtectData/CryptUnprotectData through ctypes, httpx, pytest, Ruff.

## Global Constraints

- Use DPAPI `CRYPTPROTECT_LOCAL_MACHINE`; all local users with blob access may decrypt it.
- Save only ciphertext at `local/secrets/wechat_explorer_token.dpapi` and exclude it from Git.
- Never place the Token in `config.yaml`, URLs, logs, exceptions, browser state, or command-line arguments.
- Resolution precedence is explicit argument, `WECHATEXPLORER_API_TOKEN`, persisted DPAPI Token.
- Non-Windows systems retain explicit/environment support and report persisted storage as unavailable.
- The Token posted in conversation is not copied into code or tool calls; setup requires a regenerated Token entered locally.

---

### Task 1: Machine-Scope Credential Store

**Files:**
- Create: `services/credential_store.py`
- Create: `tests/test_credential_store.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `CredentialStore(path: Path, protector: Protector | None = None)`.
- Produces: `CredentialStore.save(token: str) -> None` and `CredentialStore.load() -> str | None`.
- Produces: `load_persisted_token() -> str | None` using the repository-local default path.
- Raises: `CredentialStoreError` with secret-free messages.

- [ ] **Step 1: Write failing credential-store tests**

```python
def test_store_round_trips_ciphertext_without_plaintext(tmp_path):
    protector = ReversingProtector()
    store = CredentialStore(tmp_path / "token.dpapi", protector)
    store.save("private-token")
    assert store.path.read_bytes() == b"nekot-etavirp"
    assert store.load() == "private-token"

def test_failed_atomic_replace_keeps_previous_blob(tmp_path, monkeypatch):
    store = CredentialStore(tmp_path / "token.dpapi", ReversingProtector())
    store.save("old-token")
    monkeypatch.setattr("services.credential_store.os.replace", fail_replace)
    with pytest.raises(CredentialStoreError):
        store.save("new-token")
    assert store.load() == "old-token"
```

Add literal tests for empty input, missing blob, corrupt/decrypt failure with no ciphertext in the error, and a Windows protector call using `CRYPTPROTECT_LOCAL_MACHINE | CRYPTPROTECT_UI_FORBIDDEN`.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/test_credential_store.py -q`
Expected: collection fails because `services.credential_store` does not exist.

- [ ] **Step 3: Implement DPAPI and atomic persistence**

```python
class WindowsDpapiProtector:
    def protect(self, value: bytes) -> bytes:
        return _crypt_protect(
            value,
            flags=CRYPTPROTECT_LOCAL_MACHINE | CRYPTPROTECT_UI_FORBIDDEN,
        )

class CredentialStore:
    def save(self, token: str) -> None:
        normalized = token.strip()
        if not normalized:
            raise CredentialStoreError("WechatExplorer Token 不能为空")
        encrypted = self.protector.protect(normalized.encode("utf-8"))
        _atomic_write(self.path, encrypted)

    def load(self) -> str | None:
        if not self.path.exists():
            return None
        try:
            return self.protector.unprotect(self.path.read_bytes()).decode("utf-8")
        except Exception as exc:
            raise CredentialStoreError("无法解密已保存的 WechatExplorer Token") from exc
```

- [ ] **Step 4: Run GREEN and lint**

Run: `python -m pytest tests/test_credential_store.py -q`
Expected: all pass.

Run: `python -m ruff check services/credential_store.py tests/test_credential_store.py`
Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```powershell
git add .gitignore services/credential_store.py tests/test_credential_store.py
git commit -m "feat: persist WechatExplorer token with DPAPI"
```

### Task 2: Client Resolution and Secure Save Command

**Files:**
- Create: `services/credential_cli.py`
- Create: `tests/test_credential_cli.py`
- Modify: `services/wechat_explorer.py`
- Modify: `tests/test_wechat_explorer.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `CredentialStore.save`, `load_persisted_token`, and `CredentialStoreError` from Task 1.
- Produces: `resolve_token(explicit: str | None, loader: Callable[[], str | None]) -> str`.
- Produces: `save_and_validate(token: str, store: CredentialStore, client_factory: Callable[[str], Any]) -> None`.
- Produces CLI: `python -m services.credential_cli save`, reading `WECHATEXPLORER_TOKEN_INPUT` only from the child environment.

- [ ] **Step 1: Write failing precedence and CLI tests**

```python
def test_explicit_and_environment_tokens_override_persisted(monkeypatch):
    monkeypatch.setenv("WECHATEXPLORER_API_TOKEN", "environment-token")
    assert resolve_token("explicit-token", lambda: "stored-token") == "explicit-token"
    assert resolve_token(None, lambda: "stored-token") == "environment-token"

def test_client_falls_back_to_persisted_token(monkeypatch):
    monkeypatch.delenv("WECHATEXPLORER_API_TOKEN", raising=False)
    client = WechatExplorerClient(
        token_loader=lambda: "stored-token",
        transport=authenticated_transport("stored-token"),
    )
    assert client.query_chatrooms()[0][0] == "room@chatroom"
```

Add tests that save-and-validate calls the authenticated catalog boundary, failed validation never includes the Token, and a corrupt store becomes a safe WechatExplorer error.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/test_credential_cli.py tests/test_wechat_explorer.py -q`
Expected: missing CLI module and token-loader interface failures.

- [ ] **Step 3: Implement resolution, CLI, and documentation**

```python
def resolve_token(explicit, loader):
    token = explicit or os.environ.get("WECHATEXPLORER_API_TOKEN")
    if token and token.strip():
        return token.strip()
    try:
        token = loader()
    except CredentialStoreError as exc:
        raise WechatExplorerError(
            "无法读取已保存的 WechatExplorer Token；请重新保存凭据"
        ) from exc
    if not token or not token.strip():
        raise WechatExplorerError(
            "尚未保存 WechatExplorer Token；请运行安全凭据设置"
        )
    return token.strip()

def save_and_validate(token, store, client_factory):
    store.save(token)
    client = client_factory(token)
    try:
        client.query_chatrooms()
    finally:
        client.close()
```

The CLI reads `WECHATEXPLORER_TOKEN_INPUT`, calls `save_and_validate`, removes the environment entry in `finally`, and prints only success or secret-free failure text. README documents the secure local prompt and environment override.

- [ ] **Step 4: Run full verification**

Run: `python -m pytest -q`
Expected: all pass.

Run: `python -m ruff check .`
Expected: `All checks passed!`

Run the secure PowerShell prompt with a newly generated Token, verify the CLI succeeds, restart the service without a Token environment variable, and confirm authenticated connection works through the persisted credential.

- [ ] **Step 5: Commit and push**

```powershell
git add services/credential_cli.py tests/test_credential_cli.py services/wechat_explorer.py tests/test_wechat_explorer.py README.md
git commit -m "feat: reuse persisted WechatExplorer credentials"
git push origin main
```
