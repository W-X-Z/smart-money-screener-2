"""연기금 '침묵 후 급증' 로직 변형 비교 (research/archive 만 사용, KRX 호출 없음).

진입 가정 두 가지:
  - T+1 시가: 장 마감 후 일배치(16:10)로 발견 -> 다음날 시가 매수 (기본)
  - T 종가:   장중 잠정치로 발견 -> 당일 종가 매수
수익률은 수정주가(KRX 등락률 누적) 기준, 같은 날·같은 시장·같은 시총 5분위 종목들의
중앙값을 뺀 초과수익률로 본다 (시장 국면·소형주 효과 제거).

사용법:
    python -m research.evaluate                 # 결과: research/results/
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
ARCHIVE = ROOT / "archive"
RESULTS = ROOT / "results"
EOK = 1e8
HORIZONS = (5, 10, 20)
COOLDOWN = 20  # 같은 종목 재신호 억제 기간 (거래일)
NON_STOCK = re.compile(r"스팩|ETN|ETF|리츠|REIT|인프라")
RNG = np.random.default_rng(7)


# ---------------------------------------------------------------- data

@dataclass
class Panel:
    dates: np.ndarray
    codes: np.ndarray
    names: np.ndarray
    market: np.ndarray      # "STK" / "KSQ" (종목별 최신)
    net: np.ndarray         # 연기금 순매수 (원), 매매 없으면 0
    gross: np.ndarray       # 연기금 매수+매도 (원)
    close: np.ndarray
    open: np.ndarray
    fluc: np.ndarray
    trdval: np.ndarray
    mcap: np.ndarray


def load() -> Panel:
    pen = pd.concat([pd.read_parquet(p) for p in sorted((ARCHIVE / "pension").glob("*.parquet"))])
    px = pd.concat([pd.read_parquet(p) for p in sorted((ARCHIVE / "prices").glob("*.parquet"))])
    dates = np.array(sorted(px.date.unique()))
    codes = np.array(sorted(px.code.unique()))
    di = pd.Series(np.arange(len(dates)), index=dates)
    ci = pd.Series(np.arange(len(codes)), index=codes)
    shape = (len(dates), len(codes))

    def panel(df, values, fill):
        a = np.full(shape, fill, dtype=float)
        a[di[df.date].values, ci[df.code].values] = values
        return a

    pen = pen[pen.code.isin(ci.index) & pen.date.isin(di.index)]
    last = px.sort_values("date").groupby("code").last()
    return Panel(
        dates=dates, codes=codes,
        names=last.name.reindex(codes).values, market=last.market.reindex(codes).values,
        net=panel(pen, (pen.buy - pen.sell).values, 0.0),
        gross=panel(pen, (pen.buy + pen.sell).values, 0.0),
        close=panel(px, px.close.values, np.nan), open=panel(px, px.open.values, np.nan),
        fluc=panel(px, px.fluc_rt.values, np.nan), trdval=panel(px, px.trdval.values, np.nan),
        mcap=panel(px, px.mktcap.values, np.nan),
    )


def shift(a: np.ndarray, k: int) -> np.ndarray:
    """out[t] = a[t-k] (k>0 과거, k<0 미래)."""
    out = np.full_like(a, np.nan)
    if k > 0:
        out[k:] = a[:-k]
    elif k < 0:
        out[:k] = a[-k:]
    else:
        out[:] = a
    return out


def roll(a: np.ndarray, start: int, length: int, how: str, thr: float = 0.0) -> np.ndarray:
    """각 t 에 대해 a[t-start-length+1 .. t-start] 구간 통계. how: max | mean | nanmean | count_gt."""
    acc = cnt = None
    for j in range(length):
        s = shift(a, start + j)
        if how == "max":
            acc = s if acc is None else np.fmax(acc, s)
        elif how == "mean":
            acc = s if acc is None else acc + s
        elif how == "nanmean":
            v, n = np.nan_to_num(s), (~np.isnan(s)).astype(float)
            acc, cnt = (v, n) if acc is None else (acc + v, cnt + n)
        elif how == "count_gt":
            v = (s > thr).astype(float)
            acc = v if acc is None else acc + v
    if how == "mean":
        return acc / length
    if how == "nanmean":
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(cnt > 0, acc / cnt, np.nan)
    return acc


# ---------------------------------------------------------------- features & returns

@dataclass
class Feat:
    p: Panel
    valid: np.ndarray           # 신호 후보가 될 수 있는 (t, c)
    adj: np.ndarray             # 수정주가 지수
    fwd: Dict[tuple, np.ndarray]    # (entry, h) -> 초과수익률 %
    raw: Dict[tuple, np.ndarray]    # (entry, h) -> 절대수익률 %
    adv20: np.ndarray
    cache: dict


def build(p: Panel, lookback_max: int = 40) -> Feat:
    D, C = p.close.shape
    priced = ~np.isnan(p.close)
    first = np.where(priced.any(0), priced.argmax(0), D)
    t_idx = np.arange(D)[:, None]
    age_ok = (t_idx - first[None, :]) >= lookback_max + 5  # 침묵 구간 전체가 상장 이후
    name_ok = np.array([not NON_STOCK.search(str(n)) for n in p.names])[None, :]
    trading = priced & (np.nan_to_num(p.trdval) > 0)
    valid = trading & age_ok & name_ok

    r = np.where(np.isnan(p.fluc), 0.0, p.fluc / 100)
    adj = np.cumprod(1 + r, axis=0)
    adj[~priced] = np.nan
    jump = np.abs(np.nan_to_num(p.fluc)) > 30.5  # 상장 첫날 등 가격제한폭을 넘는 이상치

    # T+1 시가 진입: 시가 / 기준가(수정 전일종가)
    base_next = shift(p.close, -1) / (1 + shift(p.fluc, -1) / 100)
    entry_open = adj * (shift(p.open, -1) / base_next)
    entry_open[~(shift(p.open, -1) > 0)] = np.nan

    adv20 = roll(p.trdval, 1, 20, "nanmean")
    mcap = p.mcap
    fwd, raw = {}, {}
    for h in HORIZONS:
        exit_ = shift(adj, -h)
        bad = np.zeros_like(jump)
        for k in range(1, h + 1):
            bad |= shift(jump.astype(float), -k) > 0
        for entry, base in (("T+1시가", entry_open), ("T종가", adj)):
            ret = (exit_ / base - 1) * 100
            ret[bad | ~trading] = np.nan
            raw[(entry, h)] = ret
            fwd[(entry, h)] = ret - benchmark(ret, p, mcap, trading & name_ok)
    return Feat(p, valid, adj, fwd, raw, adv20, {})


def benchmark(ret: np.ndarray, p: Panel, mcap: np.ndarray, universe: np.ndarray) -> np.ndarray:
    """같은 날·같은 시장·같은 시총 5분위 종목들의 수익률 중앙값."""
    D, C = ret.shape
    t, c = np.nonzero(~np.isnan(mcap))
    d = pd.DataFrame({"t": t, "m": p.market[c], "cap": mcap[t, c], "r": ret[t, c], "u": universe[t, c]})
    base = d[d.u & d.r.notna()]
    edges = base.groupby(["t", "m"]).cap.quantile([0.2, 0.4, 0.6, 0.8]).unstack()
    e = edges.reindex(pd.MultiIndex.from_arrays([d.t, d.m])).values
    d["q"] = (d.cap.values[:, None] > e).sum(1)
    med = d[d.u & d.r.notna()].groupby(["t", "m", "q"]).r.median()
    out = np.full((D, C), np.nan)
    out[t, c] = med.reindex(pd.MultiIndex.from_arrays([d.t, d.m, d.q])).values
    return out


# ---------------------------------------------------------------- signal rules

def past(f: Feat, name: str, L: int, start: int = 1) -> np.ndarray:
    """직전 L 거래일(버스트 시작 전) 통계. name: maxabs | meanabs | maxgross | active | big."""
    key = (name, L, start)
    if key not in f.cache:
        p = f.p
        if name == "maxabs":
            f.cache[key] = roll(np.abs(p.net), start, L, "max")
        elif name == "meanabs":
            f.cache[key] = roll(np.abs(p.net), start, L, "mean")
        elif name == "maxgross":
            f.cache[key] = roll(p.gross, start, L, "max")
        elif name == "active":  # 연기금이 조금이라도 매매한 날 수
            f.cache[key] = roll(p.gross, start, L, "count_gt", 0.0)
        elif name == "big":  # |순매수| 0.3억 초과한 날 수
            f.cache[key] = roll(np.abs(p.net), start, L, "count_gt", 0.3 * EOK)
    return f.cache[key]


def burst(f: Feat, k: int) -> np.ndarray:
    """최근 k 거래일 누적 연기금 순매수 (원)."""
    key = ("burst", k)
    if key not in f.cache:
        f.cache[key] = sum(shift(f.p.net, j) for j in range(k)) if k > 1 else f.p.net
    return f.cache[key]


def rule(min_eok=1.0, adv_pct=None, cap_bp=None, quiet="maxabs", quiet_eok=0.3, ratio=None,
         L=20, k=1, big_days=None, min_adv_eok=None, adv_unless_eok=None) -> Callable[[Feat], np.ndarray]:
    """급증(크기) 조건 + 침묵 조건 조합."""
    def f(x: Feat) -> np.ndarray:
        s = burst(x, k)
        m = x.valid & (s >= min_eok * EOK)
        if adv_pct is not None and adv_unless_eok is not None:  # 큰 금액은 거래대금 조건 면제
            m &= (s >= adv_unless_eok * EOK) | (s >= adv_pct / 100 * x.adv20)
        elif adv_pct is not None:
            m &= s >= adv_pct / 100 * x.adv20
        if cap_bp is not None:
            m &= s >= cap_bp / 1e4 * x.p.mcap
        if min_adv_eok is not None:
            m &= x.adv20 >= min_adv_eok * EOK
        if quiet == "maxabs":
            m &= past(x, "maxabs", L, k) <= quiet_eok * EOK
        elif quiet == "maxgross":
            m &= past(x, "maxgross", L, k) <= quiet_eok * EOK
        elif quiet == "ratio":
            m &= s >= ratio * np.maximum(past(x, "meanabs", L, k), 1.0)
        elif quiet == "tolerant":
            m &= past(x, "big", L, k) <= big_days
        return m
    return f


def events(f: Feat, mask: np.ndarray) -> pd.DataFrame:
    """같은 종목은 COOLDOWN 거래일 안의 재신호를 제외한 이벤트 목록."""
    mask = np.nan_to_num(mask).astype(bool)
    t, c = np.nonzero(mask)
    order = np.lexsort((t, c))
    keep, last = [], {}
    for i in order:
        if t[i] - last.get(c[i], -10**9) >= COOLDOWN:
            keep.append(i)
            last[c[i]] = t[i]
    t, c = t[keep], c[keep]
    p = f.p
    df = pd.DataFrame({
        "t": t, "date": p.dates[t], "code": p.codes[c], "name": p.names[c],
        "market": np.where(p.market[c] == "STK", "코스피", "코스닥"),
        "net_eok": np.round(p.net[t, c] / EOK, 2),
        "buy2_eok": np.round(burst(f, 2)[t, c] / EOK, 2),
        "adv20_eok": np.round(f.adv20[t, c] / EOK, 2),
        "net_adv_pct": np.round(np.maximum(p.net[t, c], burst(f, 2)[t, c]) / f.adv20[t, c] * 100, 1),
        "mcap_eok": np.round(p.mcap[t, c] / EOK, 0),
        # 2일 누적 신호(전날도 0.3억 초과)는 전날을 뺀 그 이전 20일로 표시
        "past_max_eok": np.round(np.where(np.abs(p.net[np.maximum(t - 1, 0), c]) > 0.3 * EOK,
                                          past(f, "maxabs", 20, 2)[t, c], past(f, "maxabs", 20)[t, c]) / EOK, 2),
        "pref": np.array([s[-1] != "0" for s in p.codes[c]]),
    })
    for (entry, h), arr in f.fwd.items():
        df[f"x{h}_{entry}"] = np.round(arr[t, c], 2)
    for (entry, h), arr in f.raw.items():
        df[f"r{h}_{entry}"] = np.round(arr[t, c], 2)
    return df.sort_values("t").reset_index(drop=True)


# ---------------------------------------------------------------- stats

def clustered_ci(df: pd.DataFrame, col: str, n=2000):
    """신호일 단위로 묶어 재표본추출한 평균의 95% 구간 (같은 날 신호끼리의 상관 반영)."""
    d = df.dropna(subset=[col])
    if len(d) < 5:
        return (np.nan, np.nan)
    g = d.groupby("t")[col].agg(["sum", "count"])
    s, cnt = g["sum"].values, g["count"].values
    idx = RNG.integers(0, len(g), size=(n, len(g)))
    means = s[idx].sum(1) / cnt[idx].sum(1)
    return tuple(np.round(np.percentile(means, [2.5, 97.5]), 2))


PARTS = ("전반", "후반")


def summarize(name: str, ev: pd.DataFrame, weeks: float, split_t: int, entry="T+1시가") -> dict:
    out = {"로직": name, "신호수": len(ev), "주당": round(len(ev) / weeks, 2), "종목수": ev.code.nunique()}
    for h in HORIZONS:
        col = f"x{h}_{entry}"
        v = ev[col].dropna()
        out[f"{h}일 초과 평균%"] = round(v.mean(), 2) if len(v) else np.nan
        out[f"{h}일 초과 중앙%"] = round(v.median(), 2) if len(v) else np.nan
        out[f"{h}일 승률%"] = round((v > 0).mean() * 100) if len(v) else np.nan
    lo, hi = clustered_ci(ev, f"x20_{entry}")
    out["20일 평균 95%구간"] = f"{lo} ~ {hi}"
    for label, part in ((PARTS[0], ev[ev.t < split_t]), (PARTS[1], ev[ev.t >= split_t])):
        v = part[f"x20_{entry}"].dropna()
        out[f"{label} 20일 중앙%"] = round(v.median(), 2) if len(v) else np.nan
        out[f"{label} 신호수"] = len(part)
    return out


# ---------------------------------------------------------------- experiments

RECOMMENDED = "추천: 1~2일 누적 3억↑ & (5억↑ 또는 거래대금 5%↑) & 직전 20일 매일 |순매수|≤0.3억"
CURRENT = "현재: 3억↑ & 20일 매일 |순매수|≤0.3억"

EXPERIMENTS: List[tuple] = [
    # (그룹, 이름, 규칙)
    ("0 기준", CURRENT, rule(min_eok=3)),
    ("0 기준", "대조군: 1억↑ & 거래대금5%↑ (침묵조건 없음)", rule(adv_pct=5, quiet=None)),
    ("1 급증 크기", "1억↑ (침묵 현재와 동일)", rule(min_eok=1)),
    ("1 급증 크기", "1억↑ & 거래대금 2%↑", rule(adv_pct=2)),
    ("1 급증 크기", "1억↑ & 거래대금 5%↑", rule(adv_pct=5)),
    ("1 급증 크기", "1억↑ & 거래대금 10%↑", rule(adv_pct=10)),
    ("1 급증 크기", "1억↑ & 시총 1bp↑", rule(cap_bp=1)),
    ("1 급증 크기", "1억↑ & 시총 3bp↑", rule(cap_bp=3)),
    ("2 침묵 정의", "1억↑ & 거래대금5%↑ & 급증배수 10배↑", rule(adv_pct=5, quiet="ratio", ratio=10)),
    ("2 침묵 정의", "1억↑ & 거래대금5%↑ & 급증배수 20배↑", rule(adv_pct=5, quiet="ratio", ratio=20)),
    ("2 침묵 정의", "1억↑ & 거래대금5%↑ & 급증배수 50배↑", rule(adv_pct=5, quiet="ratio", ratio=50)),
    ("2 침묵 정의", "1억↑ & 거래대금5%↑ & 0.3억 초과일 2일 이하", rule(adv_pct=5, quiet="tolerant", big_days=2)),
    ("2 침묵 정의", "1억↑ & 거래대금5%↑ & 매수+매도 모두 0.3억 이하", rule(adv_pct=5, quiet="maxgross")),
    ("3 누적 매수", "2일 누적 1억↑ & 거래대금5%↑ & 급증배수 20배↑", rule(adv_pct=5, quiet="ratio", ratio=20, k=2)),
    ("3 누적 매수", "3일 누적 1억↑ & 거래대금5%↑ & 급증배수 20배↑", rule(adv_pct=5, quiet="ratio", ratio=20, k=3)),
    ("4 침묵 기간", "10일: 1억↑ & 거래대금5%↑ & 급증배수 20배↑", rule(adv_pct=5, quiet="ratio", ratio=20, L=10)),
    ("4 침묵 기간", "40일: 1억↑ & 거래대금5%↑ & 급증배수 20배↑", rule(adv_pct=5, quiet="ratio", ratio=20, L=40)),
    ("5 유동성", "1억↑ & 거래대금5%↑ & 급증배수 20배↑ & 평균거래대금 5억↑",
     rule(adv_pct=5, quiet="ratio", ratio=20, min_adv_eok=5)),
    # 금액 하한 유지하고 침묵 조건만 완화 (신호 수 늘리기)
    ("6 금액 유지·침묵 완화", "2억↑ & 20일 매일 |순매수|≤0.3억", rule(min_eok=2)),
    ("6 금액 유지·침묵 완화", "5억↑ & 20일 매일 |순매수|≤0.3억", rule(min_eok=5)),
    ("6 금액 유지·침묵 완화", "3억↑ & 20일 매일 |순매수|≤0.5억", rule(min_eok=3, quiet_eok=0.5)),
    ("6 금액 유지·침묵 완화", "3억↑ & 20일 매일 |순매수|≤1억", rule(min_eok=3, quiet_eok=1.0)),
    ("6 금액 유지·침묵 완화", "3억↑ & 0.3억 초과일 1일 이하", rule(min_eok=3, quiet="tolerant", big_days=1)),
    ("6 금액 유지·침묵 완화", "3억↑ & 0.3억 초과일 2일 이하", rule(min_eok=3, quiet="tolerant", big_days=2)),
    ("6 금액 유지·침묵 완화", "3억↑ & 급증배수 20배↑", rule(min_eok=3, quiet="ratio", ratio=20)),
    ("6 금액 유지·침묵 완화", "3억↑ & 급증배수 50배↑", rule(min_eok=3, quiet="ratio", ratio=50)),
    ("6 금액 유지·침묵 완화", "3억↑ & 10일 매일 |순매수|≤0.3억", rule(min_eok=3, L=10)),
    ("6 금액 유지·침묵 완화", "2일 누적 3억↑ & 그 전 20일 매일 |순매수|≤0.3억", rule(min_eok=3, k=2)),
    ("6 금액 유지·침묵 완화", "3억↑ & 거래대금 5%↑ & 20일 매일 |순매수|≤0.3억", rule(min_eok=3, adv_pct=5)),
    ("7 추천", RECOMMENDED, rule(min_eok=3, k=2, adv_pct=5, adv_unless_eok=5)),
]


def main() -> None:
    import argparse
    global PARTS
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", help="YYYYMMDD: 이 날짜 이전/이후로 나눠 비교 (없으면 기간 절반)")
    args = ap.parse_args()

    p = load()
    f = build(p)
    D = len(p.dates)
    start = 45  # 침묵 40일 + 여유: 이 이전은 이력이 부족해 평가에서 제외
    split_t = start + (D - start) // 2
    if args.split:
        split_t = int(np.searchsorted(p.dates, args.split))
        PARTS = ("과거 1년", "최근 1년")
    weeks = (D - start) / 5
    print(f"기간 {p.dates[start]} ~ {p.dates[-1]} ({D - start}거래일), 종목 {len(p.codes)}")
    print(f"전반/후반 경계: {p.dates[split_t]}")

    rows, all_ev = [], {}
    for group, name, fn in EXPERIMENTS:
        mask = fn(f)
        mask[:start] = False
        ev = events(f, mask)
        all_ev[name] = ev
        rows.append({"그룹": group, **summarize(name, ev, weeks, split_t)})
        r = rows[-1]
        print(f"[{group}] {name}: {r['신호수']}건 ({r['주당']}/주)  20일 초과 평균 {r['20일 초과 평균%']}% "
              f"중앙 {r['20일 초과 중앙%']}% 승률 {r['20일 승률%']}%  CI {r['20일 평균 95%구간']}")

    RESULTS.mkdir(exist_ok=True)
    summary = pd.DataFrame(rows)
    summary.to_csv(RESULTS / "variants.csv", index=False, encoding="utf-8-sig")
    # 진입 시점 비교 (T 종가)
    rows_t = [{"그룹": g, **summarize(n, all_ev[n], weeks, split_t, entry="T종가")} for g, n, _ in EXPERIMENTS]
    pd.DataFrame(rows_t).to_csv(RESULTS / "variants_T_close.csv", index=False, encoding="utf-8-sig")
    pd.to_pickle(all_ev, RESULTS / "events.pkl")
    meta = {"from": p.dates[start], "to": p.dates[-1], "days": int(D - start), "split": p.dates[split_t],
            "stocks": int(len(p.codes)), "generated": datetime.now().isoformat(timespec="minutes")}
    (RESULTS / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(f"-> {RESULTS}")


if __name__ == "__main__":
    main()
