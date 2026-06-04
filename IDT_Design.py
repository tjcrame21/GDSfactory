# -*- coding: utf-8 -*-
"""
Mu2 Phase 1 — Layout / PDK bootstrap
Created on Mon Jun  1 2026
@author: Tyler Cramer

Design Notes: Mu2 Phase 1 Project
    Platform : Aluminum nitride (AlN) on sapphire
    Wafer    : 6 inch (150 mm)
    Metal    : single gold layer for interdigital transducers (IDTs)

Targeting gdsfactory >= 9.0 (developed/validated on 9.43). The version-sensitive
spots are the Pdk(...) kwargs, LayerViews/LayerView construction, and the
LayerMap annotations — verify these first if you bump gf versions.
"""

import os
import time
from pathlib import Path

import numpy as np  # used by idt_chirped to build the pitch profile

import gdsfactory as gf
from gdsfactory.pdk import Pdk
from gdsfactory.typings import Layer
from gdsfactory.technology.layer_map import LayerMap
from gdsfactory.technology.layer_views import LayerViews, LayerView
from gdsfactory.technology import LayerLevel, LayerStack

# Import the base strip cross-section factory directly (not via PDK lookup)
from gdsfactory.cross_section import strip as strip_xs


# ----------------------------
# 0) Version guard + run metadata
# ----------------------------
_GF_MIN = (9, 0)
_gf_ver = tuple(int(x) for x in gf.__version__.split(".")[:2])
if _gf_ver < _GF_MIN:
    raise RuntimeError(
        f"This template targets gdsfactory >= {_GF_MIN[0]}.{_GF_MIN[1]}, "
        f"found {gf.__version__}. Verify Pdk/LayerViews/LayerMap API before running."
    )

RUN_TAG = os.environ.get("GF_RUN_TAG", time.strftime("%Y%m%d_%H%M%S"))
PDK_VERSION = "0.1.0"


# ----------------------------
# 1) Layer definitions (GDS layer, datatype)
# ----------------------------
class LAYER(LayerMap):
    # --- Drawn / fab layers ---
    AU: Layer = (23, 0)        # single gold metal layer (IDT fingers + busbars)
    # To add a second metal later, give it a DISTINCT datatype, e.g.:
    # AU2: Layer = (23, 728)

    # --- Process / informational layers (not necessarily drawn; drive the stack) ---
    ALN: Layer = (10, 0)         # AlN piezoelectric film footprint
    SAPPHIRE: Layer = (11, 0)    # sapphire substrate footprint
    PASV: Layer = (12, 0)        # optional dielectric / passivation

    # --- Non-fab layers (outlines, text, wafer; revisit for fab release) ---
    MASK: Layer = (900, 0)     # design-region / reticle outlines
    TXT: Layer = (901, 0)      # text / labels
    WAFER: Layer = (903, 0)    # full 6-inch wafer outline


# ----------------------------
# 2) LayerViews (.lyp) — distinct colors so KLayout renders gold vs film vs
#    substrate vs outlines clearly. Very handy when inspecting IDT fingers.
# ----------------------------
LAYER_VIEWS = LayerViews(
    layer_views={
        "AU": LayerView(layer=LAYER.AU, fill_color="#FFD700", frame_color="#B8860B"),
        "ALN": LayerView(layer=LAYER.ALN, fill_color="#9ED2C6", frame_color="#5C9A8A", transparent=True),
        "SAPPHIRE": LayerView(layer=LAYER.SAPPHIRE, fill_color="#B0C4DE", frame_color="#6A7F9C", transparent=True),
        "PASV": LayerView(layer=LAYER.PASV, fill_color="#D8BFD8", frame_color="#9A7FA0", transparent=True),
        "MASK": LayerView(layer=LAYER.MASK, fill_color="#FF4D4D", frame_color="#B30000", transparent=True),
        "TXT": LayerView(layer=LAYER.TXT, fill_color="#000000", frame_color="#000000"),
        "WAFER": LayerView(layer=LAYER.WAFER, fill_color="#CCCCCC", frame_color="#888888", transparent=True),
    }
)


