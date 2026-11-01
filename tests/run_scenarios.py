"""연습용 시나리오 3개를 Streamlit AppTest로 화면을 조작해 돌려 봄.

실행: DATABASE_URL=... ADMIN_PASSWORD=... python tests/run_scenarios.py
(명단 올리기만 AppTest가 파일 올리기를 지원하지 않아 service 함수를 직접 부름)
"""
import os
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import service  # noqa: E402
import rules  # noqa: E402

APP = str(ROOT / "streamlit_app.py")
pool = service.make_pool(os.environ["DATABASE_URL"])
service.init_db(pool, os.environ["ADMIN_PASSWORD"])
sessions = {}
fails = []


def sess(sno):
    if sno not in sessions:
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        at.text_input(key="login_student_no").input(sno)
        at.text_input(key="login_password").input(
            os.environ["ADMIN_PASSWORD"] if sno == "admin" else "1234")
        at.button(key="login_submit").click().run()
        assert at.session_state["user"] == sno, f"login failed {sno}"
        sessions[sno] = at
    return sessions[sno]


def click(sno, key):
    at = sess(sno)
    at.run()  # 새 상태 반영(자동 갱신 대신)
    btns = [b for b in at.button if b.key == key]
    if not btns or btns[0].disabled:
        return "막힘", "(단추 없음/비활성)"
    btns[0].click().run()
    if at.exception:
        raise RuntimeError(at.exception)
    return at.session_state["result_msg"]


def expect(step, got, want):
    ok = got[0] == want
    print(f"  {'OK ' if ok else 'FAIL'} 단계{step}: 기대 {want}, 결과 {got[0]}: {got[1]}")
    if not ok:
        fails.append((step, want, got))


def reset(csv, teams):
    sessions.clear()
    print(service.reset_with_roster(pool, (ROOT / "data" / csv).read_bytes(), teams))


def no(base, i):
    return str(base + i - 1)


def finish(step):
    r = click("admin", "admin_close")
    print("  마감:", r)
    at = sess("admin")
    at.run()
    at.button(key="admin_assign").click().run()
    if at.exception:
        raise RuntimeError(at.exception)
    print("  배정:", at.session_state["result_msg"])
    r = click("admin", "admin_confirm")
    expect(step, r, "승인")
    with pool.connection() as conn:
        st = service.load_state(conn)
    bad = [e for t in st["teams"] for e in rules.team_errors(t["team_no"], t["members"])]
    print("  확정 뒤 팀:", {t["name"]: [m["name"] for m in t["members"]] for t in st["teams"]})
    print("  팀이 없는 학생:", [s["name"] for s in st["unassigned"]], "규칙 위반:", bad or "없음")
    if bad or st["unassigned"]:
        fails.append((step, "규칙", bad))


def easy():
    print("== 쉬움")
    reset("쉬움.csv", 3)
    B = 2026101
    s = lambda i: no(B, i)
    expect(1, click(s(1), "apply_1"), "승인")
    expect(1, click(s(2), "apply_2"), "승인")
    expect(1, click(s(3), "apply_3"), "승인")
    expect(2, click(s(4), "apply_1"), "대기")
    expect(2, click(s(1), f"vote_yes_{s(4)}"), "승인")
    expect(3, click(s(5), "apply_2"), "대기")
    expect(3, click(s(2), f"vote_yes_{s(5)}"), "승인")
    expect(4, click(s(7), "apply_1"), "대기")
    expect(4, click(s(1), f"vote_yes_{s(7)}"), "대기")
    expect(4, click(s(4), f"vote_no_{s(7)}"), "거절")
    expect(5, click(s(6), "apply_3"), "대기")
    expect(5, click(s(3), f"vote_yes_{s(6)}"), "승인")
    finish(6)


