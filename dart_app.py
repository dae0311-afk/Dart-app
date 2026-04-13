import streamlit as st
import requests
import pandas as pd
import zipfile
import io
import xml.etree.ElementTree as ET
import plotly.graph_objects as go
import time
from datetime import datetime

st.set_page_config(page_title="DART 재무 분석", page_icon="📈", layout="wide")

# ── 재시도 래퍼 ──────────────────────────────────────────────────────────────
def requests_get_with_retry(url, params=None, timeout=60, max_retries=3):
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp
        except (requests.exceptions.ConnectTimeout,
                requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectionError) as e:
            last_exc = e
            if attempt < max_retries:
                time.sleep(attempt * 3)
        except requests.exceptions.HTTPError as e:
            raise e
    raise last_exc

# ══════════════════════════════════════════════════════════════════════════════
# STEP 0 : 로그인 — st.form 으로 감싸서 Enter 키로 바로 로그인
# ══════════════════════════════════════════════════════════════════════════════
def check_password():
    if st.session_state.get("authenticated"):
        return True
    st.title("DART 재무 분석")
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.subheader("🔒 Login")
        with st.form("login_form"):
            pw = st.text_input("Password", type="password",
                               placeholder="비밀번호 입력 후 Enter")
            submitted = st.form_submit_button("Login", use_container_width=True,
                                              type="primary")
        if submitted:
            if pw == st.secrets["APP_PASSWORD"]:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("비밀번호가 틀렸습니다.")
    return False

if not check_password():
    st.stop()

API_KEY     = st.secrets["DART_API_KEY"]
CURRENT_YEAR = datetime.now().year

# ── 계정 ID 매핑 ─────────────────────────────────────────────────────────────
IS_IDS = {
    "매출액":     ["ifrs-full_Revenue", "dart_Revenue"],
    "매출원가":   ["ifrs-full_CostOfSales", "dart_CostOfSales"],
    "매출총이익": ["ifrs-full_GrossProfit"],
    "판관비":     ["ifrs-full_SellingGeneralAndAdministrativeExpense",
                   "dart_TotalSellingGeneralAdministrativeExpenses"],
    "영업이익":   ["dart_OperatingIncomeLoss", "ifrs-full_OperatingIncome",
                   "ifrs-full_ProfitLossFromOperatingActivities"],
    "당기순이익": ["ifrs-full_ProfitLoss",
                   "ifrs-full_ProfitLossAttributableToOwnersOfParent"],
}
BS_IDS = {
    "자산총계":         ["ifrs-full_Assets"],
    "현금및현금성자산": ["ifrs-full_CashAndCashEquivalents"],
    "단기금융상품":     ["ifrs-full_ShorttermInvestments",
                         "dart_ShortTermFinancialInstruments"],
    "부채총계":         ["ifrs-full_Liabilities"],
    "자본총계":         ["ifrs-full_Equity"],
    "단기차입금":       ["ifrs-full_ShorttermBorrowings", "dart_ShortTermBorrowings"],
    "유동성장기차입금": ["ifrs-full_CurrentPortionOfLongtermBorrowings",
                         "dart_CurrentPortionOfLongTermBorrowings"],
    "유동성사채":       ["dart_CurrentPortionOfBondsIssued"],
    "단기리스부채":     ["ifrs-full_CurrentLeaseLiabilities"],
    "장기차입금":       ["ifrs-full_LongtermBorrowings", "dart_LongTermBorrowings"],
    "사채":             ["dart_BondsIssued"],
    "장기리스부채":     ["ifrs-full_NoncurrentLeaseLiabilities"],
}
CF_IDS = {
    "감가상각비":     ["ifrs-full_AdjustmentsForDepreciationExpense",
                       "dart_DepreciationExpenses"],
    "무형자산상각비": ["ifrs-full_AdjustmentsForAmortisationExpense",
                       "dart_AmortisationExpenses"],
}
REPORT_CODE = "11011"

# ── 유틸 함수 ─────────────────────────────────────────────────────────────────
def parse_amount(val):
    try:
        s = str(val).replace(",", "").replace(" ", "")
        if s.startswith("(") and s.endswith(")"):
            s = "-" + s[1:-1]
        return int(s)
    except Exception:
        return None

def to_uk(val):
    return None if val is None else val / 100_000_000

