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

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": "https://opendart.fss.or.kr/",
}

def requests_get_with_retry(url, params=None, timeout=60, max_retries=4):
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout, headers=_HEADERS)
            resp.raise_for_status()
            return resp
        except (requests.exceptions.ConnectTimeout,
                requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectionError) as e:
            last_exc = e
            if attempt < max_retries:
                time.sleep(attempt * 5)
        except requests.exceptions.HTTPError as e:
            raise e
    raise last_exc

# ── 업종명 매핑 (KSIC 앞 2자리 기준) ─────────────────────────────────────────
_INDUTY_MAP = {
    "01":"농업","02":"임업","03":"어업",
    "05":"석탄·원유·천연가스 광업","06":"금속 광업","07":"비금속광물 광업",
    "08":"기타 광업","09":"광업 지원 서비스업",
    "10":"식료품 제조업","11":"음료 제조업","12":"담배 제조업",
    "13":"섬유제품 제조업","14":"의복·의복액세서리·모피 제조업",
    "15":"가죽·가방·신발 제조업","16":"목재·나무제품 제조업",
    "17":"펄프·종이·종이제품 제조업","18":"인쇄·기록매체 복제업",
    "19":"코크스·연탄·석유정제품 제조업",
    "20":"화학물질·화학제품 제조업","21":"의약품 제조업",
    "22":"고무·플라스틱제품 제조업","23":"비금속 광물제품 제조업",
    "24":"1차 금속 제조업","25":"금속가공제품 제조업",
    "26":"전자부품·컴퓨터·통신장비 제조업",
    "27":"의료·정밀·광학기기 제조업","28":"전기장비 제조업",
    "29":"기타 기계·장비 제조업","30":"자동차·트레일러 제조업",
    "31":"기타 운송장비 제조업","32":"가구 제조업","33":"기타 제품 제조업",
    "35":"전기·가스·증기·공기조절 공급업","36":"수도업",
    "37":"하수·폐수·분뇨 처리업","38":"폐기물 처리·원료재생업",
    "39":"환경 정화·복원업",
    "41":"종합 건설업","42":"전문직별 공사업",
    "45":"자동차·부품 판매업","46":"도매·상품중개업","47":"소매업",
    "49":"육상 운송업","50":"수상 운송업","51":"항공 운송업",
    "52":"창고·운송관련 서비스업",
    "55":"숙박업","56":"음식점·주점업",
    "58":"출판업","59":"영상·오디오 제작·배급업",
    "60":"방송업","61":"통신업",
    "62":"컴퓨터 프로그래밍·시스템 통합 및 관리업",
    "63":"정보서비스업",
    "64":"금융업","65":"보험·연금업","66":"금융·보험관련 서비스업",
    "68":"부동산업",
    "70":"연구개발업","71":"전문 서비스업",
    "72":"건축기술·엔지니어링 서비스업",
    "73":"기타 과학기술 서비스업",
    "74":"사업시설 관리·조경 서비스업",
    "75":"사업 지원 서비스업",
    "84":"공공행정·국방·사회보장 행정",
    "85":"교육 서비스업","86":"보건업","87":"사회복지 서비스업",
    "90":"창작·예술·여가관련 서비스업",
    "91":"스포츠·오락관련 서비스업",
    "94":"협회 및 단체","95":"개인·가정용품 수리업",
    "96":"기타 개인 서비스업",
}

def get_industry_name(induty_code):
    code = str(induty_code).strip()
    if not code or code == "-":
        return "-"
    prefix = code[:2]
    return _INDUTY_MAP.get(prefix, code)

CORP_CLS_MAP = {"Y":"유가증권","K":"코스닥","N":"코넥스","E":"기타(비상장)"}

# ── 로그인 ────────────────────────────────────────────────────────────────────
def check_password():
    if st.session_state.get("authenticated"):
        return True
    st.title("DART 재무 분석")
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.subheader("🔒 Login")
        with st.form("login_form"):
            pw = st.text_input("Password", type="password", placeholder="비밀번호 입력 후 Enter")
            submitted = st.form_submit_button("Login", use_container_width=True, type="primary")
        if submitted:
            if pw == st.secrets["APP_PASSWORD"]:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("비밀번호가 틀렸습니다.")
    return False

if not check_password():
    st.stop()

API_KEY      = st.secrets["DART_API_KEY"]
CURRENT_YEAR = datetime.now().year

