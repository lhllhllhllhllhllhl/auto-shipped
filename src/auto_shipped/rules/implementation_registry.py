from __future__ import annotations

import importlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ImplementationRegistryError(ValueError):
    """Raised when a declared rule implementation cannot be verified."""


@dataclass(frozen=True, slots=True)
class ImplementationRegistration:
    reference: str
    runtime: str
    target: str
    test: str


@dataclass(frozen=True, slots=True)
class ImplementationRegistry:
    registry_id: str
    version: int
    implementations: dict[str, ImplementationRegistration]

    def contains(self, reference: str) -> bool:
        return reference in self.implementations


def _resolve_python_target(target: str) -> object:
    module_name, separator, attribute_path = target.partition(":")
    if not separator or not module_name or not attribute_path:
        raise ImplementationRegistryError(f"Python实现目标格式无效：{target}")
    try:
        value: object = importlib.import_module(module_name)
        for part in attribute_path.split("."):
            value = getattr(value, part)
    except (ImportError, AttributeError) as exc:
        raise ImplementationRegistryError(f"Python实现目标不存在：{target}") from exc
    if not callable(value):
        raise ImplementationRegistryError(f"Python实现目标不可调用：{target}")
    return value


def _verify_javascript_target(target: str, project_root: Path) -> None:
    relative_path, separator, symbol = target.partition("#")
    if not separator or not relative_path or not symbol:
        raise ImplementationRegistryError(f"JavaScript实现目标格式无效：{target}")
    path = project_root / relative_path
    if not path.is_file():
        raise ImplementationRegistryError(f"JavaScript实现文件不存在：{relative_path}")
    source = path.read_text(encoding="utf-8")
    if not re.search(rf"\b{re.escape(symbol)}\b", source):
        raise ImplementationRegistryError(f"JavaScript实现符号不存在：{target}")


def load_implementation_registry(
    path: str | Path,
    *,
    project_root: str | Path | None = None,
    verify_targets: bool = True,
    verify_test_files: bool = False,
) -> ImplementationRegistry:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImplementationRegistryError(f"无法读取实现注册表：{exc}") from exc
    if payload.get("schema_version") != "1.0":
        raise ImplementationRegistryError("实现注册表schema_version必须为1.0")
    registry_id = str(payload.get("registry_id") or "").strip()
    version = payload.get("version")
    if not registry_id or not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ImplementationRegistryError("实现注册表缺少有效registry_id或version")
    raw_implementations = payload.get("implementations")
    if not isinstance(raw_implementations, dict) or not raw_implementations:
        raise ImplementationRegistryError("实现注册表implementations必须是非空对象")

    root = (
        Path(project_root).resolve()
        if project_root is not None
        else Path(__file__).resolve().parents[3]
    )
    implementations: dict[str, ImplementationRegistration] = {}
    for reference, raw in raw_implementations.items():
        if not isinstance(reference, str) or not reference.strip() or not isinstance(raw, dict):
            raise ImplementationRegistryError("实现引用和实现登记必须有效")
        runtime = str(raw.get("runtime") or "").strip()
        target = str(raw.get("target") or "").strip()
        test = str(raw.get("test") or "").strip()
        if runtime not in {"python", "javascript"}:
            raise ImplementationRegistryError(f"实现{reference}的runtime无效")
        if not target or not test:
            raise ImplementationRegistryError(f"实现{reference}缺少target或test")
        test_path = root / test
        if verify_test_files and not test_path.is_file():
            raise ImplementationRegistryError(f"实现{reference}的测试文件不存在：{test}")
        if verify_targets and runtime == "python":
            _resolve_python_target(target)
        elif verify_targets:
            _verify_javascript_target(target, root)
        implementations[reference] = ImplementationRegistration(
            reference=reference,
            runtime=runtime,
            target=target,
            test=test,
        )
    return ImplementationRegistry(
        registry_id=registry_id,
        version=version,
        implementations=implementations,
    )
