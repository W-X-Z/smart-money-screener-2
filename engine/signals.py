"""연기금 '침묵 후 급증' 시그널의 속성·기본 포착·필터 (운영 일배치, 장중 스캔, 검증이 함께 사용).

기본 포착(전체): 오늘 연기금 순매수 > 0, 1~2일 누적 1억 이상, 직전 20거래일 중 |순매수| 0.3억 초과인 날 2일 이하.
필터(좁히기): 금액 하한, 침묵 강도, 규모 대비(5억 미만이면 거래대금 5% 이상), 2일 누적 인정, 우선주 제외.
웹(public/index.html)의 필터 계산은 mask() 와 같은 규칙이다.
"""

import re
from typing import Dict

import numpy as np

from engine.archive import Panel, roll, shift

EOK = 1e8
LOOKBACK = 20          # 침묵 판단 기간 (거래일)
NOISE_EOK = 0.3        # 이 금액 이하 매매는 '침묵'으로 본다
MIN_AGE = 45           # 아카이브 등장 후 최소 거래일 (신규 상장 제외)
COOLDOWN = 20          # 같은 종목 재신호 억제 (거래일)
BASE = dict(amount=1, silence=2)
NON_STOCK = re.compile(r"스팩|ETN|ETF|리츠|REIT|인프라")

AMOUNTS = (1, 3, 5)
SILENCES = (0, 1, 2)
PRESETS = {
    "추천": dict(amount=3, silence=0, scale=True, two_day=True, pref=True),
    "넓게": dict(amount=3, silence=1, scale=True, two_day=True, pref=True),
    "전체": dict(amount=1, silence=2, scale=False, two_day=True, pref=True),
}


def attributes(p: Panel) -> Dict[str, np.ndarray]:
    """(날짜, 종목)별 시그널 속성. 전날에도 0.3억 넘게 샀으면 2일 누적으로 보고, 침묵 구간을 하루 앞당긴다."""
    noise = NOISE_EOK * EOK
    net, absnet = p.net, np.abs(p.net)
    prev = shift(net, 1)
    two_day = np.nan_to_num(prev) > noise
    amount = np.where(two_day, net + np.nan_to_num(prev), net)
    big_days = np.where(two_day, roll(absnet, 2, LOOKBACK, "count_gt", noise), roll(absnet, 1, LOOKBACK, "count_gt", noise))
    past_max = np.where(two_day, roll(absnet, 2, LOOKBACK, "max"), roll(absnet, 1, LOOKBACK, "max"))
    adv20 = roll(p.trdval, 1, 20, "nanmean")
    with np.errstate(invalid="ignore", divide="ignore"):
        adv_pct = amount / adv20 * 100

    D, _ = net.shape
    priced = ~np.isnan(p.close)
    first = np.where(priced.any(0), priced.argmax(0), D)
    age_ok = (np.arange(D)[:, None] - first[None, :]) >= MIN_AGE
    name_ok = np.array([not NON_STOCK.search(str(n)) for n in p.names])[None, :]
    trading = priced & (np.nan_to_num(p.trdval) > 0)
    valid = trading & age_ok & name_ok & (np.nan_to_num(adv20) > 0)
    base = (valid & (net > 0) & (amount >= BASE["amount"] * EOK)
            & (np.nan_to_num(big_days, nan=99) <= BASE["silence"]))
    pref = np.broadcast_to(np.array([c[-1] != "0" for c in p.codes])[None, :], net.shape)
    return {"net": net, "amount": amount, "two_day": two_day, "big_days": big_days, "past_max": past_max,
            "adv20": adv20, "adv_pct": adv_pct, "base": base, "pref": pref}


def with_today(p: Panel, today: str, net_today: Dict[str, float]) -> Panel:
    """아카이브(어제까지 확정치)에 오늘 장중 잠정 순매수(원) 한 줄을 붙인 패널. 시세는 전날 값으로 채운다."""
    ci = {c: i for i, c in enumerate(p.codes)}
    row = np.zeros(len(p.codes))
    for code, v in net_today.items():
        if code in ci:
            row[ci[code]] = v

    def carry(x):
        return np.vstack([x, x[-1:]])

    return Panel(
        dates=np.append(p.dates, today), codes=p.codes, names=p.names, market=p.market,
        net=np.vstack([p.net, row]), gross=np.vstack([p.gross, np.abs(row)]),
        close=carry(p.close), open=carry(p.open), fluc=np.vstack([p.fluc, np.zeros((1, len(p.codes)))]),
        trdval=carry(p.trdval), mcap=carry(p.mcap),
    )


def mask(a: Dict[str, np.ndarray], amount=3, silence=0, scale=True, two_day=True, pref=True) -> np.ndarray:
    """기본 포착 위에 필터를 적용. pref=False 면 우선주 제외."""
    m = a["base"] & (a["amount"] >= amount * EOK) & (a["big_days"] <= silence)
    if scale:
        m &= (a["amount"] >= 5 * EOK) | (np.nan_to_num(a["adv_pct"]) >= 5)
    if not two_day:
        m &= ~a["two_day"]
    if not pref:
        m &= ~a["pref"]
    return m


def key(amount, silence, scale, two_day, pref) -> str:
    return f"a{amount}_s{silence}_r{int(scale)}_d{int(two_day)}_p{int(pref)}"


def cooldown(t: np.ndarray, c: np.ndarray, amount: np.ndarray = None) -> np.ndarray:
    """같은 종목의 COOLDOWN 거래일 안 재신호를 뺀 인덱스 (t 오름차순 가정 없이).
    amount 를 주면 20거래일 안에서 가장 크게 산 신호 하나를 남긴다 (더 큰 신호가 오면 그 날로 바꾸고 기간도 거기서 다시 센다).
    이렇게 해야 금액 하한을 낮췄을 때 높은 하한에서 보이던 신호가 작은 신호에 가려지지 않는다."""
    order = np.lexsort((t, c))
    keep, last = [], {}
    for i in order:
        prev = last.get(c[i])
        if prev is None or t[i] - prev[0] >= COOLDOWN:
            keep.append(i)
            last[c[i]] = (t[i], len(keep) - 1)
        elif amount is not None and amount[i] > amount[keep[prev[1]]]:
            keep[prev[1]] = i
            last[c[i]] = (t[i], prev[1])
    return np.array(sorted(keep, key=lambda i: t[i]), dtype=int)