# ── 계정 ID 매핑 ──────────────────────────────────────────────────────────────
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
    "단기금융상품":     ["ifrs-full_ShorttermInvestments", "dart_ShortTermFinancialInstruments"],
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
    "감가상각비":     ["ifrs-full_AdjustmentsForDepreciationExpense", "dart_DepreciationExpenses"],
    "무형자산상각비": ["ifrs-full_AdjustmentsForAmortisationExpense", "dart_AmortisationExpenses"],
}

# ── 유틸 ──────────────────────────────────────────────────────────────────────
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

# ── DART API ──────────────────────────────────────────────────────────────────
@st.cache_data(ttl=86400)
def get_corp_list():
    import os
    csv_path = os.path.join(os.path.dirname(__file__), "data", "corpcode.csv")
    if os.path.exists(csv_path):
        df = pd.read_csv(csv_path, dtype=str).fillna("")
        df["stock_code"] = df["stock_code"].str.strip()
        return df
    url = "https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key=" + API_KEY
    r = requests_get_with_retry(url, timeout=120, max_retries=4)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    root = ET.fromstring(z.read("CORPCODE.xml"))
    return pd.DataFrame([{
        "corp_code":  item.findtext("corp_code", ""),
        "corp_name":  item.findtext("corp_name", ""),
        "stock_code": item.findtext("stock_code", "").strip(),
    } for item in root.findall("list")])

def search_corp(name, df):
    return df[df["corp_name"].str.contains(name, na=False)].reset_index(drop=True)

@st.cache_data(ttl=3600)
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
    """
    [비상장 기업 지원]
    - 비상장 포함 모든 보고서 코드(11011~11014)를 전수 시도
    - CFS/OFS 우선순위는 사용자 선택 그대로 따름
    - find_by_name 폴백으로 K-GAAP 계정명도 커버
    """
    priority = ([("CFS","연결재무제표"),("OFS","별도재무제표")] if fs_preference == "CFS"
                else [("OFS","별도재무제표"),("CFS","연결재무제표")])

    # 사업보고서(11011) → 반기(11012) → 1분기(11013) → 3분기(11014) 순으로 전수 시도
    REPORT_CODES = ["11011", "11012", "11013", "11014"]

    df, used_fs_type = None, None
    for rcode in REPORT_CODES:
        for fs_div, fs_label in priority:
            d, _ = get_fs(corp_code, year, rcode, fs_div)
            if d is not None and not d.empty:
                suffix = {
                    "11011": "",
                    "11012": "(반기)",
                    "11013": "(1분기)",
                    "11014": "(3분기)",
                }.get(rcode, "")
                df, used_fs_type = d, fs_label + suffix
                break
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
# STEP 1 — 기업 검색 → 전체 테이블 표시 (행 클릭으로 즉시 선택)
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("#### STEP 1 · 기업 검색")

with st.form("search_form"):
    c1, c2 = st.columns([5, 1])
    with c1:
        q = st.text_input("기업명", placeholder="예: 삼성전자, 풀무원",
                          label_visibility="collapsed")
    with c2:
        search_btn = st.form_submit_button("검색 🔍", use_container_width=True, type="primary")

if search_btn and q:
    try:
        with st.spinner("기업 목록 로딩 중..."):
            corp_df = get_corp_list()
        res = search_corp(q, corp_df).head(50).reset_index(drop=True)
        if res.empty:
            st.warning("해당 기업을 찾을 수 없습니다.")
            st.session_state.pop("search_display", None)
        else:
            with st.spinner("기업 정보 조회 중... ({0}개)".format(len(res))):
                rows = []
                for _, row in res.iterrows():
                    info = get_corp_info(row["corp_code"])
                    ok   = info.get("status") == "000"
                    rows.append({
                        # 내부 키 (화면 비노출)
                        "_corp_code": row["corp_code"],
                        # 표시 컬럼
                        "기업명":   row["corp_name"],
                        "대표자":   info.get("ceo_nm", "-") if ok else "-",
                        "업종":     get_industry_name(info.get("induty_code","")) if ok else "-",
                        "상장구분": CORP_CLS_MAP.get(info.get("corp_cls",""), "비상장"),
                    })
            st.session_state["search_rows"] = rows
            # 기업 바뀌면 하위 단계 초기화
            st.session_state.pop("chosen_corp", None)
            st.session_state.pop("step2_ready", None)
            st.session_state.pop("result", None)
    except Exception as e:
        err = str(e)
        if any(x in err for x in ["Timeout", "Connect", "timed out"]):
            st.error("⏱️ DART 서버 연결 초과. `update_corpcode.py` 실행 후 "
                     "`data/corpcode.csv`를 GitHub에 커밋하세요.")
        else:
            st.error("오류: " + err)