# ----------------------------
# 3) LayerStack — bottom-to-top, consistent z (no overlaps).
#    Substrate top sits at z=0; AlN on top of it; gold on top of AlN.
#    Tune thicknesses to your actual process; substrate is truncated for sim.
# ----------------------------
LAYER_STACK = LayerStack(
    layers=dict(
        SAPPHIRE=LayerLevel(layer=LAYER.SAPPHIRE, thickness=500.0, zmin=-500.0, material="sapphire"),
        ALN=LayerLevel(layer=LAYER.ALN, thickness=1.0, zmin=0.0, material="AlN"),
        AU=LayerLevel(layer=LAYER.AU, thickness=0.3, zmin=1.0, material="Au"),
        PASV=LayerLevel(layer=LAYER.PASV, thickness=0.1, zmin=1.3, material="SiO2"),  # optional

        # Non-fab (zero-thickness placeholders so they're enumerable but inert)
        MASK=LayerLevel(layer=LAYER.MASK, thickness=0.0, zmin=0.0, material="temp_mask"),
        TXT=LayerLevel(layer=LAYER.TXT, thickness=0.0, zmin=0.0, material="temp_text"),
        WAFER=LayerLevel(layer=LAYER.WAFER, thickness=0.0, zmin=0.0, material="wafer"),
    )
)


# ----------------------------
# 5) Cross-sections + cell factory registry (so built-ins / routing resolve)
# ----------------------------
def xs_metal_au(width: float = 3.0, radius: float = 70.0, **kwargs):
    """Metal routing cross-section on the gold layer."""
    return strip_xs(width=width, radius=radius, layer=LAYER.AU, **kwargs)


def cell_straight(**kwargs):
    return gf.components.straight(**kwargs)


def cell_taper(**kwargs):
    return gf.components.taper(**kwargs)


def cell_bend_euler(**kwargs):
    return gf.components.bend_euler(**kwargs)


def cell_bend_circular(**kwargs):
    return gf.components.bend_circular(**kwargs)


MY_CELLS = {
    "straight": cell_straight,
    "taper": cell_taper,
    "bend_euler": cell_bend_euler,
    "bend_circular": cell_bend_circular,
}


# ----------------------------
# 6) PDK (deterministic name — NO timestamp) + activate
# ----------------------------

PDK = Pdk(
    name="mu2_phase1",
    version=PDK_VERSION,
    layers=LAYER,
    layer_views=LAYER_VIEWS,
    layer_stack=LAYER_STACK,
    cross_sections={"metal_au": xs_metal_au},
    cells=MY_CELLS,
)
PDK.activate()


# ----------------------------
# 7) Devices
#
# ----------------------------


def _finger_poly(x: float, width: float, length: float, attach_top: bool, busbar_width: float, finger_overlap: float):
    """One finger rectangle. attach_top=True hangs it from the top busbar
    (so it points down into the aperture), else from the bottom busbar."""
    ys = (1-finger_overlap) * length
    
    if attach_top:
        y0 = -length/2 + ys         # finger tip starts just below the top busbar inner edge
        y1 = length/2 + ys  # ... extends downward (negative) into the gap
        
    else:
        y0 = length/2 - ys
        y1 = -length/2 - ys
    ylo, yhi = sorted((y0, y1))
    return [(x, ylo), (x + width, ylo), (x + width, yhi), (x, yhi)]


def _busbar_polys(span: float, busbar_width: float, finger_length: float, finger_overlap: float):
    """Top and bottom busbars spanning [0, span] in x."""
    xs = finger_length/2
    ys = busbar_width/2 - (2*(1-finger_overlap) * finger_length)
    return [
        [(0, ys+busbar_width+xs), (span, ys+busbar_width+xs),
         (span, 2 * busbar_width+ys+xs), (0, 2 * busbar_width+ys+xs)],          # top
        [(0, -2 * busbar_width-ys-xs), (span, -2 * busbar_width-ys-xs),
         (span, -busbar_width-ys-xs), (0, -busbar_width-ys-xs)],                # bottom
    ]


