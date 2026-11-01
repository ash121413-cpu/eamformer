"""팀 편성 규칙 (DB, 화면과 무관한 순수 함수).

학생은 dict로 다룬다:
    {"id": int, "student_no": str, "name": str, "main_role": str, "sub_roles": [str, ...]}
"""

ROLES = ["기획자", "웹 프로그래머", "프론트엔드 디자이너", "테스터", "보고서 작성자", "발표자"]
PL = "기획자"
WP = "웹 프로그래머"
REQUIRED = (PL, WP)
MIN_SIZE = 2
MAX_SIZE = 4


def roles_of(s):
    """주 역할 + 보조 역할 (필수 포함 역할을 맡을 수 있는지 따질 때만 씀)."""
    return {s["main_role"], *s.get("sub_roles", [])}


def required_filled(members):
    """기획자와 웹 프로그래머를 서로 다른 학생이 맡도록 했을 때 채울 수 있는 필수 포함 역할 수(0~2).

    한 학생이 둘 다 할 수 있어도 하나로만 센다(이분 매칭의 최대 크기).
    """
    pl = [m for m in members if PL in roles_of(m)]
    wp = [m for m in members if WP in roles_of(m)]
    for a in pl:
        for b in wp:
            if a["student_no"] != b["student_no"]:
                return 2
    return 1 if (pl or wp) else 0


def required_missing(members):
    """아직 채우지 못한 필수 포함 역할 수."""
    return len(REQUIRED) - required_filled(members)


def missing_role_text(members):
    """채우지 못한 필수 포함 역할을 사람이 읽을 문장으로."""
    has_pl = any(PL in roles_of(m) for m in members)
    has_wp = any(WP in roles_of(m) for m in members)
    if required_filled(members) == 2:
        return ""
    if not has_pl and not has_wp:
        return "기획자와 웹 프로그래머를 맡을 학생이 없음"
    if not has_pl:
        return "기획자를 맡을 학생이 없음"
    if not has_wp:
        return "웹 프로그래머를 맡을 학생이 없음"
    return "기획자와 웹 프로그래머를 서로 다른 학생이 맡을 수 없음"


def check_join(members, applicant):
    """applicant가 members 팀에 들어갈 수 있으면 None, 아니면 막는 이유.

    지원할 때와 승인 직전 재검사에 같은 함수를 쓴다.
    """
    if len(members) >= MAX_SIZE:
        return "팀이 꽉 참"
    if any(m["main_role"] == applicant["main_role"] for m in members):
        return "같은 주 역할의 팀원이 있음"
    after = list(members) + [applicant]
    if MAX_SIZE - len(after) < required_missing(after):
        return "필수 포함 역할을 맡을 학생의 자리가 모자람"
    return None


def team_errors(team_no, members):
    """확정할 팀 구성 한 팀의 규칙 위반 목록."""
    errs = []
    n = len(members)
    if not (MIN_SIZE <= n <= MAX_SIZE):
        errs.append(f"팀{team_no}의 인원이 {n}명임(2~4명이어야 함)")
    mains = [m["main_role"] for m in members]
    dup = sorted({r for r in mains if mains.count(r) > 1})
    if dup:
        errs.append(f"팀{team_no}에 주 역할 중복: {', '.join(dup)}")
    if n and required_filled(members) < 2:
        errs.append(f"팀{team_no}: {missing_role_text(members)}")
    return errs


