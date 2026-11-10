import csv
import io

import pytest

from massanalyzer import heat
from massanalyzer.mass_tree import (
    FLAG_ERROR, FLAG_NO_MATERIAL, FLAG_NOT_SOLID, FLAG_ZERO_MASS, KIND_BODY,
    KIND_OCCURRENCE, KIND_ROOT, build_tree, format_mass, iter_collection,
    sorted_rows, to_csv,
)
from tests.fusion_mocks import Body, Collection, Component, Occurrence


def find(result, name):
    return next(n for n in result.root.walk() if n.name == name)


@pytest.fixture
def assembly():
    """
    Robot (root)                       bodies: Chassis 10 kg
      Wheel:1, Wheel:2, Wheel:3, Wheel:4  bodies: Rim 1.5 kg, Tyre 0.5 kg
      Arm:1                               bodies: Base 2 kg
        Gripper:1                         bodies: Jaw 0.25 kg
          Screw:1, Screw:2                bodies: Screw 0.002 kg
      Label:1                             bodies: Sticker (surface, 0 kg, no material)
      Hidden:1 (hidden)                   bodies: Ghost 100 kg
    """
    screw = Component("Screw", [Body("Screw", 0.002)])
    gripper = Component("Gripper", [Body("Jaw", 0.25)])
    arm = Component("Arm", [Body("Base", 2.0)])
    wheel = Component("Wheel", [Body("Rim", 1.5), Body("Tyre", 0.5)])
    label = Component("Label", [Body("Sticker", 0.0, material=None, solid=False)])
    ghost = Component("Hidden", [Body("Ghost", 100.0)])

    gripper_occ = Occurrence(gripper, children=[Occurrence(screw, 1), Occurrence(screw, 2)])
    arm_occ = Occurrence(arm, children=[gripper_occ])
    root = Component(
        "Robot",
        bodies=[Body("Chassis", 10.0)],
        occurrences=[Occurrence(wheel, i) for i in range(1, 5)]
        + [arm_occ, Occurrence(label), Occurrence(ghost, visible=False)],
    )
    return root, {"wheel": wheel, "screw": screw}


def test_iter_collection_supports_fusion_style_and_iterables():
    assert list(iter_collection(Collection([1, 2, 3]))) == [1, 2, 3]
    assert list(iter_collection([4, 5])) == [4, 5]
    assert list(iter_collection(None)) == []


def test_total_mass_includes_all_instances_and_hidden_by_default(assembly):
    root, _ = assembly
    r = build_tree(root)
    # 10 + 4*2 + (2 + 0.25 + 2*0.002) + 0 + 100
    assert r.total_mass == pytest.approx(120.254)
    assert r.root.kind == KIND_ROOT
    assert r.root.own_mass == pytest.approx(10.0)


def test_ignore_hidden_skips_hidden_subtrees(assembly):
    root, _ = assembly
    r = build_tree(root, ignore_hidden=True)
    assert r.total_mass == pytest.approx(20.254)
    assert r.skipped_hidden == 1
    assert all(n.name != "Hidden:1" for n in r.root.walk())


def test_ignore_hidden_skips_hidden_bodies():
    comp = Component("P", [Body("A", 1.0), Body("B", 2.0, visible=False)])
    root = Component("Root", occurrences=[Occurrence(comp)])
    assert build_tree(root).total_mass == pytest.approx(3.0)
    assert build_tree(root, ignore_hidden=True).total_mass == pytest.approx(1.0)


def test_hierarchical_aggregation(assembly):
    root, _ = assembly
    r = build_tree(root)
    gripper = find(r, "Gripper:1")
    arm = find(r, "Arm:1")
    assert gripper.own_mass == pytest.approx(0.25)
    assert gripper.total_mass == pytest.approx(0.254)
    assert arm.own_mass == pytest.approx(2.0)
    assert arm.total_mass == pytest.approx(2.254)
    assert find(r, "Screw:2").path == "Robot/Arm:1/Gripper:1/Screw:2"
    assert find(r, "Screw:2").depth == 3
    # every occurrence: total == own + sum(child occurrence totals)
    for n in r.root.walk():
        if n.kind != KIND_BODY:
            subs = sum(c.total_mass for c in n.children if c.kind != KIND_BODY)
            assert n.total_mass == pytest.approx(n.own_mass + subs)


def test_unbounded_depth():
    leaf = Component("Leaf", [Body("b", 0.001)])
    occ = Occurrence(leaf)
    for depth in range(60):
        occ = Occurrence(Component("L%d" % depth), children=[occ])
    r = build_tree(Component("Root", occurrences=[occ]))
    assert r.total_mass == pytest.approx(0.001)
    assert max(n.depth for n in r.root.walk()) == 62  # 61 occurrences + body


def test_multiple_instances_counted_and_mass_cached(assembly):
    root, comps = assembly
    r = build_tree(root)
    wheels = [n for n in r.root.walk() if n.name.startswith("Wheel:")]
    assert len(wheels) == 4
    assert all(w.total_mass == pytest.approx(2.0) for w in wheels)
    # physicalProperties evaluated once per native body despite 4 instances
    assert all(b.reads == 1 for b in comps["wheel"].bodies)
    summary = {c["component"]: c for c in r.component_summary()}
    assert summary["Wheel"]["instances"] == 4
    assert summary["Wheel"]["unit"] == pytest.approx(2.0)
    assert summary["Wheel"]["total"] == pytest.approx(8.0)
    assert summary["Screw"]["instances"] == 2


def test_each_instance_has_its_own_entity(assembly):
    root, _ = assembly
    r = build_tree(root)
    wheels = [n for n in r.root.walk() if n.name.startswith("Wheel:")]
    assert len({id(r.entities[w.id]) for w in wheels}) == 4
    assert r.entities[r.root.id] is None


