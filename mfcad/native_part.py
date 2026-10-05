"""Native, parametric Inventor parts over COM - the feature vocabulary of the chip builders.

Every dimension is an Inventor *user parameter*: the builder writes expressions ("well_d",
"in_x - luer_hub_d / 2 + 0.1 mm"); `NativePart.x()` creates the parameters an expression needs
(base values and derived expressions from the chip's ParamSet) the first time it is used, and
evaluates it for the initial geometry. Sketch entities are dimensioned against those
expressions - circles by diameter and centre, rectangles by size and position, polygon
vertices (thread and lug profiles) by their distance from the origin - and extents, work-plane
offsets, coil pitch/height/revolutions are expressions too. So the part re-tunes from
Inventor's Parameters dialog (fx), including the threads and the luer lugs; only a different
topology (variant) needs the script.

Conventions: sketches lie on planes parallel to XY (z = expression) or on XZ; dimensions are
unsigned distances from the projected origin, so geometry is created at its evaluated position
first and the solver keeps it on that side. The API works in cm.

Needs the miniforge `pyinventor` env (pywin32) and a running Inventor.
"""
from __future__ import annotations

import math
from pathlib import Path

import pythoncom

from .inventor_helpers import appearance, close_if_open, mark_purchased, safe
from .params import ParamSet

kPartDocumentObject = 12290
kJoin, kCut = 20481, 20482
kPos, kNeg, kSym = 20993, 20994, 20995
kHorizontalDim, kVerticalDim = 19201, 19202
kIdenticalCompute = 47361
HEALTH = {11777: "unknown", 11778: "ok", 11779: "out of date", 11780: "driver lost", 11781: "error",
          11782: "deleted", 11783: "cannot compute", 11784: "suppressed", 11785: "beyond stop node",
          11786: "inconsistent", 11787: "redundant", 11788: "newly added"}
STEP_TRANSLATOR_ID = "{90AF7F40-0C01-11D5-8E83-0010B541CD80}"
STL_TRANSLATOR_ID = "{533E9A98-FC3B-11D4-8E7E-0010B541CD80}"
kFileBrowseIOMechanism = 13059
AP214 = 3


def _name(obj, name: str) -> None:
    """Browser names must be unique; never let a name fail the build."""
    try:
        obj.Name = name
    except Exception:
        pass


def _neg(expr: str) -> str:
    return f"-({expr})"


class Sk:
    """A sketch with lazily projected origin and axes, and the model->sketch axis mapping."""

    def __init__(self, part: "NativePart", plane, name: str, z: float | None = None):
        self.part = part
        self.sk = part.cd.Sketches.Add(plane)
        _name(self.sk, name)
        self.name = name
        self.z = z                                        # None: an XZ sketch
        self._o = self._xa = self._ya = self._za = None
        a, b = self.pt(0.0, 0.0), self.pt(10.0, 0.0)
        x_h = abs(b.X - a.X) > abs(b.Y - a.Y)
        self.dim_x = kHorizontalDim if x_h else kVerticalDim          # model X (first coordinate)
        self.dim_v = kVerticalDim if x_h else kHorizontalDim          # model Y (XY sketches) or Z (XZ)

    def pt(self, u: float, v: float):
        """Sketch point of model (u, v): (x, y) at height z, or (x, z) on the XZ plane."""
        tg = self.part.tg
        p = tg.CreatePoint(u / 10, v / 10, self.z / 10) if self.z is not None else tg.CreatePoint(u / 10, 0, v / 10)
        return self.sk.ModelToSketchSpace(p)

    def tp(self, u: float, v: float, du: float = 1.5, dv: float = 1.5):
        p = self.pt(u + du, v + dv)
        return self.part.tg.CreatePoint2d(p.X, p.Y)

    @property
    def o(self):
        if self._o is None:
            self._o = self.sk.AddByProjectingEntity(self.part.origin)
        return self._o

    @property
    def xa(self):
        if self._xa is None:
            self._xa = self.sk.AddByProjectingEntity(self.part.X_AXIS)
        return self._xa

    @property
    def va(self):
        """The projected second model axis: Y on XY sketches, Z on XZ sketches."""
        if self._ya is None:
            self._ya = self.sk.AddByProjectingEntity(self.part.Y_AXIS if self.z is not None else self.part.Z_AXIS)
        return self._ya

    def profile(self):
        return self.sk.Profiles.AddForSolid()


