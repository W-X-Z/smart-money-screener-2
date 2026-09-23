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

## 웹 UI

배포 주소: **https://smart-money-pension.web.app** — 시그널 목록(최신 신호일 순), 종목 클릭 시 연기금 일별 순매수·종가 추이.

키움 API 는 Mac 에서만 호출되므로, Mac 이 결과를 Firestore 에 올리고 배포 웹이 그것을 읽습니다.

```bash
# .env 에 FIREBASE_SERVICE_ACCOUNT=<서비스 계정 JSON 경로> 추가 후
python -m screener.backfill            # 받아 둔 일별 캐시에서 과거 시그널 저장 (최초 1회)
python -m screener.publish             # 시그널 + 종목별 수급을 Firestore 에 업로드
python -m screener.run --publish       # 장중 스캔 후 바로 업로드
```

로컬에서만 보려면 `python -m screener.web` (http://localhost:8000, 키움에서 직접 조회).

웹·규칙 재배포: `python tools/deploy_firebase.py` (`firebase/firestore.rules`, `web/`). 기존 기본 사이트는 건드리지 않습니다.

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
