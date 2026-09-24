"""Claude API layer: paper analysis and geometry-spec extraction.

The LLM is used ONLY to read the paper and return structured JSON through
forced tool use (``tool_choice={"type": "tool", ...}``). It never produces
particle coordinates; that is done by ``particle_gen.py``.

Forced tool use means the response always contains a ``tool_use`` block whose
``input`` is already a parsed dict, so no client-side ``json.loads`` is needed.
"""
from __future__ import annotations

import base64
import os
from typing import Dict, List

from sophia_format import SOPHIA_INDEX

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
MAX_TOKENS = int(os.environ.get("ANTHROPIC_MAX_TOKENS", "8000"))

_SHAPE = {
    "type": "object",
    "description": "box {shape,min,max} | cylinder {shape,axis,center,radius,length_min,length_max} | sphere {shape,center,radius}",
    "properties": {
        "shape": {"type": "string", "enum": ["box", "cylinder", "sphere"]},
        "min": {"type": "array", "items": {"type": "number"}},
        "max": {"type": "array", "items": {"type": "number"}},
        "center": {"type": "array", "items": {"type": "number"}},
        "radius": {"type": "number"},
        "axis": {"type": "string", "enum": ["x", "y", "z"]},
        "length_min": {"type": "number"},
        "length_max": {"type": "number"},
    },
    "required": ["shape"],
}

REPORT_CASES_TOOL = {
    "name": "report_cases",
    "description": "Report every simulation or experimental case in the paper that could be reproduced with SOPHIA.",
    "input_schema": {
        "type": "object",
        "properties": {
            "paper_title": {"type": "string"},
            "summary": {"type": "string", "description": "2-3 sentence summary of the paper (Korean)."},
            "cases": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "short slug, e.g. case1_dam_break"},
                        "name": {"type": "string"},
                        "kind": {"type": "string", "enum": ["simulation", "experiment", "validation"]},
                        "dim": {"type": "integer", "enum": [2, 3]},
                        "physics": {"type": "array", "items": {"type": "string"}},
                        "geometry": {"type": "string", "description": "domain shape and dimensions with units"},
                        "parameters": {"type": "string", "description": "dp, fluid properties, BCs, temperatures, velocities"},
                        "source_location": {"type": "string", "description": "section / figure / table in the paper"},
                        "sophia_feasibility": {"type": "string", "description": "what can/cannot be represented; missing info"},
                    },
                    "required": ["id", "name", "dim", "geometry", "parameters"],
                },
            },
        },
        "required": ["paper_title", "cases"],
    },
}

BUILD_SPEC_TOOL = {
    "name": "build_spec",
    "description": "Return the geometry spec consumed by the deterministic particle generator.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "dim": {"type": "integer", "enum": [2, 3]},
            "dp": {"type": "number", "description": "initial particle spacing [m]"},
            "h_factor": {"type": "number", "description": "h = h_factor*dp, default 1.6"},
            "gravity": {"type": "array", "items": {"type": "number"}},
            "fluids": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["shape"],
                    "properties": {
                        **_SHAPE["properties"],
                        "rho_ref": {"type": "number"},
                        "temp": {"type": "number", "description": "[K]"},
                        "velocity": {"type": "array", "items": {"type": "number"}},
                        "hydrostatic": {"type": "boolean"},
                        "free_surface": {"type": "number"},
                    },
                },
            },
            "walls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        **_SHAPE["properties"],
                        "kind": {"type": "string", "enum": ["container", "solid"]},
                        "layers": {"type": ["integer", "string"], "description": "number of wall layers or 'full'"},
                        "open_faces": {"type": "array", "items": {"type": "string", "enum": ["x-", "x+", "y-", "y+", "z-", "z+"]}},
                        "rho_ref": {"type": "number"},
                        "temp": {"type": "number"},
                    },
                    "required": ["kind"],
                },
            },
            "dem": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "region": _SHAPE,
                        "radius": {"type": "number"},
                        "rho": {"type": "number"},
                        "arrangement": {"type": "string", "enum": ["cubic", "hex"]},
                        "temp": {"type": "number"},
                        "vol_power": {"type": "number"},
                        "jitter": {"type": "number"},
                    },
                    "required": ["region", "radius"],
                },
            },
            "open_boundaries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["inlet", "outlet"]},
                        "axis": {"type": "string", "enum": ["x", "y", "z"]},
                        "position": {"type": "number"},
                        "outward": {"type": "integer", "enum": [-1, 1]},
                        "span_min": {"type": "array", "items": {"type": "number"}},
                        "span_max": {"type": "array", "items": {"type": "number"}},
                        "layers": {"type": "integer"},
                        "velocity": {"type": "array", "items": {"type": "number"}},
                        "rho_ref": {"type": "number"},
                        "temp": {"type": "number"},
                    },
                    "required": ["type", "axis", "position", "span_min", "span_max"],
                },
            },
            "assumptions": {"type": "array", "items": {"type": "string"}, "description": "values not stated in the paper that you assumed (Korean)"},
        },
        "required": ["name", "dim", "dp", "fluids", "walls"],
    },
}