class NativePart:
    def __init__(self, app, ps: ParamSet, description: str = ""):
        self.app = app
        self.ps = ps
        self.tg = app.TransientGeometry
        tmpl = app.FileManager.GetTemplateFile(kPartDocumentObject)
        self.doc = app.Documents.Add(kPartDocumentObject, tmpl, False)
        self.cd = self.doc.ComponentDefinition
        self.feats = self.cd.Features
        self.YZ, self.XZ, self.XY = (self.cd.WorkPlanes.Item(i) for i in (1, 2, 3))
        self.X_AXIS, self.Y_AXIS, self.Z_AXIS = (self.cd.WorkAxes.Item(i) for i in (1, 2, 3))
        self.origin = self.cd.WorkPoints.Item(1)
        self.description = description
        self.warnings: list[str] = []
        self.unbound: list[str] = []
        self._planes: dict[str, object] = {}
        self._have: set[str] = set()

    # ------------------------------------------------------------------ parameters
    def x(self, expr) -> float:
        """Ensure the user parameters `expr` needs exist; return its value (mm / deg / ul)."""
        if isinstance(expr, (int, float)):
            return float(expr)
        for n in self.ps.closure(self.ps.names_in(expr)):
            if n in self._have:
                continue
            p = self.ps.items[n]
            prm = self.cd.Parameters.UserParameters.AddByExpression(n, p.expr, p.unit)
            if p.comment:
                try:
                    prm.Comment = ("(derived) " if p.derived else "") + p.comment
                except Exception:
                    pass
            self._have.add(n)
        return self.ps.ev(expr)

    def drive(self, make_dim, expr: str, what: str) -> None:
        """Create a dimension (`make_dim()`) and bind it to `expr`; a failure leaves the
        geometry at its evaluated value and is reported as not parametric."""
        try:
            make_dim().Parameter.Expression = expr
        except Exception as exc:
            self.unbound.append(f"{what}: '{expr}' ({str(exc)[:40]})")

    def constrain(self, fn, what: str) -> None:
        try:
            fn()
        except Exception as exc:
            self.unbound.append(f"{what} ({str(exc)[:40]})")

    # ------------------------------------------------------------------ planes, axes, sketches
    def plane_z(self, expr) -> tuple[object, float]:
        """Work plane parallel to XY at z = expr (XY itself for 0)."""
        z = self.x(expr)
        key = str(expr)
        if abs(z) < 1e-9 and key.strip() in ("0", "0 mm", "0.0"):
            return self.XY, 0.0
        if key not in self._planes:
            try:
                wp = self.cd.WorkPlanes.AddByPlaneAndOffset(self.XY, key)
            except Exception:
                self.unbound.append(f"work plane z = {key}: offset not parametric")
                if abs(z) < 1e-9:                        # a zero offset may be refused: use XY itself
                    self._planes[key] = self.XY
                    return self.XY, 0.0
                wp = self.cd.WorkPlanes.AddByPlaneAndOffset(self.XY, z / 10)
            wp.Visible = False
            _name(wp, f"z = {key}")
            self._planes[key] = wp
        return self._planes[key], z

    def sketch_z(self, z_expr, name: str) -> Sk:
        plane, z = self.plane_z(z_expr)
        return Sk(self, plane, name, z)

    def sketch_xz(self, name: str) -> Sk:
        return Sk(self, self.XZ, name, None)

    def axis_z_at(self, x_expr, name: str):
        """Work axis parallel to Z through (x, 0) - the axis of a port at x = expr."""
        xv = self.x(x_expr)
        try:
            wp = self.cd.WorkPlanes.AddByPlaneAndOffset(self.YZ, str(x_expr))
        except Exception:
            wp = self.cd.WorkPlanes.AddByPlaneAndOffset(self.YZ, xv / 10)
            self.unbound.append(f"axis plane x = {x_expr}: offset not parametric")
        wp.Visible = False
        _name(wp, f"x = {x_expr}")
        ax = self.cd.WorkAxes.AddByTwoPlanes(wp, self.XZ, False)
        ax.Visible = False
        _name(ax, name)
        return ax

    # ------------------------------------------------------------------ sketch geometry
    def _place_point(self, s: Sk, p, u_expr, v_expr, what: str) -> None:
        """Pin a sketch point at (u, v) = (expr, expr): on an axis when a coordinate is 0,
        otherwise by an unsigned distance from the projected origin."""
        u, v = self.x(u_expr), self.x(v_expr)
        dc = s.sk.DimensionConstraints
        gc = s.sk.GeometricConstraints
        if abs(v) < 1e-9:
            self.constrain(lambda: gc.AddCoincident(p, s.xa), f"{what} on X axis")
        else:
            self.drive(lambda: dc.AddTwoPointDistance(s.o, p, s.dim_v, s.tp(u + 2, v / 2)),
                       str(v_expr) if v > 0 else _neg(str(v_expr)), f"{what} v")
        if abs(u) < 1e-9:
            self.constrain(lambda: gc.AddCoincident(p, s.va), f"{what} on second axis")
        else:
            self.drive(lambda: dc.AddTwoPointDistance(s.o, p, s.dim_x, s.tp(u / 2, v - 2)),
                       str(u_expr) if u > 0 else _neg(str(u_expr)), f"{what} u")

    def circle(self, s: Sk, cx, cy, d, tag: str):
        """Circle of diameter expr `d` centred at (cx, cy) (expressions; 0 for on-axis)."""
        u, v, dv = self.x(cx), self.x(cy), self.x(d)
        sk = s.sk
        if abs(u) < 1e-9 and abs(v) < 1e-9:
            c = sk.SketchCircles.AddByCenterRadius(s.o, dv / 20)
        else:
            c = sk.SketchCircles.AddByCenterRadius(s.pt(u, v), dv / 20)
            self._place_point(s, c.CenterSketchPoint, cx, cy, f"{tag} centre")
        self.drive(lambda: sk.DimensionConstraints.AddDiameter(c, s.tp(u + dv / 2, v + dv / 2)), str(d), f"{tag} diameter")
        return c

    def rect(self, s: Sk, u0, v0, u1, v1, tag: str, *, wu=None, wv=None, sym_u=False, sym_v=False,
             pin_u0=None, pin_u1=None):
        """Axis-aligned rectangle between (u0, v0) and (u1, v1) (expressions). Size along the
        first/second axis bound to `wu`/`wv`; `sym_u` centres it on the second axis (symmetric
        in u), `sym_v` on the X axis; `pin_u0`/`pin_u1` bind the distance of its low/high
        u-edge from the origin."""
        a, b, c, d = self.x(u0), self.x(v0), self.x(u1), self.x(v1)
        sk = s.sk
        lines = sk.SketchLines.AddAsTwoPointRectangle(s.pt(a, b), s.pt(c, d))
        L = [lines.Item(i) for i in range(1, 5)]

        def m(sp):                       # model (u, v) of a sketch point
            q = sk.SketchToModelSpace(sp.Geometry)
            return (q.X * 10, q.Y * 10 if s.z is not None else q.Z * 10)

        along_u = [ln for ln in L if abs(m(ln.StartSketchPoint)[0] - m(ln.EndSketchPoint)[0]) > 1e-4]
        along_v = [ln for ln in L if ln not in along_u]
        u_lo, u_hi = sorted(along_u, key=lambda ln: m(ln.StartSketchPoint)[1])
        v_lo, v_hi = sorted(along_v, key=lambda ln: m(ln.StartSketchPoint)[0])
        dc, gc = sk.DimensionConstraints, sk.GeometricConstraints
        mu, mv = (a + c) / 2, (b + d) / 2
        if wu is not None:
            self.x(wu)
            self.drive(lambda: dc.AddTwoPointDistance(u_lo.StartSketchPoint, u_lo.EndSketchPoint, s.dim_x, s.tp(mu, min(b, d) - 2, 0, 0)),
                       str(wu), f"{tag} length")
        if wv is not None:
            self.x(wv)
            self.drive(lambda: dc.AddTwoPointDistance(v_lo.StartSketchPoint, v_lo.EndSketchPoint, s.dim_v, s.tp(min(a, c) - 2, mv, 0, 0)),
                       str(wv), f"{tag} width")
        if sym_u:
            self.constrain(lambda: gc.AddSymmetry(v_lo, v_hi, s.va), f"{tag} symmetric about the second axis")
        if sym_v:
            self.constrain(lambda: gc.AddSymmetry(u_lo, u_hi, s.xa), f"{tag} symmetric about X")
        for pin, line in ((pin_u0, v_lo), (pin_u1, v_hi)):
            if pin is not None:
                uv = self.x(pin)
                self.drive(lambda: dc.AddTwoPointDistance(s.o, line.StartSketchPoint, s.dim_x, s.tp(uv / 2, max(b, d) + 2, 0, 0)),
                           str(pin) if uv > 0 else _neg(str(pin)), f"{tag} edge position")
        return L

    def polygon(self, s: Sk, pts, tag: str):
        """Closed polygon through (u, v) expression pairs; every vertex pinned to the origin
        by its two coordinates, so the profile follows its parameters."""
        sk = s.sk
        vals = [(self.x(u), self.x(v)) for u, v in pts]
        sp = [s.pt(u, v) for u, v in vals]
        lines = [sk.SketchLines.AddByTwoPoints(sp[0], sp[1])]
        for q in sp[2:]:
            lines.append(sk.SketchLines.AddByTwoPoints(lines[-1].EndSketchPoint, q))
        lines.append(sk.SketchLines.AddByTwoPoints(lines[-1].EndSketchPoint, lines[0].StartSketchPoint))
        for i, (ln, (u, v)) in enumerate(zip(lines, pts)):
            self._place_point(s, ln.StartSketchPoint, u, v, f"{tag} vertex {i + 1}")
        return lines

    # ------------------------------------------------------------------ features
    def extrude(self, s: Sk, dist, direction: int, op: int, name: str, taper=None):
        self.x(dist)
        prof = s.profile()
        if taper is None:
            f = self.feats.ExtrudeFeatures.AddByDistanceExtent(prof, str(dist), direction, op)
        else:
            self.x(taper)
            f = self.feats.ExtrudeFeatures.AddByDistanceExtent(prof, str(dist), direction, op, str(taper))
        _name(f, name)
        return f

    def extrude_through(self, s: Sk, direction: int, name: str):
        f = self.feats.ExtrudeFeatures.AddByThroughAllExtent(s.profile(), direction, kCut)
        _name(f, name)
        return f

    def coil(self, s: Sk, axis, pitch, op: int, name: str, *, height=None, revolutions=None, reverse=False):
        """Coil about `axis`; right-handed (ClockwiseRotation=False, verified on the RMS holder
        and re-checked by check_*.py on the exported STEP)."""
        self.x(pitch)
        prof = s.profile()
        if height is not None:
            self.x(height)
            f = self.feats.CoilFeatures.AddByPitchAndHeight(prof, axis, str(pitch), str(height), op, reverse, False)
        else:
            self.x(revolutions)
            f = self.feats.CoilFeatures.AddByPitchAndRevolution(prof, axis, str(pitch), str(revolutions), op, reverse, False)
        _name(f, name)
        return f

    def pattern(self, features, axis, count, name: str):
        coll = self.app.TransientObjects.CreateObjectCollection()
        for f in features:
            coll.Add(f)
        self.x(count)
        try:
            pf = self.feats.CircularPatternFeatures.Add(coll, axis, True, str(count), "360 deg", True, kIdenticalCompute)
        except Exception:
            pf = self.feats.CircularPatternFeatures.Add(coll, axis, True, int(self.x(count)), "360 deg", True, kIdenticalCompute)
            self.unbound.append(f"{name}: count not parametric")
        _name(pf, name)
        return pf

    def fillet(self, edges, radius, name: str):
        if edges is None or edges.Count == 0:
            self.warnings.append(f"{name}: no edges found")
            return None
        self.x(radius)
        try:
            f = self.feats.FilletFeatures.AddSimple(edges, str(radius), False, False, False, False, False, False)
            _name(f, name)
            return f
        except Exception as exc:
            self.warnings.append(f"{name} skipped ({str(exc)[:60]})")
            return None

    def chamfer(self, edges, dist, name: str):
        if edges is None or edges.Count == 0:
            self.warnings.append(f"{name}: no edge found")
            return None
        self.x(dist)
        try:
            f = self.feats.ChamferFeatures.AddUsingDistance(edges, str(dist), False, False, False)
            _name(f, name)
            return f
        except Exception as exc:
            self.warnings.append(f"{name} skipped ({str(exc)[:60]})")
            return None

    # ------------------------------------------------------------------ edge lookup (model mm)
    def body(self):
        return self.cd.SurfaceBodies.Item(1)

    def circle_edges(self, z: float, r: float, cx: float = 0.0, cy: float = 0.0, tol: float = 0.02):
        ec = self.app.TransientObjects.CreateEdgeCollection()
        for e in self.body().Edges:
            try:
                g = e.Geometry
                rr, c = g.Radius, g.Center
            except Exception:
                continue
            if (abs(rr * 10 - r) < tol and abs(c.Z * 10 - z) < tol and abs(c.X * 10 - cx) < tol
                    and abs(c.Y * 10 - cy) < tol):
                ec.Add(e)
        return ec

    def vertical_edges(self, min_len: float):
        ec = self.app.TransientObjects.CreateEdgeCollection()
        for e in self.body().Edges:
            try:
                a, b = e.StartVertex.Point, e.StopVertex.Point
            except Exception:
                continue
            dx, dy, dz = (b.X - a.X) * 10, (b.Y - a.Y) * 10, (b.Z - a.Z) * 10
            if abs(dz) >= min_len and abs(dx) < 1e-4 and abs(dy) < 1e-4:
                ec.Add(e)
        return ec

    def feature_z_range(self, f) -> tuple[float, float]:
        rb = f.RangeBox
        return rb.MinPoint.Z * 10, rb.MaxPoint.Z * 10

    # ------------------------------------------------------------------ finishing
    def set_appearance(self, names) -> None:
        a = appearance(self.app, names)
        if a is None:
            self.warnings.append(f"appearance {names[0]!r} not found")
            return
        local = None
        for x in self.doc.Assets:
            if safe(lambda: x.DisplayName) == a.DisplayName:
                local = x
                break
        if local is None:
            local = safe(lambda: a.CopyTo(self.doc))
        try:
            self.doc.ActiveAppearance = local
        except Exception as exc:
            self.warnings.append(f"appearance not set ({str(exc)[:40]})")

    def health(self) -> list[str]:
        bad = []
        for coll in (self.cd.Features, self.cd.Sketches):
            for f in coll:
                hs = safe(lambda: f.HealthStatus)
                if hs is not None and hs != 11778:
                    bad.append(f"{safe(lambda: f.Name, '?')}: {HEALTH.get(hs, hs)}")
        return bad

    def report(self) -> dict:
        self.doc.Update()
        bodies = self.cd.SurfaceBodies
        rep = {"bodies": bodies.Count, "features": self.feats.Count,
               "user_parameters": self.cd.Parameters.UserParameters.Count}
        if bodies.Count:
            b = bodies.Item(1)
            rep["volume_mm3"] = round(b.Volume(0.0001) * 1000, 2)
            rep["faces"] = b.Faces.Count
            rb = self.cd.RangeBox
            rep["range_mm"] = [round(v * 10, 3) for v in (rb.MinPoint.X, rb.MinPoint.Y, rb.MinPoint.Z,
                                                          rb.MaxPoint.X, rb.MaxPoint.Y, rb.MaxPoint.Z)]
        rep["health"] = self.health()
        rep["warnings"] = self.warnings
        rep["not_parametric"] = self.unbound
        return rep

    def save(self, ipt: Path, step: bool = True, stl: bool = False) -> None:
        ipt = Path(ipt).resolve()
        ipt.parent.mkdir(parents=True, exist_ok=True)
        close_if_open(self.app, ipt)
        try:
            props = self.doc.PropertySets.Item("Design Tracking Properties")
            props.Item("Part Number").Value = ipt.stem
            if self.description:
                props.Item("Description").Value = self.description
        except Exception:
            pass
        mark_purchased(self.doc, ipt.stem)
        self.doc.SaveAs(str(ipt), False)
        if step:
            try:
                export_step(self.app, self.doc, ipt.with_suffix(".stp"))
            except Exception as exc:
                self.warnings.append(f"no STEP copy ({str(exc)[:50]})")
        if stl:
            try:
                export_stl(self.app, self.doc, ipt.with_suffix(".stl"))
            except Exception as exc:
                self.warnings.append(f"no STL copy ({str(exc)[:50]})")

    def close(self) -> None:
        try:
            self.doc.Close(True)
        except Exception:
            pass


