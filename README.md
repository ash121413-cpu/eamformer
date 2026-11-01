# 팀 프로젝트 팀 편성 앱

데이터과학미니프로젝트 중간 과제 · 작성자: 안서현

## 1. 배포 주소
https://teamformer-seohyun.streamlit.app

## 2. PRD와 구현 계획의 위치
- `docs/PRD.md`
- `docs/구현 계획.md`
- DB 스키마: `schema.sql`

## 3. 고른 해결 방법
| 기능 | 고른 해결 | 내용 |
|---|---|---|
| 다른 사람의 변경 보기 | 어려운 해결 | `st.fragment(run_every=3)`로 학생 화면과 관리자 팀 현황을 3초마다 바뀐 부분만 다시 그림 |
| 필수 포함 역할을 지킬 수 없는 팀 | 어려운 해결 | 지원할 때와 승인 직전에, 지원자가 들어간 뒤 남는 자리 수가 못 채운 필수 포함 역할 수보다 적으면 막음(`rules.check_join`) |
| 같은 팀에 대기 중인 지원이 여럿 | 어려운 해결 | 모든 팀원이 찬성한 순간(투표가 끝난 순서) 정원·주 역할 중복·필수 포함 역할 자리를 다시 검사 |
| 마지막 자리에 두 명이 동시에 들어감 | 어려운 해결 | `tf_members`의 `(team_id, slot_no)` 기본 키와 `student_id` 유일 제약 + 팀 행 잠금(`FOR UPDATE`) |
| AI 응답이 늦을 때 | 어려운 해결 | 모델마다 부른 때부터 120초 전체 대기 시간을 재서 끊고 다음 모델로(`ai.call_model`) |
| 3차 모델까지 실패 | 어려운 해결 | 앱이 백트래킹으로 모든 경우를 따져 5가지 검사를 통과하는 배정을 찾음(`rules.solve`). 배정이 없으면 이유를 보여 줌 |
| AI 대기 중 화면 | 어려운 해결 | "N차 모델(이름)의 응답을 기다리는 중… 경과 초 / 최대 120초"를 `assign_status`에 표시 |
| DB 일시정지 막기 | 어려운 해결 | GitHub Actions 예약 실행(`.github/workflows/keep-db-awake.yml`)으로 매일 DB에 질의 |

## 4. 쓴 모델 3개
- 1차 `nvidia/nemotron-3-ultra-550b-a55b:free`
- 2차 `google/gemma-4-31b-it:free`
- 3차 `nvidia/nemotron-3.5-super-vl:free`

## 5. 오픈라우터 AI 모델 호출 실패 사례
- 11/1 18:00, 배포한 앱에서 연습용 쉬움 시나리오 6단계(마감 → AI 배정)를 했을 때
  - 1차 `google/gemma-4-31b-it:free`: **HTTP 429**(무료 제공자 쪽 사용량 제한, 0.3초) → 실패로 보고 2차로 넘어감
  - 2차 `nvidia/nemotron-3-super-120b-a12b:free`: **HTTP 404**("무료로 쓸 수 없음, 유료 버전을 쓰라") → 실패로 보고 3차로 넘어감
  - 3차 `nvidia/nemotron-3-ultra-550b-a55b:free`: 20.7초 만에 응답, 배정안 2명이 검사 5가지를 통과해 관리자에게 보여 줌 → 확정
- 근거: 관리자 화면의 AI 호출 기록 `img/ai-log-easy.png`

## 6. 클로드가 잘못된 추론이나 답변을 한 사례
- 사례: 클로드가 2차 모델 기본값으로 `nvidia/nemotron-3-super-120b-a12b:free`를 넣었는데, 이 모델은 이미 무료 제공이 끝난 모델이었음. 배포 전에 모델 페이지 3개를 캡처해 보여 주었을 때도, 클로드는 이 모델 페이지 제목에만 "(free)"가 없다는 점을 놓치고 "3개 다 정상"이라고 답함
- 알아챈 방법: 배포한 앱에서 쉬움 시나리오 6단계(AI 배정)를 해 보니 AI 호출 기록에 2차 모델이 HTTP 404로 실패한 것이 찍힘(`img/ai-log-easy.png`)
- 고친 커밋: `5c88990` (1차 nemotron-3-ultra, 2차 gemma-4-31b-it, 3차 nemotron-3.5-super-vl로 바꿈)

## 7. 제출한 앱에서 발견한 문제점이나 개선 필요 사항
- 문제: AI 배정을 누르면 배정이 끝날 때까지 관리자 화면에 "N차 모델의 응답을 기다리는 중" 안내만 보이고 다른 조작을 할 수 없음. 부록 1(쉬움) 6단계에서는 1차·2차 모델이 실패하고 3차 모델이 응답할 때까지 약 21초(18:00:22 → 18:00:43) 동안 기다렸음. 무료 모델 3개가 모두 120초를 채우면 최대 6분 동안 이 상태가 됨
- 발견한 시나리오와 단계: 부록 1(쉬움) 6단계(마감 → AI 배정)
- 화면: `img/ai-log-easy.png` (호출 시각과 걸린 시간)
- 개선 방향: AI 배정을 백그라운드에서 돌리고 화면은 진행 상황만 갱신하게 하거나, 429처럼 바로 실패한 모델은 같은 날 다시 부르지 않도록 해서 기다리는 시간과 호출 횟수를 줄임

참고: 모델을 바꾼 뒤(커밋 `5c88990`) 연습용 중간 명단(12명, 팀 4개)으로 바로 마감하고 AI 배정을 했을 때는 1차 `nemotron-3-ultra`가 26.3초 만에 12명을 배정하고 검사를 통과했음(`img/ai-log-medium.png`)

---

## 로컬에서 실행하기
```bash
pip install -r requirements.txt
cp .env.example .env      # 값을 채움(파일 이름은 정확히 .env)
streamlit run streamlit_app.py
```

## 시험
```bash
# 연습 시나리오 3개(화면 조작) — 시험용 DB 주소로
DATABASE_URL=... ADMIN_PASSWORD=... python tests/run_scenarios.py
# AI 폴백과 규칙(네트워크 없음)
python tests/test_ai_and_rules.py
```
`data/`의 CSV는 공고 부록의 연습용 가상 명단임.
