"""Hierarchical mass computation for a Fusion assembly.

This module is deliberately free of any `adsk` import: it only relies on the
*shape* of the Fusion API objects (duck typing), so it can be exercised with
mocked objects in unit tests.

Fusion objects used:
    Component:   .name, .id, .bRepBodies, .occurrences
    Occurrence:  .name, .component, .bRepBodies, .childOccurrences, .isVisible
    BRepBody:    .name, .physicalProperties.mass (kg), .material, .isVisible,
                 .isSolid, .nativeObject, .entityToken
    Collections: .count + .item(i)
"""

import csv
import io
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

KIND_ROOT = "root"
KIND_OCCURRENCE = "occurrence"
KIND_BODY = "body"

FLAG_NO_MATERIAL = "no_material"
FLAG_ZERO_MASS = "zero_mass"
FLAG_ERROR = "error"
FLAG_NOT_SOLID = "not_solid"


@dataclass
class MassNode:
    id: str
    name: str
    kind: str
    path: str
    own_mass: float = 0.0  # kg, bodies directly owned by this node
    total_mass: float = 0.0  # kg, own_mass + children
    children: List["MassNode"] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)
    component_key: Optional[str] = None
    depth: int = 0

    @property
    def is_leaf_part(self) -> bool:
        """An occurrence without child occurrences (a 'part')."""
        return self.kind == KIND_OCCURRENCE and not any(
            c.kind == KIND_OCCURRENCE for c in self.children
        )

    def walk(self) -> Iterator["MassNode"]:
        yield self
        for child in self.children:
            yield from child.walk()

    def to_dict(self, grand_total: Optional[float] = None) -> Dict[str, Any]:
        grand_total = self.total_mass if grand_total is None else grand_total
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "path": self.path,
            "own": self.own_mass,
            "total": self.total_mass,
            "pct": percent(self.total_mass, grand_total),
            "flags": list(self.flags),
            "depth": self.depth,
            "children": [c.to_dict(grand_total) for c in self.children],
        }


@dataclass
class AnalysisResult:
    root: MassNode
    # node id -> Fusion entity (occurrence or body proxy); root maps to None
    entities: Dict[str, Any]
    skipped_hidden: int = 0

    @property
    def total_mass(self) -> float:
        return self.root.total_mass

    def warnings(self) -> List[Dict[str, Any]]:
        """Occurrences / bodies flagged as suspicious (no material, zero mass...)."""
        out = []
        for node in self.root.walk():
            if node.kind == KIND_ROOT or not node.flags:
                continue
            out.append({"id": node.id, "name": node.name, "path": node.path,
                        "kind": node.kind, "flags": list(node.flags)})
        return out

    def component_summary(self) -> List[Dict[str, Any]]:
        """Group occurrences by component: instance count, unit and total mass."""
        groups: Dict[str, Dict[str, Any]] = {}
        for node in self.root.walk():
            if node.kind != KIND_OCCURRENCE:
                continue
            key = node.component_key or node.name
            g = groups.setdefault(key, {"component": _component_name(node.name),
                                        "instances": 0, "unit": node.total_mass,
                                        "total": 0.0})
            g["instances"] += 1
            g["total"] += node.total_mass
        return sorted(groups.values(), key=lambda g: g["total"], reverse=True)

    def to_payload(self) -> Dict[str, Any]:
        return {
            "total": self.total_mass,
            "tree": self.root.to_dict(),
            "warnings": self.warnings(),
            "components": self.component_summary(),
            "skippedHidden": self.skipped_hidden,
        }


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def iter_collection(coll: Any) -> Iterable[Any]:
    """Iterate a Fusion collection (count/item) or any Python iterable."""
    if coll is None:
        return []
    if hasattr(coll, "count") and hasattr(coll, "item") and not isinstance(coll, (list, tuple)):
        return (coll.item(i) for i in range(coll.count))
    return iter(coll)


def percent(part: float, whole: float) -> float:
    return (100.0 * part / whole) if whole > 0 else 0.0


def _component_name(occurrence_name: str) -> str:
    # Fusion names occurrences "Component:1", "Component:2"...
    head, sep, tail = occurrence_name.rpartition(":")
    return head if sep and tail.isdigit() else occurrence_name


def _body_key(body: Any) -> Any:
    native = getattr(body, "nativeObject", None) or body
    token = getattr(native, "entityToken", None)
    return token if token is not None else id(native)


def _has_material(body: Any) -> bool:
    try:
        mat = body.material
    except Exception:
        return False
    return mat is not None and bool(getattr(mat, "name", "x"))


def _is_visible(entity: Any) -> bool:
    try:
        return bool(entity.isVisible)
    except Exception:
        return True