# ---------------------------------------------------------------------- STEP with colours
def _nvm_put(options, key, value):
    try:
        options.Add(key, value)
        return
    except Exception:
        pass
    dispid = options._oleobj_.GetIDsOfNames("Value")
    options._oleobj_.Invoke(dispid, 0, pythoncom.DISPATCH_PROPERTYPUT, 0, key, value)


def export_step(app, doc, out: Path) -> None:
    """AP214 STEP (keeps colours; a bare doc.SaveAs reuses whatever the STEP dialog was left on)."""
    tr = app.ApplicationAddIns.ItemById(STEP_TRANSLATOR_ID)
    if not safe(lambda: tr.Activated):
        tr.Activate()
    t = app.TransientObjects
    ctx = t.CreateTranslationContext()
    ctx.Type = kFileBrowseIOMechanism
    opts = t.CreateNameValueMap()
    med = t.CreateDataMedium()
    if tr.HasSaveCopyAsOptions(doc, ctx, opts):
        _nvm_put(opts, "ApplicationProtocolType", AP214)
        _nvm_put(opts, "IncludeSketches", False)
    med.FileName = str(out)
    tr.SaveCopyAs(doc, ctx, opts, med)


def export_stl(app, doc, out: Path) -> None:
    """Binary STL in mm for the slicer (PreForm). Inventor's "High" resolution: measured on the
    plug's threads, under 5 um chord error (12k triangles) - far below the Form 4's 50 um pixel;
    its custom-resolution options are scaled and coupled in undocumented ways, so they are avoided."""
    tr = app.ApplicationAddIns.ItemById(STL_TRANSLATOR_ID)
    if not safe(lambda: tr.Activated):
        tr.Activate()
    t = app.TransientObjects
    ctx = t.CreateTranslationContext()
    ctx.Type = kFileBrowseIOMechanism
    opts = t.CreateNameValueMap()
    med = t.CreateDataMedium()
    if tr.HasSaveCopyAsOptions(doc, ctx, opts):
        for k, v in (("ExportUnits", 5), ("Resolution", 0), ("OutputFileType", 0), ("ExportColor", False)):
            _nvm_put(opts, k, v)            # 5 = mm, 0 = High, 0 = binary
    med.FileName = str(out)
    tr.SaveCopyAs(doc, ctx, opts, med)


def read_user_parameters(app, ipt: Path) -> dict:
    """Base user parameters of a generated part (to carry Inventor-side edits back into the
    script): {name: value in mm / deg / ul}. Derived parameters are skipped."""
    from .inventor_helpers import find_open
    doc = find_open(app, Path(ipt).resolve())
    opened = doc is None
    if opened:
        doc = app.Documents.Open(str(Path(ipt).resolve()), False)
    out = {}
    try:
        for prm in doc.ComponentDefinition.Parameters.UserParameters:
            if (safe(lambda: prm.Comment) or "").startswith("(derived)"):
                continue
            unit = safe(lambda: prm.Units) or ""
            v = prm.Value
            if unit == "mm":
                v *= 10.0                      # database units: cm
            elif unit == "deg":
                v = math.degrees(v)            # database units: rad
            out[prm.Name] = round(v, 6)
    finally:
        if opened:
            doc.Close(True)
    return out
