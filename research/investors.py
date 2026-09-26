"""연기금 외 투자자에도 '침묵 후 급증'이 통하는지 빠르게 비교 (KRX 호출 없음).

research/archive/investors/{코드} 에 모은 기간만 쓴다. 같은 기간·같은 규칙으로 연기금과 나란히 본다.
수익률은 evaluate.py 와 같다: 다음 날 시가 진입, 같은 날·같은 시장·같은 시총 5분위 중앙값 대비 초과수익률.

사용법:
    python -m research.investors            # 결과: research/results/investors.md
"""

import numpy as np
import pandas as pd

from engine.archive import ARCHIVE, load
from research.evaluate import RESULTS, build, clustered_ci, events, rule

NAMES = {"pension": "연기금", "2000": "보험", "3000": "투신", "3100": "사모", "7100": "기타법인"}
RULES = [
    ("추천 (3억·침묵 0일·규모 대비)", rule(min_eok=3, k=2, adv_pct=5, adv_unless_eok=5)),
    ("넓게 (1억·침묵 2일까지)", rule(min_eok=1, quiet="tolerant", big_days=2)),
    ("대조: 3억·침묵 조건 없음", rule(min_eok=3, quiet=None)),
]
WARMUP = 22  # 수집 시작 후 이 거래일까지는 침묵 여부를 알 수 없어 신호에서 뺀다
ENTRY = "T+1시가"


def window() -> tuple:
    """투자자 데이터가 모두 있는 기간."""
    firsts, lasts = [], []
    for d in sorted((ARCHIVE / "investors").iterdir()):
        dates = pd.concat([pd.read_parquet(f, columns=["date"]) for f in d.glob("*.parquet")]).date
        firsts.append(dates.min()); lasts.append(dates.max())
    return max(firsts), min(lasts)


def stats(ev: pd.DataFrame, weeks: float) -> dict:
    x5, x20 = ev[f"x5_{ENTRY}"].dropna(), ev[f"x20_{ENTRY}"].dropna()
    lo, hi = clustered_ci(ev, f"x20_{ENTRY}")
    return {"신호": len(ev), "주당": round(len(ev) / weeks, 1), "20일 평가": len(x20),
            "5일 중앙%": round(x5.median(), 2) if len(x5) else np.nan,
            "20일 중앙%": round(x20.median(), 2) if len(x20) else np.nan,
            "20일 평균%": round(x20.mean(), 2) if len(x20) else np.nan,
            "20일 승률%": round((x20 > 0).mean() * 100) if len(x20) else np.nan,
            "평균 95%": f"{lo} ~ {hi}"}


def main() -> None:
    first, last = window()
    flows = ["pension"] + sorted(d.name for d in (ARCHIVE / "investors").iterdir())
    rows, base_mean = [], None
    for flow in flows:
        p = load(flow if flow == "pension" else f"investors/{flow}")
        f = build(p)
        t0 = int(np.searchsorted(p.dates, first)) + WARMUP
        t1 = int(np.searchsorted(p.dates, last, side="right"))
        weeks = (t1 - t0) / 5
        if base_mean is None:  # 아무 종목이나 샀을 때 (같은 기간, 신호 후보 전체)
            x = f.fwd[(ENTRY, 20)][t0:t1][f.valid[t0:t1]]
            base_mean = round(float(np.nanmean(x)), 2)
            print(f"기간 {p.dates[t0]} ~ {p.dates[t1 - 1]} ({t1 - t0}거래일), 아무 종목 20일 평균 {base_mean}%")
        for name, fn in RULES:
            m = fn(f)
            m[:t0] = False
            m[t1:] = False
            r = {"투자자": NAMES.get(flow, flow), "규칙": name, **stats(events(f, m), weeks)}
            rows.append(r)
            print(" | ".join(str(v) for v in r.values()))

    df = pd.DataFrame(rows)
    RESULTS.mkdir(exist_ok=True)
    df.to_csv(RESULTS / "investors.csv", index=False, encoding="utf-8-sig")
    head = "| " + " | ".join(df.columns) + " |\n|" + "---|" * len(df.columns) + "\n"
    body = "".join("| " + " | ".join(str(v) for v in r) + " |\n" for r in df.itertuples(index=False))
    (RESULTS / "investors.md").write_text(
        f"# 투자자별 '침묵 후 급증' 빠른 비교\n\n신호 기간 {first} + {WARMUP}거래일 ~ {last}. "
        f"아무 종목 20일 초과수익 평균 {base_mean}%.\n\n" + head + body)
    print(f"-> {RESULTS / 'investors.md'}")


if __name__ == "__main__":
    main()
