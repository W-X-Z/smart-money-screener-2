"""research/archive 의 연기금 수급·시세를 (날짜 × 종목) 배열로 읽는다."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / "research" / "archive"


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


def read(kind: str) -> pd.DataFrame:
    """월별 파일(YYYYMM.parquet)과 일별 파일(daily/YYYYMMDD.parquet)을 합친다."""
    files = sorted((ARCHIVE / kind).glob("*.parquet")) + sorted((ARCHIVE / kind / "daily").glob("*.parquet"))
    df = pd.concat([pd.read_parquet(p) for p in files])
    return df.drop_duplicates(["date", "code"], keep="last")


def load() -> Panel:
    pen, px = read("pension"), read("prices")
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
