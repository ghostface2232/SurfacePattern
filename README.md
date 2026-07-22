# SurfacePattern

Rhino 8용 파이썬 플러그인. 서피스/폴리서피스 위에 커브 패턴(타공 그리드, 하프톤, 스탬프)을
Eto 패널에서 실시간 프리뷰로 확인하며 그립니다. 제품 디자이너의 타공 패널·그라데이션 패턴
작업을 목표로 합니다.

## 주요 기능

- **타겟 선택** — 서피스, 폴리서피스, 서브 페이스(Ctrl+Shift 픽) 단위 선택
- **Grid 엔진** — 원/슬롯/육각 도형의 규칙 배열
  - 격자: square / staggered / triangular
  - 간격은 도형 가장자리 사이의 실질 간격(Gap) 기준 (0 = 도형이 맞닿음)
  - 위치·크기·회전 지터(시드 고정 랜덤)
- **Halftone 엔진** — 어트랙터(문서의 점/커브)와의 거리로 도형 크기를 변조
  - 감쇠 프로파일: linear / smooth / gaussian, 반전(invert) 지원
  - 어트랙터가 없으면 각 면의 UV 중심 기준으로 즉시 동작
  - 최소 크기 이하 도형 컬링(cull)
- **배치 모드**
  - `uv` — 면의 UV 공간에 배치, 1차 미분 기반 왜곡 보정으로 곡면에서도 mm 간격 유지 (단일 면 기본값)
  - `world` — 공통 평면에 격자를 만들어 면에 투영, 폴리서피스에서 이음매 없는 패턴 (다중 면 기본값)
- **실시간 프리뷰** — DisplayConduit 기반, 문서를 건드리지 않음
  - 드래그 중: 폴리라인 근사(draft, 최대 1500개 표시) / 조작 종료 후: 서피스에 풀백된 NURBS(full)
- **에러 UX** — 전체 트레이스백은 `~/.surfacepattern/log.txt`에 기록, 패널에는 붉은 알림 한 줄

## 요구 사항

- Rhino 8 (Windows) — 내장 CPython 3 (ScriptEditor)
- numpy (하프톤 가속용, 없어도 순수 파이썬 폴백으로 동작)

## 실행 방법

1. `SurfacePattern.rhproj`를 Rhino 8 ScriptEditor에서 엽니다.
2. `commands/SurfacePattern_cmd.py`를 실행하면 패널이 열립니다.
3. 패널에서:
   1. **Pick Targets** — 대상 서피스/폴리서피스 선택
   2. 모드 선택 — **Grid / Halftone / Stamp**(예정)
   3. **Layout** 섹션 — 도형·간격·격자 타입·지터 등 격자 공통 파라미터 (Grid·Halftone 공용)
   4. Halftone이면 **Pick Attractors**로 점/커브 지정 후 Radius·Size Min/Max 등 조절
   5. 뷰포트의 주황색 프리뷰로 확인 → **Bake**(예정)로 문서에 확정

개발용 커맨드(문서에 쓰지 않는 시각 확인용):

| 커맨드 | 용도 |
| --- | --- |
| `SPDev_TestMapping_cmd` | 매핑 코어 검증 — 면 크기에 맞춘 원형 타공 그리드를 문서에 추가 |
| `SPDev_TestConduit_cmd` | 프리뷰 컨듀잇 검증 — 실행할 때마다 off → draft → full 순환 |

## 저장소 구조

```
SurfacePattern.rhproj            ScriptEditor 프로젝트 (라이브러리 + 커맨드 + 빌드 설정)
commands/                        Rhino 커맨드 진입점
  SurfacePattern_cmd.py          메인 패널 열기
  SPDev_Test*.py                 개발용 검증 커맨드
libraries/surfacepattern/        플러그인 패키지 본체
  core/
    session.py                   세션 싱글턴(sticky), 타겟/어트랙터 선택, 리컴퓨트 오케스트레이션
    mapping.py                   UV↔3D 매핑, 왜곡 보정, 월드 투영, 커브 배치/풀백 (지오메트리 평가 전담)
    errors.py                    파일 로깅 + 커맨드라인 알림
    bake.py                      문서 쓰기 전담 (예정)
  engine/
    grid.py                      규칙 격자 배치 생성 (파라미터 공간 전용)
    halftone.py                  어트랙터 거리 기반 크기 변조
    shapes.py                    단위 도형 (NURBS + draft 폴리라인)
    stamp.py                     (예정)
  preview/
    conduit.py                   DisplayConduit 2단 렌더 (draft/full)
  ui/
    panel.py                     Eto 패널 (모드리스 Form)
  io/
    presets.py                   프리셋 저장/불러오기 (예정)
```

## 아키텍처 원칙 (AGENTS.md 요약)

- **레이어링**: ui → session만 호출, engine은 파라미터 공간에서만 동작(3D 커브 생성·문서 접근 금지),
  모든 서피스 평가(PointAt/FrameAt/PullToBrepFace 등)는 `core/mapping.py`가 전담,
  preview는 문서에 쓰지 않으며 문서 쓰기는 `core/bake.py`만 허용
- **UV 정규화**: 모듈 경계를 넘는 UV는 항상 0–1 정규화 값
- **성능 계약**: 드래그 중에는 풀백 없는 draft만, 조작이 끝나면 full 리컴퓨트 (70ms/250ms 디바운스)
- **리로드 내성**: ScriptEditor 모듈 리로드에도 세션·컨듀잇·패널이 살아남도록 sticky + 이름 기반 타입 체크

## 현재 상태

| 마일스톤 | 상태 |
| --- | --- |
| 지오메트리 코어 (선택·매핑·왜곡 보정) | ✅ |
| 프리뷰 컨듀잇 (draft/full 2단) | ✅ |
| Grid 엔진 + 패널 연동 | ✅ |
| Eto 패널 (슬라이더·섹션·디바운스) | ✅ |
| Halftone 엔진 | ✅ |
| Stamp 엔진 | ⏳ 예정 |
| 프리셋 저장/불러오기 | ⏳ 예정 |
| Bake (문서 확정) | ⏳ 예정 |
