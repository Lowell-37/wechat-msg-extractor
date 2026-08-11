"""Local command for securely saving and validating API credentials."""

import os
import sys
from collections.abc import Callable
from typing import Any

from services.credential_store import CredentialStore
from services.wechat_explorer import WechatExplorerClient

TOKEN_INPUT_ENV = "WECHATEXPLORER_TOKEN_INPUT"  # noqa: S105 - environment key, not a secret


class CredentialCliError(RuntimeError):
    """Raised when a credential cannot be saved and validated."""


def save_and_validate(
    token: str,
    store: CredentialStore,
    client_factory: Callable[[str], Any],
) -> None:
    client = None
    operation_error = None
    cleanup_error = None
    try:
        store.save(token)
        client = client_factory(token)
        client.query_chatrooms()
    except Exception as exc:  # noqa: BLE001 - sanitize the local CLI boundary
        operation_error = exc
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as exc:  # noqa: BLE001
                cleanup_error = exc
    if operation_error is not None:
        raise CredentialCliError(
            "无法保存或验证 WechatExplorer Token；请确认 Token 与本机 API 状态"
        ) from operation_error
    if cleanup_error is not None:
        raise CredentialCliError(
            "WechatExplorer Token 已处理，但本机 API 连接未能安全关闭"
        ) from cleanup_error


def main(arguments: list[str] | None = None) -> int:
    args = sys.argv[1:] if arguments is None else arguments
    if args != ["save"]:
        print("用法：python -m services.credential_cli save", file=sys.stderr)
        return 2

    token = os.environ.pop(TOKEN_INPUT_ENV, "")
    if not token.strip():
        print("失败：未从本机安全输入流程收到 Token", file=sys.stderr)
        return 1
    try:
        save_and_validate(
            token,
            CredentialStore.default(),
            lambda value: WechatExplorerClient(token=value),
        )
    except CredentialCliError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    finally:
        token = ""
    print("WechatExplorer Token 已加密保存并验证通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
