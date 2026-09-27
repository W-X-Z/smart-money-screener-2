"""장중 반복 스캔: 평일 장중에 screener.run 을 일정 간격으로 돌린다 (Mac 에서 켜두는 용도).

- 09:05 부터 15:40 까지 INTERVAL 분마다 키움 장중 잠정치를 받아 시그널을 갱신하고 푸시한다.
- 잠정치가 지난번과 같으면 저장·푸시하지 않는다 (불필요한 커밋·배포 방지).
- 오류가 나도 멈추지 않고 다음 차례에 다시 시도한다. 토큰 오류면 새로 발급받는다.
- 주말·휴장일(잠정치 없음)에는 스스로 끝난다.
자동 시작: tools/install_intraday.sh (launchd, 평일 08:55).

사용법:
    python -m screener.loop               # 20분마다, 푸시
    python -m screener.loop --every 10    # 10분마다
    python -m screener.loop --no-push     # 로컬에만 저장
"""

import argparse
import time
import traceback
from datetime import datetime, timedelta

from screener.kiwoom import Kiwoom
from screener.run import scan

START, END = (9, 5), (15, 40)
EMPTY_LIMIT = 3  # 장 시작 후에도 잠정치가 이만큼 연속으로 비면 휴장으로 보고 끝낸다


def at(now: datetime, hm: tuple) -> datetime:
    return now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=int, default=20, help="스캔 간격(분)")
    ap.add_argument("--no-push", action="store_true", help="커밋·푸시하지 않고 로컬에만 저장")
    args = ap.parse_args()

    now = datetime.now()
    if now.weekday() >= 5:
        print("주말이라 장중 스캔을 하지 않아요.")
        return
    if now > at(now, END):
        print("오늘 장이 끝나서 장중 스캔을 하지 않아요. 확정치는 16:10 일배치가 반영해요.")
        return
    if now < at(now, START):
        wait = (at(now, START) - now).total_seconds()
        print(f"{START[0]:02d}:{START[1]:02d}까지 기다렸다가 시작해요.")
        time.sleep(wait)

    api, last, empty, verified = None, "", 0, False
    while datetime.now() <= at(datetime.now(), END):
        t0 = datetime.now()
        try:
            api = api or Kiwoom()
            fp = scan(api, push=not args.no_push, verify=not verified, last=last)
            if fp is None:
                empty += 1
                if empty >= EMPTY_LIMIT and datetime.now() > at(datetime.now(), (10, 0)):
                    print("장중 잠정치가 계속 없어요. 휴장일로 보고 오늘 스캔을 끝낼게요.")
                    return
            else:
                empty, verified, last = 0, True, fp
        except (Exception, SystemExit) as e:  # 네트워크·토큰·git 오류가 나도 장중에는 계속 돈다
            print(f"[{datetime.now():%H:%M}] ⚠ 스캔 중 오류가 났어요. 다음 차례에 다시 시도해요: {e}")
            traceback.print_exc(limit=2)
            if "토큰" in str(e) or "8005" in str(e) or "401" in str(e):
                api = None
        end = at(datetime.now(), END)
        if t0 >= end:
            break
        nxt = t0 + timedelta(minutes=args.every)
        if nxt > end > datetime.now():
            nxt = end  # 장 마감 직후(15:40) 한 번 더 받아서 종가 무렵 수급까지 반영
        sleep = (nxt - datetime.now()).total_seconds()
        if sleep > 0:
            time.sleep(sleep)
    print("오늘 장중 스캔을 마쳤어요. 확정치는 16:10 일배치가 반영해요.")


if __name__ == "__main__":
    main()
