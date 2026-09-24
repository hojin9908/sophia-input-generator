"""Deterministic particle generation engine for SOPHIA input.txt.

Design rule (non-negotiable): the LLM only extracts a *geometry spec* (JSON)
from the paper. Every coordinate is produced here, deterministically, from
that spec. The same spec always yields the same input.txt.

Spec schema (JSON)
------------------
{
  "name": "dam_break_2d",
  "dim": 2,                       # 2 or 3 (2-D -> z = 0)
  "dp": 0.01,                     # initial particle spacing [m]
  "h_factor": 1.6,                # h = h_factor * dp
  "gravity": [0, -9.81, 0],       # used for hydrostatic pressure
  "extra_columns": [20],          # optional, extra base indices 14..26 to write
  "fluids": [ {<shape>, "rho_ref": 1000, "temp": 293.15,
               "velocity": [0,0,0], "p_type": 1,
               "hydrostatic": true, "free_surface": 0.6} ],
  "walls":  [ {"kind": "container", "min": [...], "max": [...],
               "layers": 3, "open_faces": ["y+"], "rho_ref": 1000, "temp": 293.15},
              {"kind": "solid", <shape>, "layers": 3 | "full", ...} ],
  "dem":    [ {"region": <shape>, "radius": 0.002, "rho": 2500,
               "arrangement": "cubic" | "hex", "temp": 293.15,
               "vol_power": 0, "jitter": 0.0, "seed": 0} ],
  "open_boundaries": [ {"type": "inlet" | "outlet", "axis": "x",
               "position": 0.0, "outward": -1,
               "span_min": [..], "span_max": [..], "layers": 4,
               "velocity": [..], "rho_ref": 1000, "temp": 293.15, "p_type": 1} ]
}

<shape> is one of
  {"shape": "box", "min": [x,y,(z)], "max": [x,y,(z)]}
  {"shape": "cylinder", "axis": "z", "center": [x,y,(z)], "radius": r,
   "length_min": a, "length_max": b}        # 2-D: a circle (axis z)
  {"shape": "sphere", "center": [x,y,(z)], "radius": r}

Governing relations
-------------------
* lattice (cell-centred):  x_i = x_min + (i + 1/2) dp,  i = 0 .. floor(L/dp)-1
* mass:                    m = rho_ref * dp^3          (lab rule)
* smoothing length:        h = h_factor * dp           (default 1.6 dp)
* hydrostatic pressure:    p = rho_ref * |g| * (y_s - y)  for y < y_s
* DEM sphere mass:         m = rho * (4/3) pi r^3
* open boundary:           `layers` (default 4) buffer layers placed outward
                           from the boundary plane at  s + n (k + 1/2) dp
"""
from __future__ import annotations

import math
from typing import Dict, List, Tuple

import numpy as np

from sophia_format import (
    P_TYPE_BOUNDARY,
    P_TYPE_DEM,
    P_TYPE_FLUID,
    select_columns,
    write_input_txt,
)

AXES = {"x": 0, "y": 1, "z": 2}
FIELD_NAMES = [
    "x", "y", "z", "ux", "uy", "uz", "m", "p_type", "h", "temp", "pres",
    "rho", "rho_ref", "rad", "ri", "dem_idx", "wx", "wy", "wz",
    "temp1", "temp2", "temp3", "vol_power", "buffer_type",
]


class SpecError(ValueError):
    """Raised when the geometry spec is invalid."""


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------
def _vec3(v, dim: int, name: str) -> np.ndarray:
    if v is None:
        raise SpecError(f"'{name}' is required")
    v = list(v)
    if len(v) < dim:
        raise SpecError(f"'{name}' needs {dim} components, got {len(v)}")
    out = np.zeros(3)
    out[:dim] = v[:dim]
    return out


