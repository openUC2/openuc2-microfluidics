"""Bought parts the chips need that openUC2-CAD-new does not have, made as small native parts
(purchased BOM structure, CAD-new style "BUY - ..." names). Existing CAD-new parts (O-rings in
STD/ISO 3601-1, glass discs in BUY) are placed from CAD-new instead - see the chip builders.
"""
from __future__ import annotations

from pathlib import Path

from .native_part import NativePart, kJoin, kPos, kNeg
from .params import P, ParamSet

CLEAR = ("Clear - Light", "Clear")


def _fmt(v: float) -> str:
    return f"{v:g}"


def coverslip_name(l: float, w: float, t: float) -> str:
    return f"BUY - Cover glass - {_fmt(w)} x {_fmt(l)} x {_fmt(t)}"


def round_coverslip_name(d: float, t: float) -> str:
    return f"BUY - Cover glass - D{_fmt(d)} x {_fmt(t)}"


def glass_plate(app, path: Path, l: float, w: float, t: float) -> dict:
    """Rectangular coverslip, l along X, w along Y; TOP face on z = 0 (it is glued under a chip
    whose frame has z = 0 on the glue plane), centred on the origin."""
    ps = ParamSet([P("glass_l", l, comment="length (X)"), P("glass_w", w, comment="width (Y)"),
                   P("glass_t", t, comment="thickness (#1.5: 0.16-0.19)")])
    part = NativePart(app, ps, f"Cover glass {_fmt(w)} x {_fmt(l)} mm, {_fmt(t)} mm")
    try:
        s = part.sketch_z("0", "glass sketch")
        part.rect(s, "-glass_l / 2", "-glass_w / 2", "glass_l / 2", "glass_w / 2", "glass",
                  wu="glass_l", wv="glass_w", sym_u=True, sym_v=True)
        part.extrude(s, "glass_t", kNeg, kJoin, "glass")
        part.set_appearance(CLEAR)
        part.save(path)
        rep = part.report()
    finally:
        part.close()
    return rep


def glass_disc(app, path: Path, d: float, t: float) -> dict:
    """Round coverslip on the XY plane, z = 0 .. t, centred on the origin."""
    ps = ParamSet([P("glass_d", d, comment="diameter"), P("glass_t", t, comment="thickness")])
    part = NativePart(app, ps, f"Round cover glass D{_fmt(d)} mm, {_fmt(t)} mm")
    try:
        s = part.sketch_z("0", "glass sketch")
        part.circle(s, 0, 0, "glass_d", "glass")
        part.extrude(s, "glass_t", kPos, kJoin, "glass")
        part.set_appearance(CLEAR)
        part.save(path)
        rep = part.report()
    finally:
        part.close()
    return rep
