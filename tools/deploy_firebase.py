"""Firestore 보안 규칙과 웹 UI(web/)를 Firebase 에 배포한다. Firebase CLI 없이 REST API 사용.

기존 기본 사이트(smart-money-screener.web.app)는 건드리지 않고 별도 Hosting 사이트에 배포한다.

사용법:
    python tools/deploy_firebase.py                 # 규칙 + 웹 배포
    python tools/deploy_firebase.py --hosting-only
    python tools/deploy_firebase.py --key /path/to/service-account.json
"""

import argparse
import gzip
import hashlib
import os
import sys
from pathlib import Path

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PROJECT = "smart-money-screener"
SITE = "smart-money-pension"
HOSTING = "https://firebasehosting.googleapis.com/v1beta1"
RULES = "https://firebaserules.googleapis.com/v1"


def session(key: str) -> AuthorizedSession:
    creds = service_account.Credentials.from_service_account_file(
        key, scopes=["https://www.googleapis.com/auth/cloud-platform", "https://www.googleapis.com/auth/firebase"])
    return AuthorizedSession(creds)


def check(resp, what):
    if resp.status_code >= 300:
        sys.exit(f"{what} 실패 [{resp.status_code}]: {resp.text[:500]}")
    return resp.json() if resp.content else {}


def deploy_rules(s: AuthorizedSession) -> None:
    content = (ROOT / "firebase" / "firestore.rules").read_text()
    ruleset = check(s.post(f"{RULES}/projects/{PROJECT}/rulesets",
                           json={"source": {"files": [{"name": "firestore.rules", "content": content}]}}),
                    "규칙 생성")
    name = f"projects/{PROJECT}/releases/cloud.firestore"
    check(s.patch(f"{RULES}/{name}", json={"release": {"name": name, "rulesetName": ruleset["name"]}}),
          "규칙 배포")
    print(f"Firestore 규칙 배포: {ruleset['name']}")


def ensure_site(s: AuthorizedSession) -> None:
    r = s.get(f"{HOSTING}/sites/{SITE}")
    if r.status_code == 200:
        return
    check(s.post(f"{HOSTING}/projects/{PROJECT}/sites", params={"siteId": SITE}, json={}), "사이트 생성")
    print(f"Hosting 사이트 생성: {SITE}")


def deploy_hosting(s: AuthorizedSession) -> None:
    ensure_site(s)
    files = {}
    for path in (ROOT / "web").rglob("*"):
        if path.is_file():
            gz = gzip.compress(path.read_bytes(), mtime=0)
            files["/" + path.relative_to(ROOT / "web").as_posix()] = (hashlib.sha256(gz).hexdigest(), gz)

    version = check(s.post(f"{HOSTING}/sites/{SITE}/versions", json={"config": {
        "headers": [{"glob": "**", "headers": {"Cache-Control": "no-cache"}}],
    }}), "버전 생성")["name"]
    pop = check(s.post(f"{HOSTING}/{version}:populateFiles",
                       json={"files": {p: h for p, (h, _) in files.items()}}), "파일 목록 등록")
    by_hash = {h: gz for h, gz in files.values()}
    for h in pop.get("uploadRequiredHashes", []):
        check(s.post(f"{pop['uploadUrl']}/{h}", data=by_hash[h],
                     headers={"Content-Type": "application/octet-stream"}), f"업로드 {h[:8]}")
    check(s.patch(f"{HOSTING}/{version}", params={"update_mask": "status"}, json={"status": "FINALIZED"}),
          "버전 확정")
    check(s.post(f"{HOSTING}/sites/{SITE}/releases", params={"versionName": version}, json={}), "릴리스")
    print(f"웹 배포 완료: https://{SITE}.web.app  ({len(files)}개 파일)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", help="서비스 계정 JSON 경로 (기본: .env 의 FIREBASE_SERVICE_ACCOUNT)")
    ap.add_argument("--hosting-only", action="store_true")
    ap.add_argument("--rules-only", action="store_true")
    args = ap.parse_args()
    key = args.key
    if not key:
        from screener.kiwoom import ROOT as _root  # noqa: F401  (.env 로드용)
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("FIREBASE_SERVICE_ACCOUNT="):
                    key = line.split("=", 1)[1].strip().strip("'\"")
    if not key or not os.path.exists(os.path.expanduser(key)):
        sys.exit("서비스 계정 JSON 경로가 필요합니다 (--key 또는 .env FIREBASE_SERVICE_ACCOUNT)")
    s = session(os.path.expanduser(key))
    if not args.hosting_only:
        deploy_rules(s)
    if not args.rules_only:
        deploy_hosting(s)


if __name__ == "__main__":
    main()
