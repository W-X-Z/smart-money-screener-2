"""신호 선별 기준 검증: 섹터·테마·수급·펀더멘탈 (research/archive 만 사용, KRX 호출 없음).

각 기준으로 추천 신호를 위·아래 절반(또는 해당/비해당)으로 나눠 20거래일 초과수익을 비교하고,
과거 1년과 최근 1년에서 같은 방향인지 본다. 한 해에만 통하는 기준은 장세 탓일 가능성이 커서 쓰지 않는다.

  테마  : 최근 120일 일간 수익률 상관이 가장 높은 20종목(테마 동료)의 신호 전 20일 수익률, 연기금 동반 매수
  섹터  : KRX 업종 (research/archive/extra/sector.parquet) 의 신호 전 20일 수익률, 연기금 동반 매수
  수급  : 외국인(9000)·기관합계(7050) 순매수 (research/archive/investors/)
  펀더멘탈: PER·PBR·배당수익률 (research/archive/extra/fundamental.parquet, 월말 값을 주가 변화로 보정)

사용법:
    python -m research.selection          # 결과: research/results/SELECTION.md
"""

import numpy as np
import pandas as pd

from engine.archive import ARCHIVE, read
from research.evaluate import RESULTS
from research.strategy import build, events, portfolio, table, trade

HOLD = 20
PEERS = 20
EOK = 1e8


def theme_features(x, ev) -> pd.DataFrame:
    p = x.p
    r = np.where(np.isnan(p.fluc), np.nan, p.fluc / 100)
    net = np.nan_to_num(p.net)
    out = []
    for t, c in zip(ev.t, ev.c):
        w = r[max(0, t - 119):t + 1]
        ok = (~np.isnan(w)).sum(0) >= 100
        ok[c] = False
        z = np.nan_to_num((w - np.nanmean(w, 0)) / (np.nanstd(w, 0) + 1e-12))
        corr = (z * z[:, [c]]).mean(0)
        corr[~ok] = -np.inf
        peers = np.argsort(corr)[-PEERS:]
        with np.errstate(invalid="ignore", divide="ignore"):
            pre = x.adj[t, peers] / x.adj[max(0, t - 20), peers] - 1
        out.append({
            "테마 동료 신호 전 20일 수익률": np.nanmean(pre) * 100,
            "테마 동료 중 최근 5일 연기금 순매수 비율": (net[t - 4:t + 1, peers].sum(0) > 0).mean() * 100,
            "테마 결속도(동료 평균 상관)": float(np.mean(corr[peers])),
        })
    return pd.DataFrame(out)


def sector_features(x, ev) -> pd.DataFrame:
    path = ARCHIVE / "extra" / "sector.parquet"
    if not path.exists():
        return pd.DataFrame(index=range(len(ev)))
    sec = pd.read_parquet(path)
    p, net = x.p, np.nan_to_num(x.p.net)
    ci = {c: i for i, c in enumerate(p.codes)}
    months = sorted(sec.date.unique())
    maps = {m: dict(zip(g.code, g.sector)) for m, g in sec.groupby("date")}
    out = []
    for t, c in zip(ev.t, ev.c):
        d = p.dates[t]
        m = max([m for m in months if m <= d], default=months[0])   # 신호일 이전 가장 가까운 월말
        mp = maps[m]
        s = mp.get(p.codes[c])
        cols = np.array([ci[k] for k, v in mp.items() if v == s and k in ci and k != p.codes[c]])
        if s is None or len(cols) < 5:
            out.append({})
            continue
        with np.errstate(invalid="ignore", divide="ignore"):
            pre = x.adj[t, cols] / x.adj[max(0, t - 20), cols] - 1
        out.append({
            "업종": s,
            "업종 신호 전 20일 수익률": np.nanmean(pre) * 100,
            "업종 내 최근 5일 연기금 순매수 비율": (net[t - 4:t + 1, cols].sum(0) > 0).mean() * 100,
            "업종 연기금 최근 5일 순매수(억)": net[t - 4:t + 1, cols].sum() / EOK,
        })
    return pd.DataFrame(out)


