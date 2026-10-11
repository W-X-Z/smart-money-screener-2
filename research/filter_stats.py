"""웹 필터 조합별 과거 성과표 (public/data/filter_stats.json).

engine/signals.py 의 기본 포착·필터와 같은 규칙으로 2년 아카이브를 다시 계산한다.
초과수익: 신호 다음날 시가 진입, 같은 날·같은 시장·같은 시총 5분위 중앙값 대비 (research/evaluate.py 와 동일).

사용법:
    python -m research.filter_stats
"""

import json
from itertools import product
from pathlib import Path

import numpy as np

from engine import signals as S
from engine.archive import load
from research import evaluate as E

OUT = Path(__file__).resolve().parent.parent / "public" / "data" / "filter_stats.json"
START = 45  # 침묵 판단 이력이 부족한 앞부분 제외


def stat(v: np.ndarray, digits=1):
    v = v[~np.isnan(v)]
    if not len(v):
        return None
    return {"mean": round(float(v.mean()), digits), "median": round(float(np.median(v)), digits),
            "win": round(float((v > 0).mean() * 100)), "n": int(len(v))}


def main() -> None:
    E.HORIZONS = (5, 20, 40)
    p = load()
    f = E.build(p)
    a = S.attributes(p)
    D = len(p.dates)
    weeks = (D - START) / 5
    split = int(np.searchsorted(p.dates, "20250926"))

    # 무작위 종목 기준선 (평균 초과수익은 수익률 분포 치우침 때문에 0 보다 크다)
    univ = f.valid.copy()
    univ[:START] = False
    baseline = {f"x{h}": stat(f.fwd[("T+1시가", h)][univ]) for h in (20, 40)}

    stats = {}
    for amount, silence, scale, two_day, pref in product(S.AMOUNTS, S.SILENCES, (True, False), (True, False), (True, False)):
        m = S.mask(a, amount, silence, scale, two_day, pref)
        m[:START] = False
        t, c = np.nonzero(m)
        k = S.cooldown(t, c, a["amount"][t, c])
        t, c = t[k], c[k]
        x20 = f.fwd[("T+1시가", 20)][t, c]
        x40 = f.fwd[("T+1시가", 40)][t, c]
        stats[S.key(amount, silence, scale, two_day, pref)] = {
            "n": int(len(t)), "per_week": round(len(t) / weeks, 1),
            "x20": stat(x20), "x40": stat(x40),
            "x20_prior": stat(x20[t < split]), "x20_recent": stat(x20[t >= split]),
        }
    out = {"from": str(p.dates[START]), "to": str(p.dates[-1]), "weeks": round(weeks, 1),
           "entry": "신호 다음날 시가", "benchmark": "같은 날·같은 시장·같은 시총 5분위 중앙값",
           "baseline": baseline, "presets": S.PRESETS, "stats": stats}
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    for name, kw in S.PRESETS.items():
        s = stats[S.key(**kw)]
        print(f"{name}: {s['per_week']}/주, 20일 {s['x20']}, 40일 {s['x40']}")
    print(f"기준선: {baseline}")
    print(f"-> {OUT} ({len(stats)} 조합)")


if __name__ == "__main__":
    main()
