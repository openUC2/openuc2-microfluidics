"""Leaf chamber chip - parameters, variants and design checks (plain Python, no Inventor).

    python leaf_chamber.py                          # variant A: table + checks
    python leaf_chamber.py --variant B --set ch_w=0.8 --set well_d=14.5
    python leaf_chamber.py --params my_chip.json    # {"ch_w": 0.8, "chamber_h": 1.0}

The same table drives build_leaf_chamber.py: every name below becomes an Inventor user
parameter of the generated parts, and every derived value is an Inventor expression, so the
.ipt can also be re-tuned in Inventor's Parameters dialog.

Frame (chip, plug, coverslips and O-ring share it - the assembly places all at identity):
  origin  = centre of the chamber on the GLUE PLANE: z = 0 is the top face of the bottom
            coverslip, i.e. the floor the leaf lies on;
  X       = along the 75 mm length, inlet luer at +X, outlet (reservoir or luer) at -X;
  Y       = across the 25 mm width;  Z = up, away from the objective (inverted microscope).

Layout (side view, through the chamber):

        plug head  ____________________                     luer face z = luer_top
                  |  aperture win_ap   |     ___
     boss_top --> |__  thread M20x1.5 _|    |   |  luer hub, 2 lugs
                  |  |              |  |    |   |
     ledge_z  --> |__|  stub + O-ring|__|  _|   |_  foot
     base_t   ---  slab ------- well_d ------------------ slab ---
     chamber_h -->     [ window glass ]
     z = 0     ========= leaf on the bottom coverslip ===== channel (open groove) == via
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from mfcad.params import D, P, ParamSet, parse_sets  # noqa: E402

CHIP_DIR = Path(__file__).resolve().parent
INV_DIR = CHIP_DIR / "INVENTOR"

# provisional CAD-new numbers (9xxx is unused in CAD-new; OPM 91xx, Raman 92xx) - Benedict assigns real ones
NUMBERS = {"assembly": 9300, "chip": 9301, "plug": 9302}
ABBREV = {"assembly": "MFLEAF", "chip": "MFLEAFCHIP", "plug": "MFLEAFPLUG"}
ORING_FILE = "ISO 3601-1 - O-ring - {id:g} x {cs:g} - NBR70.ipt"          # CAD-new STD part

VARIANTS = {
    "A": {"outlet": "reservoir", "title": "luer inlet -> chamber -> open reservoir", "set": {"out_x": 25.0}},
    "B": {"outlet": "luer", "title": "luer inlet -> chamber -> luer outlet", "set": {"out_x": 27.0}},
}


def parameters() -> list:
    return [
        # ---- slide body
        P("chip_l", 75.0, comment="slide length X (ISO 8037-1: 75-76 x 25-26 mm)"),
        P("chip_w", 25.0, comment="slide width Y"),
        P("base_t", 3.0, comment="slab top above the glue plane z = 0"),
        P("corner_r", 1.0, comment="vertical corner radius"),
        # ---- bottom coverslip (glued, the optical floor and the lid of the open channels)
        P("cs_l", 60.0, comment="bottom coverslip length X - must cover every channel and via"),
        P("cs_w", 24.0, comment="bottom coverslip width Y"),
        P("cs_t", 0.17, comment="bottom coverslip thickness (#1.5)"),
        P("cs_recess", 0.25, comment="depth of the glue band under the chip (coverslip + adhesive flush)"),
        P("cs_gap", 0.25, comment="clearance at each end of the glue band"),
        # ---- channels
        P("ch_w", 1.0, comment="channel width"),
        P("ch_h", 0.5, comment="channel depth"),
        P("ch_floor", 0.0, comment="channel floor above z = 0: 0 = open groove sealed by the coverslip"),
        P("via_d", 1.0, comment="vertical via from the channel into a port"),
        # ---- chamber
        P("well_d", 14.3, comment="chamber well diameter (leaf space; the O-ring seals on this bore)"),
        P("chamber_h", 0.8, comment="chamber height: coverslip to window glass (leaf + liquid)"),
        P("stub_len", 3.2, comment="plug stub below the stop face (window recess, wall, O-ring groove, wall)"),
        D("ledge_z", "chamber_h + stub_len", comment="top of the well = stop face of the plug"),
        P("well_chamfer", 0.6, comment="O-ring lead-in chamfer at the top of the well"),
        # ---- threaded boss
        P("boss_d", 24.0, comment="threaded boss outer diameter"),
        P("th_len", 5.0, comment="threaded length of the boss above the stop face"),
        D("boss_top", "ledge_z + th_len", comment="top of the threaded boss"),
        P("th_d", 20.0, comment="plug thread nominal diameter"),
        P("th_p", 1.5, comment="plug thread pitch"),
        P("th_clear", 0.15, comment="printing clearance per side, radial (internal +, external -)"),
        P("th_chamfer", 0.6, comment="lead-in chamfer at the top of the threaded bore"),
        P("th_start", 0.3, comment="gap between the stop face and the first internal thread groove"),
        # ---- plug
        P("stub_clear", 0.15, comment="radial clearance stub / well"),
        P("head_d", 24.0, comment="plug head diameter"),
        P("head_h", 3.0, comment="plug head height"),
        P("head_gap", 0.3, comment="head to boss top when the plug sits on the stop face"),
        P("flute_n", 16, "ul", comment="grip flutes on the head"),
        P("flute_d", 2.0, comment="grip flute diameter"),
        P("oring_id", 12.0, comment="O-ring inner diameter (CAD-new ISO 3601-1 12 x 1.5 NBR70)"),
        P("oring_cs", 1.5, comment="O-ring cross-section"),
        P("groove_root_d", 12.0, comment="O-ring groove root diameter on the stub"),
        P("groove_w", 2.1, comment="O-ring groove width (about 1.4 x cross-section)"),
        P("groove_top_gap", 0.4, comment="O-ring groove top below the stop face"),
        P("win_d", 12.0, comment="window: round coverslip glued into the stub"),
        P("win_t", 0.17, comment="window thickness"),
        P("win_fit", 0.2, comment="diametral clearance of the window recess"),
        P("win_ap", 10.0, comment="clear aperture through the plug"),
        # ---- ports: female luer lock, ISO 80369-7 / ISO 594-2
        P("in_x", 27.0, comment="inlet luer axis, distance from the chamber centre (+X)"),
        P("out_x", 25.0, comment="outlet axis (reservoir or luer), distance from the centre (-X)"),
        P("luer_top", 12.0, comment="luer face height"),
        P("luer_hub_d", 6.73, comment="hub diameter at the base of the lugs (ISO 6.73)"),
        P("luer_lug_d", 7.83, comment="diameter over the lugs (ISO 7.73-7.83)"),
        P("luer_open_d", 4.29, comment="6 % taper bore at the face (ISO 4.270-4.315)"),
        P("luer_depth", 8.0, comment="taper bore depth (male cone max 7.5)"),
        P("luer_taper_deg", 1.71836, "deg", comment="half angle of the 6 % taper, atan(0.03)"),
        P("luer_free", 6.0, comment="hub length below the face kept slim for the male collar (ISO >= 5.5)"),
        P("luer_foot_d", 10.0, comment="foot diameter under the slim hub"),
        P("luer_lead", 5.0, comment="lead of the double-start lug thread (pitch 2.5, right-hand)"),
        P("luer_lug_rev", 0.5, "ul", comment="turns per lug (2 lugs at 180 deg)"),
        P("luer_lug_base", 0.95, comment="lug width at the hub (ISO max 1.2)"),
        P("luer_lug_crest", 0.35, comment="lug width at the crest (ISO min 0.3)"),
        P("luer_lug_gap", 0.3, comment="lug top below the luer face"),
        # ---- reservoir (variant A outlet)
        P("res_d", 16.0, comment="reservoir inner diameter"),
        P("res_wall", 1.5, comment="reservoir wall"),
        P("res_floor", 1.5, comment="reservoir floor above z = 0"),
        P("res_top", 9.0, comment="reservoir rim height"),
        # ---- derived (Inventor expressions)
        D("th_bore_d", "th_d - 1.0825 * th_p + 2 * th_clear", comment="internal thread crest = bore diameter"),
        D("th_root_d", "th_d + 2 * th_clear", comment="internal thread root diameter"),
        D("th_ext_d", "th_d - 2 * th_clear", comment="plug thread crest diameter"),
        D("th_ext_root_d", "th_d - 2 * th_clear - 1.0825 * th_p", comment="plug thread root diameter"),
        D("relief_d", "th_ext_root_d - 0.2 mm", comment="plug thread relief above the stop face"),
        D("stub_d", "well_d - 2 * stub_clear", comment="plug stub diameter"),
        D("plug_top", "boss_top + head_gap + head_h", comment="top of the plug"),
        D("groove_z0", "ledge_z - groove_top_gap - groove_w", comment="O-ring groove bottom"),
        D("win_rec_d", "win_d + win_fit", comment="window recess diameter"),
        D("win_rec_h", "win_t + 0.03 mm", comment="window recess depth"),
        # internal thread groove (60 deg flanks: 2 tan 30 = 1.1547), ISO crest/root flats P/4, P/8
        D("ti_w_root", "th_p / 8", comment="internal groove width at its root"),
        D("ti_r_in", "th_bore_d / 2 - 0.1 mm", comment="internal groove profile inner edge (inside the bore)"),
        D("ti_w_in", "ti_w_root + 1.1547 * (th_root_d / 2 - ti_r_in)", comment="internal groove width at ti_r_in"),
        D("ti_zc", "ledge_z + th_start + ti_w_in / 2", comment="first internal groove centre (at +X)"),
        D("ti_height", "boss_top + th_p - ti_zc", comment="internal thread coil height"),
        # external thread groove, half a pitch off: plug ridges sit in the boss grooves when screwed home
        D("te_w_root", "th_p / 4", comment="external groove width at its root"),
        D("te_r_out", "th_ext_d / 2 + 0.1 mm", comment="external groove profile outer edge"),
        D("te_w_out", "te_w_root + 1.1547 * (te_r_out - th_ext_root_d / 2)", comment="external groove width at te_r_out"),
        D("te_zc", "ti_zc - th_p / 2", comment="first external groove centre (at +X)"),
        # the plug groove runs 0.5 mm past the boss top (its last turn runs out into the head underside):
        # it must hold the topmost boss ridge when the plug sits on the stop face, or the plug jams
        D("te_height", "boss_top + 0.5 mm - te_zc", comment="external thread coil height (runs out into the head)"),
        # luer lugs
        D("lug_r_in", "luer_hub_d / 2 - 0.1 mm", comment="lug profile inner edge (inside the hub)"),
        D("lug_zc", "luer_top - luer_lug_gap - luer_lug_base / 2 - luer_lug_rev * luer_lead", comment="lug profile centre at the coil start"),
        D("luer_bot_z", "luer_top - luer_depth", comment="bottom of the taper bore"),
    ]


def make_params(variant: str = "A", overrides: dict | None = None) -> ParamSet:
    ps = ParamSet(parameters())
    for k, v in VARIANTS[variant]["set"].items():
        ps.set(k, v)
    for k, v in (overrides or {}).items():
        ps.set(k, v)
    return ps


def names(variant: str) -> dict:
    def n(kind, suffix=""):
        return f"{kind} - {NUMBERS[key]:04d} - {ABBREV[key]} - V04{suffix}"
    out = {}
    for key, kind, suffix in (("assembly", "ASS", f" - {variant}"), ("chip", "PRT", f" - {variant}"), ("plug", "PRT", "")):
        out[key] = n(kind, suffix)
    return out


# ---------------------------------------------------------------------- checks
def checks(ps: ParamSet, outlet: str) -> list[tuple[str, str]]:
    """(level, message): level 'error' blocks the build, 'warn' is printed, 'info' is a figure."""
    out = []

    def need(ok, msg, level="error"):
        if not ok:
            out.append((level, msg))

    p = ps
    # body / coverslip
    need(p.boss_d <= p.chip_w, f"boss_d {p.boss_d} wider than the chip ({p.chip_w})")
    need(p.cs_w <= p.chip_w, f"coverslip width {p.cs_w} > chip width {p.chip_w}")
    need(p.cs_l + 2 * p.cs_gap <= p.chip_l - 6, "coverslip band leaves < 3 mm feet at the chip ends")
    need(p.cs_recess == 0 or p.cs_recess >= p.cs_t, f"cs_recess {p.cs_recess} < coverslip {p.cs_t}: the glass stands proud", "warn")
    need(p.well_d / 2 + 3 <= p.cs_w / 2, "less than 3 mm glue width beside the well")
    # open channels (ch_floor = 0) and their vias must sit on the coverslip; closed ones only need the well covered
    if p.ch_floor == 0:
        for tag, x in (("inlet", p.in_x), ("outlet", p.out_x)):
            need(x + p.via_d / 2 + 2.0 <= p.cs_l / 2, f"{tag} via at {x} mm is within 2 mm of the coverslip end ({p.cs_l / 2})")
    else:
        need(p.well_d / 2 + 3 <= p.cs_l / 2, "less than 3 mm glue length beside the well")
        need(p.ch_floor >= 0.3, "closed channel floor < 0.3 mm", "warn")
        need(p.ch_h >= 0.5 and p.ch_w >= 0.5, "closed channels below 0.5 mm are hard to clear of uncured resin", "warn")
    need(p.ch_floor + p.ch_h <= p.chamber_h, f"channel top {p.ch_floor + p.ch_h} above the chamber ceiling {p.chamber_h} - the stub would block it")
    # well, O-ring gland (static radial seal on the stub)
    gland = (p.well_d - p.groove_root_d) / 2
    squeeze = 1 - gland / p.oring_cs
    fill = (math.pi / 4 * p.oring_cs ** 2) / (gland * p.groove_w)
    stretch = p.groove_root_d / p.oring_id - 1
    need(0.15 <= squeeze <= 0.32, f"O-ring squeeze {squeeze:.0%} outside 15-32 %")
    need(fill <= 0.85, f"O-ring groove fill {fill:.0%} > 85 %")
    need(-0.03 <= stretch <= 0.06, f"O-ring stretch {stretch:+.1%} outside -3..+6 %")
    need(p.groove_z0 - (p.chamber_h + p.win_rec_h) >= 0.5, "less than 0.5 mm stub wall between window recess and O-ring groove")
    need((p.groove_root_d - p.win_ap) / 2 >= 0.8, "less than 0.8 mm between O-ring groove root and aperture")
    need((p.stub_d - p.win_rec_d) / 2 >= 0.6, f"stub wall at the window {(p.stub_d - p.win_rec_d) / 2:.2f} mm < 0.6")
    need((p.win_d - p.win_ap) / 2 >= 0.6, "window rests on less than 0.6 mm")
    need(p.groove_z0 > p.chamber_h, "O-ring groove below the stub bottom")
    # thread and stop face
    need((p.boss_d - p.th_root_d) / 2 >= 1.2, f"boss wall behind the thread {(p.boss_d - p.th_root_d) / 2:.2f} mm < 1.2")
    need((p.th_bore_d - p.well_d) / 2 - p.well_chamfer >= 0.8, "stop face (ledge) narrower than 0.8 mm")
    need(p.boss_top - p.ledge_z - p.th_start >= 3 * p.th_p, "less than 3 thread turns engaged", "warn")
    need(0.5413 * p.th_p - 2 * p.th_clear >= 0.35, "radial thread engagement < 0.35 mm")
    need(p.relief_d > p.stub_d + 1.0, "plug stop face narrower than 0.5 mm")
    # luer ports
    luer_ports = [("inlet", p.in_x)] + ([("outlet", p.out_x)] if outlet == "luer" else [])
    for tag, x in luer_ports:
        need(x + p.luer_foot_d / 2 <= p.chip_l / 2 - 2, f"{tag} luer foot within 2 mm of the chip end")
        need(x - p.luer_foot_d / 2 >= p.boss_d / 2 + 1, f"{tag} luer foot touches the chamber boss")
    need((p.luer_hub_d - p.luer_open_d) / 2 >= 1.0, "luer hub wall < 1 mm at the face")
    need(p.lug_zc - p.luer_lug_base / 2 >= p.luer_top - p.luer_free, "lugs reach below the slim hub (into the foot)")
    need(p.luer_bot_z >= p.ch_floor + p.ch_h + 1.0, "luer bore bottom less than 1 mm above the channel")
    need(p.luer_top - p.luer_free > p.base_t, "luer foot height <= 0")
    # reservoir
    if outlet == "reservoir":
        r_out = p.res_d / 2 + p.res_wall
        need(p.out_x + r_out <= p.chip_l / 2 - 2, "reservoir within 2 mm of the chip end")
        need(p.out_x - r_out >= p.boss_d / 2, "reservoir merges into the chamber boss", "warn")
        need(p.res_floor >= p.ch_floor + p.ch_h + 0.8, "reservoir floor < 0.8 mm above the channel")
    # figures
    chamber_ul = math.pi / 4 * p.well_d ** 2 * p.chamber_h
    ch_len = (p.in_x - p.well_d / 2) + (p.out_x - p.well_d / 2)
    ch_ul = ch_len * p.ch_w * p.ch_h
    out.append(("info", f"chamber volume {chamber_ul:.0f} uL (leaf space {p.well_d:g} x {p.chamber_h:g} mm), "
                        f"channels {ch_ul:.0f} uL over {ch_len:.1f} mm"))
    out.append(("info", f"O-ring {p.oring_id:g} x {p.oring_cs:g}: gland {gland:.2f} mm, squeeze {squeeze:.0%}, "
                        f"fill {fill:.0%}, stretch {stretch:+.1%}"))
    out.append(("info", f"thread M{p.th_d:g}x{p.th_p:g}: bore {p.th_bore_d:.3f}, plug crest {p.th_ext_d:.3f}, "
                        f"{(p.boss_top - p.ledge_z - p.th_start) / p.th_p:.1f} turns, radial engagement {0.5413 * p.th_p - 2 * p.th_clear:.2f} mm"))
    if outlet == "reservoir":
        out.append(("info", f"reservoir {math.pi / 4 * p.res_d ** 2 * (p.res_top - p.res_floor) / 1000:.2f} mL"))
    out.append(("info", f"heights: luer face {p.luer_top:g}, plug top {p.plug_top:g}, coverslip bottom {-p.cs_t:g} mm"))
    return [o for o in out if o[1]]


def report(ps: ParamSet, variant: str) -> int:
    outlet = VARIANTS[variant]["outlet"]
    print(f"leaf chamber {variant}: {VARIANTS[variant]['title']}")
    for row in ps.table():
        expr = f"  = {row['expr']}" if row["expr"] else ""
        print(f"  {row['name']:<15} {row['value']:>9g} {row['unit']:<3} {row['comment']}{expr}")
    errors = 0
    for level, msg in checks(ps, outlet):
        print(f"  [{level}] {msg}")
        errors += level == "error"
    return errors


def overrides_from(args) -> dict:
    ov = {}
    if getattr(args, "params", None):
        ov.update(json.loads(Path(args.params).read_text(encoding="utf-8")))
    ov.update(parse_sets(getattr(args, "set", None)))
    return ov


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--variant", choices=sorted(VARIANTS), default="A")
    ap.add_argument("--set", action="append", metavar="NAME=VALUE")
    ap.add_argument("--params", help="JSON file {name: value}")
    a = ap.parse_args()
    ps = make_params(a.variant, overrides_from(a))
    sys.exit(1 if report(ps, a.variant) else 0)


if __name__ == "__main__":
    main()
