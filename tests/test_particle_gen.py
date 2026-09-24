import json
import math
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import particle_gen as pg  # noqa: E402
from sophia_format import select_columns, write_input_txt  # noqa: E402


def load(name):
    with open(os.path.join(ROOT, "examples", name), encoding="utf-8") as f:
        return json.load(f)


def test_dam_break_counts_and_mass():
    spec = load("dam_break_2d.json")
    f = pg.generate(spec)
    fluid = f["p_type"] == 1
    # 0.6/0.01 = 60 cells per side -> 3600 fluid particles
    assert fluid.sum() == 3600
    dp = spec["dp"]
    assert np.allclose(f["m"][fluid], 1000 * dp ** 3)          # m = rho_ref dp^3
    assert np.allclose(f["h"], 1.6 * dp)                         # h = 1.6 dp
    assert np.all(f["z"] == 0)                                   # 2-D
    # first fluid particle centre at dp/2
    assert math.isclose(f["x"][fluid].min(), 0.005, abs_tol=1e-12)
    # hydrostatic p = rho g (H - y) at the lowest row
    p_bottom = f["pres"][fluid][np.argmin(f["y"][fluid])]
    assert math.isclose(p_bottom, 1000 * 9.81 * (0.6 - 0.005), rel_tol=1e-9)


def test_container_has_no_overlap_with_fluid():
    f = pg.generate(load("dam_break_2d.json"))
    pts = np.column_stack([f["x"], f["y"]])
    wall = pts[f["p_type"] == 0]
    fluid = pts[f["p_type"] == 1]
    # min distance between any wall and fluid particle should be ~dp
    d = np.min(np.linalg.norm(wall[:, None, :] - fluid[None, ::7, :], axis=2))
    assert d >= 0.01 - 1e-9
    # open top: no wall particle above y = 0.8
    assert wall[:, 1].max() < 0.8


def test_openbc_layers_and_columns(tmp_path):
    spec = load("channel_openbc_2d.json")
    f = pg.generate(spec)
    inlet = f["buffer_type"] == 1
    outlet = f["buffer_type"] == 2
    ny = int(round(0.1 / 0.005))
    assert inlet.sum() == 4 * ny and outlet.sum() == 4 * ny     # 4 buffer layers
    assert f["x"][inlet].max() < 0 and f["x"][outlet].min() > 0.5
    cols = select_columns(f)
    assert 37 in cols and 27 not in cols                         # conditional columns
    # obstacle removed fluid inside the cylinder
    fl = f["p_type"] == 1
    r = np.hypot(f["x"][fl & ~inlet & ~outlet] - 0.2, f["y"][fl & ~inlet & ~outlet] - 0.05)
    assert r.min() > 0.015
    out = tmp_path / "input.txt"
    write_input_txt(str(out), f, cols)
    header = out.read_text().splitlines()[0].split("\t")
    assert header == [str(c) for c in cols]


def test_dem_block_and_determinism(tmp_path):
    spec = load("dem_bed_3d.json")
    a = pg.generate(spec)
    b = pg.generate(spec)
    for k in a:
        assert np.array_equal(a[k], b[k])                        # deterministic
    dem = a["p_type"] == 1001
    assert dem.sum() > 0
    r = 0.004
    assert np.allclose(a["m"][dem], 2500 * 4 / 3 * math.pi * r ** 3)
    assert np.array_equal(a["dem_idx"][dem], np.arange(dem.sum()))
    assert np.all(a["rad"][~dem] == 0)                           # zeros for non-DEM
    cols = select_columns(a)
    assert cols[-10:] == list(range(27, 37)) and 37 not in cols
    # fluid excluded from the DEM region
    fl = a["p_type"] == 1
    assert a["z"][fl].min() > 0.04


def test_bad_spec():
    with pytest.raises(pg.SpecError):
        pg.generate({"dim": 2, "dp": 0, "fluids": []})
