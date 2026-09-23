# Smart Money Screener 2

연기금 일별 수급이 0에 가깝다가 갑자기 급증한 종목을 장중에 발굴하는 스크리너.

## 현재 단계: 키움 REST API 탐색

연기금 수급을 받을 수 있는 API를 확인하는 단계입니다. 키움 API는 등록된 IP에서만 호출되므로 Mac에서 실행합니다.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # KIWOOM_APP_KEY / KIWOOM_SECRET_KEY 입력
python tools/kiwoom_probe.py   # 결과: probe_output/*.json
```

장중(09:00~15:30)에 실행해야 장중 잠정치 API 응답을 확인할 수 있습니다.
