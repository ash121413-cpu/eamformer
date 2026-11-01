"""DB에 읽고 쓰는 동작. 화면(Streamlit)과 분리해 두어 시험하기 쉽게 함.

모든 함수는 (kind, message) 꼴의 결과를 돌려준다. kind는 "승인", "대기", "거절", "막힘" 가운데 하나.
"""
import csv
import io
import json
from pathlib import Path

import bcrypt
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

import rules

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class SeatTaken(Exception):
    """팀에 빈 자리가 없음."""


# ---------------------------------------------------------------- 연결
def make_pool(database_url):
    """연결 풀을 한 번만 만들고 다시 씀(질의마다 새로 연결하지 않음)."""
    pool = ConnectionPool(
        database_url,
        min_size=1,
        max_size=6,
        kwargs={"autocommit": True, "prepare_threshold": None, "row_factory": dict_row},
        check=ConnectionPool.check_connection,
        open=True,
    )
    return pool


def init_db(pool, admin_password):
    """테이블을 만들고(없으면), 관리자 계정을 만들거나 비밀번호를 갱신함."""
    with pool.connection() as conn:
        conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        row = conn.execute(
            "select id, password_hash from tf_students where student_no = 'admin'"
        ).fetchone()
        if row is None:
            conn.execute(
                "insert into tf_students (student_no, name, main_role, password_hash, is_admin)"
                " values ('admin', '관리자', null, %s, true)",
                (hash_pw(admin_password),),
            )
        elif not check_pw(admin_password, row["password_hash"]):
            conn.execute(
                "update tf_students set password_hash = %s, is_admin = true where id = %s",
                (hash_pw(admin_password), row["id"]),
            )


def hash_pw(pw):
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=10)).decode()


def check_pw(pw, hashed):
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except ValueError:
        return False


# ---------------------------------------------------------------- 읽기
def get_setting(conn, key, default=None):
    row = conn.execute("select value from tf_settings where key = %s", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "insert into tf_settings (key, value) values (%s, %s)"
        " on conflict (key) do update set value = excluded.value",
        (key, value),
    )


def login(pool, student_no, password):
    with pool.connection() as conn:
        row = conn.execute(
            "select student_no, password_hash from tf_students where student_no = %s",
            (student_no.strip(),),
        ).fetchone()
    if row and check_pw(password, row["password_hash"]):
        return row["student_no"]
    return None


def _students(conn):
    rows = conn.execute(
        "select id, student_no, name, main_role, is_admin from tf_students order by student_no"
    ).fetchall()
    subs = conn.execute("select student_id, role from tf_student_sub_roles").fetchall()
    order = {r: i for i, r in enumerate(rules.ROLES)}
    by_id = {}
    for r in rows:
        r["sub_roles"] = []
        by_id[r["id"]] = r
    for s in subs:
        if s["student_id"] in by_id:
            by_id[s["student_id"]]["sub_roles"].append(s["role"])
    for r in by_id.values():
        r["sub_roles"].sort(key=lambda x: order.get(x, 99))
    return by_id


def load_state(conn):
    """화면 한 번 그리는 데 필요한 것을 질의 몇 번으로 읽음."""
    students = _students(conn)
    teams = conn.execute("select id, team_no, name from tf_teams order by team_no").fetchall()
    members = conn.execute(
        "select team_id, slot_no, student_id from tf_members order by team_id, slot_no"
    ).fetchall()
    apps = conn.execute(
        "select a.id, a.team_id, a.student_id, a.status, a.reason, a.created_at, a.decided_at"
        " from tf_applications a order by a.id"
    ).fetchall()
    votes = conn.execute("select application_id, voter_id, vote from tf_votes").fetchall()
    settings = {r["key"]: r["value"] for r in conn.execute("select key, value from tf_settings")}
    team_by_id = {t["id"]: t for t in teams}
    for t in teams:
        t["members"] = []
    member_team = {}
    for m in members:
        team_by_id[m["team_id"]]["members"].append(students[m["student_id"]])
        member_team[m["student_id"]] = team_by_id[m["team_id"]]
    votes_by_app = {}
    for v in votes:
        votes_by_app.setdefault(v["application_id"], {})[v["voter_id"]] = v["vote"]
    for a in apps:
        a["votes"] = votes_by_app.get(a["id"], {})
        a["team"] = team_by_id.get(a["team_id"])
        a["student"] = students.get(a["student_id"])
    unassigned = [
        s for s in students.values() if not s["is_admin"] and s["id"] not in member_team
    ]
    return {
        "students": students,
        "teams": teams,
        "member_team": member_team,
        "apps": apps,
        "closed": settings.get("closed") == "1",
        "confirmed": settings.get("confirmed") == "1",
        "proposal": json.loads(settings["proposal"]) if settings.get("proposal") else None,
        "assign_status": settings.get("assign_status", ""),
        "unassigned": unassigned,
    }


