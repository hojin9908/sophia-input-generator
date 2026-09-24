"""SOPHIA input.txt column index mapping and writer.

The mapping below is the lab-confirmed latest version. It supersedes the
SOPHIA User Guide Table 5.1.1, whose indices 27-34 are outdated.

File format
-----------
* Tab separated.
* Row 1 (header) holds the *index numbers* of the variables in each column,
  e.g. ``1  2  3  4 ...``. SOPHIA maps each column by this number, so only
  the columns that are needed have to be written.
* Rows 2.. hold one particle each.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np

# index -> (variable name, dtype, description)
SOPHIA_INDEX: Dict[int, tuple] = {
    1: ("x", "f", "x-coordinate [m]"),
    2: ("y", "f", "y-coordinate [m]"),
    3: ("z", "f", "z-coordinate [m]"),
    4: ("ux", "f", "x-velocity [m/s]"),
    5: ("uy", "f", "y-velocity [m/s]"),
    6: ("uz", "f", "z-velocity [m/s]"),
    7: ("m", "f", "mass [kg]"),
    8: ("p_type", "i", "particle type (0=boundary, 1=fluid, 1001=DEM)"),
    9: ("h", "f", "smoothing length [m]"),
    10: ("temp", "f", "temperature [K]"),
    11: ("pres", "f", "pressure [Pa]"),
    12: ("rho", "f", "density [kg/m3]"),
    13: ("rho_ref", "f", "reference density [kg/m3]"),
    14: ("ftotal", "f", "magnitude of acceleration"),
    15: ("concn", "f", "concentration"),
    16: ("cc", "f", "color code"),
    17: ("vist", "f", "turbulent viscosity"),
    18: ("ct_boundary", "i", "constant-temperature wall flag"),
    19: ("hf_boundary", "i", "heat-flux wall flag"),
    20: ("lbl_surf", "i", "free-surface particle flag"),
    21: ("drho", "f", "total derivative of density"),
    22: ("denthalpy", "f", "total derivative of enthalpy"),
    23: ("dconcn", "f", "total derivative of concentration"),
    24: ("dk", "f", "total derivative of turbulence kinetic energy"),
    25: ("de", "f", "total derivative of turbulence dissipation"),
    26: ("concn_a", "f", "He concentration"),
    # ---- DEM-inclusive block (latest version) ----
    27: ("rad", "f", "DEM particle radius [m] (inp_rad)"),
    28: ("ri", "f", "DEM ri (inp_ri)"),
    29: ("dem_idx", "i", "DEM particle index (inp_demidxmk)"),
    30: ("wx", "f", "angular velocity x [rad/s] (inp_wx)"),
    31: ("wy", "f", "angular velocity y [rad/s] (inp_wy)"),
    32: ("wz", "f", "angular velocity z [rad/s] (inp_wz)"),
    33: ("temp1", "f", "DEM temperature 1 (inp_temp1)"),
    34: ("temp2", "f", "DEM temperature 2 (inp_temp2)"),
    35: ("temp3", "f", "DEM temperature 3 (inp_temp3)"),
    36: ("vol_power", "f", "volumetric power [W/m3] (inp_vol_power)"),
    # ---- open boundary only ----
    37: ("buffer_type", "i", "open-boundary buffer type (0=none, 1=inlet, 2=outlet)"),
}

NAME_TO_INDEX = {v[0]: k for k, v in SOPHIA_INDEX.items()}

P_TYPE_BOUNDARY = 0
P_TYPE_FLUID = 1
P_TYPE_DEM = 1001

BASE_COLUMNS: List[int] = list(range(1, 14))  # 1..13 always written
DEM_COLUMNS: List[int] = list(range(27, 37))  # 27..36 only when DEM exists
OPENBC_COLUMNS: List[int] = [37]  # only when open-boundary particles exist


def select_columns(fields: Dict[str, np.ndarray], extra: List[int] | None = None) -> List[int]:
    """Decide which index columns to write (conditional column output)."""
    cols = list(BASE_COLUMNS)
    if extra:
        cols += [c for c in extra if c not in cols and 14 <= c <= 26]
    p_type = fields["p_type"]
    if np.any(p_type == P_TYPE_DEM):
        cols += DEM_COLUMNS
    if "buffer_type" in fields and np.any(fields["buffer_type"] > 0):
        cols += OPENBC_COLUMNS
    return sorted(cols)


def write_input_txt(path: str, fields: Dict[str, np.ndarray], columns: List[int]) -> None:
    """Write ``fields`` (name -> 1D array of length N) to a SOPHIA input.txt."""
    n = len(fields["x"])
    data = []
    fmts = []
    for idx in columns:
        name, kind, _ = SOPHIA_INDEX[idx]
        arr = fields.get(name)
        if arr is None:
            arr = np.zeros(n)
        data.append(np.asarray(arr))
        fmts.append("%d" if kind == "i" else "%.8e")
    # integer columns are stored exactly in float64, so "%d" is lossless here
    table = np.column_stack(data).astype(np.float64) if n else np.zeros((0, len(columns)))
    header = "\t".join(str(c) for c in columns)
    with open(path, "w", newline="\n") as f:
        f.write(header + "\n")
        if n:
            np.savetxt(f, table, fmt=fmts, delimiter="\t")
