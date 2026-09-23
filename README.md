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

## 사용 API (키움 REST)

| API | 용도 | 단위 |
|---|---|---|
| ka10063 장중투자자별매매 | 오늘 연기금 잠정 순매수 (후보 발굴) | 백만원 |
| ka10059 종목별투자자기관별 | 종목의 과거 일별 연기금(`penfnd_etc`) 수급 100일 | 백만원 |
| ka10065 장중투자자별매매상위 | 연기금(6000) 순매수 상위 — ka10063 연기금 코드 검증용 | 주 |

`tools/kiwoom_probe.py` 는 API 응답 구조 탐색용 스크립트입니다.
