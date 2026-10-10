"""선별 연구용 추가 데이터 (월말 스냅숏). 시세·연기금 아카이브와 같은 날짜 체계를 쓴다.

  research/archive/extra/sector.parquet       date, code, sector       (KRX 업종 분류, MDCSTAT03901)
  research/archive/extra/fundamental.parquet  date, code, eps, per, bps, pbr, dps, dvd_yld  (MDCSTAT03501)
월말 거래일마다 시장별 1회씩 호출한다 (2년이면 약 100회). 이미 받은 날짜는 건너뛴다.
외국인·기관합계 수급은 research.collect --investors 9000,7050 으로 받는다.

사용법 (KRX_ID / KRX_PW 필요):
    python -m research.collect_extra --from 20240901 --to 20261031
"""

import argparse
import json

import pandas as pd

from research.collect import ARCHIVE, CALENDAR, MARKETS, call, to_float
from batch.krx import KRX

OUT = ARCHIVE / "extra"


def month_ends(start: str, end: str) -> list:
    cal = json.loads(CALENDAR.read_text())
    days = sorted(d for d, v in cal.items() if v == "open" and start <= d <= end)
    last = {}
    for d in days:
        last[d[:6]] = d
    return sorted(last.values())


def num(v):
    s = str(v or "").replace(",", "").strip()
    return to_float(s) if s not in ("", "-") else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", required=True)
    ap.add_argument("--to", dest="end", required=True)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    krx = KRX()
    for kind, bld, key in (("sector", "dbms/MDC/STAT/standard/MDCSTAT03901", "block1"),
                           ("fundamental", "dbms/MDC/STAT/standard/MDCSTAT03501", "output")):
        path = OUT / f"{kind}.parquet"
        old = pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=["date"])
        have = set(old.date)
        rows = []
        for day in month_ends(args.start, args.end):
            if day in have:
                continue
            for mkt in MARKETS:
                out = call(krx, bld, trdDd=day, mktId=mkt).get(key, [])
                for r in out:
                    if kind == "sector":
                        rows.append({"date": day, "code": r["ISU_SRT_CD"], "sector": r.get("IDX_IND_NM", "")})
                    else:
                        rows.append({"date": day, "code": r["ISU_SRT_CD"], "eps": num(r.get("EPS")),
                                     "per": num(r.get("PER")), "bps": num(r.get("BPS")), "pbr": num(r.get("PBR")),
                                     "dps": num(r.get("DPS")), "dvd_yld": num(r.get("DVD_YLD"))})
            print(f"  {kind} {day}: 누적 {len(rows)}행")
        if rows:
            df = pd.concat([old, pd.DataFrame(rows)]).drop_duplicates(["date", "code"], keep="last")
            df.to_parquet(path, index=False, compression="zstd")
        print(f"{kind}: 새로 {len(rows)}행")


if __name__ == "__main__":
    main()