def lattice_box(lo: np.ndarray, hi: np.ndarray, dp: float, dim: int) -> np.ndarray:
    """Cell-centred square/cubic lattice inside [lo, hi]."""
    axes = []
    for d in range(dim):
        length = hi[d] - lo[d]
        n = int(math.floor(length / dp + 1e-9))
        if n <= 0:
            return np.zeros((0, 3))
        axes.append(lo[d] + (np.arange(n) + 0.5) * dp)
    grids = np.meshgrid(*axes, indexing="ij")
    pts = np.zeros((grids[0].size, 3))
    for d in range(dim):
        pts[:, d] = grids[d].ravel()
    return pts


def shape_bounds(shape: Dict, dim: int) -> Tuple[np.ndarray, np.ndarray]:
    kind = shape.get("shape", "box")
    if kind == "box":
        return _vec3(shape.get("min"), dim, "min"), _vec3(shape.get("max"), dim, "max")
    if kind == "sphere":
        c = _vec3(shape.get("center"), dim, "center")
        r = float(shape["radius"])
        lo, hi = c - r, c + r
        lo[dim:] = 0
        hi[dim:] = 0
        return lo, hi
    if kind == "cylinder":
        c = _vec3(shape.get("center"), dim, "center")
        r = float(shape["radius"])
        ax = AXES[shape.get("axis", "z")]
        lo, hi = c - r, c + r
        if dim == 3 or ax < dim:
            lo[ax] = float(shape.get("length_min", c[ax]))
            hi[ax] = float(shape.get("length_max", c[ax]))
        lo[dim:] = 0
        hi[dim:] = 0
        return lo, hi
    raise SpecError(f"unknown shape '{kind}'")


def shape_contains(shape: Dict, pts: np.ndarray, dim: int, shrink: float = 0.0) -> np.ndarray:
    """Boolean mask of points inside the shape (optionally shrunk inward)."""
    kind = shape.get("shape", "box")
    eps = 1e-12
    if kind == "box":
        lo, hi = shape_bounds(shape, dim)
        m = np.ones(len(pts), bool)
        for d in range(dim):
            m &= (pts[:, d] >= lo[d] + shrink - eps) & (pts[:, d] <= hi[d] - shrink + eps)
        return m
    if kind == "sphere":
        c = _vec3(shape.get("center"), dim, "center")
        r = float(shape["radius"]) - shrink
        return np.sum((pts[:, :dim] - c[:dim]) ** 2, axis=1) <= r * r + eps
    if kind == "cylinder":
        c = _vec3(shape.get("center"), dim, "center")
        ax = AXES[shape.get("axis", "z")]
        r = float(shape["radius"]) - shrink
        radial = [d for d in range(dim) if d != ax]
        m = np.sum((pts[:, radial] - c[radial]) ** 2, axis=1) <= r * r + eps
        if ax < dim:
            a0 = float(shape.get("length_min", -np.inf)) + shrink
            a1 = float(shape.get("length_max", np.inf)) - shrink
            m &= (pts[:, ax] >= a0 - eps) & (pts[:, ax] <= a1 + eps)
        return m
    raise SpecError(f"unknown shape '{kind}'")


def fill_shape(shape: Dict, dp: float, dim: int) -> np.ndarray:
    lo, hi = shape_bounds(shape, dim)
    pts = lattice_box(lo, hi, dp, dim)
    return pts[shape_contains(shape, pts, dim)]


# --------------------------------------------------------------------------
# particle blocks
# --------------------------------------------------------------------------
def _block(pts: np.ndarray, **scalars) -> Dict[str, np.ndarray]:
    n = len(pts)
    blk = {k: np.zeros(n) for k in FIELD_NAMES}
    blk["x"], blk["y"], blk["z"] = pts[:, 0].copy(), pts[:, 1].copy(), pts[:, 2].copy()
    for k, v in scalars.items():
        if k in blk:
            blk[k] = np.broadcast_to(np.asarray(v, dtype=float), (n,)).copy()
    return blk