def flow_features(x, ev) -> pd.DataFrame:
    p = x.p
    ci = pd.Series(np.arange(len(p.codes)), index=p.codes)
    di = pd.Series(np.arange(len(p.dates)), index=p.dates)
    out = pd.DataFrame(index=range(len(ev)))
    for code, label in (("9000", "외국인"), ("7050", "기관합계")):
        d = ARCHIVE / "investors" / code
        if not d.exists():
            continue
        df = read(f"investors/{code}")
        df = df[df.code.isin(ci.index) & df.date.isin(di.index)]
        arr = np.full(p.net.shape, np.nan)
        arr[di[df.date].values, ci[df.code].values] = (df.buy - df.sell).values
        first = di[df.date.min()]
        t, c = ev.t.values, ev.c.values
        ok = t - 4 >= first
        day = np.where(ok, arr[t, c], np.nan)
        five = np.array([np.nansum(arr[ti - 4:ti + 1, cj]) if o else np.nan for ti, cj, o in zip(t, c, ok)])
        mcap = p.mcap[t, c]
        out[f"{label} 신호일 순매수(시총 대비 bp)"] = day / mcap * 1e4
        out[f"{label} 최근 5일 순매수(시총 대비 bp)"] = five / mcap * 1e4
        if code == "7050":   # 기관합계에서 연기금을 뺀 나머지 기관
            rest = arr[t, c] - np.nan_to_num(p.net[t, c])
            out["연기금 외 기관 신호일 순매수(시총 대비 bp)"] = np.where(ok, rest / mcap * 1e4, np.nan)
    return out


def fundamental_features(x, ev) -> pd.DataFrame:
    path = ARCHIVE / "extra" / "fundamental.parquet"
    if not path.exists():
        return pd.DataFrame(index=range(len(ev)))
    f = pd.read_parquet(path)
    p = x.p
    di = {d: i for i, d in enumerate(p.dates)}
    months = sorted(f.date.unique())
    by = {m: g.set_index("code") for m, g in f.groupby("date")}
    out = []
    for t, c in zip(ev.t, ev.c):
        d = p.dates[t]
        prior = [m for m in months if m < d]
        if not prior:
            out.append({})
            continue
        m = prior[-1]
        g = by[m]
        code = p.codes[c]
        if code not in g.index:
            out.append({})
            continue
        row = g.loc[code]
        px_ratio = p.close[t, c] / p.close[di[m], c] if m in di and p.close[di[m], c] > 0 else np.nan
        eps, bps = row.eps, row.bps
        out.append({
            "PBR": (row.pbr * px_ratio) if row.pbr and row.pbr > 0 else np.nan,
            "PER(흑자만)": (row.per * px_ratio) if eps and eps > 0 and row.per else np.nan,
            "적자 기업": float(not (eps and eps > 0)),
            "배당수익률": (row.dvd_yld / px_ratio) if row.dvd_yld is not None and px_ratio > 0 else np.nan,
        })
    return pd.DataFrame(out)


def compare(d: pd.DataFrame, cols) -> list:
    rows = []
    for col in cols:
        if col not in d or d[col].notna().sum() < 40:
            continue
        row = {"기준": col}
        signs = []
        binary = set(d[col].dropna().unique()) <= {0.0, 1.0}
        for part in ("과거 1년", "최근 1년"):
            g = d[d.part == part].dropna(subset=[col])
            if binary:
                hi, lo = g[g[col] == 1], g[g[col] == 0]
            else:
                med = g[col].median()
                hi, lo = g[g[col] > med], g[g[col] <= med]
            if len(hi) < 8 or len(lo) < 8:
                row[f"{part} 중앙 차"] = row[f"{part} 평균 차"] = row[f"{part} +30%↑ 차"] = ""
                signs.append(0)
                continue
            dm, da = hi.ex.median() - lo.ex.median(), hi.ex.mean() - lo.ex.mean()
            row[f"{part} 중앙 차"] = round(dm, 1)
            row[f"{part} 평균 차"] = round(da, 1)
            row[f"{part} +30%↑ 차"] = round((hi.ex >= 30).mean() * 100 - (lo.ex >= 30).mean() * 100, 1)
            signs.append(np.sign(dm) if np.sign(dm) == np.sign(da) else 0)
        row["건수"] = int(d[col].notna().sum())
        row["두 해 같은 방향"] = ("높을수록 좋음" if signs[0] > 0 else "낮을수록 좋음") if signs[0] and signs[0] == signs[1] else ""
        rows.append(row)
    return rows


