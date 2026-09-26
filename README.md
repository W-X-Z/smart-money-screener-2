# Smart Money Screener 2

연기금 일별 수급이 0에 가깝다가 장중에 갑자기 급증한 종목을 발굴하는 스크리너.

## 설치 (Mac, 키움 API 등록 IP에서 실행)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && open -e .env   # KIWOOM_APP_KEY / KIWOOM_SECRET_KEY 입력
```

## 스크리너 실행 (장중)

```bash
python -m screener.run                                   # 기본값
python -m screener.run --min-buy 300 --lookback 20 --quiet-max 30
```

| 옵션 | 기본값 | 의미 (금액 단위: 백만원) |
|---|---|---|
| `--min-buy` | 300 | 오늘 장중 연기금 순매수 하한 (3억) |
| `--lookback` | 20 | 침묵 판단 기간 (거래일, 오늘 제외) |
| `--quiet-max` | 30 | 침묵 기간 중 일별 \|연기금 순매수\| 상한 (0.3억) |

결과: `output/signals_YYYYMMDD_HHMM.csv`, 원본 응답 `output/raw_*.json`.

## 운영 구조

```
[일배치] GitHub Actions 평일 18:30 KST ── KRX 확정치 ──┐
                                                      ├→ public/data/signals.json 커밋 → Vercel 자동 배포
[장중]   회사 Mac (키움 잠정치) ──────────────────────┘
[웹]     Vercel 정적 페이지 → 종목 클릭 → /api/flow (Vercel 함수, 서울 리전) → KRX
```

DB 는 없습니다. 시그널 목록은 Git 에 커밋되는 JSON 파일 하나이고, 종목별 연기금 추이는 클릭할 때 조회합니다.

| 구성 | 파일 | 필요한 설정 |
|---|---|---|
| 일배치 | `batch/daily.py`, `.github/workflows/daily.yml` | GitHub Secrets `KRX_ID`, `KRX_PW` |
| 웹 | `public/index.html`, `vercel.json` | - |
| 클릭 조회 | `api/flow.py`, `api/_krx.py` | Vercel 환경변수 `KRX_ID`, `KRX_PW` |
| 장중 | `screener/run.py --push` | Mac `.env` 키움 키, `git push` 가능 |

### 최근 한 달 마이그레이션

GitHub → Actions → daily-pension-signals → Run workflow 에서 `from` / `to` 입력 (예: 20260826 / 20260925).
Mac 에서 직접: `.env` 에 `KRX_ID`, `KRX_PW` 추가 후 `python -m batch.daily --from 20260826 --to 20260925`.

### 장중 (Mac)

```bash
python -m screener.run --push   # 스캔 → 시그널 파일 커밋·푸시 → 웹 반영
```

로컬 미리보기: `python -m screener.web` (http://localhost:8000, 종목 추이는 키움에서 조회).

## 과거 검증 (backscan)

과거 일별 연기금 수급(ka10059, 약 200거래일)으로 기준값별 신호 빈도와 이후 수익률을 계산합니다.

```bash
python -m screener.backscan --limit 50   # 빠른 테스트
python -m screener.backscan              # 코스피+코스닥 전체 (첫 실행 20분 이상, 이후 캐시)
```

결과: `output/backscan_grid_*.csv` (기준값 조합별 신호수·주당 빈도·5/10/20일 수익률), `output/backscan_events_*.csv` (기본값 신호 목록).
ETF·ETN·스팩은 이름으로 제외하고, 4개 스레드로 초당 5회 이하로 호출합니다.
수익률은 **같은 날 전체 종목 중앙값 대비 초과수익률(x)**로 봅니다. 시장 국면 효과를 빼기 위해서입니다.

## 사용 API (키움 REST)

| API | 용도 | 단위 |
|---|---|---|
| ka10063 장중투자자별매매 | 오늘 연기금 잠정 순매수 (후보 발굴) | 백만원 |
| ka10059 종목별투자자기관별 | 종목의 과거 일별 연기금(`penfnd_etc`) 수급 100일 | 백만원 |
| ka10099 종목정보 리스트 | backscan 대상 종목 목록 | - |
| ka10065 장중투자자별매매상위 | 연기금(6000) 순매수 상위 — ka10063 연기금 코드 검증용 | 주 |

`tools/kiwoom_probe.py` 는 API 응답 구조 탐색용 스크립트입니다.