def test_flags_no_material_zero_mass_not_solid(assembly):
    root, _ = assembly
    r = build_tree(root)
    sticker = find(r, "Sticker")
    assert {FLAG_NO_MATERIAL, FLAG_ZERO_MASS, FLAG_NOT_SOLID} <= set(sticker.flags)
    label = find(r, "Label:1")
    assert FLAG_ZERO_MASS in label.flags and FLAG_NO_MATERIAL in label.flags
    assert find(r, "Wheel:1").flags == []
    flagged = {w["name"] for w in r.warnings()}
    assert flagged == {"Label:1", "Sticker"}


def test_mass_errors_are_flagged_not_raised():
    comp = Component("Bad", [Body("Broken", RuntimeError("boom"))])
    r = build_tree(Component("Root", occurrences=[Occurrence(comp)]))
    assert FLAG_ERROR in find(r, "Broken").flags
    assert r.total_mass == 0.0


def test_include_bodies_false_keeps_masses(assembly):
    root, _ = assembly
    r = build_tree(root, include_bodies=False)
    assert r.total_mass == pytest.approx(120.254)
    assert all(n.kind != KIND_BODY for n in r.root.walk())


def test_payload_percentages(assembly):
    root, _ = assembly
    p = build_tree(root, ignore_hidden=True).to_payload()
    assert p["tree"]["pct"] == pytest.approx(100.0)
    arm = next(c for c in p["tree"]["children"] if c["name"] == "Arm:1")
    assert arm["pct"] == pytest.approx(100 * 2.254 / 20.254)
    assert arm["children"][1]["pct"] == pytest.approx(100 * 0.254 / 20.254)


def test_sorted_rows(assembly):
    root, _ = assembly
    r = build_tree(root)
    rows = sorted_rows(r)
    assert rows[0].name == "Hidden:1"
    masses = [n.total_mass for n in rows]
    assert masses == sorted(masses, reverse=True)
    assert all(n.kind == KIND_OCCURRENCE for n in sorted_rows(r, include_bodies=False))


@pytest.mark.parametrize("kg,expected", [
    (12.5, "12.500 kg"), (1.0, "1.000 kg"), (0.5, "500.0 g"),
    (0.0125, "12.50 g"), (0.002, "2.000 g"), (0.0, "0.000 g"),
])
def test_format_mass(kg, expected):
    assert format_mass(kg) == expected


def test_csv_export(assembly):
    root, _ = assembly
    r = build_tree(root, ignore_hidden=True)
    rows = list(csv.reader(io.StringIO(to_csv(r)), delimiter=";"))
    assert rows[0][0] == "Niveau"
    assert len(rows) == 1 + sum(1 for _ in r.root.walk())
    root_row = rows[1]
    assert root_row[2] == "Robot" and root_row[5] == "20,254000" and root_row[6] == "100,00"
    screw = next(row for row in rows if row[3].endswith("Screw:2"))
    assert screw[0] == "3" and screw[2] == "      Screw:2"
    std = list(csv.reader(io.StringIO(to_csv(r, delimiter=",", decimal="."))))
    assert std[1][5] == "20.254000"


# ---------------------------------------------------------------- heatmap

def test_heat_targets_leaf_parts_and_root_bodies(assembly):
    root, _ = assembly
    r = build_tree(root)
    targets = {(k, n.name) for k, n in heat.heat_targets(r)}
    # leaf occurrences colored as a whole
    for name in ["Wheel:1", "Wheel:4", "Screw:1", "Screw:2", "Label:1", "Hidden:1"]:
        assert (heat.TARGET_OCCURRENCE, name) in targets
    # bodies of the root and of non-leaf occurrences colored individually
    for name in ["Chassis", "Base", "Jaw"]:
        assert (heat.TARGET_BODY, name) in targets
    # bodies of leaf occurrences are not targeted separately
    assert (heat.TARGET_BODY, "Rim") not in targets
    assert (heat.TARGET_OCCURRENCE, "Arm:1") not in targets


def test_normalize_linear_and_log():
    assert heat.normalize(5, 0, 10) == pytest.approx(0.5)
    assert heat.normalize(-1, 0, 10) == 0.0
    assert heat.normalize(100, 1, 1000, log_scale=True) == pytest.approx(2 / 3)
    assert heat.normalize(3, 3, 3) == 1.0
    assert heat.normalize(0, 0, 10, log_scale=True) == 0.0


def test_buckets_and_colors():
    assert heat.bucket(0.0) == 0
    assert heat.bucket(1.0) == heat.BUCKETS - 1
    assert heat.bucket(0.55) == 5
    assert heat.color_at(1.0) == (215, 30, 30)  # heaviest = red
    assert heat.color_at(0.0) == (150, 160, 175)  # lightest = grey-blue
    red_heavy = heat.bucket_color(heat.BUCKETS - 1)
    assert red_heavy[0] > red_heavy[2]
    assert heat.appearance_name(3) == "MassHeat_03"


def test_assign_buckets_heaviest_is_last(assembly):
    root, _ = assembly
    r = build_tree(root, ignore_hidden=True)
    for log in (False, True):
        assigned = heat.assign_buckets(heat.heat_targets(r), log_scale=log)
        by_name = {n.name: b for _, n, b in assigned}
        assert by_name["Chassis"] == heat.BUCKETS - 1
        assert by_name["Label:1"] == 0
        assert by_name["Wheel:1"] >= by_name["Screw:1"]
    # log scale spreads small parts more than linear
    lin = {n.name: b for _, n, b in heat.assign_buckets(heat.heat_targets(r))}
    log = {n.name: b for _, n, b in heat.assign_buckets(heat.heat_targets(r), log_scale=True)}
    assert log["Jaw"] > lin["Jaw"]
