"""과거 데이터로 '침묵 후 급증' 신호의 빈도와 이후 수익률을 확인한다.

키움 ka10059(종목별 일별 투자자 수급, 종가 포함)만 사용한다. 종목당 1~2회 호출하며,
응답은 output/cache/ 에 저장되어 재실행 시 다시 호출하지 않는다 (중단 후 이어하기 가능).

진입 가정: 신호일 종가 매수 (장중 잠정치로 발견 후 종가 전 매수). 확정치 기준이라
장중 잠정치와는 차이가 있을 수 있다.

수익률은 두 가지로 본다.
- 절대: 신호일 종가 대비 h일 뒤 종가 수익률
- 초과: 같은 날 전체 종목 h일 수익률의 중앙값을 뺀 값 (시장 국면 효과 제거)

사용법:
    python -m screener.backscan --limit 50      # 빠른 테스트 (시장별 50종목)
    python -m screener.backscan                 # 코스피+코스닥 전체
"""

import argparse
import csv
import json
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from itertools import product

from screener.kiwoom import NON_STOCK, ROOT, Kiwoom, num

OUT_DIR = ROOT / "output"
CACHE_DIR = OUT_DIR / "cache"
HORIZONS = (1, 5, 10, 20)
WORKERS = 4

GRID_MIN_BUY = (100, 300, 500, 1000)   # 신호일 연기금 순매수 하한 (백만원)
GRID_QUIET_MAX = (10, 30, 100)         # 침묵 기간 일별 |순매수| 상한 (백만원)
GRID_LOOKBACK = (10, 20, 40)           # 침묵 기간 (거래일)



def build_universe(api: Kiwoom, limit: int):
    universe = []
    for mrkt in ("0", "10"):
        raw = api.stock_list(mrkt)
        (CACHE_DIR / f"universe_{mrkt}.json").write_text(
            json.dumps([s["raw"] for s in raw], ensure_ascii=False))
        stocks = [s for s in raw if not NON_STOCK.search(s["stk_nm"])]
        for st in stocks:
            st["market"] = "코스피" if mrkt == "0" else "코스닥"
            st["size"] = st["raw"].get("upSizeName") or "미분류"
        for field in ("marketName", "kind", "upSizeName"):
            counts = Counter(str(s["raw"].get(field)) for s in stocks).most_common(6)
            print(f"  시장 {mrkt} {field}: {counts}")
        print(f"시장 {mrkt}: 전체 {len(raw)} → 일반 종목 {len(stocks)}")
        universe += stocks[:limit] if limit else stocks
    return universe


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


def fwd_returns(close, t):
    return {h: ((close[t + h] / close[t] - 1) * 100 if t + h < len(close) and close[t] > 0 else None)
            for h in HORIZONS}


def market_median(series):
    """날짜·기간별 전체 종목 수익률 중앙값."""
    pool = defaultdict(list)
    for name, dates, flow, close in series.values():
        for t, dt in enumerate(dates):
            for h, r in fwd_returns(close, t).items():
                if r is not None:
                    pool[(dt, h)].append(r)
    return {k: statistics.median(v) for k, v in pool.items() if len(v) >= 20}


def find_events(code, name, dates, flow, close, min_buy, quiet_max, lookback, mkt):
    events = []
    for t in range(lookback, len(flow)):
        if flow[t] < min_buy or close[t] <= 0:
            continue
        if max(abs(x) for x in flow[t - lookback:t]) > quiet_max:
            continue
        ev = {"종목코드": code, "종목명": name, "신호일": dates[t], "순매수(억)": round(flow[t] / 100, 1)}
        for h, r in fwd_returns(close, t).items():
            m = mkt.get((dates[t], h))
            ev[f"r{h}"] = round(r, 2) if r is not None else None
            ev[f"x{h}"] = round(r - m, 2) if r is not None and m is not None else None
        events.append(ev)
    return events


def summarize(events, n_days):
    out = {"신호수": len(events), "주당": round(len(events) / max(n_days / 5, 1), 2)}
    for h in (5, 10, 20):
        r = [e[f"r{h}"] for e in events if e[f"r{h}"] is not None]
        x = [e[f"x{h}"] for e in events if e[f"x{h}"] is not None]
        out[f"r{h}_중앙%"] = round(statistics.median(r), 2) if r else None
        out[f"x{h}_평균%"] = round(statistics.mean(x), 2) if x else None
        out[f"x{h}_중앙%"] = round(statistics.median(x), 2) if x else None
        out[f"x{h}_시장초과승률%"] = round(sum(v > 0 for v in x) / len(x) * 100) if x else None
        out[f"n{h}"] = len(x)
    return out


def _stats(events, h=20):
    x = [e[f"x{h}"] for e in events if e[f"x{h}"] is not None]
    x5 = [e["x5"] for e in events if e["x5"] is not None]
    if not x:
        return f"n={len(events)}"
    return (f"n={len(x):3d}  x5중앙={statistics.median(x5) if x5 else 0:6.2f}  "
            f"x{h}평균={statistics.mean(x):6.2f}  x{h}중앙={statistics.median(x):6.2f}  "
            f"승률={sum(v > 0 for v in x) / len(x) * 100:3.0f}%")


