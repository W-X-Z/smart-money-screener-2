"""일배치: KRX 확정치를 아카이브에 쌓고(거래일 하루 KRX 4회), 아카이브로 시그널을 계산해 public/data 에 저장.

1. research/archive 에 최근 거래일 수집 (이미 받은 날은 건너뜀, 오늘은 값이 바뀔 수 있어 다시 받음)
2. engine/signals.py 의 기본 포착(넓게)으로 시그널과 필터용 속성 계산 -> public/data/signals.json
3. 신호 후 수익률 갱신, 신호 종목의 차트 데이터 -> public/data/flow/{코드}.json
웹은 이 파일들만 읽는다 (방문자는 KRX 를 호출하지 않음).

사용법 (수집할 때만 KRX_ID / KRX_PW 필요):
    python -m batch.daily                                   # 수집 + 마지막 거래일 시그널
    python -m batch.daily --no-collect --from 20260326      # 아카이브로 기간 재계산 (백필)
"""

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from engine import signals as S
from engine.archive import load
from screener import store

KST = timezone(timedelta(hours=9))
EOK = S.EOK
FLOW_DIR = store.ROOT / "public" / "data" / "flow"
FLOW_REFRESH_DAYS = 30  # 신호일이 이 기간 안인 종목의 차트 데이터를 매일 갱신 (그 이전은 한 번 만든 뒤 고정)
FLOW_BEFORE = 60        # 차트는 첫 신호 60거래일 전부터


def load_env() -> None:
    """Mac 에서 직접 실행할 때 .env 의 KRX_ID / KRX_PW 사용."""
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith(("KRX_ID=", "KRX_PW=")):
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v.strip().strip("'\""))


def records(p, a, rows) -> list:
    out = []
    for t in rows:
        for c in np.nonzero(a["base"][t])[0]:
            out.append({
                "date": str(p.dates[t]), "code": str(p.codes[c]), "name": str(p.names[c]),
                "market": "코스피" if p.market[c] == "STK" else "코스닥",
                "tix": int(t),
                "buy_eok": round(float(a["amount"][t, c]) / EOK, 2),     # 1~2일 누적 순매수
                "net_eok": round(float(a["net"][t, c]) / EOK, 2),        # 신호일 당일 순매수
                "two_day": bool(a["two_day"][t, c]),
                "adv_pct": round(float(a["adv_pct"][t, c]), 1),
                "past_max_eok": round(float(a["past_max"][t, c]) / EOK, 2),
                "big_days": int(a["big_days"][t, c]),
                "mcap_eok": int(round(float(np.nan_to_num(p.mcap[t, c])) / EOK)),
                "pref": bool(a["pref"][t, c]),
                "lookback": S.LOOKBACK,
                "source": "일별 확정",
            })
    return out


def update_returns(p, data: dict) -> None:
    """신호 다음날 시가에 샀다면 마지막 종가까지 수익률 (수정주가 기준)."""
    r = np.where(np.isnan(p.fluc), 0.0, p.fluc / 100)
    adj = np.cumprod(1 + r, axis=0)
    priced = ~np.isnan(p.close)
    di = {d: i for i, d in enumerate(p.dates)}
    ci = {c: i for i, c in enumerate(p.codes)}
    for s in data.values():
        t, c = di.get(s["date"]), ci.get(s["code"])
        if t is None or c is None or t + 1 >= len(p.dates) or not priced[t + 1, c] or not p.open[t + 1, c] > 0:
            s["ret_pct"], s["ret_days"] = None, 0
            continue
        base_next = p.close[t + 1, c] / (1 + p.fluc[t + 1, c] / 100)
        entry = adj[t, c] * p.open[t + 1, c] / base_next
        last = int(np.nonzero(priced[:, c])[0][-1])
        s["ret_pct"] = round(float(adj[last, c] / entry - 1) * 100, 1)
        s["ret_days"] = last - t
        s["ret_to"] = str(p.dates[last])


def write_flows(p, data: dict, today: str) -> int:
    """신호 종목별 연기금 순매수(억)·종가. 최근 신호 종목은 매일, 이전 종목은 파일이 없을 때만 만든다."""
    cutoff = (datetime.strptime(today, "%Y%m%d") - timedelta(days=FLOW_REFRESH_DAYS)).strftime("%Y%m%d")
    first, recent = {}, set()
    for s in data.values():
        first[s["code"]] = min(first.get(s["code"], s["date"]), s["date"])
        if s["date"] >= cutoff:
            recent.add(s["code"])
    di = {d: i for i, d in enumerate(p.dates)}
    ci = {c: i for i, c in enumerate(p.codes)}
    FLOW_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    for code, d0 in first.items():
        path = FLOW_DIR / f"{code}.json"
        if (path.exists() and code not in recent) or code not in ci or d0 not in di:
            continue
        c = ci[code]
        rows = [{"dt": str(p.dates[t]), "pension": round(float(p.net[t, c]) / EOK, 2), "close": int(p.close[t, c])}
                for t in range(max(0, di[d0] - FLOW_BEFORE), len(p.dates)) if not np.isnan(p.close[t, c])]
        path.write_text(json.dumps({"code": code, "rows": rows}, ensure_ascii=False, separators=(",", ":")))
        n += 1
    return n


def main() -> None:
    load_env()
    today = datetime.now(KST).strftime("%Y%m%d")
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", help="시그널 계산 시작일 (기본: 마지막 거래일)")
    ap.add_argument("--to", dest="end", help="시그널 계산 종료일 (기본: 마지막 거래일)")
    ap.add_argument("--no-collect", action="store_true", help="KRX 수집 없이 아카이브로만 계산")
    args = ap.parse_args()

    if not args.no_collect:
        from research.collect import collect
        start = (datetime.now(KST) - timedelta(days=10)).strftime("%Y%m%d")
        collect(start, today, max_minutes=20, daily=True)

    p = load()
    a = S.attributes(p)
    last = str(p.dates[-1])
    start, end = args.start or last, args.end or last
    rows = [t for t, d in enumerate(p.dates) if start <= d <= end]
    recs = records(p, a, rows)
    store.replace_confirmed(start, end, recs)

    data = store.load()
    update_returns(p, data)
    store.save(data)
    flows = write_flows(p, data, today)
    print(f"마지막 거래일 {last}, 계산 기간 {start}~{end}: 기본 포착 {len(recs)}건, 차트 {flows}종목 갱신")
    for name, kw in S.PRESETS.items():
        n = sum(1 for r in recs if r["date"] == last and passes(r, **kw))
        print(f"  {last} {name}: {n}건")


def passes(r: dict, amount, silence, scale, two_day, pref) -> bool:
    """engine.signals.mask 와 같은 규칙을 저장된 시그널 한 건에 적용 (요약 출력용)."""
    return (r["buy_eok"] >= amount and r["big_days"] <= silence
            and (not scale or r["buy_eok"] >= 5 or r["adv_pct"] >= 5)
            and (two_day or not r["two_day"]) and (pref or not r["pref"]))


if __name__ == "__main__":
    main()
