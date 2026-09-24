"""Render docs/gallery.png from the bundled examples:  python scripts/make_gallery.py"""
import json, sys
import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
import numpy as np, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import EllipseCollection
import particle_gen as pg
C = {"fluid": "#2458d6", "boundary": "#475467", "dem": "#d97706", "inlet": "#16a34a", "outlet": "#dc2626"}
cases = [("dam_break_2d", "2-D dam break"), ("channel_openbc_2d", "2-D channel + cylinder (open BC)"), ("dem_bed_3d", "3-D SPH–DEM bed (mid-plane)")]
fig, axes = plt.subplots(1, 3, figsize=(15, 4.8), gridspec_kw={"width_ratios": [1.9, 3.2, 1.1]})
for ax, (name, title) in zip(axes, cases):
    spec = json.load(open(f"examples/{name}.json"))
    f = pg.generate(spec)
    P = np.array(pg.preview_points(f, spec["dim"], limit=10**7))
    vy = 2 if spec["dim"] == 3 else 1
    pt, bt = P[:, 3], P[:, 4]
    groups = [("boundary", (pt == 0)), ("fluid", (pt == 1) & (bt == 0)), ("inlet", bt == 1), ("outlet", bt == 2)]
    dp = spec["dp"]
    for key, m in groups:
        if m.any():
            ec = EllipseCollection(0.84 * dp, 0.84 * dp, 0, units="xy", offsets=P[m][:, [0, vy]], transOffset=ax.transData, facecolors=C[key], linewidths=0)
            ax.add_collection(ec)
    m = pt == 1001
    if m.any():
        d = 2 * P[m][:, 5]
        ax.add_collection(EllipseCollection(d, d, 0, units="xy", offsets=P[m][:, [0, vy]], transOffset=ax.transData, facecolors=C["dem"], edgecolors="#92400e", linewidths=0.6))
    ax.set_xlim(P[:, 0].min() - dp, P[:, 0].max() + dp); ax.set_ylim(P[:, vy].min() - dp, P[:, vy].max() + dp)
    ax.set_aspect("equal"); ax.axis("off")
    ax.set_title(f"{title}\n{len(f['x']):,} particles", fontsize=11, color="#1d2330")
handles = [plt.Line2D([], [], marker="o", ls="", color=v, label=k) for k, v in C.items()]
fig.legend(handles=handles, loc="lower center", ncol=5, frameon=False, fontsize=10)
fig.tight_layout(rect=(0, 0.07, 1, 0.97), w_pad=3)
fig.savefig("docs/gallery.png", dpi=110, facecolor="white")