@gf.cell
def idt_uniform(
    n_fingers: int = 50,
    busbar_width: float = 10.0,   # shared (per-device)
    finger_length: float = 50.0,  # shared (per-device)
    pitch: float = 2.0,           # variant knob: constant center-to-center
    finger_width: float = 1.0,
    finger_overlap: float = 0.0,  # Finger Overlap in Percent
    layer: Layer = LAYER.AU
) -> gf.Component:
    """Uniform IDT: constant pitch, alternating-polarity single-electrode fingers.
    Synchronous wavelength lambda0 = 2 * pitch (single-electrode)."""
    c = gf.Component()
    for i in range(n_fingers):
        c.add_polygon(
            _finger_poly(i * pitch, finger_width, finger_length, i % 2 == 0, busbar_width, finger_overlap),
            layer=layer,
        )
    span = (n_fingers - 1) * pitch + finger_width
    for p in _busbar_polys(span, busbar_width, finger_length, finger_overlap):
        c.add_polygon(p, layer=layer)
    c.info["lambda0_um"] = 2 * pitch
    return c


@gf.cell
def idt_chirped(
    n_fingers: int = 50,
    busbar_width: float = 10.0,   # shared (per-device)
    finger_length: float = 50.0,  # shared (per-device)
    pitch_start: float = 2.0,     # variant knobs: pitch sweeps start -> end
    pitch_end: float = 2.6,       # SCALARS ONLY — profile built inside
    finger_width: float = 1.0,
    finger_overlap: float = 0.0,  # Finger Overlap in Percent
    layer: Layer = LAYER.AU,
) -> gf.Component:
    """Linearly chirped IDT: pitch ramps from pitch_start to pitch_end """
    pitches = np.linspace(pitch_start, pitch_end, max(n_fingers - 1, 1))
    g = 0.001
    c = gf.Component()
    x = 0.0
    for i in range(n_fingers):
        # snap each finger's x to grid so the chirp stays manufacturable
        xs = round(x / g) * g
        c.add_polygon(
            _finger_poly(xs, finger_width, finger_length, i % 2 == 0, busbar_width, finger_overlap),
            layer=layer,
        )
        if i < len(pitches):
            x += pitches[i]
    span = x + finger_width
    for p in _busbar_polys(span, busbar_width, finger_length, finger_overlap):
        c.add_polygon(p, layer=layer)
    c.info["lambda0_start_um"] = 2 * pitch_start
    c.info["lambda0_end_um"] = 2 * pitch_end
    return c


@gf.cell
def idt_unidirectional(
    n_fingers: int = 50,
    busbar_width: float = 10.0,   # shared (per-device)
    finger_length: float = 50.0,  # shared (per-device)
    pitch: float = 2.0,           # variant knobs
    finger_width: float = 1.0,
    cell_offset: float = 0.5,     # intra-cell electrode asymmetry (drives directionality)
    finger_overlap: float = 0.0,  # Finger Overlap in Percent
    layer: Layer = LAYER.AU,
) -> gf.Component:
    
    """Unidirectional (SPUDT/DART-style) IDT — SCAFFOLD ONLY.
    TODO: replace the per-finger loop with a proper unit-cell builder:
    """
    c = gf.Component()
    for i in range(n_fingers):
        off = cell_offset if (i % 2) else 0.0
        c.add_polygon(
            _finger_poly(i * pitch + off, finger_width, finger_length, i % 2 == 0, busbar_width, finger_overlap),
            layer=layer,
        )
    span = (n_fingers - 1) * pitch + finger_width + cell_offset
    for p in _busbar_polys(span, busbar_width, finger_length, finger_overlap):
        c.add_polygon(p, layer=layer)
    c.info["lambda0_um"] = 2 * pitch
    return c


