"""팀 프로젝트 팀 편성 앱 (Streamlit + Supabase + OpenRouter)."""
import os
from datetime import timezone, timedelta

import streamlit as st
from dotenv import load_dotenv

import ai
import rules
import service

load_dotenv()
KST = timezone(timedelta(hours=9))
REFRESH_SECONDS = 3  # 다른 사람의 변경을 다시 표시하는 간격(5초 이하)

st.set_page_config(page_title="팀 편성", page_icon="🧩", layout="wide")


# ---------------------------------------------------------------- 설정과 연결
def cfg(name, default=""):
    """Streamlit Secrets → .env(환경 변수) 순으로 읽음. 값은 화면에 출력하지 않음."""
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.environ.get(name, default)


def ai_models():
    return [
        cfg("MODEL_1", "nvidia/nemotron-3-ultra-550b-a55b:free"),
        cfg("MODEL_2", "google/gemma-4-31b-it:free"),
        cfg("MODEL_3", "nvidia/nemotron-3.5-super-vl:free"),
    ]


@st.cache_resource(show_spinner="DB에 연결하는 중…")
def get_pool():
    url, admin_pw = cfg("DATABASE_URL"), cfg("ADMIN_PASSWORD")
    if not url or not admin_pw:
        return None
    pool = service.make_pool(url)
    service.init_db(pool, admin_pw)
    return pool


pool = get_pool()
if pool is None:
    st.error("설정이 빠졌음: DATABASE_URL 있음=%s, ADMIN_PASSWORD 있음=%s"
             % ("예" if cfg("DATABASE_URL") else "아니오", "예" if cfg("ADMIN_PASSWORD") else "아니오"))
    st.stop()


# ---------------------------------------------------------------- 공통 도우미
def set_result(kind, msg):
    st.session_state["result_msg"] = (kind, msg)


def show_result():
    kind, msg = st.session_state.get("result_msg", (None, None))
    if not kind:
        return
    text = f"{kind}: {msg}"
    {"승인": st.success, "대기": st.info, "거절": st.warning, "막힘": st.error}[kind](text)


def role_text(s):
    subs = ", ".join(s["sub_roles"]) if s["sub_roles"] else "없음"
    return f"주 역할 **{s['main_role']}** · 보조 {subs}"


def to_kst(dt):
    return dt.astimezone(KST).strftime("%m/%d %H:%M:%S") if dt else ""


def logout():
    for k in ["user", "result_msg"]:
        st.session_state.pop(k, None)


# ---------------------------------------------------------------- 로그인
def login_view():
    st.title("🧩 팀 프로젝트 팀 편성")
    with st.form("login_form"):
        st.text_input("학번", key="login_student_no")
        st.text_input("비밀번호", type="password", key="login_password")
        ok = st.form_submit_button("로그인", key="login_submit", type="primary")
    with st.container(key="result"):
        if ok:
            sno = service.login(pool, st.session_state["login_student_no"],
                                st.session_state["login_password"])
            if sno:
                st.session_state["user"] = sno
                st.session_state.pop("result_msg", None)
                st.rerun()
            st.error("막힘: 학번이나 비밀번호가 틀림")


def header(me):
    c1, c2 = st.columns([5, 1])
    with c1:
        with st.container(key="whoami"):
            who = "관리자" if me["is_admin"] else f"{me['student_no']}"
            st.markdown(f"#### 👤 {me['name']} ({who})")
    with c2:
        st.button("로그아웃", key="logout", on_click=logout)


# ---------------------------------------------------------------- 학생 화면
def cb_apply(sno, team_no):
    set_result(*service.apply(pool, sno, team_no))


def cb_vote(voter_no, applicant_no, yes):
    set_result(*service.vote(pool, voter_no, applicant_no, yes))


