"""검증 결과를 웹에 CSV 로 공개한다 (Google Sheets 가 IMPORTDATA 로 읽어 감).

  public/data/research/variants.csv            로직 변형 비교표
  public/data/research/signals_recommended.csv 추천 로직의 과거 신호와 이후 초과수익

사용법:
    python -m research.publish
"""

import json
from pathlib import Path

import pandas as pd

from research.evaluate import RECOMMENDED, RESULTS

OUT = Path(__file__).resolve().parent.parent / "public" / "data" / "research"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    meta = json.loads((RESULTS / "meta.json").read_text())
    v = pd.read_csv(RESULTS / "variants.csv")
    keep = ["그룹", "로직", "신호수", "주당", "5일 초과 중앙%", "20일 초과 평균%", "20일 초과 중앙%", "20일 승률%",
            "20일 평균 95%구간", "과거 1년 신호수", "과거 1년 20일 중앙%", "최근 1년 신호수", "최근 1년 20일 중앙%"]
    v[[c for c in keep if c in v.columns]].to_csv(OUT / "variants.csv", index=False, encoding="utf-8")

    ev = pd.read_pickle(RESULTS / "events.pkl")[RECOMMENDED].sort_values("date", ascending=False)
    out = pd.DataFrame({
        "신호일": pd.to_datetime(ev.date).dt.strftime("%Y-%m-%d"),
        "종목코드": "A" + ev.code,  # IMPORTDATA 가 앞자리 0 을 지우지 않게
        "종목명": ev.name,
        "시장": ev.market,
        "연기금 순매수(억)": ev[["net_eok", "buy2_eok"]].max(axis=1),
        "거래대금 대비(%)": ev.net_adv_pct,
        "직전 20일 최대(억)": ev.past_max_eok,
        "5일 초과수익(%)": ev["x5_T+1시가"],
        "20일 초과수익(%)": ev["x20_T+1시가"],
        "20일 수익률(%)": ev["r20_T+1시가"],
    })
    out.to_csv(OUT / "signals_recommended.csv", index=False, encoding="utf-8")
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))
    print(f"-> {OUT} ({len(out)} signals, {len(v)} variants)")


if __name__ == "__main__":
    main()
