"""이미 받아 둔 일별 수급 캐시(output/cache)에서 과거 시그널을 찾아 저장소에 넣는다.

API 를 호출하지 않는다. 캐시는 backscan 실행 시 만들어진다.

사용법:
    python -m screener.backfill
    python -m screener.backfill --min-buy 300 --lookback 20 --quiet-max 30
"""

import argparse
import json
import re

from screener import store
from screener.kiwoom import NON_STOCK, num

CACHE_FILE = re.compile(r"^([0-9A-Z]{6})_(\d{8})_(\d+)\.json$")


def latest_cache_files():
    """종목별로 가장 최근·가장 긴 캐시 파일 하나씩."""
    best = {}
    for path in store.CACHE_DIR.glob("*.json"):
        m = CACHE_FILE.match(path.name)
        if not m:
            continue
        code, dt, pages = m.group(1), m.group(2), int(m.group(3))
        if code not in best or (dt, pages) > best[code][0]:
            best[code] = ((dt, pages), path)
    return {code: path for code, (_, path) in best.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-buy", type=float, default=300, help="연기금 순매수 하한 (백만원)")
    ap.add_argument("--lookback", type=int, default=20, help="침묵 판단 기간 (거래일)")
    ap.add_argument("--quiet-max", type=float, default=30, help="침묵 기간 중 일별 |순매수| 상한 (백만원)")
    args = ap.parse_args()

    meta = store.universe_meta()
    signals = []
    for code, path in latest_cache_files().items():
        info = meta.get(code, {"name": code, "market": ""})
        if NON_STOCK.search(info["name"]):
            continue
        rows = sorted({r["dt"]: r for r in json.loads(path.read_text())}.values(), key=lambda r: r["dt"])
        flow = [num(r["penfnd_etc"]) for r in rows]
        for t in range(args.lookback, len(rows)):
            past = flow[t - args.lookback:t]
            if flow[t] < args.min_buy or max(abs(x) for x in past) > args.quiet_max:
                continue
            signals.append({
                "date": rows[t]["dt"],
                "code": code,
                "name": info["name"],
                "market": info["market"],
                "buy_eok": round(flow[t] / 100, 2),
                "past_max_eok": round(max(abs(x) for x in past) / 100, 2),
                "active_days": sum(1 for x in past if x != 0),
                "lookback": args.lookback,
                "source": "일별 확정",
            })
    store.upsert(signals)
    print(f"과거 시그널 {len(signals)}건 저장 -> {store.SIGNALS_PATH}")


if __name__ == "__main__":
    main()