def make_fluid(f: Dict, spec: Dict) -> Dict[str, np.ndarray]:
    dim, dp = spec["dim"], spec["dp"]
    pts = fill_shape(f, dp, dim)
    rho_ref = float(f.get("rho_ref", 1000.0))
    vel = _vec3(f.get("velocity", [0, 0, 0]), dim, "velocity")
    blk = _block(
        pts,
        m=rho_ref * dp ** 3,
        p_type=int(f.get("p_type", P_TYPE_FLUID)),
        h=spec["h_factor"] * dp,
        temp=float(f.get("temp", 293.15)),
        rho=rho_ref,
        rho_ref=rho_ref,
        ux=vel[0], uy=vel[1], uz=vel[2],
    )
    if f.get("hydrostatic"):
        g = np.asarray(spec.get("gravity", [0, -9.81, 0]), float)
        gmag = float(np.linalg.norm(g))
        up = AXES.get(f.get("up_axis", "y" if dim == 2 else "z"), 1)
        lo, hi = shape_bounds(f, dim)
        ys = float(f.get("free_surface", hi[up]))
        blk["pres"] = np.maximum(rho_ref * gmag * (ys - pts[:, up]), 0.0)
    return blk


def make_wall(w: Dict, spec: Dict) -> Dict[str, np.ndarray]:
    dim, dp = spec["dim"], spec["dp"]
    kind = w.get("kind", "container")
    layers = w.get("layers", 3)
    if kind == "container":
        lo = _vec3(w.get("min"), dim, "min")
        hi = _vec3(w.get("max"), dim, "max")
        L = int(layers) * dp
        elo, ehi = lo.copy(), hi.copy()
        open_faces = set(w.get("open_faces", []))
        for name, d in AXES.items():
            if d >= dim:
                continue
            if f"{name}-" not in open_faces:
                elo[d] -= L
            if f"{name}+" not in open_faces:
                ehi[d] += L
        pts = lattice_box(elo, ehi, dp, dim)
        inside = np.ones(len(pts), bool)
        for d in range(dim):
            inside &= (pts[:, d] > lo[d]) & (pts[:, d] < hi[d])
        pts = pts[~inside]
    elif kind == "solid":
        pts = fill_shape(w, dp, dim)
        if layers != "full":
            core = shape_contains(w, pts, dim, shrink=int(layers) * dp)
            pts = pts[~core]
    else:
        raise SpecError(f"unknown wall kind '{kind}'")
    rho_ref = float(w.get("rho_ref", 1000.0))
    vel = _vec3(w.get("velocity", [0, 0, 0]), dim, "velocity")
    return _block(
        pts,
        m=rho_ref * dp ** 3,
        p_type=int(w.get("p_type", P_TYPE_BOUNDARY)),
        h=spec["h_factor"] * dp,
        temp=float(w.get("temp", 293.15)),
        rho=rho_ref,
        rho_ref=rho_ref,
        ux=vel[0], uy=vel[1], uz=vel[2],
    )


def make_dem(d: Dict, spec: Dict, idx_start: int) -> Dict[str, np.ndarray]:
    dim, dp = spec["dim"], spec["dp"]
    r = float(d["radius"])
    region = d["region"]
    lo, hi = shape_bounds(region, dim)
    s = 2.0 * r
    if d.get("arrangement", "cubic") == "hex" and dim >= 2:
        # hexagonal (2-D) / staggered layers (3-D): row spacing sqrt(3) r
        pts = []
        dy = math.sqrt(3.0) * r
        ny = int(math.floor((hi[1] - lo[1] - 2 * r) / dy + 1e-9)) + 1
        for j in range(max(ny, 0)):
            off = r if j % 2 else 0.0
            row_lo = lo.copy()
            row_lo[0] += off
            row_lo[1] = lo[1] + j * dy
            row_hi = hi.copy()
            row_hi[1] = row_lo[1] + s
            pts.append(lattice_box(row_lo, row_hi, s, dim))
        pts = np.vstack(pts) if pts else np.zeros((0, 3))
    else:
        pts = lattice_box(lo, hi, s, dim)
    pts = pts[shape_contains(region, pts, dim, shrink=r)]
    jitter = float(d.get("jitter", 0.0))
    if jitter > 0 and len(pts):
        rng = np.random.default_rng(int(d.get("seed", 0)))
        pts[:, :dim] += rng.uniform(-jitter * r, jitter * r, size=(len(pts), dim))
    rho = float(d.get("rho", 2500.0))
    temp = float(d.get("temp", 293.15))
    vol = (4.0 / 3.0) * math.pi * r ** 3
    n = len(pts)
    blk = _block(
        pts,
        m=rho * vol,
        p_type=P_TYPE_DEM,
        h=spec["h_factor"] * dp,
        temp=temp,
        rho=rho,
        rho_ref=rho,
        rad=r,
        ri=float(d.get("ri", r)),
        temp1=temp, temp2=temp, temp3=temp,
        vol_power=float(d.get("vol_power", 0.0)),
    )
    blk["dem_idx"] = idx_start + np.arange(n, dtype=float)
    return blk


