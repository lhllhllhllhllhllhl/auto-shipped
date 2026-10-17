from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Sequence


CommandRunner = Callable[[Sequence[str]], dict[str, Any]]


class LarkCliClientError(RuntimeError):
    """Raised when lark-cli execution or user identity cannot be trusted."""


@dataclass(frozen=True, slots=True)
class LarkCliUserIdentity:
    user_id: str
    identity_type: str
    display_name: str = ""


class LarkCliClient:
    """Shared lark-cli execution, response parsing and identity compatibility."""

    def __init__(
        self,
        *,
        executable: str = "lark-cli",
        runner: CommandRunner | None = None,
    ) -> None:
        self.executable = executable
        self._runner = runner or self._subprocess_runner

    def _subprocess_runner(self, command: Sequence[str]) -> dict[str, Any]:
        executable = shutil.which(command[0])
        if executable is None:
            raise LarkCliClientError(
                "未找到lark-cli；请安装并用飞书用户身份登录后重试。"
            )
        environment = dict(os.environ)
        environment["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
        environment["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
        completed = subprocess.run(
            [executable, *command[1:]],
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )
        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip()
            raise LarkCliClientError(
                f"lark-cli执行失败（{completed.returncode}）：{message}"
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise LarkCliClientError("lark-cli未返回有效JSON。") from exc
        if not isinstance(payload, dict):
            raise LarkCliClientError("lark-cli返回值不是对象。")
        return payload

    def run(self, *args: str) -> dict[str, Any]:
        payload = self._runner([self.executable, *args])
        if not isinstance(payload, dict):
            raise LarkCliClientError("lark-cli适配器返回值不是对象。")
        if payload.get("ok") is False:
            raise LarkCliClientError(str(payload.get("error") or payload))
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    def get_current_user_identity(self) -> LarkCliUserIdentity:
        try:
            data = self.run("auth", "status", "--json", "--verify")
        except LarkCliClientError as auth_error:
            if not self._is_unknown_command(auth_error, "auth"):
                raise
            return self._identity_from_whoami(auth_error)

        user = (data.get("identities") or {}).get("user") or {}
        user_id = str(user.get("openId") or user.get("open_id") or "").strip()
        if (
            user.get("available") is not True
            or str(user.get("status") or "").strip().lower() != "ready"
            or not user_id
        ):
            return self._unavailable_identity()
        return LarkCliUserIdentity(
            user_id=user_id,
            identity_type="user",
            display_name=str(
                user.get("userName") or user.get("user_name") or ""
            ).strip(),
        )

    @staticmethod
    def _is_unknown_command(error: LarkCliClientError, command: str) -> bool:
        message = str(error).lower()
        return "unknown command" in message and command.lower() in message

    def _identity_from_whoami(
        self,
        auth_error: LarkCliClientError,
    ) -> LarkCliUserIdentity:
        try:
            data = self.run("whoami", "--as", "user")
        except LarkCliClientError as whoami_error:
            if not self._is_unknown_command(whoami_error, "whoami"):
                raise LarkCliClientError(
                    "当前lark-cli不支持auth status，whoami兼容身份探针也失败："
                    f"auth={auth_error}; whoami={whoami_error}"
                ) from whoami_error
            return self._identity_from_user_info(
                auth_error=auth_error,
                whoami_error=whoami_error,
            )

        delegated = data.get("onBehalfOf") or data.get("on_behalf_of") or {}
        user_id = str(
            delegated.get("openId") or delegated.get("open_id") or ""
        ).strip()
        if (
            str(data.get("identity") or "").strip().lower() != "user"
            or data.get("available") is not True
            or str(
                data.get("tokenStatus") or data.get("token_status") or ""
            ).strip().lower()
            != "ready"
            or not user_id
        ):
            return self._unavailable_identity()
        return LarkCliUserIdentity(
            user_id=user_id,
            identity_type="user",
            display_name=str(
                delegated.get("userName") or delegated.get("user_name") or ""
            ).strip(),
        )

    def _identity_from_user_info(
        self,
        *,
        auth_error: LarkCliClientError,
        whoami_error: LarkCliClientError,
    ) -> LarkCliUserIdentity:
        try:
            data = self.run(
                "api",
                "GET",
                "/open-apis/authen/v1/user_info",
                "--as",
                "user",
                "--json",
            )
        except LarkCliClientError as user_info_error:
            raise LarkCliClientError(
                "当前lark-cli不支持auth status和whoami，user_info兼容身份探针也失败："
                f"auth={auth_error}; whoami={whoami_error}; "
                f"user_info={user_info_error}"
            ) from user_info_error

        user_id = str(data.get("open_id") or data.get("openId") or "").strip()
        if not user_id:
            return self._unavailable_identity()
        return LarkCliUserIdentity(
            user_id=user_id,
            identity_type="user",
            display_name=str(data.get("name") or data.get("en_name") or "").strip(),
        )

    @staticmethod
    def _unavailable_identity() -> LarkCliUserIdentity:
        return LarkCliUserIdentity(user_id="", identity_type="unavailable")
