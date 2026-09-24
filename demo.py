"""Demo mode (SOPHIA_DEMO=1): try the whole UI without an Anthropic API key.

Any uploaded PDF is ignored; the three bundled example specs in ``examples/``
are returned as if they had been extracted from a paper.
"""
from __future__ import annotations

import json
import os
from typing import Dict

EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples")

_CASES = [
    {
        "id": "dam_break_2d",
        "name": "2-D Dam Break",
        "kind": "validation",
        "dim": 2,
        "physics": ["free surface", "hydrostatic"],
        "geometry": "수조 1.61 m × 0.8 m, 수주 0.6 m × 0.6 m (좌측)",
        "parameters": "dp = 0.01 m, ρ = 1000 kg/m³, T = 293.15 K, 벽 3층, 상부 개방",
        "source_location": "[데모] examples/dam_break_2d.json",
        "sophia_feasibility": "그대로 재현 가능",
    },
    {
        "id": "channel_openbc_2d",
        "name": "2-D Channel Flow past a Cylinder (Open BC)",
        "kind": "simulation",
        "dim": 2,
        "physics": ["open boundary", "inlet/outlet", "obstacle"],
        "geometry": "채널 0.5 m × 0.1 m, 원기둥 R = 0.015 m @ (0.2, 0.05)",
        "parameters": "dp = 0.005 m, 입구 속도 0.1 m/s, buffer 4층 (inlet/outlet)",
        "source_location": "[데모] examples/channel_openbc_2d.json",
        "sophia_feasibility": "OpenBC buffer_type(37) 컬럼 사용",
    },
    {
        "id": "dem_bed_3d",
        "name": "3-D Heated Particle Bed (SPH–DEM)",
        "kind": "simulation",
        "dim": 3,
        "physics": ["DEM", "heat generation", "hydrostatic"],
        "geometry": "용기 0.08 × 0.08 × 0.14 m, 하부 0.04 m 구역에 DEM 구 배치",
        "parameters": "dp = 0.004 m, r = 4 mm, ρ_s = 2500 kg/m³, q''' = 1 MW/m³, T_s = 350 K",
        "source_location": "[데모] examples/dem_bed_3d.json",
        "sophia_feasibility": "DEM 컬럼(27–36) 사용",
    },
]


def analyze_paper(pdf_bytes: bytes, manual_context: str = "") -> Dict:
    return {
        "paper_title": "Demo mode — bundled example cases",
        "summary": "데모 모드입니다. 업로드한 PDF 대신 저장소에 포함된 예제 케이스 3종을 보여줍니다.",
        "cases": _CASES,
    }


def build_spec(pdf_bytes: bytes, case: Dict, manual_context: str = "", notes: str = "") -> Dict:
    with open(os.path.join(EXAMPLES_DIR, f"{case['id']}.json"), encoding="utf-8") as fh:
        spec = json.load(fh)
    spec["assumptions"] = ["[데모] 예제 spec을 그대로 반환했습니다."]
    return spec
