"""Build the leaf chamber chip as native, parametric Inventor parts and an assembly.

    python build_leaf_chamber.py                     # variants A and B, plug, glass, assemblies
    python build_leaf_chamber.py --variant A --set ch_w=0.8 --set chamber_h=1.0
    python build_leaf_chamber.py --params my_chip.json --show
    python build_leaf_chamber.py --variant A --from-ipt "INVENTOR\\PRT - 9301 - MFLEAFCHIP - V04 - A.ipt"

Writes into INVENTOR/ (CAD-new style names, provisional numbers - see leaf_chamber.NUMBERS):
  PRT - 9301 - MFLEAFCHIP - V04 - A|B.ipt   the printed chip (A: reservoir outlet, B: luer outlet)
  PRT - 9302 - MFLEAFPLUG - V04.ipt         the printed screw plug with window and O-ring groove
  BUY - Cover glass - 24 x 60 x 0.17.ipt    bottom coverslip;  BUY - Cover glass - D12 x 0.17.ipt window
  ASS - 9300 - MFLEAF - V04 - A|B.iam       chip + plug + coverslips + CAD-new O-ring, closed state
  ... .stp (AP214) copies and "<name> - params.json" (parameters, checks, build report)

Every dimension is an Inventor user parameter (open the part, fx, change e.g. ch_w, well_d,
chamber_h, in_x, th_p - the features follow, threads and luer lugs included). The script stays
the source of truth: `--from-ipt` reads parameters you changed in Inventor back in, so a
re-run keeps them. Needs the miniforge `pyinventor` env and a running Inventor; the session
helper restores SilentOperation and closes only the documents this script opened.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))
from leaf_chamber import (INV_DIR, ORING_FILE, VARIANTS, checks, make_params, names, overrides_from,  # noqa: E402
                          report)
from mfcad.inventor_helpers import (Builder, CadNew, check_provisional, close_if_open, find_open,  # noqa: E402
                                    kAssemblyDocumentObject, safe, session)
from mfcad.native_part import NativePart, export_step, kCut, kJoin, kNeg, kPos, read_user_parameters  # noqa: E402
from mfcad.params import ParamSet  # noqa: E402
from mfcad.stock_parts import coverslip_name, glass_disc, glass_plate, round_coverslip_name  # noqa: E402

BLACK = ("Low Gloss - Black", "Smooth - Black", "Satin - Black")   # Formlabs Black resin look


# ---------------------------------------------------------------------- the chip
def luer_port(part: NativePart, tag: str, xe: str) -> None:
    """Female luer lock (ISO 80369-7): slim hub on a foot, two right-hand lugs (2-start,
    5 mm lead) near the face. The 6 % taper bore is cut later (after the bosses)."""
    s = part.sketch_z("base_t", f"{tag} luer foot sketch")
    part.circle(s, xe, 0, "luer_foot_d", f"{tag} luer foot")
    part.extrude(s, "luer_top - luer_free - base_t", kPos, kJoin, f"{tag} luer foot")
    s = part.sketch_z("base_t", f"{tag} luer hub sketch")
    part.circle(s, xe, 0, "luer_hub_d", f"{tag} luer hub")
    part.extrude(s, "luer_top - base_t", kPos, kJoin, f"{tag} luer hub")
    ax = part.axis_z_at(xe, f"{tag} luer axis")
    s = part.sketch_xz(f"{tag} luer lug profile")
    part.polygon(s, [(f"{xe} + lug_r_in", "lug_zc - luer_lug_base / 2"),
                     (f"{xe} + luer_lug_d / 2", "lug_zc - luer_lug_crest / 2"),
                     (f"{xe} + luer_lug_d / 2", "lug_zc + luer_lug_crest / 2"),
                     (f"{xe} + lug_r_in", "lug_zc + luer_lug_base / 2")], f"{tag} lug")
    up = (safe(lambda: ax.Line.Direction.Z, 1.0) or 1.0) > 0
    lug = part.coil(s, ax, "luer_lead", kJoin, f"{tag} luer lug (RH, lead luer_lead)",
                    revolutions="luer_lug_rev", reverse=not up)
    part.pattern([lug], ax, "2", f"{tag} luer lugs x2")


def luer_bore(part: NativePart, tag: str, xe: str) -> None:
    """6 % taper bore, opening luer_open_d at the face, narrowing downwards."""
    p = part.ps
    r_bot = (p.luer_open_d / 2) - p.luer_depth * math.tan(math.radians(p.luer_taper_deg))
    x = part.x(xe)
    for taper in ("-luer_taper_deg", "luer_taper_deg"):          # Inventor's taper sign, settled by geometry
        s = part.sketch_z("luer_top", f"{tag} luer bore sketch")
        part.circle(s, xe, 0, "luer_open_d", f"{tag} luer bore")
        f = part.extrude(s, "luer_depth", kNeg, kCut, f"{tag} luer taper bore (6 %)", taper=taper)
        if part.circle_edges(p.luer_bot_z, r_bot, cx=x).Count:
            return
        try:
            f.Delete(False, False, False)
        except Exception:
            f.Delete()
    part.warnings.append(f"{tag} luer taper: bottom edge r = {r_bot:.3f} not found")


def build_chip(app, ps: ParamSet, variant: str, path: Path) -> dict:
    outlet = VARIANTS[variant]["outlet"]
    part = NativePart(app, ps, f"Leaf chamber chip {variant} ({VARIANTS[variant]['title']}), print in black resin")
    try:
        # slab with a glue band underneath for the bottom coverslip
        recess = ps.cs_recess > 0
        s = part.sketch_z("-cs_recess" if recess else "0", "slab sketch")
        part.rect(s, "-chip_l / 2", "-chip_w / 2", "chip_l / 2", "chip_w / 2", "slab",
                  wu="chip_l", wv="chip_w", sym_u=True, sym_v=True)
        part.extrude(s, "base_t + cs_recess" if recess else "base_t", kPos, kJoin, "slab")
        part.fillet(part.vertical_edges(ps.base_t), "corner_r", "slab corners")
        if recess:
            s = part.sketch_z("0", "glue band sketch")
            part.rect(s, "-(cs_l / 2 + cs_gap)", "-(chip_w / 2 + 1 mm)", "cs_l / 2 + cs_gap", "chip_w / 2 + 1 mm",
                      "glue band", wu="cs_l + 2 * cs_gap", wv="chip_w + 2 mm", sym_u=True, sym_v=True)
            part.extrude(s, "cs_recess + 0.5 mm", kNeg, kCut, "glue band (bottom coverslip)")
        # bosses
        s = part.sketch_z("base_t", "chamber boss sketch")
        part.circle(s, 0, 0, "boss_d", "chamber boss")
        part.extrude(s, "boss_top - base_t", kPos, kJoin, "chamber boss")
        if outlet == "reservoir":
            s = part.sketch_z("base_t", "reservoir boss sketch")
            part.circle(s, "-out_x", 0, "res_d + 2 * res_wall", "reservoir boss")
            part.extrude(s, "res_top - base_t", kPos, kJoin, "reservoir boss")
        ports = [("inlet", "in_x")] + ([("outlet", "-out_x")] if outlet == "luer" else [])
        for tag, xe in ports:
            luer_port(part, tag, xe)
        # chamber well, threaded bore, lead-ins, internal thread
        s = part.sketch_z("0", "well sketch")
        part.circle(s, 0, 0, "well_d", "well")
        part.extrude(s, "ledge_z", kPos, kCut, "chamber well")
        s = part.sketch_z("ledge_z", "thread bore sketch")
        part.circle(s, 0, 0, "th_bore_d", "thread bore")
        part.extrude(s, "boss_top - ledge_z + 1 mm", kPos, kCut, "thread bore")
        part.chamfer(part.circle_edges(ps.boss_top, ps.th_bore_d / 2), "th_chamfer", "thread lead-in")
        part.chamfer(part.circle_edges(ps.ledge_z, ps.well_d / 2), "well_chamfer", "O-ring lead-in")
        s = part.sketch_xz("internal thread groove profile")
        part.polygon(s, [("ti_r_in", "ti_zc - ti_w_in / 2"), ("th_root_d / 2", "ti_zc - ti_w_root / 2"),
                         ("th_root_d / 2", "ti_zc + ti_w_root / 2"), ("ti_r_in", "ti_zc + ti_w_in / 2")], "internal groove")
        part.coil(s, part.Z_AXIS, "th_p", kCut, f"M{ps.th_d:g}x{ps.th_p:g} internal thread (coil cut, RH)", height="ti_height")
        # outlet reservoir, luer bores
        if outlet == "reservoir":
            s = part.sketch_z("res_top", "reservoir sketch")
            part.circle(s, "-out_x", 0, "res_d", "reservoir")
            part.extrude(s, "res_top - res_floor", kNeg, kCut, "reservoir")
        for tag, xe in ports:
            luer_bore(part, tag, xe)
        # channels (open grooves on the glue face when ch_floor = 0) and vias into the ports
        s = part.sketch_z("ch_floor", "channel sketch")
        part.rect(s, "well_d / 4", "-ch_w / 2", "in_x", "ch_w / 2", "inlet channel", wv="ch_w", sym_v=True,
                  pin_u0="well_d / 4", pin_u1="in_x")
        part.rect(s, "-out_x", "-ch_w / 2", "-well_d / 4", "ch_w / 2", "outlet channel", wv="ch_w", sym_v=True,
                  pin_u0="-out_x", pin_u1="-well_d / 4")
        part.extrude(s, "ch_h", kPos, kCut, "channels")
        vias = [("inlet", "in_x", "luer_bot_z"),
                ("outlet", "-out_x", "res_floor" if outlet == "reservoir" else "luer_bot_z")]
        for tag, xe, top in vias:
            s = part.sketch_z("ch_floor", f"{tag} via sketch")
            part.circle(s, xe, 0, "via_d", f"{tag} via")
            part.extrude(s, f"{top} - ch_floor + 0.3 mm", kPos, kCut, f"{tag} via")
        part.set_appearance(BLACK)
        part.save(path, stl=True)
        rep = part.report()
    finally:
        part.close()
    return rep


# ---------------------------------------------------------------------- the plug
def build_plug(app, ps: ParamSet, path: Path) -> dict:
    part = NativePart(app, ps, "Leaf chamber screw plug: window D12 glued in the stub, O-ring 12 x 1.5 on the stub")
    try:
        s = part.sketch_z("boss_top + head_gap", "head sketch")
        part.circle(s, 0, 0, "head_d", "head")
        part.extrude(s, "head_h", kPos, kJoin, "head")
        s = part.sketch_z("ledge_z", "thread body sketch")
        part.circle(s, 0, 0, "th_ext_d", "thread body")
        part.extrude(s, "boss_top + head_gap - ledge_z", kPos, kJoin, "thread body")
        s = part.sketch_z("chamber_h", "stub sketch")
        part.circle(s, 0, 0, "stub_d", "stub")
        part.extrude(s, "ledge_z - chamber_h", kPos, kJoin, "stub")
        s = part.sketch_z("ledge_z", "thread relief sketch")
        part.circle(s, 0, 0, "th_ext_d + 1 mm", "relief outer")
        part.circle(s, 0, 0, "relief_d", "relief")
        part.extrude(s, "th_p", kPos, kCut, "thread relief")
        s = part.sketch_z("groove_z0", "O-ring groove sketch")
        part.circle(s, 0, 0, "stub_d + 1 mm", "groove outer")
        part.circle(s, 0, 0, "groove_root_d", "groove root")
        part.extrude(s, "groove_w", kPos, kCut, "O-ring groove")
        part.chamfer(part.circle_edges(ps.plug_top, ps.head_d / 2), "0.5 mm", "head chamfer")
        part.chamfer(part.circle_edges(ps.chamber_h, ps.stub_d / 2), "0.3 mm", "stub lead-in")
        s = part.sketch_xz("external thread groove profile")
        part.polygon(s, [("th_ext_root_d / 2", "te_zc - te_w_root / 2"), ("te_r_out", "te_zc - te_w_out / 2"),
                         ("te_r_out", "te_zc + te_w_out / 2"), ("th_ext_root_d / 2", "te_zc + te_w_root / 2")], "external groove")
        part.coil(s, part.Z_AXIS, "th_p", kCut, f"M{ps.th_d:g}x{ps.th_p:g} external thread (coil cut, RH)", height="te_height")
        s = part.sketch_z("plug_top", "aperture sketch")
        part.circle(s, 0, 0, "win_ap", "aperture")
        part.extrude_through(s, kNeg, "window aperture")
        s = part.sketch_z("chamber_h", "window recess sketch")
        part.circle(s, 0, 0, "win_rec_d", "window recess")
        part.extrude(s, "win_rec_h", kPos, kCut, "window recess")
        s = part.sketch_z("plug_top", "grip flute sketch")
        part.circle(s, "head_d / 2", 0, "flute_d", "grip flute")
        flute = part.extrude(s, "head_h + 0.5 mm", kNeg, kCut, "grip flute")
        part.pattern([flute], part.Z_AXIS, "flute_n", "grip flutes")
        part.set_appearance(BLACK)
        part.save(path, stl=True)
        rep = part.report()
    finally:
        part.close()
    return rep


# ---------------------------------------------------------------------- assembly
def interference(app, cd, a, b) -> float:
    """Interference volume (mm3) between two occurrences."""
    s1 = app.TransientObjects.CreateObjectCollection()
    s2 = app.TransientObjects.CreateObjectCollection()
    s1.Add(a)
    s2.Add(b)
    res = cd.AnalyzeInterference(s1, s2)
    return round(sum(safe(lambda: r.Volume, 0.0) for r in res) * 1000, 3)


def build_assembly(app, ps: ParamSet, variant: str, files: dict, path: Path) -> dict:
    if path.exists():
        doc = app.Documents.Open(str(path), False)
    else:
        doc = app.Documents.Add(kAssemblyDocumentObject, app.FileManager.GetTemplateFile(kAssemblyDocumentObject), False)
        doc.SaveAs(str(path), False)
    rep = {}
    try:
        b = Builder(app, doc)
        occ = {"chip": b.place("chip", files["chip"], (0, 0, 0)),
               "plug": b.place("plug", files["plug"], (0, 0, 0)),
               "coverslip": b.place("bottom coverslip", files["coverslip"], (0, 0, 0)),
               "window": b.place("window", files["window"], (0, 0, ps.chamber_h))}
        if files.get("oring"):
            # CAD-new O-ring: axis along its Y, cross-section between y = 0 and cs -> part Y onto model Z
            zc = ps.groove_z0 + ps.groove_w / 2
            occ["oring"] = b.place("O-ring", files["oring"], (0, 0, zc - ps.oring_cs / 2),
                                   cols=((1, 0, 0), (0, 0, 1), (0, -1, 0)))
        removed = b.prune()
        doc.Update()
        doc.Save()
        cd = doc.ComponentDefinition
        pairs = [("chip", "plug"), ("chip", "coverslip"), ("chip", "window"), ("plug", "window"),
                 ("plug", "coverslip"), ("chip", "oring"), ("plug", "oring")]
        rep["interference_mm3"] = {f"{a}/{c}": interference(app, cd, occ[a], occ[c]) for a, c in pairs
                                   if a in occ and c in occ}
        rep["occurrences"] = b.placed
        rep["removed"] = removed
        export_step(app, doc, path.with_suffix(".stp"))
    finally:
        doc.Close(True)
    return rep


# ---------------------------------------------------------------------- main
def build(app, variants: list[str], overrides: dict, do_assembly: bool, lib: CadNew | None) -> dict:
    INV_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    stock_done: dict[str, Path] = {}
    plug_done: Path | None = None
    for v in variants:
        ps = make_params(v, overrides)
        nm = names(v)
        if report(ps, v):
            raise SystemExit(f"variant {v}: design checks failed (see [error] above) - fix the parameters")
        if lib is not None:
            clash = check_provisional(lib, nm.values())
            if clash:
                print(f"  [warn] provisional numbers already used in CAD-new: {clash}")
        asm = INV_DIR / f"{nm['assembly']}.iam"
        reopen = close_if_open(app, asm)
        files = {"chip": INV_DIR / f"{nm['chip']}.ipt", "plug": INV_DIR / f"{nm['plug']}.ipt",
                 "coverslip": INV_DIR / f"{coverslip_name(ps.cs_l, ps.cs_w, ps.cs_t)}.ipt",
                 "window": INV_DIR / f"{round_coverslip_name(ps.win_d, ps.win_t)}.ipt"}
        rep = {"variant": v, "outlet": VARIANTS[v]["outlet"], "names": nm}
        print(f"\n== chip {files['chip'].name}")
        rep["chip"] = build_chip(app, ps, v, files["chip"])
        print(json.dumps(rep["chip"], indent=1))
        if plug_done is None:
            print(f"== plug {files['plug'].name}")
            rep["plug"] = build_plug(app, ps, files["plug"])
            print(json.dumps(rep["plug"], indent=1))
            plug_done = files["plug"]
        for key, fn, args in (("coverslip", glass_plate, (ps.cs_l, ps.cs_w, ps.cs_t)),
                              ("window", glass_disc, (ps.win_d, ps.win_t))):
            if files[key].name not in stock_done:
                close_if_open(app, files[key])
                rep[key] = fn(app, files[key], *args)
                stock_done[files[key].name] = files[key]
        if lib is not None:
            try:
                files["oring"] = lib.find(ORING_FILE.format(id=ps.oring_id, cs=ps.oring_cs))
            except (FileNotFoundError, LookupError) as exc:
                print(f"  [warn] {exc} - assembly without O-ring")
        if do_assembly:
            print(f"== assembly {nm['assembly']}.iam")
            rep["assembly"] = build_assembly(app, ps, v, files, asm)
            print(json.dumps(rep["assembly"]["interference_mm3"], indent=1))
            if reopen:
                safe(lambda: app.Documents.Open(str(asm), True))
        rep["checks"] = checks(ps, VARIANTS[v]["outlet"])
        rep["overrides"] = ps.overrides_vs(make_params(v))
        ps.dump(INV_DIR / f"{nm['chip']} - params.json", rep)
        out[v] = rep
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--variant", choices=sorted(VARIANTS) + ["all"], default="all")
    ap.add_argument("--set", action="append", metavar="NAME=VALUE", help="override a parameter (repeatable)")
    ap.add_argument("--params", help="JSON file {name: value} with overrides")
    ap.add_argument("--from-ipt", help="take the base parameters of a generated .ipt (edits made in Inventor)")
    ap.add_argument("--no-assembly", action="store_true")
    ap.add_argument("--show", action="store_true", help="open the assembly in Inventor afterwards")
    a = ap.parse_args()
    variants = sorted(VARIANTS) if a.variant == "all" else [a.variant]
    try:
        lib = CadNew()
    except FileNotFoundError as exc:
        print(f"[warn] {exc}")
        lib = None
    with session() as app:
        overrides = {}
        if a.from_ipt:
            known = make_params(variants[0]).items
            overrides.update({k: v for k, v in read_user_parameters(app, Path(a.from_ipt)).items()
                              if k in known and not known[k].derived})
        overrides.update(overrides_from(a))
        build(app, variants, overrides, not a.no_assembly, lib)
        if a.show:
            for v in variants:
                asm = INV_DIR / f"{names(v)['assembly']}.iam"
                if asm.exists() and find_open(app, asm) is None:
                    safe(lambda: app.Documents.Open(str(asm), True))


if __name__ == "__main__":
    main()
