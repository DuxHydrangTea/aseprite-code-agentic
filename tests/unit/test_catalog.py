"""Unit tests for dynamic ToolCatalog discovery, search, and parameter audit."""

from __future__ import annotations

import inspect

from aseprite_codemode.catalog import ToolCatalog
from aseprite_codemode.security import (
    CONDITIONAL_PATH_PARAM_NAMES,
    NON_PATH_PARAM_NAMES,
    PATH_PARAM_NAMES,
)


def test_catalog_discovers_all_upstream_tools(catalog: ToolCatalog) -> None:
    assert catalog.total_tools >= 100
    assert catalog.total_tools == 123
    cats = catalog.categories()
    assert len(cats) >= 18
    assert "drawing" in cats
    assert "animation" in cats
    assert "palette" in cats


def test_catalog_search_filters_and_details(catalog: ToolCatalog, upstream_pkg) -> None:
    unfiltered = catalog.search()
    assert unfiltered["showing"] == 12
    assert "categories" in unfiltered
    assert len(unfiltered["categories"]) == 21

    res = catalog.search(query="draw rows", category="drawing", detail="summary", limit=10)
    assert "categories" not in res
    assert res["matched"] >= 1
    assert res["showing"] >= 1
    top = res["tools"][0]
    assert top["sdk_call"] == "ase.drawing.draw_rows"
    assert top["policy"] == "allowed"
    assert "signature" not in top
    assert "docstring" not in top

    sig_res = catalog.search(query="draw_rows", detail="signature", limit=5)
    assert sig_res["tools"][0]["signature"].startswith("ase.drawing.draw_rows(")
    assert "docstring" not in sig_res["tools"][0]

    full_res = catalog.search(query="draw_rows", detail="full", limit=5)
    assert "docstring" in full_res["tools"][0]
    assert len(full_res["tools"][0]["parameters"]) >= 5

    drawing_specs = ToolCatalog.for_category(upstream_pkg, "drawing")
    assert any(s.name == "draw_rows" for s in drawing_specs)


def test_catalog_policy_markers(catalog: ToolCatalog) -> None:
    lua_spec = catalog.get("run_lua_script")
    assert lua_spec is not None
    assert lua_spec.policy == "gated_raw_lua"

    srv_spec = catalog.get("start_preview_server")
    assert srv_spec is not None
    assert srv_spec.policy == "blocked_preview_server"


def test_all_upstream_path_params_are_classified(catalog: ToolCatalog) -> None:
    """Audit every parameter across all 123 upstream tools so no path parameter goes unguarded."""
    suspect_substrings = (
        "file",
        "path",
        "dir",
        "folder",
        "font",
        "out",
        "src",
        "dst",
        "target",
        "source",
        "data",
        "name",
    )
    classified = PATH_PARAM_NAMES | CONDITIONAL_PATH_PARAM_NAMES | NON_PATH_PARAM_NAMES

    unclassified: list[str] = []
    for spec in catalog.specs:
        sig = inspect.signature(spec.callable_ref)
        for pname in sig.parameters:
            if any(sub in pname.lower() for sub in suspect_substrings):
                if pname not in classified:
                    unclassified.append(f"{spec.category}.{spec.name}:{pname}")

    assert unclassified == [], f"Unclassified path-like upstream parameters: {unclassified}"