class IDT:
    """An IDT *family* = one physical device.

        dev = IDT(n_fingers=60, busbar_width=12.0, finger_length=60.0)
        u = dev.uniform(pitch=1.9)
        ch = dev.chirped(pitch_start=1.8, pitch_end=2.1)
        ud = dev.unidirectional(pitch=1.9)
    """

    def __init__(self, n_fingers: int, busbar_width: float, finger_length: float, finger_overlap: float):
        self.n_fingers = n_fingers
        self.busbar_width = busbar_width
        self.finger_length = finger_length
        self.finger_overlap = finger_overlap

    def uniform(self, pitch: float = 2.0, finger_width: float = 1.0, layer: Layer = LAYER.AU):
        return idt_uniform(self.n_fingers, self.busbar_width, self.finger_length,
                           pitch, finger_width,self.finger_overlap, layer)

    def chirped(self, pitch_start: float = 2.0, pitch_end: float = 2.6,
                finger_width: float = 1.0, layer: Layer = LAYER.AU):
        return idt_chirped(self.n_fingers, self.busbar_width, self.finger_length,
                          pitch_start, pitch_end, finger_width, self.finger_overlap, layer)

    def unidirectional(self, pitch: float = 2.0, finger_width: float = 1.0,
                       cell_offset: float = 0.5, layer: Layer = LAYER.AU):
        return idt_unidirectional(self.n_fingers, self.busbar_width, self.finger_length,
                                 pitch, finger_width, cell_offset, self.finger_overlap, layer)


# ----------------------------
# 8) Masks  (assembly @gf.cell) — defined after devices it assembles
# ----------------------------
@gf.cell
def reticle_mu2_phase1() -> gf.Component:
    c = gf.Component()

    # 6-inch (150 mm) wafer outline, centered at origin
    wafer = gf.components.circle(radius=75000.0, layer=LAYER.WAFER)
    c.add_ref(wafer)

    # One device family -> three transducer types sharing its params.
    dev = IDT(n_fingers=40, busbar_width=10.0, finger_length=50.0, finger_overlap = 0.9)
    variants = [
        ("uniform", dev.uniform(pitch=2.0)),
        ("chirped", dev.chirped(pitch_start=2.0, pitch_end=2.6)),
        ("unidir", dev.unidirectional(pitch=2.0)),
    ]

    # Row them out, each in its own MASK design-region with a TXT label.
    x_cursor = 0.0
    gap_between = 80.0
    pad = 20.0
    for name, comp in variants:
        ref = c.add_ref(comp)
        ref.dmove((x_cursor - comp.dxmin, 0.0))
        b = ref.dbbox()
        c.add_polygon(
            [(b.left - pad, b.bottom - pad), (b.right + pad, b.bottom - pad),
             (b.right + pad, b.top + pad), (b.left - pad, b.top + pad)],
            layer=LAYER.MASK,
        )
        lbl = gf.components.text(text=name, size=20.0, layer=LAYER.TXT)
        t = c.add_ref(lbl)
        t.dmove((b.left, b.top + pad + 10.0))
        x_cursor = b.right + pad + gap_between

    # Project label
    title = gf.components.text(text="MU2_P1", size=200.0, layer=LAYER.TXT)
    tt = c.add_ref(title)
    tt.dmove((0.0, c.dbbox().top + 300.0))
    return c


# ----------------------------
# 9) Register device/mask factories into the active PDK.
# ----------------------------
for _factory in (idt_uniform, idt_chirped, idt_unidirectional, reticle_mu2_phase1):
    PDK.cells[_factory.__name__] = _factory


if __name__ == "__main__":
    # Clears cached cells so re-running in Spyder/Jupyter doesn't raise on
    # duplicate names — the right alternative to baking timestamps into names.
    gf.clear_cache()
    print(f"gdsfactory {gf.__version__} | PDK mu2_phase1 v{PDK_VERSION} | run {RUN_TAG}")

    top = reticle_mu2_phase1()
    top.info["run_tag"] = RUN_TAG
    top.info["pdk_version"] = PDK_VERSION
    top.info["gdsfactory_version"] = gf.__version__


    # OS-agnostic, configurable output dir (override with MU2_OUTDIR env var)
    outdir = Path(os.environ.get(
        "MU2_OUTDIR", r"E:\WorkFiles\Projects\Mantech\Ported\tcramer\Layout"
    ))
    outdir.mkdir(parents=True, exist_ok=True)

    gdspath = outdir / f"mu2_phase1_{RUN_TAG}.gds"
    top.write_gds(gdspath)
    print(f"wrote {gdspath}")

    # show() needs a running KLayout; guard so headless runs don't crash
    try:
        top.show()
    except Exception as e:
        print(f"skipped show(): {e}")