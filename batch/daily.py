"""일배치: KRX 확정치로 연기금 '침묵 후 급증' 시그널을 찾아 public/data/signals.json 에 저장.

장 마감 후 GitHub Actions 에서 실행한다. KRX_ID / KRX_PW 환경변수 필요.

사용법:
    python -m batch.daily                              # 오늘(KST)
    python -m batch.daily --from 20260826 --to 20260925  # 기간 (마이그레이션)
"""

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
from _krx import KRX, MARKETS  # noqa: E402

from screener import store  # noqa: E402
from screener.kiwoom import NON_STOCK  # noqa: E402

KST = timezone(timedelta(hours=9))
MAX_CALENDAR_DAYS = 150


def fetch_days(krx: KRX, start: str, end: str, lookback: int):
    """end 부터 과거로 거래일별 연기금 순매수를 모은다. start 이전 거래일도 lookback 개 확보.

    Returns: 오름차순 [(day, {code: {"name", "market", "net"}})]
    """
    days, before_start = [], 0
    d = datetime.strptime(end, "%Y%m%d")
    for _ in range(MAX_CALENDAR_DAYS):
        day = d.strftime("%Y%m%d")
        d -= timedelta(days=1)
        if datetime.strptime(day, "%Y%m%d").weekday() >= 5:
            continue
        flows = {}
        for mkt, market in MARKETS.items():
            for r in krx.pension_net_by_stock(day, mkt):
                flows[r["code"]] = {"name": r["name"], "market": market, "net": r["net"]}
            time.sleep(0.5)
        if not flows:
            continue  # 휴장일
        days.append((day, flows))
        print(f"  {day}: {len(flows)} 종목")
        if day < start:
            before_start += 1
            if before_start >= lookback:
                break
    return sorted(days)


def detect(days, start, listed, min_buy, quiet_max, lookback):
    signals = []
    for i, (day, flows) in enumerate(days):
        if day < start or i < lookback:
            continue
        window = days[i - lookback:i]
        for code, f in flows.items():
            if f["net"] < min_buy or NON_STOCK.search(f["name"]):
                continue
            if listed.get(code, "") > window[0][0]:
                continue  # 침묵 기간 중 상장 -> 원래 거래가 없던 종목
            past = [w[1].get(code, {}).get("net", 0) for w in window]
            past_max = max(abs(x) for x in past)
            if past_max > quiet_max:
                continue
            signals.append({
                "date": day,
                "code": code,
                "name": f["name"],
                "market": f["market"],
                "buy_eok": round(f["net"] / 1e8, 2),
                "past_max_eok": round(past_max / 1e8, 2),
                "active_days": sum(1 for x in past if x != 0),
                "lookback": lookback,
                "source": "일별 확정",
            })
    return signals


def load_env() -> None:
    """Mac 에서 직접 실행할 때 .env 의 KRX_ID / KRX_PW 사용."""
    import os
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith(("KRX_ID=", "KRX_PW=")):
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v.strip().strip("'\""))


def main() -> None:
    load_env()
    today = datetime.now(KST).strftime("%Y%m%d")
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default=today)
    ap.add_argument("--to", dest="end", default=today)
    ap.add_argument("--min-buy", type=float, default=3.0, help="연기금 순매수 하한 (억)")
    ap.add_argument("--quiet-max", type=float, default=0.3, help="침묵 기간 일별 |순매수| 상한 (억)")
    ap.add_argument("--lookback", type=int, default=20, help="침묵 기간 (거래일)")
    args = ap.parse_args()

    krx = KRX()
    print(f"기간 {args.start} ~ {args.end}, 거래일 수집:")
    days = fetch_days(krx, args.start, args.end, args.lookback)
    if not any(d >= args.start for d, _ in days):
        print("대상 기간에 거래일 없음 (휴장)")
        return
    listed = krx.listing_dates()
    signals = detect(days, args.start, listed, args.min_buy * 1e8, args.quiet_max * 1e8, args.lookback)

    existing = store.load()
    for s in signals:
        prev = existing.get(f"{s['code']}_{s['date']}", {})
        if prev.get("source", "").startswith("장중"):
            s["intraday"] = prev["source"]  # 장중에 먼저 잡힌 신호였다는 기록
    store.upsert(signals)
    print(f"시그널 {len(signals)}건 저장 -> {store.SIGNALS_PATH.relative_to(store.ROOT)}")
    for s in sorted(signals, key=lambda x: x["date"]):
        print(f"  {s['date']} {s['name']}({s['code']}) {s['market']} +{s['buy_eok']}억")


if __name__ == "__main__":
    main()
