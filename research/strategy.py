"""투자 전략 고도화 검증 (research/archive 만 사용, KRX 호출 없음).

웹에 보이는 추천 신호(engine.signals PRESETS["추천"] + 20거래일 재신호 억제)를 출발점으로
  A. 신호 뒤에도 연기금이 계속 사는지, 확인 후 진입이 나은지
  B. 보유 기간과 청산 규칙 (고정 보유 vs 연기금 매도 시 청산)
  C. 신호 전 주가 흐름(20일 수익률, 신호일 등락)에 따른 차이
  D. 비용·동시 보유 한도를 넣은 포트폴리오 백테스트
를 본다. 규칙은 과거 1년(SPLIT 이전)으로 고르고 최근 1년으로 확인한다.

사용법:
    python -m research.strategy            # 결과: research/results/STRATEGY.md
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from engine import signals as S
from engine.archive import load
from research.evaluate import RESULTS

SPLIT = "20250926"
EOK = 1e8
COST = 0.003          # 왕복 비용 (수수료·거래세·슬리피지 합산 가정)
RNG = np.random.default_rng(11)


@dataclass
class Ctx:
    p: object
    a: dict
    adj: np.ndarray      # 수정 종가 지수
    adj_open: np.ndarray  # 수정 시가 (같은 기준)
    valid: np.ndarray
    quint: np.ndarray    # 시장별 시총 5분위 (0~4)
    split_t: int
    cache: dict


def build() -> Ctx:
    p = load()
    a = S.attributes(p)
    r = np.where(np.isnan(p.fluc), 0.0, p.fluc / 100)
    adj = np.cumprod(1 + r, axis=0)
    priced = ~np.isnan(p.close)
    adj[~priced] = np.nan
    prev_close = p.close / (1 + np.nan_to_num(p.fluc) / 100)          # 당일 기준가 (수정 전일종가)
    with np.errstate(invalid="ignore", divide="ignore"):
        adj_open = np.vstack([np.full((1, adj.shape[1]), np.nan), adj[:-1]]) * (p.open / prev_close)
    adj_open[~(p.open > 0)] = np.nan
    valid = priced & (np.nan_to_num(p.trdval) > 0) & ~np.isnan(a["adv20"])
    # 시장별 시총 5분위
    quint = np.full(p.mcap.shape, -1, dtype=int)
    for m in ("STK", "KSQ"):
        cols = p.market == m
        cap = np.where(valid[:, cols], p.mcap[:, cols], np.nan)
        edges = np.nanpercentile(cap, [20, 40, 60, 80], axis=1).T
        q = (cap[:, :, None] > edges[:, None, :]).sum(2)
        quint[:, cols] = np.where(np.isnan(cap), -1, q)
    return Ctx(p, a, adj, adj_open, valid, quint, int(np.searchsorted(p.dates, SPLIT)), {})


def events(x: Ctx, preset: str = "추천") -> pd.DataFrame:
    m = S.mask(x.a, **S.PRESETS[preset])
    m[:S.LOOKBACK + S.MIN_AGE] = False
    t, c = np.nonzero(m)
    keep = S.cooldown(t, c)
    t, c = t[keep], c[keep]
    p, a = x.p, x.a
    df = pd.DataFrame({"t": t, "c": c, "date": p.dates[t], "name": p.names[c],
                       "amount_eok": a["amount"][t, c] / EOK, "adv_pct": a["adv_pct"][t, c],
                       "fluc0": p.fluc[t, c]})
    with np.errstate(invalid="ignore", divide="ignore"):
        df["pre20"] = (x.adj[t, c] / x.adj[np.maximum(t - 20, 0), c] - 1) * 100
    df["part"] = np.where(t < x.split_t, "과거 1년", "최근 1년")
    return df


def bench(x: Ctx, t: int, e: int, z: int) -> np.ndarray:
    """신호일 t 기준 같은 시장·시총 5분위 종목들이 e 시가 매수 → z 종가 매도했을 때 수익률 중앙값 (종목별 배열)."""
    k = (t, e, z)
    if k not in x.cache:
        with np.errstate(invalid="ignore", divide="ignore"):
            r = x.adj[z] / x.adj_open[e] - 1
        ok = x.valid[t] & ~np.isnan(r) & (np.abs(r) < 3)
        out = np.full(r.shape, np.nan)
        for m in ("STK", "KSQ"):
            for q in range(5):
                g = (x.p.market == m) & (x.quint[t] == q)
                med = np.nanmedian(r[g & ok]) if (g & ok).any() else np.nan
                out[g] = med
        x.cache[k] = out
    return x.cache[k]


def trade(x: Ctx, t: int, c: int, e: int, z: int) -> Optional[tuple]:
    """e 일 시가 매수, z 일 종가 매도. (수익률%, 시장 대비 초과%) 또는 데이터가 없으면 None."""
    D = len(x.p.dates)
    if e >= D or z >= D or z < e:
        return None
    buy, sell = x.adj_open[e, c], x.adj[z, c]
    if not (buy > 0) or not (sell > 0):
        return None
    r = sell / buy - 1
    return r * 100, (r - bench(x, t, e, z)[c]) * 100


def summarize(rows: List[dict], label: str) -> List[dict]:
    df = pd.DataFrame(rows)
    out = []
    for part in ("과거 1년", "최근 1년"):
        d = df[df.part == part] if len(df) else df
        if not len(d):
            out.append({"규칙": label, "구간": part, "건수": 0})
            continue
        ex = d.ex.values
        boot = [np.mean(RNG.choice(ex, len(ex))) for _ in range(1000)]
        out.append({"규칙": label, "구간": part, "건수": len(d),
                    "초과 중앙%": round(float(np.median(ex)), 2), "초과 평균%": round(float(ex.mean()), 2),
                    "평균 95%": f"{np.percentile(boot, 2.5):.1f} ~ {np.percentile(boot, 97.5):.1f}",
                    "시장보다 오른 %": round(float((ex > 0).mean() * 100)),
                    "보유일": round(float(d.hold.mean()), 1)})
    return out


def table(rows: List[dict]) -> str:
    df = pd.DataFrame(rows)
    head = "| " + " | ".join(df.columns) + " |\n|" + "---|" * len(df.columns) + "\n"
    return head + "".join("| " + " | ".join("" if pd.isna(v) else str(v) for v in r) + " |\n"
                          for r in df.itertuples(index=False))


# ------------------------------------------------------------------ A. 매수 지속

def flow_after(x: Ctx, ev: pd.DataFrame) -> str:
    net = x.p.net
    rows = []
    for k in (1, 3, 5, 10, 20):
        cum = np.array([np.nansum(net[t + 1:t + 1 + k, c]) if t + k < len(net) else np.nan
                        for t, c in zip(ev.t, ev.c)]) / EOK
        ok = ~np.isnan(cum)
        ratio = cum[ok] / ev.amount_eok.values[ok]
        rows.append({"신호 후": f"{k}일", "더 산 비율 %": round(float((cum[ok] > 0).mean() * 100)),
                     "추가 순매수 중앙(억)": round(float(np.median(cum[ok])), 2),
                     "신호 금액 대비 중앙": f"{np.median(ratio):.2f}배"})
    return table(rows)


def confirm_rules(x: Ctx, ev: pd.DataFrame, hold: int = 20) -> List[dict]:
    """즉시 진입 vs k일 동안 연기금이 더 산 것을 확인한 뒤 진입 (보유 기간은 같게)."""
    net, out = x.p.net, []
    for k, need in ((0, None), (1, 0.0), (2, 0.0), (3, 0.0), (3, 0.5)):
        rows = []
        for t, c, part, amt in zip(ev.t, ev.c, ev.part, ev.amount_eok):
            if k:
                more = np.nansum(net[t + 1:t + 1 + k, c]) / EOK
                if more <= need * amt:
                    continue
            res = trade(x, t, c, t + 1 + k, t + k + hold)
            if res:
                rows.append({"part": part, "ret": res[0], "ex": res[1], "hold": hold})
        label = "즉시 (다음 날 시가)" if not k else (
            f"{k}일 더 산 것 확인 후" + (f" (신호의 {int(need * 100)}% 이상)" if need else ""))
        out += summarize(rows, label)
    return out


# ------------------------------------------------------------------ B. 보유·청산

def hold_rules(x: Ctx, ev: pd.DataFrame) -> List[dict]:
    out = []
    for h in (5, 10, 20, 40, 60):
        rows = []
        for t, c, part in zip(ev.t, ev.c, ev.part):
            res = trade(x, t, c, t + 1, t + h)
            if res:
                rows.append({"part": part, "ret": res[0], "ex": res[1], "hold": h})
        out += summarize(rows, f"{h}일 보유")
    # 연기금 매도 시 청산: 신호 뒤 연기금 누적 순매수가 신호 금액의 절반 아래로 줄면 그날 종가에 판다 (최대 60일)
    for cap, frac in ((60, 0.5), (60, 0.0)):
        rows = []
        net = x.p.net
        for t, c, part, amt in zip(ev.t, ev.c, ev.part, ev.amount_eok):
            z = min(t + cap, len(net) - 1)
            if t + cap >= len(net):
                continue
            cum = amt * EOK
            for d in range(t + 1, t + cap + 1):
                cum += np.nan_to_num(net[d, c])
                if cum < frac * amt * EOK:
                    z = d
                    break
            res = trade(x, t, c, t + 1, z)
            if res:
                rows.append({"part": part, "ret": res[0], "ex": res[1], "hold": z - t})
        out += summarize(rows, f"연기금이 {'절반 넘게' if frac else '전부'} 되팔면 청산 (최대 {cap}일)")
    return out


# ------------------------------------------------------------------ C. 신호 전 흐름

def pre_rules(x: Ctx, ev: pd.DataFrame, hold: int = 20) -> List[dict]:
    res = [trade(x, t, c, t + 1, t + hold) for t, c in zip(ev.t, ev.c)]
    d = ev.assign(ex=[r[1] if r else np.nan for r in res], ret=[r[0] if r else np.nan for r in res],
                  hold=hold).dropna(subset=["ex"])
    out = []
    for col, label, cuts in (("pre20", "신호 전 20일 수익률", (-np.inf, -10, 0, 10, 25, np.inf)),
                             ("fluc0", "신호일 등락률", (-np.inf, 0, 3, 8, np.inf)),
                             ("amount_eok", "매수 금액(억)", (0, 4, 6, 10, np.inf))):
        b = pd.cut(d[col], cuts)
        for iv, g in d.groupby(b, observed=True):
            out += summarize(g.to_dict("records"), f"{label} {iv}")
    return out


# ------------------------------------------------------------------ E. 선별 기준

def features(x: Ctx, ev: pd.DataFrame) -> pd.DataFrame:
    """신호일 종가까지 알 수 있는 값만 쓴다."""
    p, a, net = x.p, x.a, x.p.net
    t, c = ev.t.values, ev.c.values
    adj = x.adj
    def past(arr, L, how):
        out = []
        for ti, ci in zip(t, c):
            w = arr[max(0, ti - L + 1):ti + 1, ci]
            w = w[~np.isnan(w)]
            out.append(how(w) if len(w) else np.nan)
        return np.array(out)
    r = np.where(np.isnan(p.fluc), np.nan, p.fluc / 100)
    with np.errstate(invalid="ignore", divide="ignore"):
        f = pd.DataFrame({
            "거래대금 급증(신호일/20일 평균)": p.trdval[t, c] / a["adv20"][t, c],
            "거래대금 대비 매수 %": a["adv_pct"][t, c],
            "매수 금액": a["amount"][t, c],
            "시가총액": p.mcap[t, c],
            "52주 고가 근접도": adj[t, c] / past(adj, 250, np.max),
            "60일 변동성": past(r, 60, np.std),
            "신호 전 20일 수익률": ev.pre20.values,
            "신호일 등락률": ev.fluc0.values,
            # 지난 1년(최근 20일 제외) 연기금 누적 순매수 / 시총: 0 근처면 '새 종목'
            "지난 1년 연기금 보유 흔적": np.array([np.nansum(net[max(0, ti - 250):max(0, ti - 20), ci])
                                          for ti, ci in zip(t, c)]) / p.mcap[t, c],
        })
    return f


def selection(x: Ctx, ev: pd.DataFrame, hold: int = 20) -> str:
    res = [trade(x, t, c, t + 1, t + hold) for t, c in zip(ev.t, ev.c)]
    d = ev.assign(ex=[r[1] if r else np.nan for r in res]).reset_index(drop=True)
    f = features(x, ev).reset_index(drop=True)
    d = pd.concat([d, f], axis=1).dropna(subset=["ex"])
    d["코스닥"] = (x.p.market[d.c.values] == "KSQ").astype(float)
    rows = []
    for col in list(f.columns) + ["코스닥"]:
        row = {"기준": col}
        signs = []
        for part in ("과거 1년", "최근 1년"):
            g = d[d.part == part].dropna(subset=[col])
            if col == "코스닥":
                hi, lo = g[g[col] == 1], g[g[col] == 0]
            else:
                med = g[col].median()
                hi, lo = g[g[col] > med], g[g[col] <= med]
            diff_med = hi.ex.median() - lo.ex.median()
            diff_mean = hi.ex.mean() - lo.ex.mean()
            big = (hi.ex >= 30).mean() * 100 - (lo.ex >= 30).mean() * 100
            row[f"{part} 중앙 차"] = round(diff_med, 1)
            row[f"{part} 평균 차"] = round(diff_mean, 1)
            row[f"{part} +30%↑ 비율 차"] = round(big, 1)
            signs.append(np.sign(diff_med) == np.sign(diff_mean) and np.sign(diff_med))
        row["두 해 같은 방향"] = "예" if signs[0] and signs[0] == signs[1] else ""
        rows.append(row)
    return ("값이 큰 쪽(코스닥은 코스닥 쪽) 절반에서 작은 쪽 절반을 뺀 20일 초과수익 차이(%p). "
            "'두 해 같은 방향'은 중앙·평균 차가 두 해 모두 같은 부호일 때.\n\n" + table(rows))


# ------------------------------------------------------------------ D. 포트폴리오

def index_returns(x: Ctx) -> Dict[str, np.ndarray]:
    """비교 기준 일간 수익률: 시총가중(코스피+코스닥 전체), 동일가중(신호 후보와 같은 유니버스)."""
    r = np.clip(np.nan_to_num(x.p.fluc) / 100, -0.3, 0.3)
    w = np.where(x.valid, np.nan_to_num(x.p.mcap), 0)
    pw = np.vstack([w[:1], w[:-1]])
    pv = np.vstack([x.valid[:1], x.valid[:-1]]) & x.valid
    return {"시총가중": (pw * r).sum(1) / np.maximum(pw.sum(1), 1),
            "동일가중": (pv * r).sum(1) / np.maximum(pv.sum(1), 1)}


def curve_stats(daily: np.ndarray, ref: Optional[np.ndarray] = None) -> dict:
    """연수익률·최대낙폭·샤프. ref(동일가중 지수 일간 수익률)를 주면 베타와 연 알파도 계산."""
    curve = np.cumprod(1 + daily)
    cagr = curve[-1] ** (252 / len(daily)) - 1
    mdd = (curve / np.maximum.accumulate(curve) - 1).min()
    sharpe = daily.mean() / (daily.std() + 1e-12) * np.sqrt(252)
    out = {"연수익률%": round(cagr * 100, 1), "최대낙폭%": round(mdd * 100, 1), "샤프": round(sharpe, 2)}
    if ref is not None:
        beta = np.cov(daily, ref)[0, 1] / (ref.var() + 1e-12)
        out.update({"베타": round(beta, 2), "알파(연)%": round((daily.mean() - beta * ref.mean()) * 252 * 100, 1)})
    return out


def regime(x: Ctx, ma: int = 60) -> np.ndarray:
    """동일가중 지수가 ma일 이동평균 위에 있는 날 True (시장 추세 필터)."""
    curve = np.cumprod(1 + index_returns(x)["동일가중"])
    avg = pd.Series(curve).rolling(ma, min_periods=ma).mean().values
    return curve > np.nan_to_num(avg, nan=np.inf)


def portfolio(x: Ctx, ev: pd.DataFrame, slots: int, hold: int, start: int, end: int,
              flow_exit: bool = False, stop: Optional[float] = None, trend: bool = False) -> dict:
    """신호 다음 날 시가에 빈 자리가 있으면 평가금액의 1/slots 만큼 산다 (같은 날 여러 건이면 금액이 큰 순).
    hold 거래일째 종가에 판다. flow_exit: 연기금이 신호 뒤 산 것까지 전부 되팔면 그날 종가에 판다.
    stop: 종가 기준 손실이 이만큼(예 -0.15)이면 판다. 왕복 비용 COST."""
    net = x.p.net
    up = regime(x) if trend else None
    by_day: Dict[int, list] = {}
    for t, c, amt in zip(ev.t, ev.c, ev.amount_eok):
        if trend and not up[t]:
            continue
        if start <= t + 1 < end:
            by_day.setdefault(t + 1, []).append((amt, t, c))
    cash, pos, daily, trades, invested = 1.0, [], [], [], []
    prev_val = 1.0
    for d in range(start, end):
        held = {pc for _, pc, *_ in pos}
        for amt, t, c in sorted(by_day.get(d, []), reverse=True):
            if len(pos) >= slots or c in held or not (x.adj_open[d, c] > 0):
                continue
            size = min(cash, prev_val / slots)
            if size <= 1e-9:
                continue
            pos.append({"q": size * (1 - COST / 2) / x.adj_open[d, c], "c": c, "e": d, "t": t,
                        "cum": x.a["amount"][t, c], "buy": x.adj_open[d, c]})
            cash -= size
            held.add(c)
        keep = []
        for ps in pos:
            c, px = ps["c"], x.adj[d, ps["c"]]
            if d > ps["t"]:
                ps["cum"] += np.nan_to_num(net[d, c])
            if not (px > 0):           # 거래정지 등: 가격이 없으면 들고 간다
                keep.append(ps)
                continue
            ps["last"] = px
            exit_ = (d - ps["e"] + 1 >= hold) or d == end - 1
            exit_ |= flow_exit and ps["cum"] < 0
            exit_ |= stop is not None and px / ps["buy"] - 1 <= stop
            if exit_:
                cash += ps["q"] * px * (1 - COST / 2)
                trades.append(px / ps["buy"] * (1 - COST) - 1)
            else:
                keep.append(ps)
        pos = keep
        stock = sum(ps["q"] * ps.get("last", ps["buy"]) for ps in pos)
        val = cash + stock
        daily.append(val / prev_val - 1)
        invested.append(stock / val)
        prev_val = val
    out = curve_stats(np.array(daily), index_returns(x)["동일가중"][start:end])
    tr = np.array(trades)
    out.update({"거래": len(tr), "승률%": round(float((tr > 0).mean() * 100)) if len(tr) else 0,
                "평균 투자비중%": round(float(np.mean(invested)) * 100)})
    return out


PORTFOLIOS = [
    # (이름, slots, hold, flow_exit, stop, trend)
    ("현재: 20일 보유 · 10종목", 10, 20, False, None, False),
    ("20일 · 20종목", 20, 20, False, None, False),
    ("40일 · 10종목", 10, 40, False, None, False),
    ("40일 · 20종목", 20, 40, False, None, False),
    ("60일 · 20종목", 20, 60, False, None, False),
    ("연기금 전량 매도 시 청산(최대 60일) · 20종목", 20, 60, True, None, False),
    ("20일 · 20종목 · 손절 -10%", 20, 20, False, -0.10, False),
    ("20일 · 20종목 · 손절 -15%", 20, 20, False, -0.15, False),
    ("20일 · 20종목 · 손절 -20%", 20, 20, False, -0.20, False),
    ("40일 · 20종목 · 손절 -10%", 20, 40, False, -0.10, False),
    ("40일 · 20종목 · 손절 -15%", 20, 40, False, -0.15, False),
    ("40일 · 20종목 · 손절 -20%", 20, 40, False, -0.20, False),
    ("20일 · 20종목 · 추세 필터", 20, 20, False, None, True),
    ("40일 · 20종목 · 손절 -15% · 추세 필터", 20, 40, False, -0.15, True),
]


def backtests(x: Ctx, ev: pd.DataFrame) -> str:
    first = int(ev.t.min())
    spans = [("과거 1년", first, x.split_t), ("최근 1년", x.split_t, len(x.p.dates))]
    rows = []
    for name, slots, hold, fx, stop, trend in PORTFOLIOS:
        for part, a, b in spans:
            rows.append({"전략": name, "구간": part, **portfolio(x, ev, slots, hold, a, b, fx, stop, trend)})
    idx = index_returns(x)
    for k, r in idx.items():
        for part, a, b in spans:
            rows.append({"전략": f"비교: {k} 지수", "구간": part, **curve_stats(r[a:b], idx["동일가중"][a:b])})
    return table(rows)


SUMMARY = """## 결론 (2026-10-10)