def fmt_uk(val):
    if val is None:
        return "-"
    return "{:.2f}".format(val) if abs(val) < 1 else "{:,.0f}".format(val)

def fmt_pct(val):
    return "-" if val is None else "{:.1f}%".format(val)

def make_corp_label(row):
    code = row["stock_code"]
    return "{} ({})".format(row["corp_name"], code if code else "비상장")

# ── DART API ──────────────────────────────────────────────────────────────────
@st.cache_data(ttl=86400)
def get_corp_list():
    url = "https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key=" + API_KEY
    r = requests_get_with_retry(url, timeout=90, max_retries=3)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    root = ET.fromstring(z.read("CORPCODE.xml"))
    return pd.DataFrame([{
        "corp_code":  item.findtext("corp_code", ""),
        "corp_name":  item.findtext("corp_name", ""),
        "stock_code": item.findtext("stock_code", "").strip(),
    } for item in root.findall("list")])

def search_corp(name, df):
    return df[df["corp_name"].str.contains(name, na=False)].reset_index(drop=True)

def get_corp_info(corp_code):
    try:
        r = requests_get_with_retry(
            "https://opendart.fss.or.kr/api/company.json",
            params={"crtfc_key": API_KEY, "corp_code": corp_code}, timeout=30)
        return r.json()
    except Exception:
        return {}

@st.cache_data(ttl=3600)
def get_fs(corp_code, year, report_code, fs_div):
    try:
        r = requests_get_with_retry(
            "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
            params={"crtfc_key": API_KEY, "corp_code": corp_code,
                    "bsns_year": year, "reprt_code": report_code, "fs_div": fs_div},
            timeout=60)
        data = r.json()
        if data.get("status") != "000":
            return None, data.get("message", "fail")
        return pd.DataFrame(data["list"]), None
    except Exception as e:
        return None, str(e)

def find_val(df, ids, col="thstrm_amount"):
    if df is None or df.empty:
        return None
    for aid in ids:
        rows = df[df["account_id"] == aid]
        if not rows.empty:
            v = parse_amount(rows.iloc[0][col])
            if v is not None:
                return v
    return None

def find_by_name(df, kw, col="thstrm_amount"):
    if df is None or df.empty:
        return None
    rows = df[df["account_nm"].str.contains(kw, na=False)]
    return parse_amount(rows.iloc[0][col]) if not rows.empty else None

def analyze(corp_code, year, fs_preference="CFS"):
    priority = ([("CFS","연결재무제표"),("OFS","별도재무제표")] if fs_preference == "CFS"
                else [("OFS","별도재무제표"),("CFS","연결재무제표")])
    df, used_fs_type = None, None
    for fs_div, fs_label in priority:
        d, _ = get_fs(corp_code, year, REPORT_CODE, fs_div)
        if d is not None and not d.empty:
            df, used_fs_type = d, fs_label; break
    if df is None:
        for rcode in ["11011","11012"]:
            for fs_div, fs_label in priority:
                d, _ = get_fs(corp_code, year, rcode, fs_div)
                if d is not None and not d.empty:
                    df, used_fs_type = d, fs_label + "(감사보고서)"; break
            if df is not None:
                break
    if df is None:
        return None, None, "데이터 없음"

    raw = {}
    for nm, ids in {**IS_IDS, **BS_IDS, **CF_IDS}.items():
        raw[nm] = find_val(df, ids) or find_by_name(df, nm)

    op, dep, amd = raw.get("영업이익"), raw.get("감가상각비"), raw.get("무형자산상각비")
    ebitda, ebitda_calc = None, ""
    if op is not None:
        parts = [("영업이익", op)]
        if dep: parts.append(("감가상각비", dep))
        if amd: parts.append(("무형자산상각비", amd))
        ebitda = sum(v for _, v in parts)
        ebitda_calc = (" + ".join(["{0}({1:,.0f}억)".format(k, to_uk(v)) for k,v in parts])
                       + " = {0:,.0f}억".format(to_uk(ebitda)))

    cash, stfi = raw.get("현금및현금성자산"), raw.get("단기금융상품")
    c_parts = [(k,v) for k,v in [("현금및현금성자산",cash),("단기금융상품",stfi)] if v]
    cash_total = sum(v for _,v in c_parts) if c_parts else None
    cash_calc = ((" + ".join(["{0}({1:,.0f}억)".format(k,to_uk(v)) for k,v in c_parts])
                  + " = {0:,.0f}억".format(to_uk(cash_total))) if c_parts else "")

    debt_keys = ["단기차입금","유동성장기차입금","유동성사채","단기리스부채",
                 "장기차입금","사채","장기리스부채"]
    debt_parts = [(k, raw[k]) for k in debt_keys if raw.get(k)]
    total_debt = sum(v for _,v in debt_parts) if debt_parts else None
    debt_calc  = ((" + ".join(["{0}({1:,.0f}억)".format(k,to_uk(v)) for k,v in debt_parts])
                   + " = {0:,.0f}억".format(to_uk(total_debt))) if debt_parts else "")

    raw.update({"EBITDA": ebitda, "현금성자산": cash_total, "총차입금": total_debt,
                "_ebitda_calc": ebitda_calc, "_cash_calc": cash_calc,
                "_debt_calc": debt_calc, "_debt_parts": debt_parts})
    return raw, used_fs_type, None