def student_by_no(conn, student_no):
    for s in _students(conn).values():
        if s["student_no"] == student_no:
            return s
    return None


def _team_members(conn, team_id):
    ids = [
        r["student_id"]
        for r in conn.execute(
            "select student_id from tf_members where team_id = %s order by slot_no", (team_id,)
        )
    ]
    studs = _students(conn)
    return [studs[i] for i in ids]


def _add_member(conn, team_id, student_id):
    """빈 자리 번호 가운데 가장 작은 곳에 넣음. 자리 유일성은 DB 기본 키가 지킴."""
    used = {
        r["slot_no"]
        for r in conn.execute("select slot_no from tf_members where team_id = %s", (team_id,))
    }
    free = [n for n in range(1, 5) if n not in used]
    if not free:
        raise SeatTaken("팀이 꽉 참")
    conn.execute(
        "insert into tf_members (team_id, slot_no, student_id) values (%s, %s, %s)",
        (team_id, free[0], student_id),
    )


# ---------------------------------------------------------------- 명단
def parse_roster(data: bytes):
    """CSV(utf-8-sig, BOM 있어도 됨)를 읽어 학생 목록으로. 틀리면 ValueError."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("CSV를 UTF-8로 읽을 수 없음")
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise ValueError("CSV가 비어 있음")
    header = [h.strip() for h in rows[0]]
    if header != ["student_no", "name", "main_role", "sub_roles"]:
        raise ValueError("첫 줄이 student_no,name,main_role,sub_roles 가 아님")
    out, seen = [], set()
    for i, r in enumerate(rows[1:], start=2):
        if len(r) != 4:
            raise ValueError(f"{i}번째 줄의 열이 4개가 아님")
        sno, name, main, subs = (c.strip() for c in r)
        if not sno or not name:
            raise ValueError(f"{i}번째 줄에 학번이나 이름이 없음")
        if sno == "admin" or sno in seen:
            raise ValueError(f"{i}번째 줄의 학번이 중복되거나 쓸 수 없음: {sno}")
        if main not in rules.ROLES:
            raise ValueError(f"{i}번째 줄의 주 역할이 6개 역할이 아님: {main}")
        sub_list = [x.strip() for x in subs.split(";") if x.strip()] if subs else []
        if len(sub_list) > 2 or any(x not in rules.ROLES or x == main for x in sub_list):
            raise ValueError(f"{i}번째 줄의 보조 역할이 틀림: {subs}")
        seen.add(sno)
        out.append({"student_no": sno, "name": name, "main_role": main, "sub_roles": sub_list})
    if not out:
        raise ValueError("CSV에 학생이 없음")
    return out


def reset_with_roster(pool, data, team_count):
    try:
        roster = parse_roster(data)
    except ValueError as e:
        return "막힘", f"명단을 읽지 못함: {e}"
    if not (1 <= int(team_count) <= 20):
        return "막힘", "팀 수는 1~20이어야 함"
    hashes = [hash_pw("1234") for _ in roster]
    with pool.connection() as conn, conn.transaction():
        conn.execute("lock table tf_members, tf_applications in exclusive mode")
        conn.execute("delete from tf_votes")
        conn.execute("delete from tf_applications")
        conn.execute("delete from tf_members")
        conn.execute("delete from tf_teams")
        conn.execute("delete from tf_students where not is_admin")
        conn.execute("delete from tf_settings")
        set_setting(conn, "closed", "0")
        set_setting(conn, "confirmed", "0")
        for n in range(1, int(team_count) + 1):
            conn.execute("insert into tf_teams (team_no, name) values (%s, %s)", (n, f"팀{n}"))
        for s, h in zip(roster, hashes):
            sid = conn.execute(
                "insert into tf_students (student_no, name, main_role, password_hash)"
                " values (%s, %s, %s, %s) returning id",
                (s["student_no"], s["name"], s["main_role"], h),
            ).fetchone()["id"]
            for role in s["sub_roles"]:
                conn.execute(
                    "insert into tf_student_sub_roles (student_id, role) values (%s, %s)",
                    (sid, role),
                )
    return "승인", f"명단으로 처음 상태를 만듦(학생 {len(roster)}명, 팀 {int(team_count)}개)"


# ---------------------------------------------------------------- 지원과 투표
def _closed_locked(conn):
    row = conn.execute(
        "select value from tf_settings where key = 'closed' for share"
    ).fetchone()
    return row is not None and row["value"] == "1"


def apply(pool, student_no, team_no):
    with pool.connection() as conn, conn.transaction():
        if _closed_locked(conn):
            return "막힘", "모집이 마감됨"
        team = conn.execute(
            "select id, name from tf_teams where team_no = %s for update", (team_no,)
        ).fetchone()
        if team is None:
            return "막힘", "없는 팀임"
        me = student_by_no(conn, student_no)
        if me is None or me["is_admin"]:
            return "막힘", "학생만 지원할 수 있음"
        if conn.execute("select 1 from tf_members where student_id = %s", (me["id"],)).fetchone():
            return "막힘", "이미 팀이 있음"
        if conn.execute(
            "select 1 from tf_applications where student_id = %s and status = '대기'", (me["id"],)
        ).fetchone():
            return "막힘", "결과를 기다리는 지원이 있음"
        members = _team_members(conn, team["id"])
        why = rules.check_join(members, me)
        if why:
            return "막힘", why
        if not members:
            _add_member(conn, team["id"], me["id"])
            conn.execute(
                "insert into tf_applications (team_id, student_id, status, reason, decided_at)"
                " values (%s, %s, '승인', '빈 팀이라 바로 승인', now())",
                (team["id"], me["id"]),
            )
            return "승인", f"{team['name']}에 들어감(빈 팀이라 바로 승인)"
        conn.execute(
            "insert into tf_applications (team_id, student_id, status) values (%s, %s, '대기')",
            (team["id"], me["id"]),
        )
        return "대기", f"{team['name']}에 지원함. 팀원 {len(members)}명의 투표를 기다림"


def _latest_app_status(conn, applicant_id):
    row = conn.execute(
        "select a.status, a.reason, t.name from tf_applications a join tf_teams t on t.id = a.team_id"
        " where a.student_id = %s order by a.id desc limit 1",
        (applicant_id,),
    ).fetchone()
    return row


def vote(pool, voter_no, applicant_no, yes):
    with pool.connection() as conn, conn.transaction():
        if _closed_locked(conn):
            return "막힘", "모집이 마감됨"
        voter = student_by_no(conn, voter_no)
        applicant = student_by_no(conn, applicant_no)
        if voter is None or applicant is None:
            return "막힘", "학생을 찾을 수 없음"
        app = conn.execute(
            "select id, team_id from tf_applications where student_id = %s and status = '대기'",
            (applicant["id"],),
        ).fetchone()
        if app is None:
            last = _latest_app_status(conn, applicant["id"])
            if last:
                return last["status"], f"{applicant['name']}의 {last['name']} 지원은 이미 처리됨({last['reason'] or last['status']})"
            return "막힘", "투표할 지원이 없음"
        team = conn.execute(
            "select id, name from tf_teams where id = %s for update", (app["team_id"],)
        ).fetchone()
        # 잠근 뒤 상태를 다시 읽음(그 사이에 처리되었을 수 있음)
        status = conn.execute(
            "select status, reason from tf_applications where id = %s", (app["id"],)
        ).fetchone()
        if status["status"] != "대기":
            return status["status"], f"{applicant['name']}의 지원은 이미 처리됨({status['reason']})"
        members = _team_members(conn, team["id"])
        if voter["id"] not in {m["id"] for m in members}:
            return "막힘", f"{team['name']}의 팀원만 투표할 수 있음"
        if conn.execute(
            "select 1 from tf_votes where application_id = %s and voter_id = %s",
            (app["id"], voter["id"]),
        ).fetchone():
            return "막힘", "이미 투표함(투표는 바꿀 수 없음)"
        conn.execute(
            "insert into tf_votes (application_id, voter_id, vote) values (%s, %s, %s)",
            (app["id"], voter["id"], "찬성" if yes else "거부"),
        )
        if not yes:
            conn.execute(
                "update tf_applications set status = '거절', reason = %s, decided_at = now() where id = %s",
                (f"{voter['name']}이(가) 거부", app["id"]),
            )
            return "거절", f"{applicant['name']}의 {team['name']} 지원이 거절됨({voter['name']}이(가) 거부)"
        yes_ids = {
            r["voter_id"]
            for r in conn.execute(
                "select voter_id from tf_votes where application_id = %s and vote = '찬성'",
                (app["id"],),
            )
        }
        left = [m for m in members if m["id"] not in yes_ids]
        if left:
            return "대기", f"{applicant['name']}의 지원: 아직 투표하지 않은 팀원 {len(left)}명"
        # 모든 팀원이 찬성 → 승인 직전 재검사
        why = rules.check_join(members, applicant)
        if why:
            conn.execute(
                "update tf_applications set status = '거절', reason = %s, decided_at = now() where id = %s",
                (f"승인 직전 검사: {why}", app["id"]),
            )
            return "거절", f"{applicant['name']}의 지원이 거절됨(승인 직전 검사: {why})"
        try:
            with conn.transaction():
                _add_member(conn, team["id"], applicant["id"])
        except (SeatTaken, psycopg.errors.UniqueViolation):
            conn.execute(
                "update tf_applications set status = '거절', reason = '자리가 없음', decided_at = now() where id = %s",
                (app["id"],),
            )
            return "거절", f"{applicant['name']}의 지원이 거절됨(자리가 없음)"
        conn.execute(
            "update tf_applications set status = '승인', reason = '모든 팀원 찬성', decided_at = now() where id = %s",
            (app["id"],),
        )
        return "승인", f"{applicant['name']}이(가) {team['name']}의 팀원이 됨"


# ---------------------------------------------------------------- 마감과 배정
def close_recruiting(pool):
    with pool.connection() as conn, conn.transaction():
        row = conn.execute("select value from tf_settings where key = 'closed' for update").fetchone()
        if row is None:
            return "막힘", "명단을 먼저 올려야 함"
        if row["value"] == "1":
            return "막힘", "이미 마감됨"
        conn.execute("lock table tf_members in exclusive mode")
        n_rej = conn.execute(
            "update tf_applications set status = '거절', reason = '모집 마감', decided_at = now()"
            " where status = '대기'"
        ).rowcount
        n_out = conn.execute(
            "delete from tf_members where team_id in"
            " (select team_id from tf_members group by team_id having count(*) = 1)"
        ).rowcount
        set_setting(conn, "closed", "1")
    return "승인", f"모집을 마감함(대기 지원 {n_rej}건 거절, 1명뿐인 팀에서 빠진 학생 {n_out}명)"


def assignment_input(state):
    """배정 계산에 쓸 지금 팀 구성과 팀이 없는 학생."""
    teams = {t["team_no"]: list(t["members"]) for t in state["teams"]}
    return teams, list(state["unassigned"])


def save_proposal(pool, proposal, status_text):
    with pool.connection() as conn:
        set_setting(conn, "proposal", json.dumps(proposal, ensure_ascii=False) if proposal else "")
        set_setting(conn, "assign_status", status_text)


def log_ai(pool, attempt, model, outcome, detail, elapsed):
    with pool.connection() as conn:
        conn.execute(
            "insert into tf_ai_logs (attempt, model, outcome, detail, elapsed_sec)"
            " values (%s, %s, %s, %s, %s)",
            (attempt, model, outcome, (detail or "")[:2000], round(elapsed, 1)),
        )


def recent_ai_logs(pool, limit=15):
    with pool.connection() as conn:
        return conn.execute(
            "select created_at, attempt, model, outcome, detail, elapsed_sec"
            " from tf_ai_logs order by id desc limit %s",
            (limit,),
        ).fetchall()


def confirm(pool):
    with pool.connection() as conn, conn.transaction():
        conn.execute("select value from tf_settings where key = 'closed' for update")
        state = load_state(conn)
        if not state["closed"]:
            return "막힘", "모집을 먼저 마감해야 함"
        if state["confirmed"]:
            return "막힘", "이미 확정함"
        prop = state["proposal"]
        if prop is None:
            return "막힘", "확정할 배정안이 없음(AI 배정을 먼저 눌러야 함)"
        teams, unassigned = assignment_input(state)
        pairs = [(p["student_no"], p["team_no"]) for p in prop["assignments"]]
        errs = rules.validate_assignment(teams, unassigned, pairs)
        if errs:
            return "막힘", "배정안이 지금 상태와 맞지 않음: " + "; ".join(errs)
        team_id = {t["team_no"]: t["id"] for t in state["teams"]}
        sid = {s["student_no"]: s["id"] for s in unassigned}
        for sno, tno in pairs:
            _add_member(conn, team_id[tno], sid[sno])
        set_setting(conn, "confirmed", "1")
        set_setting(conn, "proposal", "")
        set_setting(conn, "assign_status", "배정을 확정함")
    return "승인", f"배정안을 확정함(학생 {len(pairs)}명 배정)"