# 검색 결과 테이블 — 행 클릭으로 즉시 선택
if "search_rows" in st.session_state:
    rows = st.session_state["search_rows"]
    display_df = pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("_")}
                                for r in rows])

    st.caption("🔍 {0}개 기업 — 행을 클릭하면 바로 선택됩니다".format(len(rows)))

    event = st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        selection_mode="single-row",
        on_select="rerun",
        key="corp_table",
    )

    sel = event.selection.rows
    if sel:
        idx    = sel[0]
        chosen = rows[idx]
        # 행 클릭 즉시 → STEP 2 활성화 (별도 버튼 불필요)
        prev = st.session_state.get("chosen_corp", {})
        if prev.get("_corp_code") != chosen["_corp_code"]:
            st.session_state["chosen_corp"] = chosen
            st.session_state["step2_ready"] = True
            st.session_state.pop("result", None)
        st.success("✅ 선택: **{0}** | {1} | {2}".format(
            chosen["기업명"], chosen["대표자"], chosen["상장구분"]))

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — 재무제표 구분 + 연도 범위 (비상장도 동일하게 선택 가능)
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.get("step2_ready"):
    corp      = st.session_state["chosen_corp"]
    corp_code = corp["_corp_code"]
    corp_name = corp["기업명"]

    st.markdown("#### STEP 2 · 조회 설정  —  **{0}**".format(corp_name))

    all_years    = [str(y) for y in range(CURRENT_YEAR, 2009, -1)]
    default_to   = str(CURRENT_YEAR - 1)
    default_from = str(CURRENT_YEAR - 5)

    col_fs, col_yr1, col_yr2 = st.columns([3, 1, 1])

    with col_fs:
        fs_pref = st.radio(
            "재무제표 구분", options=["연결 우선", "별도 우선"], index=0, horizontal=True,
            help="연결 없는 연도는 별도로, 별도 없는 연도는 연결로 자동 대체됩니다.")
        fs_preference = "CFS" if fs_pref == "연결 우선" else "OFS"

    with col_yr1:
        year_from = st.selectbox("시작 연도", all_years,
            index=all_years.index(default_from) if default_from in all_years else len(all_years)-1)
    with col_yr2:
        year_to = st.selectbox("종료 연도", all_years,
            index=all_years.index(default_to) if default_to in all_years else 0)

    if int(year_from) > int(year_to):
        st.warning("⚠️ 시작 연도가 종료 연도보다 큽니다.")
        st.stop()

    selected_years = [str(y) for y in range(int(year_from), int(year_to) + 1)]
    st.caption("📅 {0}년 ~ {1}년 ({2}개 연도)".format(year_from, year_to, len(selected_years)))

    # 비상장 안내 메시지
    if corp.get("상장구분") in ("비상장", "기타(비상장)"):
        st.info("ℹ️ 비상장 기업: DART에 XBRL 재무제표를 제출한 외감법인만 조회됩니다. "
                "PDF 감사보고서만 제출한 기업은 데이터가 없을 수 있습니다.")

    st.markdown("")

    if st.button("📊 재무제표 출력", type="primary", use_container_width=True):
        year_data, year_fstype = {}, {}
        prog = st.progress(0, text="데이터 수집 중...")
        for i, year in enumerate(selected_years):
            d, fs_used, err = analyze(corp_code, year, fs_preference)
            if d is not None:
                year_data[year]   = d
                year_fstype[year] = fs_used
            else:
                st.warning("{0}년: 데이터 없음 — DART XBRL 미제출 가능성".format(year))
            prog.progress((i+1)/len(selected_years), text="{0}년 수집 완료".format(year))
        prog.empty()
        if year_data:
            fs_set = set(year_fstype.values())
            st.session_state["result"] = {
                "year_data":   year_data,
                "year_fstype": year_fstype,
                "corp_name":   corp_name,
                "corp_code":   corp_code,
                "mixed_fs":    any("연결" in f for f in fs_set) and any("별도" in f for f in fs_set),
            }
        else:
            st.error("조회된 데이터가 없습니다. 해당 기업이 DART에 XBRL 재무제표를 제출했는지 확인해주세요.")

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — 결과
# ══════════════════════════════════════════════════════════════════════════════
if "result" in st.session_state:
    r = st.session_state["result"]
    year_data    = r["year_data"]
    year_fstype  = r["year_fstype"]
    sel_name     = r["corp_name"]
    corp_code_r  = r["corp_code"]
    mixed_fs     = r["mixed_fs"]
    years_sorted = sorted(year_data.keys())

    st.markdown("#### STEP 3 · 결과  —  **{0}**".format(sel_name))

    fs_detail = "  |  ".join(["{0}: {1}".format(y, year_fstype[y]) for y in years_sorted])
    if mixed_fs:
        st.warning("⚠️ **연결/별도 혼재** — 일부 연도 자동 대체\n\n📋 **연도별 기준:** " + fs_detail)
    else:
        st.info("📋 조회 기준: **{0}** (전 연도 동일)".format(list(year_fstype.values())[0]))

    info = get_corp_info(corp_code_r)
    if info.get("status") == "000":
        with st.expander("기업 기본 정보", expanded=False):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("기업명",  info.get("corp_name", "-"))
            c2.metric("대표자",  info.get("ceo_nm", "-"))
            c3.metric("설립일",  info.get("est_dt", "-"))
            c4.metric("결산월", (info.get("acc_mt") or "-") + "월")

    with st.expander("1단계: 원재료 데이터 수집 (단위: 억원)", expanded=False):
        raw_items = (list(IS_IDS.keys()) +
                     ["현금및현금성자산","단기금융상품","감가상각비","무형자산상각비"] +
                     ["단기차입금","유동성장기차입금","유동성사채","단기리스부채",
                      "장기차입금","사채","장기리스부채","자산총계","부채총계","자본총계"])
        raw_rows = []
        for item in raw_items:
            row_d = {"계정": item}
            for y in years_sorted:
                v = to_uk(year_data[y].get(item))
                row_d[y] = fmt_uk(v) if v is not None else "미조회"
            raw_rows.append(row_d)
        st.dataframe(pd.DataFrame(raw_rows), use_container_width=True, hide_index=True)

    with st.expander("2단계: EBITDA / 현금성자산 / 총차입금 계산", expanded=False):
        for year in years_sorted:
            d = year_data[year]
            st.markdown("**── {0}년 ({1}) ──**".format(year, year_fstype.get(year, "-")))
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

    st.markdown("##### 최종 요약 재무제표 (단위: 억원)")
    summary_df = build_table(year_data)
    st.dataframe(summary_df, use_container_width=True, hide_index=True, height=700)

    with st.expander("교차 검증", expanded=False):
        vrows = [{"연도": y,
                  "재무제표 종류":    year_fstype.get(y,"-"),
                  "EBITDA(계산)":     fmt_uk(to_uk(year_data[y].get("EBITDA"))),
                  "현금성자산(계산)": fmt_uk(to_uk(year_data[y].get("현금성자산"))),
                  "총차입금(계산)":   fmt_uk(to_uk(year_data[y].get("총차입금")))}
                 for y in years_sorted]
        st.dataframe(pd.DataFrame(vrows), use_container_width=True, hide_index=True)
        st.success("2단계 계산값과 최종 요약표 수치 일치 확인 완료")

    st.markdown("##### 손익 추이")
    fig = go.Figure()
    for acc, color in zip(["매출액","EBITDA","영업이익","당기순이익"],
                           ["#1f77b4","#9467bd","#2ca02c","#ff7f0e"]):
        vals = [to_uk(year_data[y].get(acc)) for y in years_sorted]
        fig.add_trace(go.Bar(
            name=acc, x=years_sorted, y=vals, marker_color=color,
            text=["{:,.0f}".format(v) if v is not None else "-" for v in vals],
            textposition="outside"))
    fig.update_layout(barmode="group", yaxis_title="억원",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        height=400, plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, use_container_width=True)

    st.divider()
    csv = summary_df.to_csv(index=False, encoding="utf-8-sig")
    st.download_button("⬇️ CSV 다운로드", csv, sel_name + "_재무제표.csv", "text/csv")