def medium():
    print("== 중간")
    reset("중간.csv", 4)
    s = lambda i: no(2026201, i)
    expect(1, click(s(1), "apply_1"), "승인")
    expect(2, click(s(5), "apply_1"), "대기")
    expect(2, click(s(1), f"vote_yes_{s(5)}"), "승인")
    expect(3, click(s(2), "apply_1"), "막힘")
    expect(4, click(s(6), "apply_2"), "승인")
    expect(5, click(s(9), "apply_2"), "대기")
    expect(5, click(s(6), f"vote_yes_{s(9)}"), "승인")
    expect(6, click(s(10), "apply_2"), "대기")
    expect(6, click(s(6), f"vote_yes_{s(10)}"), "대기")
    expect(6, click(s(9), f"vote_no_{s(10)}"), "거절")
    expect(7, click(s(3), "apply_3"), "승인")
    expect(7, click(s(11), "apply_4"), "승인")
    expect(8, click(s(12), "apply_4"), "대기")
    expect(8, click(s(12), "apply_3"), "막힘")
    r = click("admin", "admin_close")
    expect(9, r, "승인")
    with pool.connection() as conn:
        st = service.load_state(conn)
    print("  마감 뒤 팀이 없는 학생:", [x["name"] for x in st["unassigned"]])
    print("  학생12 지원:", [a["status"] for a in st["apps"] if a["student"]["student_no"] == s(12)])
    expect(10, click(s(2), "apply_3"), "막힘")
    # 마감은 이미 했으므로 배정과 확정만
    at = sess("admin")
    at.run()
    at.button(key="admin_assign").click().run()
    print("  배정:", at.session_state["result_msg"])
    expect(11, click("admin", "admin_confirm"), "승인")
    with pool.connection() as conn:
        st = service.load_state(conn)
    bad = [e for t in st["teams"] for e in rules.team_errors(t["team_no"], t["members"])]
    print("  확정 뒤 팀:", {t["name"]: [m["name"] for m in t["members"]] for t in st["teams"]})
    print("  규칙 위반:", bad or "없음", "남은 학생:", [x["name"] for x in st["unassigned"]])
    if bad or st["unassigned"]:
        fails.append((11, "규칙", bad))


def hard():
    print("== 어려움")
    reset("어려움.csv", 4)
    s = lambda i: no(2026301, i)
    expect(1, click(s(1), "apply_1"), "승인")
    expect(2, click(s(4), "apply_1"), "대기")
    expect(2, click(s(1), f"vote_yes_{s(4)}"), "승인")
    expect(3, click(s(7), "apply_1"), "대기")
    expect(3, click(s(1), f"vote_yes_{s(7)}"), "대기")
    expect(3, click(s(4), f"vote_yes_{s(7)}"), "승인")
    expect(4, click(s(2), "apply_2"), "승인")
    expect(5, click(s(8), "apply_2"), "대기")
    expect(5, click(s(2), f"vote_yes_{s(8)}"), "승인")
    expect(6, click(s(5), "apply_2"), "대기")
    expect(6, click(s(6), "apply_2"), "대기")
    expect(7, click(s(2), f"vote_yes_{s(5)}"), "대기")
    expect(7, click(s(8), f"vote_yes_{s(5)}"), "승인")
    expect(8, click(s(2), f"vote_yes_{s(6)}"), "대기")
    expect(8, click(s(8), f"vote_yes_{s(6)}"), "대기")
    expect(8, click(s(5), f"vote_yes_{s(6)}"), "거절")
    expect(9, click(s(11), "apply_3"), "승인")
    expect(10, click(s(15), "apply_3"), "대기")
    expect(10, click(s(11), f"vote_yes_{s(15)}"), "승인")
    expect(11, click(s(14), "apply_3"), "막힘")
    expect(12, click(s(10), "apply_4"), "승인")
    expect(13, click(s(13), "apply_1"), "대기")
    expect(13, click(s(1), f"vote_yes_{s(13)}"), "대기")
    finish(15)


easy()
medium()
hard()
print("\n실패:", fails or "없음")