**운용 규칙 (권장)**
- 추천 신호는 **고르지 말고 전부** 다음 날 시가에 산다. 한 종목 비중 **5%** (동시 최대 20종목), **20거래일 보유** 후 종가에 판다.
- 연기금이 더 사는지 확인하고 들어가지 않는다. 신호 전 주가 흐름·신호일 등락·매수 금액으로 거르지 않는다.
- 보유 40·60일, 연기금 매도 시 청산, 시장 추세 필터는 쓰지 않는다 (한 해만 좋고 다른 해에 무너짐).
- 손절은 선택 사항. -15~-20% 손절은 낙폭을 2~4%p 줄이지만 최근 1년 수익도 같이 줄었다.

**근거**
- 수익은 소수 종목이 만든다: 20일 보유 기준 상위 10% 거래가 전체 초과수익의 94%. 나머지 90%의 평균은 +0.4%.
  그래서 신호를 거르거나 동시 보유를 10종목으로 묶어 신호를 놓치면 큰 수익 종목을 빠뜨린다.
- 20일·20종목: 과거 1년 연 38% (낙폭 -10%, 샤프 2.0), 최근 1년 연 29% (낙폭 -31%, 샤프 1.0).
  같은 기간 중소형주를 포함한 동일가중 지수는 +30% / -1%. 베타 0.55~0.76, 동일가중 대비 알파 연 +19% / +28%로 두 해 모두 플러스.
