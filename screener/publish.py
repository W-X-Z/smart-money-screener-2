"""시그널과 종목별 연기금 수급을 Firestore 에 올려 배포 웹에서 보이게 한다.

- pension_signals/latest : 시그널 전체 목록 (JSON 문자열)
- pension_flow/{종목코드}  : 최근 약 100거래일 연기금 순매수(억)·종가 (JSON 문자열)

.env 에 FIREBASE_SERVICE_ACCOUNT=<서비스 계정 JSON 경로> 가 필요하다.

사용법:
    python -m screener.publish          # 오늘 올리지 않은 종목의 수급만 새로 조회
    python -m screener.publish --all    # 모든 시그널 종목 수급 새로 조회
"""

import argparse
import json
import os
import sys
from datetime import datetime

from screener import store
from screener.kiwoom import load_env


def firestore_client():
    import firebase_admin
    from firebase_admin import credentials, firestore

    load_env()
    key = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "")
    if not key or not os.path.exists(os.path.expanduser(key)):
        sys.exit(".env 에 FIREBASE_SERVICE_ACCOUNT=<서비스 계정 JSON 경로> 를 설정하세요")
    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(os.path.expanduser(key)))
    return firestore.client()


def publish(refresh_all: bool = False, codes=None) -> None:
    from screener.web import pension_flow  # 키움 조회 + 캐시

    db = firestore_client()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    today = now[:10]
    signals = sorted(store.load().values(), key=lambda s: (s["date"], s["buy_eok"]), reverse=True)
    db.document("pension_signals/latest").set({
        "json": json.dumps(signals, ensure_ascii=False), "updated_at": now, "count": len(signals),
    })
    print(f"시그널 {len(signals)}건 업로드")

    targets = codes if codes is not None else sorted({s["code"] for s in signals})
    done = 0
    for code in targets:
        ref = db.document(f"pension_flow/{code}")
        if not refresh_all and codes is None:
            snap = ref.get(["updated_at"])
            if snap.exists and str(snap.get("updated_at")).startswith(today):
                continue
        try:
            rows = pension_flow(code)
        except Exception as e:
            print(f"  {code} 수급 조회 실패: {e}")
            continue
        ref.set({"json": json.dumps(rows, ensure_ascii=False), "updated_at": now})
        done += 1
    print(f"종목 수급 {done}건 업로드")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="모든 시그널 종목의 수급을 새로 조회")
    args = ap.parse_args()
    publish(refresh_all=args.all)


if __name__ == "__main__":
    main()