def make_open_boundary(o: Dict, spec: Dict) -> Dict[str, np.ndarray]:
    dim, dp = spec["dim"], spec["dp"]
    ax = AXES[o.get("axis", "x")]
    outward = 1.0 if float(o.get("outward", -1)) > 0 else -1.0
    layers = int(o.get("layers", 4))
    pos = float(o["position"])
    lo = _vec3(o.get("span_min"), dim, "span_min")
    hi = _vec3(o.get("span_max"), dim, "span_max")
    lo[ax], hi[ax] = 0.0, dp  # dummy one-cell thickness along the normal
    base = lattice_box(lo, hi, dp, dim)
    layers_pts = []
    for k in range(layers):
        p = base.copy()
        p[:, ax] = pos + outward * (k + 0.5) * dp
        layers_pts.append(p)
    pts = np.vstack(layers_pts) if layers_pts else np.zeros((0, 3))
    rho_ref = float(o.get("rho_ref", 1000.0))
    vel = _vec3(o.get("velocity", [0, 0, 0]), dim, "velocity")
    btype = 1 if o.get("type", "inlet") == "inlet" else 2
    return _block(
        pts,
        m=rho_ref * dp ** 3,
        p_type=int(o.get("p_type", P_TYPE_FLUID)),
        h=spec["h_factor"] * dp,
        temp=float(o.get("temp", 293.15)),
        pres=float(o.get("pres", 0.0)),
        rho=rho_ref,
        rho_ref=rho_ref,
        ux=vel[0], uy=vel[1], uz=vel[2],
        buffer_type=btype,
    )


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------
def normalize_spec(spec: Dict) -> Dict:
    spec = dict(spec)
    dim = int(spec.get("dim", 2))
    if dim not in (2, 3):
        raise SpecError("dim must be 2 or 3")
    dp = float(spec.get("dp", 0))
    if dp <= 0:
        raise SpecError("dp must be > 0")
    spec["dim"], spec["dp"] = dim, dp
    spec["h_factor"] = float(spec.get("h_factor", 1.6))
    for key in ("fluids", "walls", "dem", "open_boundaries"):
        spec[key] = list(spec.get(key) or [])
    if not (spec["fluids"] or spec["walls"] or spec["dem"]):
        raise SpecError("spec has no fluids, walls or dem blocks")
    return spec


def _concat(blocks: List[Dict[str, np.ndarray]]) -> Dict[str, np.ndarray]:
    blocks = [b for b in blocks if len(b["x"])]
    if not blocks:
        return {k: np.zeros(0) for k in FIELD_NAMES}
    return {k: np.concatenate([b[k] for b in blocks]) for k in FIELD_NAMES}


