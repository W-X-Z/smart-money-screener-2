"""로직 검증용 KRX 아카이브 수집기 (증분).

거래일 하루에 KRX 호출 4번으로 필요한 데이터를 전부 받는다.
  - 투자자별 순매수상위종목(연기금) 코스피·코스닥: 그날 연기금이 매매한 모든 종목의 매수·매도 금액
  - 전종목 시세 코스피·코스닥: 종가·시가·등락률·거래대금·시가총액
종목별로 따로 부르지 않으므로 호출 수가 종목 수와 무관하다.
휴장일은 첫 호출이 비면 나머지를 부르지 않고, 이미 저장한 날짜는 다시 부르지 않는다.

저장 (Git 에 커밋):
  research/archive/pension/YYYYMM.parquet  date, code, buy, sell (원)
  research/archive/prices/YYYYMM.parquet   date, code, name, market, open, close, fluc_rt, trdval, mktcap
  research/archive/calendar.json           조회를 마친 날짜 -> "open" | "holiday"

사용법 (KRX_ID / KRX_PW 필요):
    python -m research.collect --from 20250926 --to 20260925 --max-minutes 50
"""

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from batch.krx import KRX, KRXError, PENSION, to_int  # noqa: E402

ARCHIVE = Path(__file__).resolve().parent / "archive"
CALENDAR = ARCHIVE / "calendar.json"
MARKETS = ("STK", "KSQ")
SLEEP = 0.4  # 호출 간격 (초)


def to_float(v) -> float:
    s = str(v or "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def call(krx: KRX, bld: str, **params) -> dict:
    """일시 오류는 3번까지 재시도."""
    for attempt in range(4):
        try:
            time.sleep(SLEEP)
            return krx.query(bld, **params)
        except (KRXError, OSError) as e:
            if attempt == 3:
                raise
            print(f"    재시도 {attempt + 1}: {e}")
            time.sleep(2 ** (attempt + 1))
            krx.session = None  # 다음 호출에서 재로그인


def pension_rows(krx: KRX, day: str) -> list:
    rows = []
    for mkt in MARKETS:
        out = call(krx, "dbms/MDC/STAT/standard/MDCSTAT02401",
                   strtDd=day, endDd=day, mktId=mkt, invstTpCd=PENSION).get("output", [])
        rows += [{"date": day, "code": r["ISU_SRT_CD"],
                  "buy": to_int(r["BID_TRDVAL"]), "sell": to_int(r["ASK_TRDVAL"])} for r in out]
        if not out and mkt == MARKETS[0]:
            return []  # 코스피가 비면 휴장일
    return rows


def price_rows(krx: KRX, day: str) -> list:
    rows = []
    for mkt in MARKETS:
        out = call(krx, "dbms/MDC/STAT/standard/MDCSTAT01501", mktId=mkt, trdDd=day).get("OutBlock_1", [])
        rows += [{"date": day, "code": r["ISU_SRT_CD"], "name": r["ISU_ABBRV"], "market": mkt,
                  "open": to_int(r["TDD_OPNPRC"]), "close": to_int(r["TDD_CLSPRC"]),
                  "fluc_rt": to_float(r["FLUC_RT"]), "trdval": to_int(r["ACC_TRDVAL"]),
                  "mktcap": to_int(r["MKTCAP"])} for r in out]
    return rows


def flush(kind: str, rows: list) -> None:
    """월별 parquet 에 병합 저장."""
    by_month = defaultdict(list)
    for r in rows:
        by_month[r["date"][:6]].append(r)
    for month, new in by_month.items():
        path = ARCHIVE / kind / f"{month}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(new)
        if path.exists():
            df = pd.concat([pd.read_parquet(path), df])
        df = df.drop_duplicates(["date", "code"], keep="last").sort_values(["date", "code"])
        df.to_parquet(path, index=False, compression="zstd")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", required=True)
    ap.add_argument("--to", dest="end", required=True)
    ap.add_argument("--max-minutes", type=float, default=50, help="이 시간을 넘기면 저장 후 종료 (다음 실행이 이어서 수집)")
    args = ap.parse_args()

    calendar = json.loads(CALENDAR.read_text()) if CALENDAR.exists() else {}
    todo, d = [], datetime.strptime(args.end, "%Y%m%d")
    while d >= datetime.strptime(args.start, "%Y%m%d"):
        day = d.strftime("%Y%m%d")
        if d.weekday() < 5 and day not in calendar:
            todo.append(day)
        d -= timedelta(days=1)
    print(f"수집 대상 {len(todo)}일 (이미 저장된 날짜·주말 제외)")

    krx = KRX()
    started, pension, prices, calls = time.time(), [], [], 0
    stopped_early = False
    try:
        for i, day in enumerate(todo, 1):
            if (time.time() - started) / 60 > args.max_minutes:
                stopped_early = True
                print(f"시간 제한 도달: {i - 1}/{len(todo)}일 수집 후 중단 (다시 실행하면 이어서 수집)")
                break
            p = pension_rows(krx, day)
            calls += 1 if not p else 2
            if not p:
                calendar[day] = "holiday"
                continue
            q = price_rows(krx, day)
            calls += 2
            pension += p
            prices += q
            calendar[day] = "open"
            if len(prices) >= 20 * 2700:  # 약 20거래일마다 중간 저장
                flush("pension", pension)
                flush("prices", prices)
                pension, prices = [], []
                CALENDAR.write_text(json.dumps(calendar, sort_keys=True, indent=0))
            print(f"  {day}: 연기금 {len(p)}종목, 시세 {len(q)}종목  ({i}/{len(todo)}, 호출 {calls})")
    finally:
        flush("pension", pension)
        flush("prices", prices)
        ARCHIVE.mkdir(parents=True, exist_ok=True)
        CALENDAR.write_text(json.dumps(calendar, sort_keys=True, indent=0))
        opened = sum(1 for v in calendar.values() if v == "open")
        print(f"완료: KRX 호출 {calls}회, {(time.time() - started) / 60:.1f}분, 누적 거래일 {opened}일")
    if stopped_early:
        sys.exit(0)


if __name__ == "__main__":
    main()