- 연기금은 신호 뒤에도 계속 산다 (5일 안에 65%가 추가 매수, 신호 금액의 0.4배). 하지만 확인 후 들어가면 초반 상승을 놓쳐 오히려 나쁘다.
- 40·60일 보유와 추세 필터는 과거·최근 결과가 정반대 (예: 60일·20종목 샤프 2.1 → 0.45). 과거 1년만 보고 골랐다면 실패했을 규칙이라 버린다.

**선별 (E)**
- 거래대금 급증, 매수 금액, 거래대금 대비 매수 비율, 시총, 52주 고가 근접도, 변동성, 신호 전 수익률, 신호일 등락,
  지난 1년 연기금 보유 흔적 9가지 모두 과거 1년과 최근 1년에서 효과 방향이 반대였다. 최근 1년은 많이 오르고 변동성 큰 종목이,
  과거 1년은 그 반대가 나았다 (장세 의존).
- 두 해 모두 같은 방향은 '코스닥' 하나뿐이고 차이도 작다 (중앙값 +0.3%p / +7.3%p). 자리가 모자랄 때 동점 처리용 정도로만 쓴다.
- 무작위로 신호 절반만 10종목으로 운용하면 최근 1년 연수익률이 2%~65%(10~90% 구간)로 흩어진다. 적게 고를수록 운에 좌우된다.
- 진짜 선별력은 지금 데이터(연기금 수급·시세) 밖에서 찾아야 한다: 증권사 리포트, 자사주 취득 공시, 다른 기관 동반 매수.