def robustness(events, meta):
    """기본 조합 신호가 특정 시장·규모·기간·날짜·종목에 쏠려 있는지 확인."""
    print(f"\n=== 견고성 점검 ({len(events)}건) ===")
    groups = [
        ("시장", lambda e: meta[e["종목코드"]]["market"]),
        ("규모", lambda e: meta[e["종목코드"]]["size"]),
        ("월", lambda e: e["신호일"][:6]),
    ]
    for label, key in groups:
        print(f"[{label}별]")
        buckets = defaultdict(list)
        for e in events:
            buckets[key(e)].append(e)
        for k in sorted(buckets):
            print(f"  {k:8s} {_stats(buckets[k])}")

    dates = sorted(e["신호일"] for e in events)
    if dates:
        mid = dates[len(dates) // 2]
        print("[전반/후반]")
        print(f"  ~{mid}  {_stats([e for e in events if e['신호일'] < mid])}")
        print(f"  {mid}~  {_stats([e for e in events if e['신호일'] >= mid])}")

    top_dates = Counter(dates).most_common(5)
    print(f"[신호 많은 날짜] {top_dates}  (한 날짜 쏠림 = 지수 리밸런싱 등 의심)")
    repeat = Counter(e["종목코드"] for e in events)
    print(f"[종목 중복] 고유 종목 {len(repeat)}개, 2회 이상 신호 {sum(1 for v in repeat.values() if v > 1)}개")

    x = sorted((e for e in events if e["x20"] is not None), key=lambda e: e["x20"])
    if len(x) >= 20:
        cut = max(len(x) // 20, 1)
        trimmed = [e["x20"] for e in x[cut:-cut]]
        print(f"[이상치] x20 상하위 5% 제외 평균 {statistics.mean(trimmed):.2f}% "
              f"(전체 평균 {statistics.mean(e['x20'] for e in x):.2f}%)")
        print("  상위 5: " + ", ".join(f"{e['종목명']}({e['신호일']}) {e['x20']:+.1f}%" for e in x[-5:][::-1]))
        print("  하위 5: " + ", ".join(f"{e['종목명']}({e['신호일']}) {e['x20']:+.1f}%" for e in x[:5]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="시장별 앞에서 N종목만 (테스트용)")
    ap.add_argument("--pages", type=int, default=2, help="종목당 조회 페이지 (1페이지=100거래일)")
    ap.add_argument("--focus", default="300,30,20", help="견고성 점검할 조합 min_buy,quiet_max,lookback (백만원)")
    args = ap.parse_args()

    today = date.today().strftime("%Y%m%d")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    api = Kiwoom()
    universe = build_universe(api, args.limit)

    meta = {s["stk_cd"]: s for s in universe}
    series, done = {}, 0
    with ThreadPoolExecutor(WORKERS) as pool:
        futures = {pool.submit(load_series, api, s["stk_cd"], today, args.pages): s for s in universe}
        for fut in as_completed(futures):
            s = futures[fut]
            done += 1
            try:
                series[s["stk_cd"]] = (s["stk_nm"],) + fut.result()
            except Exception as e:  # 거래정지 등 개별 종목 오류는 건너뜀
                print(f"  {s['stk_cd']} {s['stk_nm']} 건너뜀: {e}")
            if done % 100 == 0:
                print(f"  {done}/{len(universe)} 조회")
    n_days = max((len(v[1]) for v in series.values()), default=0)
    print(f"수집 완료: {len(series)} 종목, 최대 {n_days} 거래일\n")

    mkt = market_median(series)
    grid, all_events = [], {}
    for min_buy, quiet_max, lookback in product(GRID_MIN_BUY, GRID_QUIET_MAX, GRID_LOOKBACK):
        events = []
        for code, (name, dates, flow, close) in series.items():
            events += find_events(code, name, dates, flow, close, min_buy, quiet_max, lookback, mkt)
        grid.append({"min_buy(억)": min_buy / 100, "quiet_max(억)": quiet_max / 100, "lookback": lookback,
                     **summarize(events, max(n_days - lookback, 1))})
        all_events[(min_buy, quiet_max, lookback)] = events

    stamp = date.today().strftime("%Y%m%d")
    grid_path = OUT_DIR / f"backscan_grid_{stamp}.csv"
    with grid_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(grid[0].keys()))
        w.writeheader()
        w.writerows(grid)
    ev_path = OUT_DIR / f"backscan_events_{stamp}.csv"
    merged = {}
    for key, events in all_events.items():
        for e in events:
            merged.setdefault((e["종목코드"], e["신호일"]), e)
    if merged:
        with ev_path.open("w", newline="", encoding="utf-8-sig") as f:
            rows = sorted(merged.values(), key=lambda e: e["신호일"])
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    cols = ["min_buy(억)", "quiet_max(억)", "lookback", "신호수", "주당",
            "x5_평균%", "x5_중앙%", "x5_시장초과승률%", "x20_평균%", "x20_중앙%", "x20_시장초과승률%", "n20"]
    print("x = 같은 날 전체 종목 중앙값 대비 초과수익률")
    print(" | ".join(cols))
    for g in grid:
        if g["신호수"]:
            print(" | ".join(str(g[c]) for c in cols))
    focus = tuple(int(v) for v in args.focus.split(","))
    if focus in all_events:
        robustness(all_events[focus], meta)
    print(f"\n그리드 -> {grid_path}")
    print(f"신호 목록 (모든 조합 합집합) -> {ev_path} ({len(merged)}건)")


if __name__ == "__main__":
    main()
