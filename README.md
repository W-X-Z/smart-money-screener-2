# Smart Money Screener 2

연기금 매매가 0에 가깝다가 갑자기 대규모 순매수가 들어온 종목을 찾는 스크리너. 웹: https://smart-money-screener-2.vercel.app

## 구조

```
[일배치] GitHub Actions 평일 16:10, 18:40 KST
         KRX 확정치 수집 (거래일 하루 4회 호출) → research/archive (2년치 이상 누적)
         → engine 으로 시그널·필터 속성 계산 → public/data 커밋 → Vercel 자동 배포
[장중]   회사 Mac: 키움 장중 잠정치(오늘) + 아카이브(어제까지) → 같은 engine → 커밋·푸시
[웹]     Vercel 정적 페이지. signals.json, filter_stats.json, flow/{코드}.json 만 읽음 (KRX 호출 없음)
```

DB 는 없습니다. 원천 데이터(`research/archive`)와 웹 데이터(`public/data`)가 모두 Git 에 커밋됩니다.

| 구성 | 파일 | 필요한 설정 |
|---|---|---|
| 공통 규칙 | `engine/archive.py`, `engine/signals.py` | - |
| 일배치 | `batch/daily.py`, `research/collect.py`, `.github/workflows/daily.yml` | GitHub Secrets `KRX_ID`, `KRX_PW` |
| 웹 | `public/index.html`, `vercel.json` | - |
| 장중 | `screener/run.py` | Mac `.env` 키움 키, `git push` 가능 |
| 검증 | `research/evaluate.py`, `research/filter_stats.py` | - |

## 시그널 규칙

- **기본 포착 (넓게)**: 오늘 연기금 순매수 > 0, 1~2일 누적 순매수 1억 이상, 직전 20거래일 중 |순매수| 0.3억 초과인 날 2일 이하. 전날도 0.3억 넘게 샀으면 2일 누적으로 보고 침묵 구간을 하루 앞당긴다.
- **필터 (웹에서 선택)**: 금액 하한(1/3/5억), 침묵(0.3억 초과일 0/1/2일 이하), 규모 대비(5억 미만이면 거래대금 5% 이상), 2일 연속 매수 포함, 우선주 포함, 시장. 같은 종목은 20거래일 안의 재신호를 뺀다.
- **프리셋**: 추천(3억·0일·규모 대비), 균형(3억·1일 이하·규모 대비), 넓게(기본 포착 전체).
- 필터 조합 72개의 과거 2년 성과(`public/data/filter_stats.json`)를 웹에 함께 보여준다.

검증 결과: `research/results/REPORT.md`

## Mac 설치와 장중 스캔

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && open -e .env   # KIWOOM_APP_KEY / KIWOOM_SECRET_KEY 입력

python -m screener.run          # 장중 스캔 (git pull 후 아카이브 + 키움 잠정치)
python -m screener.run --push   # 스캔 결과를 커밋·푸시해 웹에 반영
```

로컬 미리보기: `python -m screener.web` (http://localhost:8000).

## 운영 작업

```bash
python -m batch.daily --no-collect --from 20260326     # 아카이브로 기간 재계산 (KRX 호출 없음)
python -m research.evaluate --split 20250926            # 로직 변형 비교
python -m research.filter_stats                         # 필터 조합 성과표 갱신
```

과거 데이터 추가 수집: GitHub → Actions → research-archive → Run workflow (`from`, `to`). 이미 받은 날짜는 다시 부르지 않습니다.

## 사용 API

| 출처 | API | 용도 |
|---|---|---|
| KRX 정보데이터시스템 | MDCSTAT02401 투자자별 순매수상위종목 (연기금) | 하루치 전 종목 연기금 매수·매도 |
| KRX 정보데이터시스템 | MDCSTAT01501 전종목 시세 | 종가·시가·등락률·거래대금·시총 |
| 키움 REST | ka10063 장중투자자별매매 | 오늘 연기금 잠정 순매수 (장중 스캔) |
| 키움 REST | ka10065 장중투자자별매매상위 | 연기금 코드 검증 |

`tools/kiwoom_probe.py` 는 키움 API 응답 구조 탐색용, `screener/backscan.py` 는 초기 키움 기반 검증 스크립트입니다.
