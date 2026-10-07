"""Check the generated leaf chamber on its STEP exports (independent of Inventor) and draw figures.

    uv run --with cadquery --with trimesh --with rtree --with scipy --with shapely --with networkx ^
        --with matplotlib python check_leaf_chamber.py [--variant A|B|all]

Reads INVENTOR/<chip> - params.json and the part STEPs written by build_leaf_chamber.py.
Checks (by point sampling in the mesh domain - Inventor STEPs break OCC booleans):
  - internal thread, plug thread and luer lugs are RIGHT-handed (a standard male luer locks on),
  - plug ridges sit in the boss grooves (sampled overlap of the closed state is ~0),
  - fluid path: channel centre line, vias, luer bores / reservoir are open; the channel walls
    are closed; every open channel lies on the bottom coverslip.
Figures: docs/section_<v>.png (XZ section through chamber and ports), docs/plan_<v>.png (plan
sections), docs/exploded_<v>.png (assembly order).
CadQuery processes on this machine segfault at interpreter exit, hence os._exit at the end.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import trimesh

HERE = Path(__file__).resolve().parent
INV = HERE / "INVENTOR"
DOCS = HERE / "docs"
sys.path.insert(0, str(HERE))
from leaf_chamber import VARIANTS, names  # noqa: E402


def load_mesh(step: Path, tol: float = 0.02) -> trimesh.Trimesh:
    import cadquery as cq
    shape = cq.importers.importStep(str(step)).val()
    v, f = shape.tessellate(tol, 0.2)
    return trimesh.Trimesh(np.array([(p.x, p.y, p.z) for p in v]), np.array(f), process=True)


def inside(mesh: trimesh.Trimesh, pts) -> np.ndarray:
    return mesh.contains(np.atleast_2d(np.asarray(pts, float)))


def transformed(mesh, R=np.eye(3), t=(0, 0, 0)):
    m = mesh.copy()
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = t
    m.apply_transform(T)
    return m


def check(variant: str, render: bool = True) -> list[tuple[bool, str]]:
    nm = names(variant)
    prm = json.loads((INV / f"{nm['chip']} - params.json").read_text(encoding="utf-8"))
    p = {r["name"]: r["value"] for r in prm["parameters"]}
    outlet = VARIANTS[variant]["outlet"]
    chip = load_mesh(INV / f"{nm['chip']}.stp")
    plug = load_mesh(INV / f"{nm['plug']}.stp")
    res: list[tuple[bool, str]] = []

    def ok(cond, msg):
        res.append((bool(cond), msg))

    P = p["th_p"]
    q = P / 4                                        # a quarter turn of a right-hand helix rises P/4
    # internal thread: groove centre at +X is ti_zc + k P; at +Y (90 deg CCW seen from +Z) RH -> + P/4
    r_i = (p["th_bore_d"] / 2 + p["th_root_d"] / 2) / 2
    zc = p["ti_zc"] + P
    rh = inside(chip, [(0, r_i, zc + q)])[0]          # True = material
    lh = inside(chip, [(0, r_i, zc - q)])[0]
    ok(not rh and lh, f"internal thread right-handed (groove at +Y, z = {zc + q:.2f})")
    # plug thread: groove centre at +X is te_zc + k P
    r_e = (p["th_ext_d"] / 2 + p["th_ext_root_d"] / 2) / 2
    ze = p["te_zc"] + 2 * P
    rh = inside(plug, [(0, r_e, ze + q)])[0]
    lh = inside(plug, [(0, r_e, ze - q)])[0]
    ok(not rh and lh, f"plug thread right-handed (groove at +Y, z = {ze + q:.2f})")
    # phasing: sample the overlap shell between plug crest and boss bore over the engaged length
    zs = np.linspace(p["ledge_z"] + P + 0.1, p["boss_top"] - p["th_chamfer"] - 0.1, 60)
    th = np.linspace(0, 2 * math.pi, 72, endpoint=False)
    rr = np.linspace(p["th_bore_d"] / 2 + 0.02, p["th_ext_d"] / 2 - 0.02, 4)
    pts = np.array([(r * math.cos(t), r * math.sin(t), z) for z in zs for t in th for r in rr])
    both = inside(chip, pts) & inside(plug, pts)
    ok(both.mean() < 0.002, f"plug ridges sit in the boss grooves (overlap {both.mean():.2%} of {len(pts)} samples)")
    # luer lugs: lug 1 starts at +X at lug_zc and rises lead * rev over the turn; RH -> at 45 deg z rises lead/8
    ports = [("inlet", p["in_x"])] + ([("outlet", -p["out_x"])] if outlet == "luer" else [])
    r_l = (p["luer_hub_d"] / 2 + p["luer_lug_d"] / 2) / 2
    for tag, x in ports:
        c45, s45 = math.cos(math.pi / 4), math.sin(math.pi / 4)
        z_rh = p["lug_zc"] + p["luer_lead"] / 8
        z_lh = p["lug_zc"] + p["luer_lead"] * (p["luer_lug_rev"] - 1 / 8)
        a = inside(chip, [(x + r_l * c45, r_l * s45, z_rh)])[0]
        b = inside(chip, [(x + r_l * c45, r_l * s45, z_lh)])[0]
        ok(a and not b, f"{tag} luer lugs right-handed (lug at 45 deg, z = {z_rh:.2f})")
        bore = [(x, 0, z) for z in np.linspace(p["luer_bot_z"] + 0.1, p["luer_top"] - 0.1, 12)]
        ok(not inside(chip, bore).any(), f"{tag} luer bore open")
        r_top = p["luer_open_d"] / 2
        ok(inside(chip, [(x + r_top + 0.3, 0, p["luer_top"] - 0.5)])[0], f"{tag} luer bore wall closed")
    # fluid path
    zch = p["ch_floor"] + p["ch_h"] / 2
    line = [(x, 0, zch) for x in np.linspace(-p["out_x"], p["in_x"], 120)]
    ok(not inside(chip, line).any(), "channel centre line open from outlet to inlet")
    side = [(x, p["ch_w"] / 2 + 0.25, zch) for x in np.linspace(p["well_d"] / 2 + 0.5, p["in_x"] - 1.0, 30)]
    ok(inside(chip, side).all(), "inlet channel side wall closed")
    for tag, x, top in (("inlet", p["in_x"], p["luer_bot_z"]),
                        ("outlet", -p["out_x"], p["res_floor"] if outlet == "reservoir" else p["luer_bot_z"])):
        via = [(x, 0, z) for z in np.linspace(zch, top + 0.1, 15)]
        ok(not inside(chip, via).any(), f"{tag} via open up to z = {top:g}")
    if p["ch_floor"] == 0:
        far = max(p["in_x"], p["out_x"]) + p["via_d"] / 2
        ok(far <= p["cs_l"] / 2 - 2 and p["ch_w"] / 2 < p["cs_w"] / 2 - 3, "open channels and vias lie on the bottom coverslip")
    # closed state: chamber between coverslip and window, O-ring on the bore
    plug_bottom = inside(plug, [(0, 0, p["chamber_h"] + 0.05), (p["stub_d"] / 2 - 0.3, 0, p["chamber_h"] + 0.05)])
    if p.get("plug_window", 1) > 0.5:
        ok(not plug_bottom[0] and plug_bottom[1], "window recess open in the middle, stub wall around it")
    else:
        ok(plug_bottom.all(), "solid plug: stub closed over the chamber")
    ok(not inside(chip, [(0, 0, p["chamber_h"] / 2), (p["well_d"] / 2 - 0.2, 0, p["chamber_h"] / 2)]).any(), "chamber open")

    if render:
        draw(variant, p, outlet, chip, plug)
    return res


def draw(variant, p, outlet, chip, plug):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon as MPoly

    cs = trimesh.creation.box((p["cs_l"], p["cs_w"], p["cs_t"]))
    cs.apply_translation((0, 0, -p["cs_t"] / 2))
    win = trimesh.creation.cylinder(radius=p["win_d"] / 2, height=p["win_t"], sections=96)
    win.apply_translation((0, 0, p["chamber_h"] + p["win_t"] / 2))
    rc = (p["oring_id"] + p["oring_cs"]) / 2
    oring = trimesh.creation.torus(rc, p["oring_cs"] / 2, major_sections=96, minor_sections=24)
    oring.apply_translation((0, 0, p["groove_z0"] + p["groove_w"] / 2))
    parts = [(chip, "#2b2b2b", "chip (black resin)"), (plug, "#5a5a6e", "plug"), (cs, "#7fc8e8", "coverslip"),
             (win, "#7fc8e8", "window"), (oring, "#c0392b", f"O-ring {p['oring_id']:g} x {p['oring_cs']:g}")]
    if p.get("plug_window", 1) < 0.5:
        parts = [x for x in parts if x[2] != "window"]

    def section(ax, origin, normal, to2d):
        for m, col, lab in parts:
            sec = m.section(plane_origin=origin, plane_normal=normal)
            if sec is None:
                continue
            planar, T = sec.to_2D(to_2D=to2d)
            for poly in planar.polygons_full:
                xy = np.array(poly.exterior.coords)
                ax.add_patch(MPoly(xy, closed=True, fc=col, ec="k", lw=0.3, label=lab))
                for hole in poly.interiors:
                    ax.add_patch(MPoly(np.array(hole.coords), closed=True, fc="white", ec="k", lw=0.3))
                lab = None
        ax.set_aspect("equal")
        ax.autoscale_view()

    DOCS.mkdir(exist_ok=True)
    # XZ section through chamber and ports (y = 0), seen from -Y: x to the right, z up
    to_xz = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], float)
    fig, axs = plt.subplots(2, 1, figsize=(13, 7.5), gridspec_kw={"height_ratios": [1.15, 1]})
    section(axs[0], (0, 0, 0), (0, 1, 0), to_xz)
    axs[0].set_title(f"Leaf chamber {variant}: section y = 0 (closed, plug screwed home) - {VARIANTS[variant]['title']}")
    axs[0].set_xlabel("x [mm]  (inlet luer at +x)")
    axs[0].set_ylabel("z [mm]")
    axs[0].legend(loc="upper left", fontsize=7, ncol=5)
    section(axs[1], (0, 0, 0), (0, 1, 0), to_xz)
    axs[1].set_xlim(-12.5, 12.5)
    axs[1].set_ylim(-0.6, p["plug_top"] + 0.4)
    plug_txt = f"window D{p['win_d']:g} in the stub" if p.get("plug_window", 1) > 0.5 else "solid plug"
    axs[1].set_title(f"detail: chamber {p['well_d']:g} x {p['chamber_h']:g} mm, channel {p['ch_w']:g} x {p['ch_h']:g} mm, {plug_txt}, "
                     f"O-ring on the stub, M{p['th_d']:g}x{p['th_p']:g} thread, stop face at z = {p['ledge_z']:g}", fontsize=9)
    for ax in axs:
        ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(DOCS / f"section_{variant}.png", dpi=150)
    plt.close(fig)
    # plan view: sections at the channel height and above the slab
    to_xy = np.eye(4)
    fig, axs = plt.subplots(2, 1, figsize=(11, 7))
    zch = p["ch_floor"] + p["ch_h"] / 2
    for ax, z, title in ((axs[0], zch, f"plan at z = {zch:g} (channel level, seen from above)"),
                         (axs[1], p["base_t"] + 1.5, f"plan at z = {p['base_t'] + 1.5:g} (bosses, ports)")):
        for m, col, lab in parts[:2]:
            sec = m.section(plane_origin=(0, 0, z), plane_normal=(0, 0, 1))
            if sec is None:
                continue
            planar, _ = sec.to_2D(to_2D=to_xy)
            for poly in planar.polygons_full:
                ax.add_patch(MPoly(np.array(poly.exterior.coords), closed=True, fc=col, ec="k", lw=0.3))
                for hole in poly.interiors:
                    ax.add_patch(MPoly(np.array(hole.coords), closed=True, fc="white", ec="k", lw=0.3))
        ax.add_patch(MPoly(np.array([(-p["cs_l"] / 2, -p["cs_w"] / 2), (p["cs_l"] / 2, -p["cs_w"] / 2),
                                     (p["cs_l"] / 2, p["cs_w"] / 2), (-p["cs_l"] / 2, p["cs_w"] / 2)]),
                           closed=True, fill=False, ec="#2e86c1", lw=1.0, ls="--"))
        ax.set_title(title + "; dashed: bottom coverslip", fontsize=9)
        ax.set_aspect("equal")
        ax.set_xlim(-p["chip_l"] / 2 - 1, p["chip_l"] / 2 + 1)
        ax.set_ylim(-p["chip_w"] / 2 - 1, p["chip_w"] / 2 + 1)
        ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(DOCS / f"plan_{variant}.png", dpi=150)
    plt.close(fig)
    # exploded view in assembly order: coverslip, chip, window, O-ring, plug
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    lift = {"coverslip": -8.0, "window": 12.0, f"O-ring {p['oring_id']:g} x {p['oring_cs']:g}": 16.0, "plug": 20.0}
    light = np.array([-0.4, -0.7, 0.9])
    light /= np.linalg.norm(light)
    fig = plt.figure(figsize=(11, 7.5))
    ax = fig.add_subplot(111, projection="3d")
    for m, col, lab in parts:
        key = lab.split(" (")[0]
        mm = m.copy()
        mm.apply_translation((0, 0, lift.get(key, 0.0)))
        shade = 0.45 + 0.55 * np.clip(mm.face_normals @ light, 0, 1)
        rgb = np.array(matplotlib.colors.to_rgb(col))
        fc = np.clip(np.outer(shade, rgb) + (0.12 if col == "#2b2b2b" else 0.0), 0, 1)
        ax.add_collection3d(Poly3DCollection(mm.triangles, facecolors=fc, edgecolors="none",
                                             alpha=0.55 if col == "#7fc8e8" else 1.0))
    ax.set_xlim(-38, 38)
    ax.set_ylim(-20, 20)
    ax.set_zlim(-10, 34)
    ax.set_box_aspect((76, 40, 44))
    ax.view_init(elev=24, azim=-58)
    ax.set_axis_off()
    ax.set_title(f"Leaf chamber {variant} - exploded: " + ", ".join(lab.split(" (")[0] for _, _, lab in parts), fontsize=10)
    fig.tight_layout()
    fig.savefig(DOCS / f"exploded_{variant}.png", dpi=130)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--variant", choices=sorted(VARIANTS) + ["all"], default="B")
    ap.add_argument("--no-render", action="store_true")
    a = ap.parse_args()
    variants = sorted(VARIANTS) if a.variant == "all" else [a.variant]
    failed = 0
    for v in variants:
        print(f"leaf chamber {v}")
        for good, msg in check(v, not a.no_render):
            print(f"  [{'ok' if good else 'FAIL'}] {msg}")
            failed += not good
    print("all checks passed" if not failed else f"{failed} check(s) FAILED")
    sys.stdout.flush()
    os._exit(1 if failed else 0)


if __name__ == "__main__":
    main()
