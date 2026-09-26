"""장중 스캔: 키움 장중 잠정치(오늘) + 아카이브(어제까지 확정치)로 일배치와 같은 규칙의 시그널을 찾는다.

1) git pull 로 최신 아카이브를 받고
2) 키움 ka10063 에서 오늘 연기금 잠정 순매수(연기금이 매매한 전 종목) 조회
3) engine/signals.py 로 시그널 속성 계산 (기본 포착 = '넓게', 웹 필터가 같은 규칙으로 좁혀 봄)
4) public/data/signals.json 에 '장중 HH:MM' 으로 저장, --push 면 커밋·푸시해 웹 반영
장 마감 후 일배치가 확정치로 다시 계산하면 '장중 → 확정' 으로 표시가 바뀐다.

사용법 (Mac, 키움 등록 IP):
    python -m screener.run            # 스캔 + 저장
    python -m screener.run --push     # 스캔 + 저장 + 커밋·푸시
"""

import argparse
import subprocess
from datetime import datetime

from batch.daily import passes, records, write_flows
from engine import signals as S
from engine.archive import load
from screener import store
from screener.kiwoom import ROOT, Kiwoom, num

PENSION_INVSR = "3"  # ka10063 투자자 구분 중 연기금 (ka10065 연기금 상위와 대조해 확인한 값)


def verify_pension_code(api: Kiwoom, intraday: list) -> None:
    """ka10065(연기금 확정 코드 6000) 상위 종목의 순매수 수량이 ka10063 결과와 일치하는지 대조."""
    by_code = {r["stk_cd"]: r for r in intraday}
    top = api.intraday_top_pension("001") + api.intraday_top_pension("101")
    matched = sum(1 for t in top
                  if t["stk_cd"] in by_code and num(by_code[t["stk_cd"]]["netprps_qty"]) == num(t["netslmt"]))
    print(f"[검증] 연기금 코드 대조: {matched}/{len(top)} 종목 일치")
    if top and matched < len(top) * 0.5:
        print("  ⚠ 일치율이 낮습니다. 결과를 신뢰하지 마세요.")


def git(*cmd) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *cmd], cwd=ROOT, check=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true", help="시그널을 커밋·푸시해 웹에 반영")
    ap.add_argument("--no-pull", action="store_true", help="시작할 때 git pull 생략")
    args = ap.parse_args()

    now = datetime.now()
    today = now.strftime("%Y%m%d")
    if not args.no_pull:
        git("pull", "--rebase", "--autostash", "-q")

    p = load()
    if str(p.dates[-1]) >= today:
        print("아카이브에 오늘 확정치가 이미 있습니다. 일배치 결과를 사용하세요.")
        return

    api = Kiwoom()
    intraday = api.intraday_investor(PENSION_INVSR)
    print(f"장중 연기금 잠정 데이터: {len(intraday)} 종목 (아카이브 마지막 거래일 {p.dates[-1]})")
    verify_pension_code(api, intraday)
    net_today = {r["stk_cd"]: num(r["netprps_amt"]) * 1e6 for r in intraday}  # 백만원 -> 원

    p2 = S.with_today(p, today, net_today)
    a = S.attributes(p2)
    recs = records(p2, a, [len(p2.dates) - 1])
    for r in recs:
        r["source"] = f"장중 {now:%H:%M}"
    n = store.upsert(recs)
    print(f"기본 포착 {len(recs)}건 저장 ({n}건 갱신)")
    for name, kw in S.PRESETS.items():
        hits = [r for r in recs if passes(r, **kw)]
        print(f"  {name}: {len(hits)}건 " + ", ".join(f"{r['name']} {r['buy_eok']}억" for r in hits[:8]))

    if args.push and recs:
        codes = {r["code"] for r in recs}
        write_flows(p2, {k: v for k, v in store.load().items() if v["code"] in codes}, today)
        git("add", "public/data")
        if git("diff", "--cached", "--quiet").returncode != 0:
            git("commit", "-q", "-m", f"장중 시그널 {today} {now:%H:%M} ({len(recs)}건)")
            git("pull", "--rebase", "--autostash", "-q")
            if git("push", "-q").returncode == 0:
                print("푸시 완료 -> 웹 반영")


if __name__ == "__main__":
    main()
