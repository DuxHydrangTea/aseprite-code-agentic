"""Dynamic tool discovery and progressive search catalog for diivi/aseprite-mcp."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Callable, Literal

from .security import PREVIEW_SERVER_TOOLS, RAW_LUA_TOOLS

DetailLevel = Literal["summary", "signature", "full"]
PolicyKind = Literal["allowed", "gated_raw_lua", "blocked_preview_server"]


def _format_annotation(annotation: Any) -> str:
    if annotation is inspect.Parameter.empty:
        return "Any"
    if isinstance(annotation, type):
        return annotation.__name__
    text = str(annotation)
    return text.replace("typing.", "")


def _serialize_default(default: Any) -> Any:
    if default is inspect.Parameter.empty:
        return None
    if isinstance(default, (str, int, float, bool)) or default is None:
        return default
    return repr(default)


def _format_signature(category: str, name: str, sig: inspect.Signature) -> str:
    parts: list[str] = []
    for param in sig.parameters.values():
        ann = _format_annotation(param.annotation)
        if param.default is inspect.Parameter.empty:
            parts.append(f"{param.name}: {ann}")
        else:
            parts.append(f"{param.name}: {ann} = {param.default!r}")
    return f"ase.{category}.{name}({', '.join(parts)})"


def _extract_parameters(sig: inspect.Signature) -> list[dict[str, Any]]:
    params: list[dict[str, Any]] = []
    for param in sig.parameters.values():
        required = param.default is inspect.Parameter.empty
        entry: dict[str, Any] = {
            "name": param.name,
            "type": _format_annotation(param.annotation),
            "required": required,
        }
        if not required:
            entry["default"] = _serialize_default(param.default)
        params.append(entry)
    return params


def _tool_policy(name: str) -> PolicyKind:
    if name in RAW_LUA_TOOLS:
        return "gated_raw_lua"
    if name in PREVIEW_SERVER_TOOLS:
        return "blocked_preview_server"
    return "allowed"


@dataclass(frozen=True)
class ToolSpec:
    """Metadata and callable reference for one upstream aseprite-mcp tool."""

    name: str
    category: str
    signature: str
    summary: str
    docstring: str
    parameters: list[dict[str, Any]]
    policy: PolicyKind
    callable_ref: Callable[..., Any] = field(repr=False, compare=False)

    @property
    def sdk_call(self) -> str:
        return f"ase.{self.category}.{self.name}"

    def to_dict(self, detail: DetailLevel = "signature") -> dict[str, Any]:
        data: dict[str, Any] = {
            "sdk_call": self.sdk_call,
            "name": self.name,
            "category": self.category,
            "summary": self.summary,
            "policy": self.policy,
        }
        if detail in ("signature", "full"):
            data["signature"] = self.signature
        if detail == "full":
            data["docstring"] = self.docstring
            data["parameters"] = self.parameters
        return data


def _inspect_category_module(upstream_pkg: ModuleType, category: str) -> list[ToolSpec]:
    """Import one upstream tool module and extract its registered @mcp.tool() specs."""
    mod = importlib.import_module(f"{upstream_pkg.__name__}.tools.{category}")
    tool_manager = getattr(upstream_pkg.mcp, "_tool_manager", None)
    registered_names: set[str] = (
        set(tool_manager._tools.keys()) if tool_manager is not None else set()
    )

    specs: list[ToolSpec] = []
    for fn_name, fn in inspect.getmembers(mod, inspect.isfunction):
        if fn.__module__ != mod.__name__:
            continue
        if fn_name.startswith("_"):
            continue
        if registered_names and fn_name not in registered_names:
            continue
        sig = inspect.signature(fn)
        raw_doc = inspect.getdoc(fn) or ""
        summary = raw_doc.splitlines()[0].strip() if raw_doc else ""
        specs.append(
            ToolSpec(
                name=fn_name,
                category=category,
                signature=_format_signature(category, fn_name, sig),
                summary=summary,
                docstring=raw_doc,
                parameters=_extract_parameters(sig),
                policy=_tool_policy(fn_name),
                callable_ref=fn,
            )
        )
    return sorted(specs, key=lambda s: s.name)


class ToolCatalog:
    """Searchable index over all tools registered by upstream aseprite_mcp."""

    def __init__(self, specs: list[ToolSpec]) -> None:
        self._specs: list[ToolSpec] = sorted(specs, key=lambda s: (s.category, s.name))
        self._by_category: dict[str, list[ToolSpec]] = {}
        self._by_name: dict[str, ToolSpec] = {}
        for spec in self._specs:
            self._by_category.setdefault(spec.category, []).append(spec)
            self._by_name[spec.name] = spec

    @classmethod
    def for_category(cls, upstream_pkg: ModuleType, category: str) -> list[ToolSpec]:
        """Discover @mcp.tool() specs for a single category module without importing others."""
        return _inspect_category_module(upstream_pkg, category)

    @classmethod
    def from_upstream(cls, upstream_pkg: ModuleType) -> "ToolCatalog":
        """Discover all @mcp.tool() functions from an imported upstream `aseprite_mcp` package."""
        tools_pkg = importlib.import_module(f"{upstream_pkg.__name__}.tools")
        specs: list[ToolSpec] = []
        for mod_info in pkgutil.iter_modules(tools_pkg.__path__):
            specs.extend(_inspect_category_module(upstream_pkg, mod_info.name))
        return cls(specs)

    @property
    def total_tools(self) -> int:
        return len(self._specs)

    @property
    def specs(self) -> list[ToolSpec]:
        return list(self._specs)

    def by_category(self) -> dict[str, list[ToolSpec]]:
        return {k: list(v) for k, v in self._by_category.items()}

    def categories(self) -> dict[str, int]:
        return {cat: len(items) for cat, items in self._by_category.items()}

    def get(self, name: str) -> ToolSpec | None:
        return self._by_name.get(name)

    def search(
        self,
        query: str = "",
        category: str = "",
        detail: DetailLevel = "signature",
        limit: int = 12,
    ) -> dict[str, Any]:
        """Filter and rank tools by category and multi-token search query."""
        if detail not in ("summary", "signature", "full"):
            detail = "signature"
        effective_limit = max(1, min(int(limit), 150))

        cat_filter = category.strip().lower()
        candidates = (
            self._by_category.get(cat_filter, [])
            if cat_filter
            else self._specs
        )

        tokens = [tok for tok in query.strip().lower().split() if tok]
        if not tokens:
            matched_specs = list(candidates)
        else:
            scored: list[tuple[int, str, str, ToolSpec]] = []
            for spec in candidates:
                score = _score_match(spec, tokens)
                if score > 0:
                    scored.append((score, spec.category, spec.name, spec))
            scored.sort(key=lambda item: (-item[0], item[1], item[2]))
            matched_specs = [item[3] for item in scored]

        shown = matched_specs[:effective_limit]
        result: dict[str, Any] = {
            "total_tools": self.total_tools,
            "matched": len(matched_specs),
            "showing": len(shown),
            "tools": [spec.to_dict(detail=detail) for spec in shown],
        }
        if not query.strip() and not category.strip():
            result["categories"] = self.categories()
        return result


def _score_match(spec: ToolSpec, tokens: list[str]) -> int:
    name_l = spec.name.lower()
    cat_l = spec.category.lower()
    sum_l = spec.summary.lower()
    doc_l = spec.docstring.lower()

    total_score = 0
    for tok in tokens:
        token_score = 0
        if tok == name_l:
            token_score = 100
        elif tok in name_l:
            token_score = 40
        elif tok in cat_l:
            token_score = 25
        elif tok in sum_l:
            token_score = 15
        elif tok in doc_l:
            token_score = 5

        if token_score == 0:
            return 0
        total_score += token_score

    return total_score
