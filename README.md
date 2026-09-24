# SOPHIA Input Generator

논문 PDF를 올리면 그 안의 시뮬레이션/실험 케이스를 정리하고, 사용자가 고른 케이스를
GPU SPH/DEM 코드 **SOPHIA**(SNU ESLAB)의 초기 입자 파일 `input.txt`로 만들어 주는 Flask 웹 앱입니다.

```
PDF 업로드 ─► 케이스 목록 (Claude, report_cases) ─► 케이스 선택
          ─► geometry spec JSON (Claude, build_spec, 웹에서 편집 가능)
          ─► 결정론적 입자 생성 (particle_gen.py) ─► input.txt 다운로드
```

## 설계 원칙

| 구분 | 담당 | 이유 |
|---|---|---|
| 논문 해석, 형상·물성 추출 | Claude API (tool use, `tool_choice` 강제) | 비정형 텍스트 이해 |
| 입자 좌표 수천~수백만 개 생성 | `particle_gen.py` (NumPy) | LLM은 정밀 좌표를 신뢰성 있게 생성하지 못함 |
| 매뉴얼 참조 (RAG) | `rag.py` 순수 Python BM25 | 외부 임베딩 API 불필요 |

- 같은 spec을 넣으면 항상 같은 `input.txt`가 나옵니다 (DEM jitter도 seed 고정).
- `tool_choice`로 도구 호출을 강제하므로 응답이 이미 dict로 파싱되어 `json.loads`가 필요 없습니다.

## input.txt 형식

탭 구분, 첫 줄은 **변수 인덱스 번호**, 둘째 줄부터 입자 1개당 1줄입니다.
연구실 확인된 최신 매핑이며, User Guide Table 5.1.1의 27–34번은 구버전이므로 따르지 않습니다.

| 인덱스 | 변수 | 인덱스 | 변수 |
|---|---|---|---|
| 1–3 | x, y, z | 27 | rad |
| 4–6 | ux, uy, uz | 28 | ri |
| 7 | m | 29 | dem_idx |
| 8 | p_type (0=boundary, 1=fluid, 1001=DEM) | 30–32 | wx, wy, wz |
| 9 | h | 33–35 | temp1, temp2, temp3 |
| 10 | temp | 36 | vol_power |
| 11 | pres | 37 | buffer_type (1=inlet, 2=outlet) |
| 12, 13 | rho, rho_ref | 14–26 | ftotal … concn_a (`extra_columns`로 선택) |

**조건부 컬럼 출력**: 1–13은 항상, 27–36은 DEM 입자가 있을 때만, 37은 open boundary 입자가 있을 때만 씁니다.
SPH/경계 입자의 DEM 컬럼은 0입니다.

### 생성 규칙

- 격자(셀 중심): $x_i = x_{min} + (i + \tfrac12)\,d_p$
- 질량: $m = \rho_{ref}\, d_p^3$  — 예) $\rho_{ref}=1000$, $d_p=0.01$ → $m = 10^{-3}$ kg
- 스무딩 길이: $h = 1.6\, d_p$ — 예) $d_p=0.01$ → $h=0.016$ m
- 정수압(옵션): $p = \rho_{ref}\,|g|\,(y_s - y)$ — 예) 수심 0.6 m 바닥 첫 줄($y=0.005$) → $p = 1000·9.81·0.595 ≈ 5837$ Pa
- DEM 질량: $m = \rho \tfrac43 \pi r^3$
- Open boundary: 경계면에서 바깥쪽으로 4개 buffer 층, $x = x_b + n\,(k+\tfrac12)\,d_p$ (ESLAB OpenBC, Tafuni et al. 2018)

## 설치 및 실행

### Windows (로컬)

```bat
git clone https://github.com/<your-id>/sophia-input-generator.git
cd sophia-input-generator
copy .env.example .env        & rem ANTHROPIC_API_KEY 입력
run.bat
```

`run.bat`은 MSYS2의 `python`이 python.org Python 3.12를 가리는 문제를 피하려고 `py` 런처를 먼저 사용합니다.
직접 실행 시에도 `py -m pip install -r requirements.txt` → `py app.py` 순서를 권장합니다. 브라우저에서 http://127.0.0.1:5000 을 엽니다.

### Linux 서버 (sudo 불필요)

```bash
cp .env.example .env && vi .env   # ANTHROPIC_API_KEY
./start_server.sh                 # gunicorn --workers 1 --threads 8, 데몬 실행
crontab -e                        # 재부팅 자동 시작
# @reboot /home/<user>/sophia-input-generator/start_server.sh
```

> **worker는 반드시 1개**입니다. 세션 상태가 프로세스 메모리의 `SESSIONS` dict에 있으므로
> worker가 여러 개면 요청이 다른 프로세스로 가서 `session not found`가 납니다. 동시성은 `--threads`로 확보합니다.

### spec만으로 CLI 생성 (API 키 불필요)

```bash
python particle_gen.py examples/dam_break_2d.json input.txt
```

## 매뉴얼 RAG

`manuals/`에 SOPHIA User/Theory Guide(PDF·txt·md)를 넣으면 시작 시 BM25 인덱스를 만듭니다.
연구실 내부 문서이므로 저장소에는 포함하지 않습니다.

## geometry spec 예시

```json
{
  "name": "dam_break_2d", "dim": 2, "dp": 0.01, "h_factor": 1.6, "gravity": [0, -9.81, 0],
  "fluids": [{"shape": "box", "min": [0, 0], "max": [0.6, 0.6], "rho_ref": 1000, "hydrostatic": true}],
  "walls":  [{"kind": "container", "min": [0, 0], "max": [1.61, 0.8], "layers": 3, "open_faces": ["y+"]}],
  "dem": [], "open_boundaries": []
}
```

- shape: `box`(min/max), `cylinder`(axis, center, radius, length_min/max; 2-D에서는 원), `sphere`(center, radius)
- walls: `container`(유체를 감싸는 용기, `open_faces`로 개방면 지정) / `solid`(장애물, `layers` 또는 `"full"`)
- dem: `region`, `radius`, `rho`, `arrangement`(`cubic`|`hex`), `jitter`, `seed`, `vol_power`
- open_boundaries: `type`(inlet|outlet), `axis`, `position`, `outward`(±1), `span_min/max`, `layers`(기본 4)

전체 예시는 [`examples/`](examples/)를 참고하십시오.

## 파일 구조

```
app.py             Flask 라우트, SESSIONS
llm.py             Claude API: report_cases / build_spec 도구 정의
particle_gen.py    결정론적 입자 생성 엔진 (+CLI)
sophia_format.py   인덱스 매핑, input.txt writer
rag.py             BM25 매뉴얼 검색
templates/         웹 UI
examples/          spec 예시 (dam break, open BC 채널, DEM 베드)
tests/             pytest
run.bat            Windows 실행 스크립트
start_server.sh    Linux gunicorn 실행 스크립트
```

## 테스트

```bash
python -m pytest -q
```

## 확인이 필요한 가정

- open boundary buffer 입자의 `p_type`은 기본 1(fluid)이며 spec의 `p_type`으로 바꿀 수 있습니다.
- DEM `ri`는 기본값을 반지름과 같게, `dem_idx`는 0부터 부여합니다 (`dem_idx_start`로 변경).
- 2-D에서도 연구실 규칙에 따라 $m=\rho_{ref} d_p^3$을 사용합니다.