@st.fragment(run_every=REFRESH_SECONDS)
def student_view(sno):
    with pool.connection() as conn:
        state = service.load_state(conn)
    me = next((s for s in state["students"].values() if s["student_no"] == sno), None)
    if me is None:  # 명단이 바뀌어 계정이 사라짐
        logout()
        st.rerun()
    my_team = state["member_team"].get(me["id"])
    my_pending = next((a for a in state["apps"]
                       if a["student_id"] == me["id"] and a["status"] == "대기"), None)

    with st.container(key="result"):
        show_result()

    # 내 상태
    if state["confirmed"]:
        st.success("팀 편성이 확정되었음")
    elif state["closed"]:
        st.warning("모집이 마감되었음. 지원과 투표를 할 수 없음")
    st.write(role_text(me))
    if my_team:
        st.markdown(f"**내 팀: {my_team['name']}**")
    else:
        st.markdown("**아직 팀이 없음**")
    if my_pending:
        n_mem = len(my_pending["team"]["members"])
        n_yes = sum(1 for v in my_pending["votes"].values() if v == "찬성")
        st.info(f"{my_pending['team']['name']}에 지원하고 결과를 기다리는 중 (찬성 {n_yes} / 팀원 {n_mem}명)")
    else:
        mine = [a for a in state["apps"] if a["student_id"] == me["id"]]
        if mine and mine[-1]["status"] == "거절":
            st.caption(f"최근 지원: {mine[-1]['team']['name']} 거절 ({mine[-1]['reason']})")

    # 내 팀에 들어온 지원
    if my_team:
        incoming = [a for a in state["apps"]
                    if a["team_id"] == my_team["id"] and a["status"] == "대기"]
        st.subheader("내 팀에 들어온 지원")
        if not incoming:
            st.caption("기다리는 지원이 없음")
        for a in incoming:
            s = a["student"]
            with st.container(border=True):
                st.markdown(f"**{s['name']}** ({s['student_no']}) · {role_text(s)}")
                voted = [f"{state['students'][v]['name']} {x}" for v, x in a["votes"].items()]
                st.caption("투표: " + (", ".join(voted) if voted else "아직 없음"))
                mine = a["votes"].get(me["id"])
                if mine:
                    st.write(f"내 투표: {mine}")
                else:
                    b1, b2, _ = st.columns([1, 1, 4])
                    b1.button("찬성", key=f"vote_yes_{s['student_no']}", type="primary",
                              disabled=state["closed"],
                              on_click=cb_vote, args=(sno, s["student_no"], True))
                    b2.button("거부", key=f"vote_no_{s['student_no']}",
                              disabled=state["closed"],
                              on_click=cb_vote, args=(sno, s["student_no"], False))

    # 팀 목록
    st.subheader("팀 목록")
    can_apply = not (my_team or my_pending or state["closed"])
    cols = st.columns(min(4, max(1, len(state["teams"]))))
    for i, t in enumerate(state["teams"]):
        with cols[i % len(cols)]:
            with st.container(key=f"team_{t['team_no']}", border=True):
                mark = " · ⭐ 내 팀" if my_team and my_team["id"] == t["id"] else ""
                st.markdown(f"### {t['name']}{mark}")
                st.caption(f"{len(t['members'])} / 4명")
                for m in t["members"]:
                    st.markdown(f"- **{m['name']}**  \n  {role_text(m)}")
                if not t["members"]:
                    st.caption("빈 팀")
                if not my_team:
                    st.button("이 팀에 지원", key=f"apply_{t['team_no']}",
                              disabled=not can_apply,
                              on_click=cb_apply, args=(sno, t["team_no"]))


# ---------------------------------------------------------------- 관리자 화면
def cb_start():
    f = st.session_state.get("admin_roster_file")
    if f is None:
        set_result("막힘", "명단 CSV 파일을 먼저 올려야 함")
        return
    set_result(*service.reset_with_roster(pool, f.getvalue(), st.session_state["admin_team_count"]))


def cb_close():
    set_result(*service.close_recruiting(pool))


def cb_confirm():
    set_result(*service.confirm(pool))


def run_assign(status_box):
    """AI 배정. 몇 차 모델을 기다리는지 status_box에 계속 보여 줌."""
    with pool.connection() as conn:
        state = service.load_state(conn)
    if not state["closed"]:
        return "막힘", "모집을 먼저 마감해야 함"
    if state["confirmed"]:
        return "막힘", "이미 확정함"
    teams, unassigned = service.assignment_input(state)

    # 1) 규칙을 지키는 배정이 있는지 먼저 끝까지 따져 봄(없으면 AI를 부르지 않음)
    solution = rules.solve(teams, unassigned)
    if solution is None:
        reasons = rules.infeasible_reasons(teams, unassigned)
        text = "규칙을 지키는 배정이 없음: " + " / ".join(reasons)
        service.save_proposal(pool, None, text)
        return "막힘", text
    if not unassigned:
        prop = {"source": "팀이 없는 학생이 없음", "assignments": []}
        service.save_proposal(pool, prop, "팀이 없는 학생이 없음. 확정을 누르면 됨")
        return "승인", "팀이 없는 학생이 없음. 지금 팀 구성이 규칙을 지키므로 확정할 수 있음"

    # 2) AI 1차 → 2차 → 3차
    live = status_box.empty()
    models = ai_models()

    def on_status(text):
        live.info("⏳ " + text)

    def on_log(i, model, outcome, detail, el):
        service.log_ai(pool, i, model, outcome, detail, el)

    pairs, which = ai.assign_with_ai(cfg("OPENROUTER_API_KEY"), models, teams, unassigned,
                                     on_status=on_status, on_log=on_log)
    live.empty()
    if pairs:
        source = f"{which}차 모델({models[which - 1]})의 배정안(검사 통과)"
    else:
        # 3) 3차까지 실패 → 앱이 모든 경우를 따져 찾은 배정
        pairs = solution
        source = "1~3차 모델이 모두 실패하여 앱이 직접 찾은 배정(검사 통과)"
    names = {s["student_no"]: s["name"] for s in unassigned}
    prop = {"source": source,
            "assignments": [{"student_no": sno, "name": names[sno], "team_no": t} for sno, t in pairs]}
    service.save_proposal(pool, prop, source)
    return "승인", f"배정안을 만듦: {source}. 확인한 뒤 확정을 누르기 바람"