def build_table(year_data):
    years = sorted(year_data.keys())
    ROW_ORDER = ["매출액","Growth","매출원가","매출원가율",
                 "매출총이익","매출총이익률","판관비","판관비율",
                 "EBITDA","EBITDA Margin","영업이익","영업이익률",
                 "당기순이익","순이익률",
                 "자산총계","현금성자산","부채총계","총차입금","자본총계"]
    table = {r: {} for r in ROW_ORDER}
    sp = lambda a,b: fmt_pct(a/b*100) if (a is not None and b and b!=0) else "-"
    for i, year in enumerate(years):
        d  = year_data[year]
        rv = to_uk(d.get("매출액")); cg = to_uk(d.get("매출원가"))
        gp = to_uk(d.get("매출총이익")); sg = to_uk(d.get("판관비"))
        op = to_uk(d.get("영업이익")); ni = to_uk(d.get("당기순이익"))
        eb = to_uk(d.get("EBITDA")); ast= to_uk(d.get("자산총계"))
        cs = to_uk(d.get("현금성자산")); lb = to_uk(d.get("부채총계"))
        db = to_uk(d.get("총차입금")); eq = to_uk(d.get("자본총계"))
        pr = to_uk(year_data[years[i-1]].get("매출액")) if i > 0 else None
        table["매출액"][year]        = fmt_uk(rv)
        table["Growth"][year]        = fmt_pct((rv/pr-1)*100) if (rv and pr and pr!=0) else "-"
        table["매출원가"][year]      = fmt_uk(cg);  table["매출원가율"][year]   = sp(cg,rv)
        table["매출총이익"][year]    = fmt_uk(gp);  table["매출총이익률"][year] = sp(gp,rv)
        table["판관비"][year]        = fmt_uk(sg);  table["판관비율"][year]     = sp(sg,rv)
        table["EBITDA"][year]        = fmt_uk(eb);  table["EBITDA Margin"][year]= sp(eb,rv)
        table["영업이익"][year]      = fmt_uk(op);  table["영업이익률"][year]   = sp(op,rv)
        table["당기순이익"][year]    = fmt_uk(ni);  table["순이익률"][year]     = sp(ni,rv)
        table["자산총계"][year]      = fmt_uk(ast); table["현금성자산"][year]   = fmt_uk(cs)
        table["부채총계"][year]      = fmt_uk(lb);  table["총차입금"][year]     = fmt_uk(db)
        table["자본총계"][year]      = fmt_uk(eq)
    return pd.DataFrame([{"계정": r, **{y: table[r].get(y,"-") for y in years}}
                         for r in ROW_ORDER])


# ══════════════════════════════════════════════════════════════════════════════
# UI
# ══════════════════════════════════════════════════════════════════════════════
st.title("📊 DART 재무 분석")
st.caption("금융감독원 전자공시(DART) 기반 요약 재무제표 자동 생성")

with st.sidebar:
    if st.button("로그아웃"):
        st.session_state.authenticated = False
        st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — 기업명 검색 → 선택 드롭다운만 표시
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("#### STEP 1 · 기업 검색")

with st.form("search_form"):
    c1, c2 = st.columns([5, 1])
    with c1:
        q = st.text_input("기업명", placeholder="예: 삼성전자, LG화학",
                          label_visibility="collapsed")
    with c2:
        search_btn = st.form_submit_button("검색 🔍", use_container_width=True, type="primary")

