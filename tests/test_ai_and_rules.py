"""AI 폴백과 규칙 함수 단위 시험(네트워크 없이 가짜 응답으로)."""
import sys, time, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ai, rules

def S(no, main, *subs): return {"id": no, "student_no": str(no), "name": f"s{no}", "main_role": main, "sub_roles": list(subs)}
PL, WP, FD, TT, RW, PT = rules.ROLES
teams = {1: [S(1, PL), S(2, WP)], 2: [], 3: [S(3, TT, PL), S(4, FD, WP)]}
un = [S(5, PL), S(6, WP), S(7, RW), S(8, PT)]
good = json.dumps({"assignments": [{"student_no": "5", "team": 2}, {"student_no": "6", "team": 2}, {"student_no": "7", "team": 1}, {"student_no": "8", "team": 3}]})
bad = json.dumps({"assignments": [{"student_no": "5", "team": 2}, {"student_no": "6", "team": 1}, {"student_no": "7", "team": 2}, {"student_no": "8", "team": 3}]})

ai.TOTAL_TIMEOUT = 3  # 시험용으로 짧게
calls = []
def fake(api_key, model, prompt):
    calls.append(model)
    if model == "slow": time.sleep(10); return good
    if model == "empty": return ""
    if model == "bad": return "```json\n" + bad + "\n```"
    if model == "429": raise RuntimeError("HTTP 429: rate limited")
    return good
ai._request = fake
logs = []
log = lambda *a: logs.append(a[:3])

t = time.time()
pairs, which = ai.assign_with_ai("k", ["slow", "bad", "good"], teams, un, on_log=log)
print("1) slow→bad→good:", which, round(time.time() - t, 1), "초", logs)
assert which == 3 and logs[0][2] == "시간 초과" and logs[1][2] == "검사 실패"
logs.clear()
pairs, which = ai.assign_with_ai("k", ["empty", "429", "bad"], teams, un, on_log=log)
print("2) 모두 실패:", pairs, logs)
assert pairs is None
sol = rules.solve(teams, un)
print("3) 앱이 직접 찾은 배정:", sol, rules.validate_assignment(teams, un, sol))
assert not rules.validate_assignment(teams, un, sol)

# 배정 없음: 팀이 꽉 찼는데 웹 프로그래머가 없음
t2 = {1: [S(1, PL), S(2, FD), S(3, TT), S(4, RW)], 2: []}
u2 = [S(5, WP), S(6, PL)]
print("4) 배정 없음:", rules.solve(t2, u2), rules.infeasible_reasons(t2, u2))
assert rules.solve(t2, u2) is None
# 필수 포함 역할 자리 예시(공고 3장)
print("5) 테스터+발표자 팀에 보고서 작성자:", rules.check_join([S(1, TT), S(2, PT)], S(3, RW)))
print("6) PL;WP 혼자 팀은 1개만 채움:", rules.required_filled([S(1, PL, WP)]))
# 8명 / 4팀 빈 팀 여럿 — 탐색 시간
import random
random.seed(1)
big_un = [S(100 + i, random.choice(rules.ROLES), random.choice([PL, WP])) for i in range(8)]
t = time.time(); r = rules.solve({1: [], 2: [], 3: [], 4: []}, big_un); print("7) 8명 4빈팀:", r is not None, round(time.time() - t, 2), "초")
print("모두 통과")