def generate(spec: Dict, max_particles: int = 20_000_000) -> Dict[str, np.ndarray]:
    """Build all particle fields from a geometry spec."""
    spec = normalize_spec(spec)
    dim = spec["dim"]

    walls = [make_wall(w, spec) for w in spec["walls"]]
    obc = [make_open_boundary(o, spec) for o in spec["open_boundaries"]]
    dem_blocks, idx = [], int(spec.get("dem_idx_start", 0))
    for d in spec["dem"]:
        b = make_dem(d, spec, idx)
        idx += len(b["x"])
        dem_blocks.append(b)

    fluids = []
    solids = [w for w in spec["walls"] if w.get("kind") == "solid"]
    for f in spec["fluids"]:
        b = make_fluid(f, spec)
        if solids and len(b["x"]):
            pts = np.column_stack([b["x"], b["y"], b["z"]])
            keep = np.ones(len(pts), bool)
            for s in solids:
                keep &= ~shape_contains(s, pts, dim)
            b = {k: v[keep] for k, v in b.items()}
        if f.get("exclude_dem", True) and dem_blocks and len(b["x"]):
            for d in spec["dem"]:
                pts = np.column_stack([b["x"], b["y"], b["z"]])
                keep = ~shape_contains(d["region"], pts, dim)
                b = {k: v[keep] for k, v in b.items()}
        fluids.append(b)

    fields = _concat(walls + fluids + dem_blocks + obc)
    if len(fields["x"]) > max_particles:
        raise SpecError(f"too many particles ({len(fields['x']):,} > {max_particles:,}); increase dp")
    if dim == 2:
        fields["z"][:] = 0.0
        fields["uz"][:] = 0.0
    return fields


def summarize(fields: Dict[str, np.ndarray], spec: Dict) -> Dict:
    p = fields["p_type"]
    n = len(p)
    out = {
        "total": int(n),
        "fluid": int(np.sum((p == P_TYPE_FLUID) & (fields["buffer_type"] == 0))),
        "boundary": int(np.sum(p == P_TYPE_BOUNDARY)),
        "dem": int(np.sum(p == P_TYPE_DEM)),
        "buffer_inlet": int(np.sum(fields["buffer_type"] == 1)),
        "buffer_outlet": int(np.sum(fields["buffer_type"] == 2)),
        "columns": select_columns(fields, spec.get("extra_columns")),
    }
    if n:
        pts = np.column_stack([fields["x"], fields["y"], fields["z"]])
        out["bbox_min"] = pts.min(axis=0).round(9).tolist()
        out["bbox_max"] = pts.max(axis=0).round(9).tolist()
        out["total_mass"] = float(fields["m"].sum())
    return out


def build_input_file(spec: Dict, out_path: str) -> Dict:
    """Generate particles and write input.txt. Returns a summary dict."""
    fields = generate(spec)
    cols = select_columns(fields, spec.get("extra_columns"))
    write_input_txt(out_path, fields, cols)
    return summarize(fields, normalize_spec(spec))


def preview_points(fields: Dict[str, np.ndarray], dim: int = 2, limit: int = 20000) -> List[List[float]]:
    """Subsampled [x, y, z, p_type, buffer_type, rad] rows for the web preview.

    3-D: returns the x-z mid-plane slice (the lattice plane y* closest to the
    domain centre, plus every DEM sphere that the plane y = y* cuts), so the
    interior is visible instead of being hidden behind the walls.
    """
    n = len(fields["x"])
    idx = np.arange(n)
    if dim == 3 and n:
        y = fields["y"]
        y_mid = 0.5 * (y.min() + y.max())
        lattice = fields["p_type"] != P_TYPE_DEM
        ys = y[lattice][np.argmin(np.abs(y[lattice] - y_mid))] if lattice.any() else y_mid
        in_plane = lattice & (np.abs(y - ys) < 1e-9)
        cut_dem = (~lattice) & (np.abs(y - ys) <= fields["rad"])
        idx = idx[in_plane | cut_dem]
    step = max(1, len(idx) // limit)
    idx = idx[::step]
    return np.column_stack([
        fields["x"][idx], fields["y"][idx], fields["z"][idx],
        fields["p_type"][idx], fields["buffer_type"][idx], fields["rad"][idx],
    ]).round(7).tolist()


if __name__ == "__main__":  # CLI: python particle_gen.py spec.json input.txt
    import json
    import sys

    if len(sys.argv) != 3:
        print("usage: python particle_gen.py <spec.json> <input.txt>")
        sys.exit(1)
    with open(sys.argv[1], encoding="utf-8") as fh:
        s = json.load(fh)
    print(json.dumps(build_input_file(s, sys.argv[2]), indent=2))