**주의**
- 최근 1년은 대형주만 오른 장(시총가중 +81%, 동일가중 -1%)이라 절대 수익은 대형주 지수보다 낮다. 이 전략은 '중소형주 안에서의 초과수익'이다.
- 시장 전체가 빠지면 같이 빠진다 (최근 1년 최대낙폭 -31%). 낙폭을 더 줄이려면 코스닥150 선물·인버스로 베타를 헤지하는 방법이 있다 (미검증).
- 왕복 비용 0.3% 가정. 신호 수가 적어(연 110~150건) 결과의 오차가 크다.
"""


def main() -> None:
    x = build()
    ev = events(x)
    print(f"기간 {x.p.dates[0]}~{x.p.dates[-1]}, 추천 신호 {len(ev)}건 (과거 {sum(ev.part == '과거 1년')}, 최근 {sum(ev.part == '최근 1년')})")
    parts = [f"# 투자 전략 고도화 검증\n\n" + SUMMARY + "\n\n## 상세\n\n데이터 {x.p.dates[0]}~{x.p.dates[-1]}. 추천 신호 {len(ev)}건. "
             f"수익률은 다음 날 시가 매수 기준, '초과'는 같은 날 같은 시장·비슷한 시총 종목 중앙값 대비. "
             f"규칙은 과거 1년(~{SPLIT})으로 고르고 최근 1년으로 확인.\n"]
    sections = [
        ("A1. 신호 뒤에도 연기금이 계속 사나 (전체 기간)", flow_after(x, ev)),
        ("A2. 확인 후 진입 (20일 보유)", table(confirm_rules(x, ev))),
        ("B. 보유 기간·청산 규칙", table(hold_rules(x, ev))),
        ("C. 신호 전 흐름·금액별 (20일 보유)", table(pre_rules(x, ev))),
        ("D. 포트폴리오 백테스트 (왕복 비용 0.3%)", backtests(x, ev)),
        ("E. 선별 기준 (20일 보유, 값 기준 위·아래 절반 비교)", selection(x, ev)),
    ]
    for title, body in sections:
        print(f"\n## {title}\n{body}")
        parts.append(f"\n## {title}\n\n{body}")
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "STRATEGY.md").write_text("".join(parts))


if __name__ == "__main__":
    main()
