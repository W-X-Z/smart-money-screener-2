"""과거 데이터로 '침묵 후 급증' 신호의 빈도와 이후 수익률을 확인한다.

키움 ka10059(종목별 일별 투자자 수급, 종가 포함)만 사용한다. 종목당 1~2회 호출하며,
응답은 output/cache/ 에 저장되어 재실행 시 다시 호출하지 않는다.

진입 가정: 신호일 종가 매수 (장중 잠정치로 발견 후 종가 전 매수). 확정치 기준이라
장중 잠정치와는 차이가 있을 수 있다.

사용법:
    python -m screener.backscan --limit 50      # 빠른 테스트 (50종목)
    python -m screener.backscan                 # 코스피+코스닥 전체 (첫 실행 20분 이상)
"""

import argparse
import csv
import json
import statistics
from datetime import date
from itertools import product

from screener.kiwoom import ROOT, Kiwoom, num

OUT_DIR = ROOT / "output"
CACHE_DIR = OUT_DIR / "cache"
HORIZONS = (1, 5, 10, 20)

GRID_MIN_BUY = (100, 300, 500, 1000)   # 신호일 연기금 순매수 하한 (백만원)
GRID_QUIET_MAX = (10, 30, 100)         # 침묵 기간 일별 |순매수| 상한 (백만원)
GRID_LOOKBACK = (10, 20, 40)           # 침묵 기간 (거래일)


def load_series(api: Kiwoom, code: str, today: str, pages: int):
    cache = CACHE_DIR / f"{code}_{today}_{pages}.json"
    if cache.exists():
        rows = json.loads(cache.read_text())
    else:
        rows = api.daily_investor(code, today, pages=pages)
        cache.write_text(json.dumps(rows, ensure_ascii=False))
    rows = sorted({r["dt"]: r for r in rows}.values(), key=lambda r: r["dt"])  # 과거 -> 최근
    return (
        [r["dt"] for r in rows],
        [num(r["penfnd_etc"]) for r in rows],
        [abs(num(r["cur_prc"])) for r in rows],
    )


def find_events(code, name, dates, flow, close, min_buy, quiet_max, lookback):
    events = []
    for t in range(lookback, len(flow)):
        if flow[t] < min_buy or close[t] <= 0:
            continue
        if max(abs(x) for x in flow[t - lookback:t]) > quiet_max:
            continue
        ev = {"종목코드": code, "종목명": name, "신호일": dates[t], "순매수(억)": round(flow[t] / 100, 1)}
        for h in HORIZONS:
            ev[f"r{h}"] = round((close[t + h] / close[t] - 1) * 100, 2) if t + h < len(close) else None
        events.append(ev)
    return events


def summarize(events, n_days):
    weeks = max(n_days / 5, 1)
    out = {"신호수": len(events), "주당": round(len(events) / weeks, 2)}
    for h in HORIZONS:
        vals = [e[f"r{h}"] for e in events if e[f"r{h}"] is not None]
        out[f"r{h}_평균%"] = round(statistics.mean(vals), 2) if vals else None
        out[f"r{h}_승률%"] = round(sum(v > 0 for v in vals) / len(vals) * 100) if vals else None
        out[f"r{h}_n"] = len(vals)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="시장별 앞에서 N종목만 (테스트용)")
    ap.add_argument("--pages", type=int, default=2, help="종목당 조회 페이지 (1페이지=100거래일)")
    args = ap.parse_args()

    today = date.today().strftime("%Y%m%d")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    api = Kiwoom()

    universe = []
    for mrkt in ("0", "10"):
        stocks = [s for s in api.stock_list(mrkt) if "스팩" not in s["stk_nm"]]
        print(f"시장 {mrkt}: {len(stocks)} 종목")
        universe += stocks[: args.limit] if args.limit else stocks
    if universe:
        print(f"ka10099 응답 필드 예시: {list(universe[0]['raw'].keys())}")

    series, n_days = {}, 0
    for i, s in enumerate(universe, 1):
        try:
            series[s["stk_cd"]] = (s["stk_nm"],) + load_series(api, s["stk_cd"], today, args.pages)
            n_days = max(n_days, len(series[s["stk_cd"]][1]))
        except Exception as e:  # 거래정지 등 개별 종목 오류는 건너뜀
            print(f"  {s['stk_cd']} {s['stk_nm']} 건너뜀: {e}")
        if i % 100 == 0:
            print(f"  {i}/{len(universe)} 조회")
    print(f"수집 완료: {len(series)} 종목, 최대 {n_days} 거래일\n")

    grid, all_events = [], {}
    for min_buy, quiet_max, lookback in product(GRID_MIN_BUY, GRID_QUIET_MAX, GRID_LOOKBACK):
        events = []
        for code, (name, dates, flow, close) in series.items():
            events += find_events(code, name, dates, flow, close, min_buy, quiet_max, lookback)
        usable_days = max(n_days - lookback, 1)
        grid.append({"min_buy(억)": min_buy / 100, "quiet_max(억)": quiet_max / 100, "lookback": lookback,
                     **summarize(events, usable_days)})
        all_events[(min_buy, quiet_max, lookback)] = events

    # 비교 기준: 전체 종목·전체 날짜의 무조건부 수익률 (신호가 이보다 나아야 의미가 있음)
    base = []
    for code, (name, dates, flow, close) in series.items():
        for t in range(len(close)):
            if close[t] > 0:
                ev = {f"r{h}": ((close[t + h] / close[t] - 1) * 100 if t + h < len(close) else None) for h in HORIZONS}
                base.append(ev)
    grid.insert(0, {"min_buy(억)": "전체", "quiet_max(억)": "-", "lookback": "-", **summarize(base, 1)})
    grid[0]["신호수"], grid[0]["주당"] = "-", "-"

    stamp = date.today().strftime("%Y%m%d")
    grid_path = OUT_DIR / f"backscan_grid_{stamp}.csv"
    with grid_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(grid[0].keys()))
        w.writeheader()
        w.writerows(grid)

    default_events = all_events[(300, 30, 20)]
    ev_path = OUT_DIR / f"backscan_events_{stamp}.csv"
    if default_events:
        with ev_path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(default_events[0].keys()))
            w.writeheader()
            w.writerows(sorted(default_events, key=lambda e: e["신호일"]))

    cols = ["min_buy(억)", "quiet_max(억)", "lookback", "신호수", "주당", "r5_평균%", "r5_승률%", "r10_평균%", "r20_평균%", "r20_승률%"]
    print(" | ".join(cols))
    for g in grid:
        print(" | ".join(str(g[c]) for c in cols))
    print(f"\n그리드 -> {grid_path}")
    print(f"기본값(3억/0.3억/20일) 신호 목록 -> {ev_path} ({len(default_events)}건)")


if __name__ == "__main__":
    main()