class _Builder:
    def __init__(self, ignore_hidden: bool, include_bodies: bool):
        self.ignore_hidden = ignore_hidden
        self.include_bodies = include_bodies
        self.entities: Dict[str, Any] = {}
        self.mass_cache: Dict[Any, Tuple[float, List[str]]] = {}
        self.skipped_hidden = 0
        self._next = 0

    def new_id(self) -> str:
        nid = "n%d" % self._next
        self._next += 1
        return nid

    def body_mass(self, body: Any) -> Tuple[float, List[str]]:
        """Mass in kg + flags for a body. Instances share the native body, so
        the (expensive) physicalProperties call is cached on the native body."""
        key = _body_key(body)
        if key in self.mass_cache:
            return self.mass_cache[key]
        flags: List[str] = []
        try:
            mass = float(body.physicalProperties.mass or 0.0)
        except Exception:
            mass = 0.0
            flags.append(FLAG_ERROR)
        if getattr(body, "isSolid", True) is False:
            flags.append(FLAG_NOT_SOLID)
        if not _has_material(body):
            flags.append(FLAG_NO_MATERIAL)
        if mass <= 0.0:
            flags.append(FLAG_ZERO_MASS)
        self.mass_cache[key] = (mass, flags)
        return mass, flags

    def fill(self, node: MassNode, bodies: Any, occurrences: Any) -> None:
        for body in iter_collection(bodies):
            if self.ignore_hidden and not _is_visible(body):
                self.skipped_hidden += 1
                continue
            mass, bflags = self.body_mass(body)
            node.own_mass += mass
            if FLAG_NO_MATERIAL in bflags and FLAG_NO_MATERIAL not in node.flags:
                node.flags.append(FLAG_NO_MATERIAL)
            if self.include_bodies:
                bnode = MassNode(id=self.new_id(), name=body.name, kind=KIND_BODY,
                                 path=node.path + "/" + body.name, own_mass=mass,
                                 total_mass=mass, flags=list(bflags), depth=node.depth + 1)
                self.entities[bnode.id] = body
                node.children.append(bnode)

        for occ in iter_collection(occurrences):
            if self.ignore_hidden and not _is_visible(occ):
                self.skipped_hidden += 1
                continue
            node.children.append(self.occurrence(occ, node))

        node.total_mass = node.own_mass + sum(
            c.total_mass for c in node.children if c.kind != KIND_BODY
        )
        if node.total_mass <= 0.0 and FLAG_ZERO_MASS not in node.flags:
            node.flags.append(FLAG_ZERO_MASS)

    def occurrence(self, occ: Any, parent: MassNode) -> MassNode:
        comp = getattr(occ, "component", None)
        node = MassNode(id=self.new_id(), name=occ.name, kind=KIND_OCCURRENCE,
                        path=parent.path + "/" + occ.name, depth=parent.depth + 1,
                        component_key=str(getattr(comp, "id", None) or getattr(comp, "name", occ.name)))
        self.entities[node.id] = occ
        # Occurrence.bRepBodies returns body proxies in the assembly context,
        # childOccurrences returns occurrence proxies: each instance is walked.
        self.fill(node, getattr(occ, "bRepBodies", None), getattr(occ, "childOccurrences", None))
        return node


def build_tree(root_component: Any, ignore_hidden: bool = False,
               include_bodies: bool = True) -> AnalysisResult:
    """Walk rootComponent -> occurrences -> childOccurrences (unbounded depth)."""
    b = _Builder(ignore_hidden, include_bodies)
    root = MassNode(id=b.new_id(), name=root_component.name, kind=KIND_ROOT,
                    path=root_component.name, depth=0,
                    component_key=str(getattr(root_component, "id", root_component.name)))
    b.entities[root.id] = None
    b.fill(root, getattr(root_component, "bRepBodies", None),
           getattr(root_component, "occurrences", None))
    return AnalysisResult(root=root, entities=b.entities, skipped_hidden=b.skipped_hidden)


# --------------------------------------------------------------------------
# Formatting / export
# --------------------------------------------------------------------------

def format_mass(kg: float, decimals: int = 3) -> str:
    """Human readable mass: kg when >= 1 kg, g otherwise."""
    if kg is None:
        return "-"
    if abs(kg) >= 1.0:
        return "%.*f kg" % (decimals, kg)
    grams = kg * 1000.0
    if abs(grams) >= 100:
        return "%.1f g" % grams
    return "%.*f g" % (decimals if abs(grams) < 10 else 2, grams)


CSV_HEADER = ["Niveau", "Type", "Nom", "Chemin", "Masse propre (kg)",
              "Masse cumulée (kg)", "% du total", "Alertes"]


def to_csv(result: AnalysisResult, delimiter: str = ";", decimal: str = ",") -> str:
    """Hierarchical CSV (depth-first order). Defaults suit French Excel."""
    def num(x: float, nd: int = 6) -> str:
        return ("%.*f" % (nd, x)).replace(".", decimal)

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter, lineterminator="\n")
    w.writerow(CSV_HEADER)
    total = result.total_mass
    for node in result.root.walk():
        w.writerow([node.depth, node.kind, ("  " * node.depth) + node.name, node.path,
                    num(node.own_mass), num(node.total_mass),
                    num(percent(node.total_mass, total), 2), "|".join(node.flags)])
    return buf.getvalue()


def sorted_rows(result: AnalysisResult, include_bodies: bool = True) -> List[MassNode]:
    """All non-root nodes sorted by cumulated mass, heaviest first."""
    rows = [n for n in result.root.walk() if n.kind != KIND_ROOT
            and (include_bodies or n.kind != KIND_BODY)]
    return sorted(rows, key=lambda n: n.total_mass, reverse=True)
