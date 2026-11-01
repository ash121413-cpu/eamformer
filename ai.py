"""오픈라우터로 배정안을 받는 부분.

- 모델 1차, 2차, 3차를 차례로 부름
- 모델 하나를 부른 때부터 응답을 다 받을 때까지 전체 120초를 재서, 넘으면 끊고 다음 모델로 넘어감
- 빈 응답, 오류, HTTP 429, 검사 실패도 실패로 보고 다음 모델로 넘어감
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

import requests

import rules

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TOTAL_TIMEOUT = 120  # 초. 모델마다 전체 대기 시간

_executor = ThreadPoolExecutor(max_workers=4)


def _fmt_student(s):
    subs = ", ".join(s["sub_roles"]) if s["sub_roles"] else "없음"
    return f'- 학번 {s["student_no"]}, 이름 {s["name"]}, 주 역할 {s["main_role"]}, 보조 역할 {subs}'


def build_prompt(teams, unassigned):
    lines = [
        "너는 대학 수업의 팀 편성 도우미다. 아래의 팀이 없는 학생을 기존 팀에 배정하라.",
        "",
        "[규칙]",
        "1. 팀이 없는 학생은 빠짐없이 모두 배정한다. 이미 팀에 있는 학생은 옮기지 않는다.",
        "2. 한 학생은 한 팀에만 배정한다.",
        "3. 배정 뒤 모든 팀은 2~4명이어야 한다(빈 팀 없음).",
        "4. 한 팀 안에 주 역할이 같은 학생이 2명 이상이면 안 된다(보조 역할은 이 규칙과 무관).",
        "5. 모든 팀에는 '기획자'를 맡을 학생과 '웹 프로그래머'를 맡을 학생이 서로 다른 사람으로 1명씩 있어야 한다."
        " 주 역할이나 보조 역할 어느 쪽으로든 맡을 수 있다. 한 학생이 둘 다 할 수 있어도 한 역할만 맡는다.",
        "",
        "[지금 팀 구성]",
    ]
    for t in sorted(teams):
        ms = teams[t]
        lines.append(f"팀{t} ({len(ms)}명):" + ("" if ms else " (빈 팀)"))
        lines.extend(_fmt_student(m) for m in ms)
    lines += ["", "[팀이 없는 학생]"]
    lines.extend(_fmt_student(s) for s in unassigned)
    lines += [
        "",
        "[답 형식]",
        "설명 없이 JSON 하나만 출력하라. team은 팀 번호(정수)다.",
        '{"assignments": [{"student_no": "학번", "team": 1}, ...]}',
    ]
    return "\n".join(lines)


def parse_answer(text):
    """모델 응답에서 [(student_no, team_no), ...]를 꺼냄. 못 꺼내면 ValueError."""
    if not text or not text.strip():
        raise ValueError("빈 응답")
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    m = re.search(r"\{.*\}", t, flags=re.S)
    if not m:
        raise ValueError("JSON을 찾지 못함")
    data = json.loads(m.group(0))
    items = data.get("assignments")
    if not isinstance(items, list):
        raise ValueError("assignments 목록이 없음")
    out = []
    for it in items:
        sno = str(it.get("student_no", "")).strip()
        team = it.get("team", it.get("team_no"))
        if isinstance(team, str):
            team = int(re.sub(r"\D", "", team) or -1)
        out.append((sno, int(team)))
    return out


def _request(api_key, model, prompt):
    resp = requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 6000,
            "temperature": 0.2,
            "reasoning": {"effort": "low"},
        },
        timeout=(10, TOTAL_TIMEOUT + 5),
    )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"오류 응답: {str(data['error'])[:200]}")
    choices = data.get("choices") or []
    content = (choices[0].get("message") or {}).get("content") if choices else None
    return content or ""


def call_model(api_key, model, prompt, on_tick=None):
    """모델 하나를 부름. 반환 (text 또는 None, 실패 이유 또는 None, 걸린 초).

    부른 때부터 TOTAL_TIMEOUT초가 넘으면 기다리기를 그만두고 실패로 돌려줌
    (응답이 천천히 흘러와도 전체 시간으로 끊음).
    """
    start = time.monotonic()
    fut = _executor.submit(_request, api_key, model, prompt)
    while True:
        elapsed = time.monotonic() - start
        remaining = TOTAL_TIMEOUT - elapsed
        if remaining <= 0:
            fut.cancel()
            return None, f"{TOTAL_TIMEOUT}초 안에 응답하지 않음", time.monotonic() - start
        if on_tick:
            on_tick(elapsed)
        try:
            text = fut.result(timeout=min(1.0, remaining))
            return text, None, time.monotonic() - start
        except FutureTimeout:
            continue
        except Exception as e:  # HTTP 오류, 429, 연결 오류
            return None, str(e)[:300], time.monotonic() - start


def assign_with_ai(api_key, models, teams, unassigned, on_status=None, on_log=None):
    """1차→2차→3차 순으로 부르고, 검사를 통과한 첫 배정안을 돌려줌.

    반환: (pairs 또는 None, 쓴 모델 차수 또는 None)
    """
    prompt = build_prompt(teams, unassigned)
    for i, model in enumerate(models, start=1):
        def tick(el, i=i, model=model):
            if on_status:
                on_status(f"{i}차 모델({model})의 응답을 기다리는 중… {int(el)}초 / 최대 {TOTAL_TIMEOUT}초")

        tick(0)
        if not api_key:
            if on_log:
                on_log(i, model, "오류", "OPENROUTER_API_KEY가 없음", 0.0)
            continue
        text, err, el = call_model(api_key, model, prompt, on_tick=tick)
        if err:
            outcome = "시간 초과" if "초 안에" in err else "오류"
            if on_log:
                on_log(i, model, outcome, err, el)
            continue
        try:
            pairs = parse_answer(text)
        except Exception as e:
            if on_log:
                on_log(i, model, "빈 응답" if not (text or "").strip() else "형식 오류", f"{e}: {(text or '')[:300]}", el)
            continue
        errs = rules.validate_assignment(teams, unassigned, pairs)
        if errs:
            if on_log:
                on_log(i, model, "검사 실패", "; ".join(errs), el)
            continue
        if on_log:
            on_log(i, model, "성공", f"{len(pairs)}명 배정, 검사 통과", el)
        return pairs, i
    return None, None