_INDEX_TEXT = "\n".join(f"{k}={v[0]}" for k, v in SOPHIA_INDEX.items())

SYSTEM_PROMPT = f"""You are an assistant at SNU ESLAB that prepares inputs for SOPHIA,
a GPU SPH/DEM code (C++/CUDA). You read research papers and extract simulation
setups. You NEVER output particle coordinates; you only output structured specs
through the provided tool. Use SI units. When the paper omits a value, choose a
physically reasonable one and list it in `assumptions`.

SOPHIA input.txt index mapping (lab-confirmed, latest):
{_INDEX_TEXT}
p_type: 0=boundary, 1=fluid, 1001=DEM. m = rho_ref*dp^3, h = 1.6*dp.
Open boundaries use 4 buffer layers extending outward from the boundary plane
(ESLAB OpenBC / Tafuni et al. 2018)."""


def _client():
    import anthropic

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set (see .env.example)")
    return anthropic.Anthropic(api_key=key)


def _pdf_block(pdf_bytes: bytes) -> Dict:
    return {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.standard_b64encode(pdf_bytes).decode("ascii"),
        },
    }


def _call_tool(tool: Dict, content: List[Dict], model: str | None = None) -> Dict:
    resp = _client().messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{"role": "user", "content": content}],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == tool["name"]:
            return block.input
    raise RuntimeError(f"model did not call {tool['name']} (stop_reason={resp.stop_reason})")


def analyze_paper(pdf_bytes: bytes, manual_context: str = "") -> Dict:
    text = (
        "첨부 논문에서 SOPHIA로 재현 가능한 시뮬레이션/실험 케이스를 모두 찾아 "
        "report_cases 도구로 보고하십시오. 설명 문자열은 한국어로 작성하십시오."
    )
    if manual_context:
        text += "\n\n[SOPHIA manual excerpts]\n" + manual_context
    return _call_tool(REPORT_CASES_TOOL, [_pdf_block(pdf_bytes), {"type": "text", "text": text}])


def build_spec(pdf_bytes: bytes, case: Dict, manual_context: str = "", notes: str = "") -> Dict:
    import json

    text = (
        "아래 케이스의 초기 입자 배치를 위한 geometry spec을 build_spec 도구로 반환하십시오.\n"
        f"[선택 케이스]\n{json.dumps(case, ensure_ascii=False, indent=2)}\n"
        "- 좌표는 [m] 단위, 2-D는 x-y 평면(z=0)입니다.\n"
        "- 벽은 container(유체를 둘러싼 용기) 또는 solid(장애물)로 표현하십시오.\n"
        "- dp는 논문 값을 우선 사용하고, 없으면 대표 길이의 1/50~1/100 수준으로 가정하십시오."
    )
    if notes:
        text += f"\n[사용자 요청]\n{notes}"
    if manual_context:
        text += "\n\n[SOPHIA manual excerpts]\n" + manual_context
    return _call_tool(BUILD_SPEC_TOOL, [_pdf_block(pdf_bytes), {"type": "text", "text": text}])
