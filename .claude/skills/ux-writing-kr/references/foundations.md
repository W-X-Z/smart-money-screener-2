# 토대 — 이 스킬이 따르는 UX 라이팅 리서치

이 스킬의 규칙은 즉흥이 아니다. 영어권에서 수십 년 쌓인 UX 라이팅·마이크로카피 연구를 **한국어 환경(번역투·높임·말맛)에 맞게 옮긴 것**이다. 아래는 실제 출처와, 그 원리가 이 스킬 어디에 어떻게 반영됐는지다.

## 핵심 출처

- **Nielsen Norman Group (NN/g)** — 에러 메시지, 마이크로카피, 폼 디자인, 시스템 상태 가시성에 대한 사용성 연구의 표준. 이 스킬의 "에러는 원인+행동", "대기 중 상태 알림", "안심 설계"가 여기서 나온다. (nngroup.com)
- **Kinneret Yifrah, 《Microcopy: The Complete Guide》** — 마이크로카피 단행본의 정본. 보이스·톤, 버튼·에러·빈 화면·폼 카피의 실전 원칙. 이 스킬의 표면별 가이드(`surfaces.md`) 골격이 여기에 빚지고 있다.
- **Jakob Nielsen, 10대 사용성 휴리스틱** — 특히 "시스템 상태의 가시성", "에러 예방", "사용자가 에러를 인지·복구하도록 돕기". 안심 축의 이론적 뿌리.
- **Material Design — Writing** (Google) — 톤, 대소문자, 간결성, 에러 문구 규칙. (m3.material.io)
- **Apple Human Interface Guidelines — Writing** — 명료함, 사용자 시점, 군더더기 제거.
- **Mailchimp Content Style Guide** — 보이스·톤을 맥락(사용자 감정 상태)에 따라 조절하는 모델. "불안한 사용자 앞에서 농담하지 않는다"의 출처.
- **Shopify Polaris — Content guidelines** — 행동 중심 동사, 버튼은 그 일을 말하기, 일관성.
- **GOV.UK Design System / Style Guide** — 쉬운 말(plain language), 에러 요약, 사용자를 탓하지 않는 문구.

## 원리 → 이 스킬 매핑

| 정통 원리 | 출처 | 이 스킬에서 |
|---|---|---|
| 시스템 상태를 항상 보이게 | Nielsen 휴리스틱 #1 | 안심 §대기/처리 중, 토스트로 결과 알림 |
| 에러는 원인·해법을 사람 말로, 탓하지 않기 | NN/g, GOV.UK | `surfaces.md` 에러, `translationese-lens.md` #2·#5 |
| 버튼은 그 버튼이 하는 일을 말한다 | Polaris, Yifrah | `surfaces.md` 버튼, UX 원칙 렌즈 |
| 보이스·톤을 사용자 감정에 맞춘다 | Mailchimp | `voice-register.md` 보이스, "민감 맥락에서 위트 금지" |
| 군더더기 제거·쉬운 말 | Apple HIG, GOV.UK | UX 원칙 렌즈, `translationese-lens.md` #8·#9 |
| 되돌릴 수 있게(undo)·결과 미리 알리기 | NN/g 에러 복구 | 안심 §되돌릴 수 없는 행동, `surfaces.md` 다이얼로그·토스트 |

## 한국어로 옮길 때 더해지는 것

위 출처는 대부분 영어 기준이라, 그대로는 한국어에서 안 통한다. 이 스킬이 추가로 책임지는 한국어 고유 레이어:

1. **번역투 제거** — 영어 원리를 직역하면 오히려 어색해진다("성공적으로 ~되었습니다"). `translationese-lens.md`.
2. **높임 레지스터** — 영어엔 없는 해요체/합쇼체/반말 선택과 일관성. `voice-register.md`.
3. **한국 서비스 말맛** — 토스·배민·당근이 정립한 "통보가 아니라 대화" 결. `voice-register.md`.

즉 이 스킬 = **검증된 글로벌 UX 라이팅 원리 + 한국어 네이티브 레이어.**
