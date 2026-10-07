# openUC2 microfluidics

Parametric microfluidic chips for openUC2 microscopes. Each chip is built by a Python script as
**native Autodesk Inventor parts** (`.ipt`) and an assembly (`.iam`). The script talks to Inventor
over COM (pywin32, the PyInventor toolchain). Every dimension ends up as an Inventor user
parameter, and the script also exports STEP and print-ready STL files.

| chip | folder | |
|---|---|---|
| Leaf chamber | [`chips/leaf_chamber`](chips/leaf_chamber/README.md) | slide-format perfusion chamber for a leaf sample: screw plug with O-ring (optional window), luer-lock inlet, luer (B, default) or reservoir (A) outlet, glued bottom coverslip |

![leaf chamber](chips/leaf_chamber/docs/exploded_B.png)

## Layout

```
mfcad/                      shared library
  params.py                 parameter tables: base values + derived expressions, valid both in
                            Python (checks, initial geometry) and as Inventor expressions
  native_part.py            NativePart: sketches dimensioned against parameter expressions,
                            extrudes, coils (threads, lugs), patterns, fillets, chamfers,
                            health report, AP214 STEP and STL export, reading parameters back
  stock_parts.py            bought glass parts that openUC2-CAD-new lacks (coverslips)
  inventor_helpers.py       the shared openUC2 builder helper (session, CAD-new lookup, Builder) -
                            identical copy of openuc2-opmsimulator / openuc2-openraman, keep in sync
chips/<chip>/
  <chip>.py                 parameters, variants, design checks (plain Python)
  build_<chip>.py           Inventor builder: parts, assembly, interference check, STEP/STL
  check_<chip>.py           independent checks on the STEP exports + figures for docs/
  INVENTOR/                 generated .ipt/.iam/.stp/.stl and "<part> - params.json"
  docs/                     figures
```

## Environments

- **Inventor builds:** `C:\Users\benir\miniforge3\envs\pyinventor\python.exe` with Inventor 2025
  running. The session helper restores `SilentOperation` and closes only the documents the script
  opened.
- **Checks and figures:** `uv run --with cadquery --with trimesh --with rtree --with scipy --with shapely --with networkx --with matplotlib python check_<chip>.py`.
- **Parameter tables and design checks:** any Python 3.10+ (`python <chip>.py`).

## Adding a chip

1. Copy `chips/leaf_chamber` to `chips/<new_chip>` and rename the three scripts.
2. In `<chip>.py`, declare the knobs with `P(name, value, unit, comment)` and the derived values
   with `D(name, "expression", ...)`. Write expressions as plain arithmetic of parameter names,
   with units on literal numbers (`0.3 mm`). Write the design checks.
3. In `build_<chip>.py`, describe the features with `NativePart`, always in parameter expressions:
   `part.circle(s, "-out_x", 0, "res_d", ...)`, `part.extrude(s, "res_top - res_floor", kNeg, kCut, ...)`.
   Parameters are created in the part on first use, so each part carries only what it needs.
4. Names follow openUC2-CAD-new (`PRT - 9xxx - <ABBREV> - V04 - <variant>`, `BUY - ...`). Use
   CAD-new parts where they exist; the O-rings come from `STD/ISO 3601-1 - O-rings`.
   Provisional numbers: 9300–9302 are taken by the leaf chamber.

Frame convention used so far: z = 0 on the glue plane (top of the bottom coverslip = sample
plane), chamber centre at the origin, inlet towards +X.