def select_backtest(x, d: pd.DataFrame, rules: dict) -> str:
    """기준값(과거 1년 중앙값)으로 걸러 포트폴리오를 돌린다. 기준값은 과거 1년에서만 정한다 (최근 1년을 보지 않음)."""
    first, split, end = int(d.t.min()), x.split_t, len(x.p.dates)
    prior = d[d.part == "과거 1년"]
    rows = []
    for name, cols in rules.items():
        keep = np.ones(len(d), bool)
        for col in cols:
            keep &= (d[col] > prior[col].median()).values
        sub = d[keep]
        for slots in (10, 20):
            for part, a, b in (("과거 1년", first, split), ("최근 1년", split, end)):
                r = portfolio(x, sub, slots, HOLD, a, b)
                rows.append({"선별": name, "동시 보유": slots, "구간": part,
                             "신호": int(((sub.t >= a - 1) & (sub.t < b)).sum()), **r})
    return table(rows)


def main() -> None:
    x = build()
    ev = events(x).reset_index(drop=True)
    res = [trade(x, t, c, t + 1, t + HOLD) for t, c in zip(ev.t, ev.c)]
    ev["ex"] = [r[1] if r else np.nan for r in res]
    groups = {"테마": theme_features(x, ev), "섹터": sector_features(x, ev),
              "수급": flow_features(x, ev), "펀더멘탈": fundamental_features(x, ev)}
    d = pd.concat([ev] + list(groups.values()), axis=1).dropna(subset=["ex"])
    parts = ["# 신호 선별 기준 검증 (섹터·테마·수급·펀더멘탈)\n\n"
             f"추천 신호 {len(d)}건, 20거래일 초과수익(같은 시장·비슷한 시총 중앙값 대비). "
             "값이 큰 쪽 절반에서 작은 쪽 절반을 뺀 차이(%p), 0/1 기준은 해당에서 비해당을 뺀 차이.\n"]
    consistent = []
    for name, f in groups.items():
        cols = [c for c in f.columns if c != "업종"]
        rows = compare(d, cols)
        consistent += [(r["기준"], r["두 해 같은 방향"]) for r in rows if r.get("두 해 같은 방향")]
        body = table(rows) if rows else "데이터 없음\n"
        print(f"\n## {name}\n{body}")
        parts.append(f"\n## {name}\n\n{body}")
    if "업종" in d:
        top = d.groupby("업종").ex.agg(["count", "median", "mean"]).query("count >= 8").round(1)
        body = top.sort_values("median", ascending=False).to_markdown() if len(top) else ""
        parts.append(f"\n## 업종별 (8건 이상)\n\n{body}\n")
        print(body)
    parts.append("\n## 두 해 모두 같은 방향인 기준\n\n" +
                 ("\n".join(f"- {c}: {s}" for c, s in consistent) if consistent else "없음") + "\n")
    print("consistent:", consistent)
    rules = {"전체 (선별 없음)": []}
    for col, _ in consistent:
        rules[col] = [col]
    if len(consistent) > 1:
        rules["모두 충족"] = [c for c, _ in consistent]
    body = select_backtest(x, d, rules)
    print(body)
    parts.append("\n## 선별 포트폴리오 (기준값은 과거 1년 중앙값, 20일 보유, 왕복 비용 0.3%)\n\n" + body)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "SELECTION.md").write_text("".join(parts))
    d.to_csv(RESULTS / "selection_features.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
