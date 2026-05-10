from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json

from .tools import Tool, ToolRegistry


DEFAULT_ACTION_CATEGORIES = {
    "search_mock": "read_only",
    "extract_facts": "transform",
    "draft_report": "model_generation",
    "finalize_report": "filesystem_write",
    "flaky_echo": "utility",
}


class PolicyViolation(RuntimeError):
    pass


@dataclass
class PolicyDecision:
    tool_name: str
    action_category: str
    allowed: bool
    reason: str
    risky: bool
    requires_approval: bool
    allowed_output_roots: list[str]
    checked_arguments: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "action_category": self.action_category,
            "allowed": self.allowed,
            "reason": self.reason,
            "risky": self.risky,
            "requires_approval": self.requires_approval,
            "allowed_output_roots": self.allowed_output_roots,
            "checked_arguments": self.checked_arguments,
        }


class PolicyEngine:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        store_root: str | Path = ".runs",
        config: dict[str, Any] | None = None,
        config_path: str | Path | None = None,
    ) -> None:
        self.registry = registry
        self.store_root = Path(store_root).resolve()
        resolved_config_path = Path(config_path).resolve() if config_path else None
        self.config_path = str(resolved_config_path) if resolved_config_path else None
        self.config_base_dir = resolved_config_path.parent if resolved_config_path else Path.cwd().resolve()
        base = default_policy_config(registry=registry, store_root=self.store_root)
        self.config = _merge_dicts(base, config or {})

    @classmethod
    def from_file(
        cls,
        registry: ToolRegistry,
        *,
        store_root: str | Path = ".runs",
        path: str | Path,
    ) -> "PolicyEngine":
        loaded = load_policy_file(path)
        return cls(registry=registry, store_root=store_root, config=loaded, config_path=path)

    def describe(self) -> dict[str, Any]:
        configured_roots = list(self.config.get("default_allowed_write_roots", []))
        return {
            "version": self.config.get("version", 1),
            "policy_file": self.config_path,
            "policy_base_dir": str(self.config_base_dir),
            "store_root": str(self.store_root),
            "default_allowed_write_roots": configured_roots,
            "resolved_default_allowed_write_roots": [str(self._resolve_policy_path(item)) for item in configured_roots],
            "tool_policies": self.config.get("tool_policies", {}),
        }

    def evaluate(self, tool_name: str, arguments: dict[str, Any]) -> PolicyDecision:
        tool = self.registry.get(tool_name)
        tool_rules = self._tool_rules(tool)
        category = str(tool_rules.get("action_category") or tool.action_category)
        allowed_output_roots = [str(item) for item in tool_rules.get("allowed_output_roots", [])]

        if tool_rules.get("enabled", True) is False:
            return PolicyDecision(
                tool_name=tool_name,
                action_category=category,
                allowed=False,
                reason=f"Tool '{tool_name}' is disabled by policy.",
                risky=tool.risky,
                requires_approval=tool.risky,
                allowed_output_roots=allowed_output_roots,
                checked_arguments=sorted(arguments.keys()),
            )

        write_targets = self._extract_write_targets(arguments)
        if write_targets:
            allowed, reason = self._check_write_targets(write_targets=write_targets, allowed_roots=allowed_output_roots)
            return PolicyDecision(
                tool_name=tool_name,
                action_category=category,
                allowed=allowed,
                reason=reason,
                risky=tool.risky,
                requires_approval=tool.risky,
                allowed_output_roots=allowed_output_roots,
                checked_arguments=sorted(arguments.keys()),
            )

        return PolicyDecision(
            tool_name=tool_name,
            action_category=category,
            allowed=True,
            reason=f"Tool '{tool_name}' is allowed by policy as {category}.",
            risky=tool.risky,
            requires_approval=tool.risky,
            allowed_output_roots=allowed_output_roots,
            checked_arguments=sorted(arguments.keys()),
        )

    def _tool_rules(self, tool: Tool) -> dict[str, Any]:
        tool_policies = self.config.get("tool_policies", {})
        rules = dict(tool_policies.get(tool.name, {}))
        rules.setdefault("enabled", True)
        rules.setdefault("action_category", tool.action_category)
        if tool.action_category == "filesystem_write":
            defaults = list(self.config.get("default_allowed_write_roots", []))
            rules.setdefault("allowed_output_roots", defaults)
        else:
            rules.setdefault("allowed_output_roots", [])
        return rules

    def _extract_write_targets(self, arguments: dict[str, Any]) -> list[str]:
        write_targets: list[str] = []
        for name in ("output_path", "path"):
            value = arguments.get(name)
            if isinstance(value, str) and value.strip():
                write_targets.append(value)
        return write_targets

    def _check_write_targets(self, *, write_targets: list[str], allowed_roots: list[str]) -> tuple[bool, str]:
        resolved_roots = [self._resolve_policy_path(item) for item in allowed_roots]
        if not resolved_roots:
            return False, "Filesystem writes are denied because policy defines no allowed output roots."

        for target in write_targets:
            resolved_target = Path(target).resolve()
            if not any(_is_relative_to(resolved_target, root) for root in resolved_roots):
                roots_text = ", ".join(str(root) for root in resolved_roots)
                return False, (
                    f"Write target '{resolved_target}' is outside allowed output roots: {roots_text}."
                )
        roots_text = ", ".join(str(root) for root in resolved_roots)
        return True, f"Write targets are allowed under: {roots_text}."

    def _resolve_policy_path(self, value: str | Path) -> Path:
        candidate = Path(value)
        if candidate.is_absolute():
            return candidate.resolve()
        return (self.config_base_dir / candidate).resolve()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def default_policy_config(registry: ToolRegistry, store_root: str | Path = ".runs") -> dict[str, Any]:
    resolved_store_root = str(Path(store_root).resolve())
    tool_policies: dict[str, Any] = {}
    for tool in registry.list():
        category = tool.action_category or DEFAULT_ACTION_CATEGORIES.get(tool.name, "utility")
        entry: dict[str, Any] = {
            "enabled": True,
            "action_category": category,
            "requires_approval": tool.risky,
            "risky": tool.risky,
        }
        if category == "filesystem_write":
            entry["allowed_output_roots"] = [resolved_store_root]
        tool_policies[tool.name] = entry
    return {
        "version": 1,
        "default_allowed_write_roots": [resolved_store_root],
        "tool_policies": tool_policies,
    }


def load_policy_file(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    with file_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("Policy file must contain a JSON object")
    return data