@st.fragment(run_every=REFRESH_SECONDS)
def admin_live():
    with pool.connection() as conn:
        state = service.load_state(conn)
    flags = []
    flags.append("모집 마감됨" if state["closed"] else "모집 중")
    if state["confirmed"]:
        flags.append("배정 확정됨")
    st.caption(" · ".join(flags) + f" · 학생 {len(state['students']) - 1}명 · 팀 {len(state['teams'])}개")

    st.subheader("팀이 없는 학생")
    with st.container(key="unassigned", border=True):
        if state["unassigned"]:
            for s in state["unassigned"]:
                st.markdown(f"- **{s['name']}** ({s['student_no']}) · {role_text(s)}")
        else:
            st.caption("없음")

    st.subheader("팀 구성")
    cols = st.columns(min(4, max(1, len(state["teams"]))))
    for i, t in enumerate(state["teams"]):
        with cols[i % len(cols)]:
            with st.container(key=f"team_{t['team_no']}", border=True):
                st.markdown(f"### {t['name']}")
                st.caption(f"{len(t['members'])} / 4명")
                for m in t["members"]:
                    st.markdown(f"- **{m['name']}**  \n  {role_text(m)}")
                if not t["members"]:
                    st.caption("빈 팀")
                if state["confirmed"]:
                    errs = rules.team_errors(t["team_no"], t["members"])
                    st.caption("✅ 규칙 지킴" if not errs else "⚠️ " + "; ".join(errs))

    pend = [a for a in state["apps"] if a["status"] == "대기"]
    with st.expander(f"대기 중인 지원 {len(pend)}건 / 전체 지원 기록"):
        for a in reversed(state["apps"]):
            st.write(f"{to_kst(a['created_at'])} · {a['student']['name']} → {a['team']['name']} · "
                     f"**{a['status']}** {('(' + a['reason'] + ')') if a['reason'] else ''}")


def admin_view():
    with st.container(key="result"):
        result_slot = st.empty()

    st.subheader("1. 명단 올리기")
    c1, c2 = st.columns([3, 1])
    c1.file_uploader("명단 CSV (student_no,name,main_role,sub_roles)", type=["csv"],
                     key="admin_roster_file")
    c2.number_input("팀 수", min_value=1, max_value=20, value=3, step=1, key="admin_team_count")
    st.button("명단으로 처음 상태 만들기", key="admin_start", on_click=cb_start,
              help="지원, 투표, 팀원, 마감 표시를 모두 지우고 명단의 학생과 빈 팀을 만듦")

    st.subheader("2. 모집 마감")
    st.button("모집 마감", key="admin_close", on_click=cb_close)

    admin_live()

    st.subheader("3. AI 배정과 확정")
    clicked = st.button("AI 배정", key="admin_assign", type="primary")
    status_box = st.container(key="assign_status", border=True)
    if clicked:
        set_result(*run_assign(status_box))
    with pool.connection() as conn:
        state = service.load_state(conn)
    with status_box:
        if state["assign_status"]:
            if state["assign_status"].startswith("규칙을 지키는 배정이 없음"):
                st.error(state["assign_status"])
            else:
                st.write(state["assign_status"])
        else:
            st.caption("마감한 뒤 AI 배정을 누르면 여기에 진행 상황이 보임")

    prop = state["proposal"]
    if prop and not state["confirmed"]:
        st.markdown(f"**배정안** — {prop['source']}")
        by_no = {s["student_no"]: s for s in state["unassigned"]}
        rows = []
        for p in prop["assignments"]:
            s = by_no.get(p["student_no"])
            if s:
                rows.append({"이름": s["name"], "학번": s["student_no"], "주 역할": s["main_role"],
                             "보조 역할": ", ".join(s["sub_roles"]) or "없음",
                             "배정된 팀": f"팀{p['team_no']}"})
        if rows:
            st.dataframe(rows, hide_index=True, width="stretch")
    st.button("배정안 확정", key="admin_confirm", on_click=cb_confirm,
              disabled=not prop or state["confirmed"])

    with st.expander("AI 호출 기록 (최근 15건, 한국 시각)"):
        logs = service.recent_ai_logs(pool)
        if not logs:
            st.caption("아직 없음")
        for lg in logs:
            st.write(f"{to_kst(lg['created_at'])} · {lg['attempt']}차 `{lg['model']}` · "
                     f"**{lg['outcome']}** · {lg['elapsed_sec']}초 · {lg['detail']}")
        st.caption("모델: " + " → ".join(f"{i}차 {m}" for i, m in enumerate(ai_models(), 1)))

    with result_slot.container():
        show_result()


# ---------------------------------------------------------------- 시작
user = st.session_state.get("user")
if not user:
    login_view()
else:
    with pool.connection() as conn:
        me = service.student_by_no(conn, user)
    if me is None:
        logout()
        st.rerun()
    header(me)
    st.divider()
    if me["is_admin"]:
        admin_view()
    else:
        student_view(user)