def validate_assignment(teams, unassigned, assignment):
    """배정안 검사(공고 2.5절 4항의 다섯 가지).

    teams: {team_no: [학생, ...]}  (지금 팀 구성, 옮기지 않음)
    unassigned: [학생, ...]
    assignment: [(student_no, team_no), ...]  AI나 앱이 낸 배정안
    반환: 위반 문장 목록(비었으면 통과)
    """
    errs = []
    by_no = {s["student_no"]: s for s in unassigned}
    seen = {}
    for sno, tno in assignment:
        sno = str(sno).strip()
        if sno not in by_no:
            errs.append(f"팀이 없는 학생이 아닌 학번이 배정됨: {sno}")
            continue
        if tno not in teams:
            errs.append(f"없는 팀 번호: {tno}")
            continue
        if sno in seen and seen[sno] != tno:
            errs.append(f"{by_no[sno]['name']}이(가) 여러 팀에 배정됨")
        seen[sno] = tno
    # 1. 빠짐없이 배정
    left = [s["name"] for s in unassigned if s["student_no"] not in seen]
    if left:
        errs.append("배정되지 않은 학생: " + ", ".join(left))
    # 2는 위 '여러 팀' 검사. 3~5는 팀마다 검사
    final = {t: list(ms) for t, ms in teams.items()}
    for sno, tno in seen.items():
        final[tno].append(by_no[sno])
    for t in sorted(final):
        errs.extend(team_errors(t, final[t]))
    return errs


def solve(teams, unassigned, node_limit=3_000_000):
    """다섯 가지 검사를 모두 통과하는 배정을 모든 경우를 따져 찾는다(백트래킹).

    반환: [(student_no, team_no), ...] 또는 None(배정 없음).
    """
    team_nos = sorted(teams)
    cur = {t: list(teams[t]) for t in team_nos}
    # 필수 포함 역할을 맡을 수 있는 학생, 선택지가 적은 학생부터
    order = sorted(
        unassigned,
        key=lambda s: (-len(roles_of(s) & set(REQUIRED)), s["main_role"], s["student_no"]),
    )
    result = []
    nodes = [0]

    def deficit():
        return sum(max(0, MIN_SIZE - len(cur[t])) for t in team_nos)

    def rec(i):
        nodes[0] += 1
        if nodes[0] > node_limit:
            raise TimeoutError
        remaining = len(order) - i
        if deficit() > remaining:
            return False
        if i == len(order):
            return all(not team_errors(t, cur[t]) for t in team_nos)
        s = order[i]
        tried_empty = False
        for t in team_nos:
            if not cur[t]:
                if tried_empty:  # 빈 팀은 서로 같으므로 하나만 시도
                    continue
                tried_empty = True
            if check_join(cur[t], s) is not None:
                continue
            cur[t].append(s)
            result.append((s["student_no"], t))
            if rec(i + 1):
                return True
            cur[t].pop()
            result.pop()
        return False

    try:
        return list(result) if rec(0) else None
    except TimeoutError:
        return None


def infeasible_reasons(teams, unassigned):
    """규칙을 지키는 배정이 없을 때 그 이유(사람이 읽을 문장)."""
    reasons = []
    for t in sorted(teams):
        ms = teams[t]
        if ms and MAX_SIZE - len(ms) < required_missing(ms):
            reasons.append(
                f"팀{t}에 {missing_role_text(ms)} (남은 자리 {MAX_SIZE - len(ms)}개로 채울 수 없음)"
            )
    total = sum(len(ms) for ms in teams.values()) + len(unassigned)
    if total < MIN_SIZE * len(teams):
        reasons.append(f"학생이 {total}명이라 팀 {len(teams)}개를 모두 2명 이상으로 채울 수 없음")
    if total > MAX_SIZE * len(teams):
        reasons.append(f"학생이 {total}명이라 팀 {len(teams)}개에 4명씩 넣어도 자리가 모자람")
    for t in sorted(teams):
        ms = teams[t]
        if not ms:
            continue
        need = required_missing(ms)
        if need == 0:
            continue
        can = [s for s in unassigned if check_join(ms, s) is None
               and (roles_of(s) & set(REQUIRED))]
        if not can and len(ms) < MAX_SIZE and not any(f"팀{t}에" in r for r in reasons):
            reasons.append(f"팀{t}에 {missing_role_text(ms)} (들어갈 수 있는 남은 학생 가운데 맡을 학생이 없음)")
    if not reasons:
        reasons.append("모든 경우를 따져 보았지만 인원, 주 역할 중복, 필수 포함 역할을 동시에 지키는 배정이 없음")
    return reasons