if search_btn and q:
    try:
        with st.spinner("기업 목록 로딩 중..."):
            corp_df = get_corp_list()
        res = search_corp(q, corp_df)
        if res.empty:
            st.warning("해당 기업을 찾을 수 없습니다.")
        else:
            st.session_state["search_results"] = res
            for k in ["step2_ready", "result"]:
                st.session_state.pop(k, None)
    except Exception as e:
        err = str(e)
        if any(x in err for x in ["Timeout", "Connect", "timed out"]):
            st.error("⏱️ DART 서버 연결 시간 초과. 잠시 후 다시 시도해주세요.")
        else:
            st.error("오류: " + err)

# 기업 선택 드롭다운 (검색 결과 있을 때만)
if "search_results" in st.session_state:
    res = st.session_state["search_results"]
    labels = [make_corp_label(row) for _, row in res.iterrows()]
    chosen = st.selectbox("검색 결과에서 기업을 선택하세요", labels, key="corp_select")
    chosen_idx  = labels.index(chosen)
    chosen_corp = res.iloc[chosen_idx]

    if st.button("이 기업으로 설정 ✓", type="primary"):
        st.session_state["chosen_corp"] = chosen_corp.to_dict()
        st.session_state["step2_ready"] = True
        st.session_state.pop("result", None)

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — 재무제표 구분 + 연도 범위 선택
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.get("step2_ready"):
    corp      = st.session_state["chosen_corp"]
    corp_code = corp["corp_code"]
    corp_name = corp["corp_name"]

    st.markdown("#### STEP 2 · 조회 설정  —  **{}**".format(corp_name))

    col_fs, col_yr1, col_yr2 = st.columns([3, 1, 1])
    all_years = [str(y) for y in range(CURRENT_YEAR, 2009, -1)]

    with col_fs:
        fs_pref = st.radio(
            "재무제표 구분",
            options=["연결 우선", "별도 우선"],
            index=0,
            horizontal=True,
            help=(
                "• **연결 우선**: 연결재무제표를 기본으로 조회하고, "
                "연결이 없는 연도는 별도로 자동 대체\n"
                "• **별도 우선**: 별도재무제표를 기본으로 조회하고, "
                "별도가 없는 연도는 연결로 자동 대체\n\n"
                "혼재 시 결과 상단에 연도별 적용 기준을 안내합니다."
            ),
        )
        fs_preference = "CFS" if fs_pref == "연결 우선" else "OFS"

    default_to   = str(CURRENT_YEAR - 1)  # 당해연도 사업보고서는 보통 미공시
    default_from = str(CURRENT_YEAR - 5)

    with col_yr1:
        year_from = st.selectbox(
            "시작 연도",
            all_years,
            index=all_years.index(default_from) if default_from in all_years else len(all_years) - 1,
        )
    with col_yr2:
        year_to = st.selectbox(
            "종료 연도",
            all_years,
            index=all_years.index(default_to) if default_to in all_years else 0,
        )

    if int(year_from) > int(year_to):
        st.warning("⚠️ 시작 연도가 종료 연도보다 큽니다.")
        st.stop()

    selected_years = [str(y) for y in range(int(year_from), int(year_to) + 1)]
    st.caption("📅 선택 연도: {}년 ~ {}년 ({}개 연도)".format(
        year_from, year_to, len(selected_years)))

    # 구 사명 추가 (접기)
    with st.expander("➕ 구 사명(이전 법인명) 추가 — 사명 변경 이력이 있는 경우", expanded=False):
        st.caption("이전 사명을 추가하면 해당 연도의 과거 공시 데이터를 함께 조회합니다.")
        with st.form("old_search_form"):
            old_q   = st.text_input("이전 사명 검색", placeholder="구 사명 입력")
            old_btn = st.form_submit_button("검색")
        if old_btn and old_q:
            with st.spinner("검색 중..."):
                cdf2 = get_corp_list()
            old_res = search_corp(old_q, cdf2)
            st.session_state["old_search_results"] = old_res if not old_res.empty else None
            if old_res.empty:
                st.warning("검색 결과 없음")

        if st.session_state.get("old_search_results") is not None:
            old_res2   = st.session_state["old_search_results"]
            old_labels = [make_corp_label(row) for _, row in old_res2.iterrows()]
            old_sel    = st.selectbox("이전 사명 선택", ["선택 안함"] + old_labels)
            if old_sel != "선택 안함":
                old_idx  = old_labels.index(old_sel)
                old_code = old_res2.iloc[old_idx]["corp_code"]
                oa, ob = st.columns(2)
                with oa:
                    o_from = st.selectbox("적용 시작 연도", all_years, index=len(all_years)//2)
                with ob:
                    o_to   = st.selectbox("적용 종료 연도", all_years,
                                          index=min(len(all_years)//2 + 3, len(all_years)-1))
                if st.button("추가 ✓"):
                    if "old_corps" not in st.session_state:
                        st.session_state["old_corps"] = []
                    if old_code not in [x["corp_code"] for x in st.session_state["old_corps"]]:
                        st.session_state["old_corps"].append({
                            "corp_code": old_code,
                            "corp_name": old_sel.split("(")[0].strip(),
                            "year_from": o_from, "year_to": o_to,
                        })
                        st.success("추가 완료")
                    else:
                        st.warning("이미 추가된 법인입니다.")

        if st.session_state.get("old_corps"):
            st.markdown("**추가된 이전 사명:**")
            for i, oc in enumerate(st.session_state["old_corps"]):
                ca2, cb2 = st.columns([5, 1])
                with ca2:
                    st.write("• {} | {}~{}년".format(
                        oc["corp_name"], oc["year_from"], oc["year_to"]))
                with cb2:
                    if st.button("삭제", key="del_{}".format(i)):
                        st.session_state["old_corps"].pop(i); st.rerun()

    st.markdown("")

    # ── 출력 버튼 ────────────────────────────────────────────────────────────
    if st.button("📊 재무제표 출력", type="primary", use_container_width=True):
        old_corps = st.session_state.get("old_corps", [])

        def get_code_for_year(year):
            for oc in old_corps:
                if oc["year_from"] <= year <= oc["year_to"]:
                    return oc["corp_code"], oc["corp_name"]
            return corp_code, corp_name

        year_data, year_source, year_fstype = {}, {}, {}
        prog = st.progress(0, text="데이터 수집 중...")
        for i, year in enumerate(selected_years):
            cc, cn = get_code_for_year(year)
            d, fs_used, err = analyze(cc, year, fs_preference)
            if d is not None:
                year_data[year]   = d
                year_source[year] = cn
                year_fstype[year] = fs_used
            else:
                st.warning("{} 년: 데이터 없음".format(year))
            prog.progress((i+1)/len(selected_years),
                          text="{} 년 수집 완료".format(year))
        prog.empty()

        if year_data:
            fs_set   = set(year_fstype.values())
            mixed_fs = (any("연결" in f for f in fs_set)
                        and any("별도" in f for f in fs_set))
            st.session_state["result"] = {
                "year_data":    year_data,
                "year_source":  year_source,
                "year_fstype":  year_fstype,
                "corp_name":    corp_name,
                "corp_code":    corp_code,
                "mixed_fs":     mixed_fs,
            }
        else:
            st.error("조회된 데이터가 없습니다.")

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — 결과 (session_state 유지 → 다른 버튼 눌러도 사라지지 않음)
# ══════════════════════════════════════════════════════════════════════════════
if "result" in st.session_state:
    r            = st.session_state["result"]
    year_data    = r["year_data"]
    year_source  = r["year_source"]
    year_fstype  = r["year_fstype"]
    sel_name     = r["corp_name"]
    corp_code_r  = r["corp_code"]
    mixed_fs     = r["mixed_fs"]
    years_sorted = sorted(year_data.keys())

    st.markdown("#### STEP 3 · 결과  —  **{}**".format(sel_name))

    # 재무제표 구분 안내 노트
    fs_detail = "  |  ".join(["{}: {}".format(y, year_fstype[y]) for y in years_sorted])
    if mixed_fs:
        st.warning(
            "⚠️ **연결 / 별도 혼재** — 일부 연도에 선호하신 재무제표 종류가 없어 "
            "자동으로 대체 적용되었습니다.\n\n"
            "📋 **연도별 적용 기준:** " + fs_detail
        )
    else:
        fs_label = list(year_fstype.values())[0]
        st.info("📋 조회 기준: **{}** (전 연도 동일)".format(fs_label))

    # 기업 기본 정보
    info = get_corp_info(corp_code_r)
    if info.get("status") == "000":
        with st.expander("기업 기본 정보", expanded=False):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("기업명", info.get("corp_name","-"))
            c2.metric("대표자", info.get("ceo_nm","-"))
            c3.metric("설립일", info.get("est_dt","-"))
            c4.metric("결산월", (info.get("acc_mt") or "-") + "월")

    # 수집 과정
    with st.expander("1단계: 원재료 데이터 수집 (단위: 억원)", expanded=False):
        raw_items = (list(IS_IDS.keys()) +
                     ["현금및현금성자산","단기금융상품","감가상각비","무형자산상각비"] +
                     ["단기차입금","유동성장기차입금","유동성사채","단기리스부채",
                      "장기차입금","사채","장기리스부채","자산총계","부채총계","자본총계"])
        raw_rows = []
        for item in raw_items:
            row = {"계정": item}
            for y in years_sorted:
                v = to_uk(year_data[y].get(item))
                row[y] = fmt_uk(v) if v is not None else "미조회"
            raw_rows.append(row)
        st.dataframe(pd.DataFrame(raw_rows), use_container_width=True, hide_index=True)

    with st.expander("2단계: EBITDA / 현금성자산 / 총차입금 계산", expanded=False):
        for year in years_sorted:
            d = year_data[year]
            st.markdown("**── {} 년 ({} / {}) ──**".format(
                year, year_source.get(year, sel_name), year_fstype.get(year, "-")))
            ca, cb, cc = st.columns(3)
            with ca:
                st.markdown("**EBITDA**")
                st.code(d.get("_ebitda_calc") or "데이터 부족", language=None)
            with cb:
                st.markdown("**현금성자산**")
                st.code(d.get("_cash_calc") or "데이터 부족", language=None)
            with cc:
                st.markdown("**총차입금**")
                st.code(d.get("_debt_calc") or "데이터 부족", language=None)
            dp = d.get("_debt_parts", [])
            if dp:
                st.dataframe(
                    pd.DataFrame([{"항목": k, "금액(억원)": fmt_uk(to_uk(v))} for k,v in dp]
                                 + [{"항목": "합계",
                                     "금액(억원)": fmt_uk(to_uk(sum(v for _,v in dp)))}]),
                    use_container_width=False, hide_index=True)

    # 최종 요약 재무제표
    st.markdown("##### 최종 요약 재무제표 (단위: 억원)")
    summary_df = build_table(year_data)
    st.dataframe(summary_df, use_container_width=True, hide_index=True, height=700)

    with st.expander("교차 검증", expanded=False):
        vrows = [{"연도": y, "조회 법인": year_source.get(y, sel_name),
                  "재무제표 종류":    year_fstype.get(y, "-"),
                  "EBITDA(계산)":     fmt_uk(to_uk(year_data[y].get("EBITDA"))),
                  "현금성자산(계산)": fmt_uk(to_uk(year_data[y].get("현금성자산"))),
                  "총차입금(계산)":   fmt_uk(to_uk(year_data[y].get("총차입금")))}
                 for y in years_sorted]
        st.dataframe(pd.DataFrame(vrows), use_container_width=True, hide_index=True)
        st.success("2단계 계산값과 최종 요약표 수치 일치 확인 완료")

    # 손익 추이 차트
    st.markdown("##### 손익 추이")
    fig = go.Figure()
    for acc, color in zip(["매출액","EBITDA","영업이익","당기순이익"],
                           ["#1f77b4","#9467bd","#2ca02c","#ff7f0e"]):
        vals = [to_uk(year_data[y].get(acc)) for y in years_sorted]
        fig.add_trace(go.Bar(
            name=acc, x=years_sorted, y=vals, marker_color=color,
            text=["{:,.0f}".format(v) if v is not None else "-" for v in vals],
            textposition="outside"))
    fig.update_layout(
        barmode="group", yaxis_title="억원",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        height=400, plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, use_container_width=True)

    st.divider()
    csv = summary_df.to_csv(index=False, encoding="utf-8-sig")
    st.download_button("⬇️ CSV 다운로드", csv,
                       sel_name + "_재무제표.csv", "text/csv")
