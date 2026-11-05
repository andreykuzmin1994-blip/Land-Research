"""Static guards: how research.py may reach a protected module.

reviews/22_site_fit (review findings 1 and 3), applied to ``site_fit`` and
``zoning``. The per-attribute scans in test_site_fit_scoring.py and
test_zoning_scoring.py look only at ``<module>.<attr>``; these checks make
that the ONLY way research.py can reach the module, so an alias
(``import site_fit as _sf``), a from-import (``from site_fit import
Projection``), a bare reference (``x = site_fit``, ``getattr(site_fit, ...)``,
``vars(site_fit)``) or a reflective lookup (``importlib``, ``sys.modules``,
``__dict__``, ``__import__``) fails the suite. Offline (SR-6).
"""

from __future__ import annotations

import ast
from typing import Iterable

# Modules that import site_fit / zoning themselves, so ``<mod>.site_fit``
# would reach the protected module through them.
REEXPORTING_MODULES = frozenset({"reporting", "runner", "cli", "zoning_import"})
PROTECTED_NAMES = REEXPORTING_MODULES | {"site_fit", "zoning"}
REFLECTIVE_NAMES = frozenset({"__import__", "eval", "exec", "compile", "globals", "vars",
                              "locals", "importlib", "attrgetter"})
REFLECTIVE_ATTRS = frozenset({"__dict__", "__globals__", "__builtins__", "__code__",
                              "__closure__", "__getattribute__", "__setattr__", "__delattr__",
                              "__subclasses__", "modules", "_getframe", "f_globals",
                              "attrgetter"})
REFLECTIVE_MODULES = frozenset({"importlib", "gc", "inspect", "ctypes", "builtins"})
GETATTR_FAMILY = frozenset({"getattr", "setattr", "delattr", "hasattr"})


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    out = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            out[child] = node
    return out


def module_reference_problems(src: str, module: str, allowed: Iterable[str],
                              banned_names: Iterable[str] = ()) -> list[str]:
    """Every way ``src`` reaches ``module`` other than ``module.<allowed>``
    after a plain ``import module``; empty list = clean."""
    tree = ast.parse(src)
    allowed = set(allowed)
    banned_names = set(banned_names)
    parent = _parents(tree)
    problems: list[str] = []
    plain_imports = 0
    for node in ast.walk(tree):
        at = getattr(node, "lineno", "?")
        if isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]
                if a.name == module:
                    if a.asname is not None:
                        problems.append(f"line {at}: import {module} as {a.asname}")
                    else:
                        plain_imports += 1
                elif a.asname == module:
                    problems.append(f"line {at}: import {a.name} as {module}")
                if root in REFLECTIVE_MODULES:
                    problems.append(f"line {at}: import {a.name}")
                if root in REEXPORTING_MODULES and a.name != module:
                    problems.append(f"line {at}: import {a.name} (re-exports {module})")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root == module or root in REEXPORTING_MODULES or root in REFLECTIVE_MODULES:
                problems.append(f"line {at}: from {node.module} import ...")
            for a in node.names:
                if a.name == module or a.asname == module:
                    problems.append(f"line {at}: from-import binds {module}")
        elif isinstance(node, ast.Name):
            if node.id == module:
                p = parent.get(node)
                if not (isinstance(p, ast.Attribute) and p.value is node
                        and isinstance(node.ctx, ast.Load) and p.attr in allowed):
                    problems.append(f"line {at}: bare reference to {module}")
            if node.id in REFLECTIVE_NAMES:
                problems.append(f"line {at}: {node.id}")
            if node.id in banned_names:
                problems.append(f"line {at}: name {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr == module:
                problems.append(f"line {at}: .{module} attribute")
            if node.attr in REFLECTIVE_ATTRS:
                problems.append(f"line {at}: .{node.attr}")
            if node.attr in banned_names:
                problems.append(f"line {at}: attribute {node.attr}")
        elif isinstance(node, ast.arg) and node.arg == module:
            problems.append(f"line {at}: parameter named {module}")
        elif isinstance(node, (ast.Global, ast.Nonlocal)) and module in node.names:
            problems.append(f"line {at}: global {module}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and \
                node.func.id in GETATTR_FAMILY:
            name = node.args[1] if len(node.args) > 1 else None
            if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
                problems.append(f"line {at}: {node.func.id} with a computed name")
            elif name.value in PROTECTED_NAMES or name.value in banned_names \
                    or name.value.startswith("__"):
                problems.append(f"line {at}: {node.func.id}(..., {name.value!r})")
    if plain_imports != 1:
        problems.append(f"expected exactly one plain 'import {module}', found {plain_imports}")
    return problems
