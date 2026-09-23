"""연기금 '침묵 후 급증' 장중 스크리너 v0.

1) 장중 잠정치(ka10063)에서 오늘 연기금 순매수 >= MIN_BUY 인 종목을 후보로 뽑고
2) 후보마다 과거 일별 연기금 수급(ka10059)을 조회해
3) 직전 LOOKBACK 거래일 동안 |연기금 순매수| 가 모두 QUIET_MAX 이하였던 종목만 신호로 출력한다.

사용법:
    python -m screener.run
    python -m screener.run --min-buy 300 --lookback 20 --quiet-max 30
금액 단위는 모두 백만원 (300 = 3억).
"""

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

from screener.kiwoom import ROOT, Kiwoom, num

OUT_DIR = ROOT / "output"
PENSION_INVSR = "3"  # ka10063 투자자 구분 중 연기금으로 추정되는 코드 (verify_pension_code 로 확인)


def verify_pension_code(api: Kiwoom, intraday: list) -> None:
    """ka10065(연기금 확정 코드 6000) 상위 종목의 순매수 수량이 ka10063 결과와 일치하는지 대조."""
    by_code = {r["stk_cd"]: r for r in intraday}
    top = api.intraday_top_pension("001") + api.intraday_top_pension("101")
    matched = sum(
        1 for t in top
        if t["stk_cd"] in by_code and num(by_code[t["stk_cd"]]["netprps_qty"]) == num(t["netslmt"])
    )
    print(f"[검증] ka10063 invsr={PENSION_INVSR} vs 연기금 상위(ka10065): {matched}/{len(top)} 종목 순매수 수량 일치")
    if top and matched < len(top) * 0.5:
        print("  ⚠ 일치율이 낮습니다. invsr 코드가 연기금이 아닐 수 있으니 결과를 신뢰하지 마세요.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-buy", type=float, default=300, help="오늘 연기금 순매수 하한 (백만원)")
    ap.add_argument("--lookback", type=int, default=20, help="침묵 판단 기간 (거래일)")
    ap.add_argument("--quiet-max", type=float, default=30, help="침묵 기간 중 일별 |순매수| 상한 (백만원)")
    ap.add_argument("--show", type=int, default=15, help="진단 표에 보여줄 후보 수")
    args = ap.parse_args()

    now = datetime.now()
    today = now.strftime("%Y%m%d")
    api = Kiwoom()

    intraday = api.intraday_investor(PENSION_INVSR)
    print(f"장중 연기금 잠정 데이터: {len(intraday)} 종목")
    verify_pension_code(api, intraday)

    candidates = [r for r in intraday if num(r["netprps_amt"]) >= args.min_buy]
    candidates.sort(key=lambda r: num(r["netprps_amt"]), reverse=True)
    print(f"오늘 순매수 >= {args.min_buy:.0f}백만원 후보: {len(candidates)} 종목\n")

    rows, history, unit_check = [], {}, []
    for r in candidates:
        daily = api.daily_investor(r["stk_cd"], today)
        past = [d for d in daily if d["dt"] < today][: args.lookback]
        history[r["stk_cd"]] = daily
        today_row = next((d for d in daily if d["dt"] == today), None)
        if today_row and num(today_row["penfnd_etc"]):
            unit_check.append(num(today_row["penfnd_etc"]) / num(r["netprps_amt"]))
        if len(past) < args.lookback:
            continue  # 상장 직후 등 이력 부족
        past_flow = [num(d["penfnd_etc"]) for d in past]
        past_max = max(abs(x) for x in past_flow)
        today_buy = num(r["netprps_amt"])
        rows.append({
            "신호": past_max <= args.quiet_max,
            "종목코드": r["stk_cd"],
            "종목명": r["stk_nm"],
            "등락률": num(r["flu_rt"]),
            "오늘순매수(억)": round(today_buy / 100, 1),
            "직전증감(억)": round(num(r["netprps_amt_irds"]) / 100, 1),
            "과거최대|x|(억)": round(past_max / 100, 2),
            "과거매매일수": sum(1 for x in past_flow if x != 0),
            "기준초과일수": sum(1 for x in past_flow if abs(x) > args.quiet_max),
            "급증배수": round(today_buy / max(past_max, 1), 1),
        })
    if unit_check:
        unit_check.sort()
        print(f"[검증] 일별(ka10059) 오늘값 / 장중(ka10063) 비율 중앙값: {unit_check[len(unit_check) // 2]:.2f} "
              f"({len(unit_check)}종목, 장 마감 후 1.0 근처면 단위 일치)")
    # 조용했던 순서 (과거 최대 절댓값 오름차순)
    rows.sort(key=lambda x: (x["과거최대|x|(억)"], -x["오늘순매수(억)"]))

    OUT_DIR.mkdir(exist_ok=True)
    stamp = now.strftime("%Y%m%d_%H%M")
    (OUT_DIR / f"raw_{stamp}.json").write_text(
        json.dumps({"intraday": intraday, "history": history}, ensure_ascii=False))
    if rows:
        out = OUT_DIR / f"candidates_{stamp}.csv"
        with out.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"후보 전체 -> {out}")

    signals = [x for x in rows if x["신호"]]
    print(f"\n🔔 신호 {len(signals)} 종목 (과거 {args.lookback}일 매일 |순매수| <= {args.quiet_max / 100:g}억)")
    print(f"\n가장 조용했던 후보 상위 {args.show} (과거 {args.lookback}일 기준)")
    cols = [c for c in rows[0].keys() if c != "신호"] if rows else []
    print("    " + " | ".join(cols))
    for x in rows[: args.show]:
        mark = "✅" if x["신호"] else "  "
        print(f"{mark}  " + " | ".join(str(x[c]) for c in cols))


if __name__ == "__main__":
    main()
