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

# ── 요청 헤더 / 재시도 래퍼 ──────────────────────────────────────────────────
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
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

# ── 업종명 매핑 (KSIC 앞 2자리) ──────────────────────────────────────────────
_INDUTY_MAP = {
    "01":"농업","02":"임업","03":"어업",
    "05":"석탄·원유·천연가스 광업","06":"금속 광업","07":"비금속광물 광업",
    "10":"식료품 제조업","11":"음료 제조업","12":"담배 제조업",
    "13":"섬유제품 제조업","14":"의복·모피 제조업",
    "15":"가죽·가방·신발 제조업","16":"목재·나무제품 제조업",
    "17":"펄프·종이제품 제조업","18":"인쇄·기록매체 복제업",
    "19":"코크스·석유정제품 제조업",
    "20":"화학물질·화학제품 제조업","21":"의약품 제조업",
    "22":"고무·플라스틱제품 제조업","23":"비금속 광물제품 제조업",
    "24":"1차 금속 제조업","25":"금속가공제품 제조업",
    "26":"전자부품·컴퓨터·통신장비 제조업",
    "27":"의료·정밀·광학기기 제조업","28":"전기장비 제조업",
    "29":"기타 기계·장비 제조업","30":"자동차·트레일러 제조업",
    "31":"기타 운송장비 제조업","32":"가구 제조업","33":"기타 제품 제조업",
    "35":"전기·가스·증기 공급업","36":"수도업",
    "37":"하수·폐수·분뇨 처리업","38":"폐기물 처리·원료재생업",
    "41":"종합 건설업","42":"전문직별 공사업",
    "45":"자동차·부품 판매업","46":"도매·상품중개업","47":"소매업",
    "49":"육상 운송업","50":"수상 운송업","51":"항공 운송업",
    "52":"창고·운송관련 서비스업",
    "55":"숙박업","56":"음식점·주점업",
    "58":"출판업","59":"영상·오디오 제작·배급업",
    "60":"방송업","61":"통신업",
    "62":"컴퓨터 프로그래밍·시스템 통합업","63":"정보서비스업",
    "64":"금융업","65":"보험·연금업","66":"금융·보험관련 서비스업",
    "68":"부동산업",
    "70":"연구개발업","71":"전문 서비스업",
    "72":"건축기술·엔지니어링 서비스업",
    "74":"사업시설 관리·조경 서비스업","75":"사업 지원 서비스업",
    "84":"공공행정·국방","85":"교육 서비스업",
    "86":"보건업","87":"사회복지 서비스업",
    "90":"창작·예술·여가관련 서비스업","91":"스포츠·오락관련 서비스업",
    "94":"협회 및 단체","96":"기타 개인 서비스업",
}

def get_industry_name(induty_code):
    code = str(induty_code).strip()
    return _INDUTY_MAP.get(code[:2], code) if code and code != "-" else "-"

CORP_CLS_MAP = {"Y":"유가증권","K":"코스닥","N":"코넥스","E":"기타(비상장)"}

# ── 로그인 ────────────────────────────────────────────────────────────────────
def check_password():
    if st.session_state.get("authenticated"):
        return True
    st.title("DART 재무 분석")
    _, col, _ = st.columns([1, 2, 1])
    with col:
        st.subheader("🔒 Login")
        with st.form("login_form"):
            pw = st.text_input("Password", type="password", placeholder="비밀번호 입력 후 Enter")
            if st.form_submit_button("Login", use_container_width=True, type="primary"):
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
    "매출액":     ["ifrs-full_Revenue","dart_Revenue"],
    "매출원가":   ["ifrs-full_CostOfSales","dart_CostOfSales"],
    "매출총이익": ["ifrs-full_GrossProfit"],
    "판관비":     ["ifrs-full_SellingGeneralAndAdministrativeExpense",
                   "dart_TotalSellingGeneralAdministrativeExpenses"],
    "영업이익":   ["dart_OperatingIncomeLoss","ifrs-full_OperatingIncome",
                   "ifrs-full_ProfitLossFromOperatingActivities"],
    "당기순이익": ["ifrs-full_ProfitLoss",
                   "ifrs-full_ProfitLossAttributableToOwnersOfParent"],
}
BS_IDS = {
    "자산총계":         ["ifrs-full_Assets"],
    "현금및현금성자산": ["ifrs-full_CashAndCashEquivalents"],
    "단기금융상품":     ["ifrs-full_ShorttermInvestments","dart_ShortTermFinancialInstruments"],
    "부채총계":         ["ifrs-full_Liabilities"],
    "자본총계":         ["ifrs-full_Equity"],
    "단기차입금":       ["ifrs-full_ShorttermBorrowings","dart_ShortTermBorrowings"],
    "유동성장기차입금": ["ifrs-full_CurrentPortionOfLongtermBorrowings",
                         "dart_CurrentPortionOfLongTermBorrowings"],
    "유동성사채":       ["dart_CurrentPortionOfBondsIssued"],
    "단기리스부채":     ["ifrs-full_CurrentLeaseLiabilities"],
    "장기차입금":       ["ifrs-full_LongtermBorrowings","dart_LongTermBorrowings"],
    "사채":             ["dart_BondsIssued"],
    "장기리스부채":     ["ifrs-full_NoncurrentLeaseLiabilities"],
}
CF_IDS = {
    "감가상각비":     ["ifrs-full_AdjustmentsForDepreciationExpense","dart_DepreciationExpenses"],
    "무형자산상각비": ["ifrs-full_AdjustmentsForAmortisationExpense","dart_AmortisationExpenses"],
}

# ── 유틸 ──────────────────────────────────────────────────────────────────────
def parse_amount(val):
    try:
        s = str(val).replace(",","").replace(" ","")
        if s.startswith("(") and s.endswith(")"):
            s = "-" + s[1:-1]
        return int(s)
    except:
        return None

def to_uk(val):
    return None if val is None else val / 100_000_000

def fmt_uk(val):
    if val is None: return "-"
    return "{:.2f}".format(val) if abs(val) < 1 else "{:,.0f}".format(val)

def fmt_pct(val):
    return "-" if val is None else "{:.1f}%".format(val)

# ── 파생 지표 계산 (XBRL·문서 공통 사용) ─────────────────────────────────────
def compute_derived(raw):
    op, dep, amd = raw.get("영업이익"), raw.get("감가상각비"), raw.get("무형자산상각비")
    ebitda, ebitda_calc = None, ""
    if op is not None:
        parts = [("영업이익", op)]
        if dep: parts.append(("감가상각비", dep))
        if amd: parts.append(("무형자산상각비", amd))
        ebitda = sum(v for _, v in parts)
        ebitda_calc = (" + ".join([f"{k}({to_uk(v):,.0f}억)" for k,v in parts])
                       + f" = {to_uk(ebitda):,.0f}억")

    cash, stfi = raw.get("현금및현금성자산"), raw.get("단기금융상품")
    c_parts = [(k,v) for k,v in [("현금및현금성자산",cash),("단기금융상품",stfi)] if v]
    cash_total = sum(v for _,v in c_parts) if c_parts else None
    cash_calc  = ((" + ".join([f"{k}({to_uk(v):,.0f}억)" for k,v in c_parts])
                   + f" = {to_uk(cash_total):,.0f}억") if c_parts else "")

    debt_keys = ["단기차입금","유동성장기차입금","유동성사채","단기리스부채",
                 "장기차입금","사채","장기리스부채"]
    debt_parts = [(k,raw[k]) for k in debt_keys if raw.get(k)]
    total_debt = sum(v for _,v in debt_parts) if debt_parts else None
    debt_calc  = ((" + ".join([f"{k}({to_uk(v):,.0f}억)" for k,v in debt_parts])
                   + f" = {to_uk(total_debt):,.0f}억") if debt_parts else "")

    raw.update({"EBITDA": ebitda, "현금성자산": cash_total, "총차입금": total_debt,
                "_ebitda_calc": ebitda_calc, "_cash_calc": cash_calc,
                "_debt_calc": debt_calc, "_debt_parts": debt_parts})
    return raw

# ── DART XBRL API ─────────────────────────────────────────────────────────────
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
    return pd.DataFrame([{"corp_code": item.findtext("corp_code",""),
                           "corp_name": item.findtext("corp_name",""),
                           "stock_code": item.findtext("stock_code","").strip()}
                          for item in root.findall("list")])

def search_corp(name, df):
    return df[df["corp_name"].str.contains(name, na=False)].reset_index(drop=True)

@st.cache_data(ttl=3600)
def get_corp_info(corp_code):
    try:
        r = requests_get_with_retry("https://opendart.fss.or.kr/api/company.json",
                                    params={"crtfc_key": API_KEY, "corp_code": corp_code},
                                    timeout=30)
        return r.json()
    except:
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
            return None, data.get("message","fail")
        return pd.DataFrame(data["list"]), None
    except Exception as e:
        return None, str(e)

def find_val(df, ids, col="thstrm_amount"):
    if df is None or df.empty: return None
    for aid in ids:
        rows = df[df["account_id"] == aid]
        if not rows.empty:
            v = parse_amount(rows.iloc[0][col])
            if v is not None: return v
    return None

def find_by_name(df, kw, col="thstrm_amount"):
    if df is None or df.empty: return None
    rows = df[df["account_nm"].str.contains(kw, na=False)]
    return parse_amount(rows.iloc[0][col]) if not rows.empty else None

# ── DART 공시 문서 파싱 (비상장 기업 XBRL 없을 때 사용) ───────────────────────
@st.cache_data(ttl=1800)
def find_filing_rcept_no(corp_code, year):
    """
    해당 연도 감사/사업보고서 접수번호 조회.
    - 비상장 기업 감사보고서는 pblntf_ty="F"(외부감사관련)에 등록됨
    - 사업보고서는 pblntf_ty="A"(정기공시)에 등록됨
    - 날짜 범위: 당해 1월 ~ 익년 12월 (늦은 제출 완전 대응)
    """
    bgn = f"{year}0101"
    end = f"{int(year)+1}1231"   # ← 다음해 말까지 (기존: +6개월 → 수정: +18개월)

    # F(외부감사관련) 먼저 → A(정기공시) → 전체 순으로 시도
    # 비상장 감사보고서는 F에만 있으므로 F를 1순위로
    for pblntf_ty in ["F", "A", ""]:
        try:
            r = requests_get_with_retry(
                "https://opendart.fss.or.kr/api/list.json",
                params={"crtfc_key": API_KEY, "corp_code": corp_code,
                        "bgn_de": bgn, "end_de": end,
                        "pblntf_ty": pblntf_ty, "page_count": 40},  # ← 40건으로 확대
                timeout=30)
            data = r.json()
            if data.get("status") != "000" or not data.get("list"):
                continue
            items = data["list"]
            # 감사보고서 > 사업보고서 > 연결감사 순 우선 매칭
            for kw in ["감사보고서", "사업보고서", "연결감사"]:
                for item in items:
                    if kw in item.get("report_nm", ""):
                        return item["rcept_no"], item.get("report_nm", "")
            # 키워드 매칭 없으면 첫 번째 항목 사용
            return items[0]["rcept_no"], items[0].get("report_nm", "")
        except:
            continue
    return None, None


@st.cache_data(ttl=3600)
def get_document_html(rcept_no):
    """DART 공시 문서 ZIP 다운로드 → HTML 텍스트 반환"""
    try:
        r = requests_get_with_retry(
            "https://opendart.fss.or.kr/api/document.xml",
            params={"crtfc_key": API_KEY, "rcept_no": rcept_no},
            timeout=90)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        html_files = sorted([f for f in z.namelist()
                              if f.lower().endswith(('.html','.htm'))])
        combined = ""
        for fname in html_files[:10]:
            for enc in ['utf-8','cp949','euc-kr']:
                try:
                    combined += z.read(fname).decode(enc, errors='ignore') + "\n"
                    break
                except:
                    continue
        return combined or None
    except:
        return None


def _detect_unit(text):
    """재무제표 숫자 단위 감지 → 원 단위 환산 승수 반환"""
    t = text.replace(" ","")
    if "단위:억원" in t:   return 100_000_000
    if "단위:백만원" in t: return 1_000_000
    if "단위:천원" in t:   return 1_000
    return 1


def _extract_num(s):
    """셀 텍스트 → 정수 변환 (괄호 음수 처리)"""
    s = str(s).strip().replace(',','').replace(' ','').replace('\xa0','')
    if not s or s in ('-','–','—'): return None
    if s.startswith('(') and s.endswith(')'): s = '-' + s[1:-1]
    try:    return int(float(s))
    except: return None


def _clean_label(text):
    """셀 라벨 정규화 — 공백·특수문자·주석번호 제거"""
    import re
    t = text.replace("\xa0","").replace("\u3000","").replace("\n","")
    t = re.sub(r"[\s　]","", t)           # 모든 종류 공백 제거
    t = re.sub(r"[①②③④⑤⑥⑦⑧⑨⑩]","", t)  # 주석 번호
    t = re.sub(r"\([^)]*\)","", t)       # (주석) 괄호 제거 — 단, 음수는 별도처리
    return t


def parse_html_financials(html_content, _debug_log=None):
    """
    DART HTML 문서에서 재무 수치 추출.
    _debug_log: list → 파싱 과정 기록 (None이면 기록 안 함)
    반환값: {계정명: 원 단위 금액}
    """
    def log(msg):
        if _debug_log is not None:
            _debug_log.append(msg)

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        log("❌ beautifulsoup4 미설치")
        return {}

    # ── 인코딩별 파싱 시도 ─────────────────────────────────────────────────────
    soup = None
    for parser in ["lxml", "html.parser"]:
        try:
            soup = BeautifulSoup(html_content, parser)
            log(f"✅ HTML 파서: {parser}")
            break
        except Exception as e:
            log(f"⚠️ {parser} 실패: {e}")
    if soup is None:
        return {}

    full_text = soup.get_text()
    unit = _detect_unit(full_text)
    log(f"📐 단위 감지: {unit:,}원 단위")

    # ── 계정명 키워드 매핑 ─────────────────────────────────────────────────────
    KWDS = {
        "매출액":           ["매출액","수익(매출액)","영업수익","총수익","매출"],
        "매출원가":         ["매출원가","제품매출원가","상품매출원가"],
        "매출총이익":       ["매출총이익","매출총손익"],
        "판관비":           ["판매비와관리비","판매비및관리비","판관비","영업비용"],
        "영업이익":         ["영업이익","영업손익","영업이익(손실)"],
        "당기순이익":       ["당기순이익","당기순손익","당기순이익(손실)","분기순이익"],
        "자산총계":         ["자산총계","자산합계"],
        "현금및현금성자산": ["현금및현금성자산","현금및현금성자산(단기금융상품포함)",
                             "현금과예금","현금및예금"],
        "단기금융상품":     ["단기금융상품","단기투자자산","단기금융자산"],
        "부채총계":         ["부채총계","부채합계"],
        "자본총계":         ["자본총계","자본합계"],
        "단기차입금":       ["단기차입금","단기차입"],
        "유동성장기차입금": ["유동성장기차입금","유동성장기부채","유동성장기차입"],
        "장기차입금":       ["장기차입금","장기차입"],
        "사채":             ["사채"],
        "감가상각비":       ["감가상각비","유형자산감가상각비"],
        "무형자산상각비":   ["무형자산상각비","무형자산의상각"],
    }

    results = {}
    matched_tables = 0

    for tbl_idx, table in enumerate(soup.find_all("table")):
        tbl_text = table.get_text()
        # 재무제표 테이블인지 간단 필터 (최소 2개 키워드 포함)
        kw_hits = sum(1 for kw in ["매출","자산","부채","자본","이익"] if kw in tbl_text)
        if kw_hits < 2:
            continue
        matched_tables += 1

        for row in table.find_all("tr"):
            cells = row.find_all(["td","th"])
            if len(cells) < 2:
                continue

            raw_label = cells[0].get_text(strip=True)
            label     = _clean_label(raw_label)

            for key, kwds in KWDS.items():
                if key in results:
                    continue
                # 완전 일치 우선, 그 다음 시작 일치
                if any(label == kw.replace(" ","") for kw in kwds) or \
                   any(label.startswith(kw.replace(" ","")) for kw in kwds):
                    # 숫자 셀 탐색: 2번째~6번째 중 첫 번째 유효값
                    for cell in cells[1:6]:
                        val = _extract_num(cell.get_text())
                        if val is not None and val != 0:
                            results[key] = val * unit
                            log(f"  ✅ {key}: {val:,} × {unit:,} = {val*unit:,}")
                            break

    log(f"📊 재무 테이블 {matched_tables}개 처리 → {len(results)}개 계정 추출")
    if "매출액" not in results:
        log("⚠️ '매출액' 미추출 — 테이블 구조가 예상과 다를 수 있음")
    return results


def analyze_from_document(corp_code, year):
    """XBRL 없는 경우: 공시 HTML 문서 파싱으로 재무 수치 추출 (디버그 로그 포함)"""
    debug_log = [f"=== {year}년 문서파싱 시작 ==="]

    rcept_no, report_nm = find_filing_rcept_no(corp_code, year)
    if not rcept_no:
        debug_log.append("❌ 공시 목록에서 감사/사업보고서를 찾지 못함")
        return None, None, "DART 공시 없음", debug_log

    debug_log.append(f"✅ 공시 발견: {report_nm} (rcept_no={rcept_no})")

    html = get_document_html(rcept_no)
    if not html:
        debug_log.append("❌ 문서 ZIP 다운로드 실패 또는 HTML 파일 없음")
        return None, None, f"문서 다운로드 실패 (rcept_no={rcept_no})", debug_log

    debug_log.append(f"✅ HTML 문서 수신: {len(html):,} bytes")

    raw = parse_html_financials(html, _debug_log=debug_log)
    if not raw or "매출액" not in raw:
        debug_log.append(f"❌ 매출액 추출 실패 — 파싱된 항목: {list(raw.keys())}")
        return None, None, f"재무데이터 파싱 실패", debug_log

    raw = compute_derived(raw)
    label = f"별도재무제표(문서파싱·{report_nm})" if report_nm else "별도재무제표(문서파싱)"
    debug_log.append(f"✅ 파싱 완료: {len(raw)}개 항목")
    return raw, label, None, debug_log


# ── 메인 분석 함수 (XBRL → 문서파싱 순서로 시도) ─────────────────────────────
def analyze(corp_code, year, fs_preference="CFS"):
    priority = ([("CFS","연결재무제표"),("OFS","별도재무제표")] if fs_preference == "CFS"
                else [("OFS","별도재무제표"),("CFS","연결재무제표")])

    # ── 1순위: XBRL 구조화 데이터 ─────────────────────────────────────────────
    df, used_fs_type = None, None
    for rcode in ["11011","11012","11013","11014"]:
        for fs_div, fs_label in priority:
            d, _ = get_fs(corp_code, year, rcode, fs_div)
            if d is not None and not d.empty:
                suffix = {"11011":"","11012":"(반기)","11013":"(1분기)","11014":"(3분기)"}.get(rcode,"")
                df, used_fs_type = d, fs_label + suffix
                break
        if df is not None:
            break

    if df is not None:
        raw = {}
        for nm, ids in {**IS_IDS, **BS_IDS, **CF_IDS}.items():
            raw[nm] = find_val(df, ids) or find_by_name(df, nm)
        raw = compute_derived(raw)
        return raw, used_fs_type, None, []

    # ── 2순위: 공시 HTML 문서 파싱 (비상장 기업 대응) ─────────────────────────
    raw, label, err, dbg = analyze_from_document(corp_code, year)
    return raw, label, err, dbg


# ── 요약 테이블 생성 ──────────────────────────────────────────────────────────
def build_table(year_data):
    years = sorted(year_data.keys())
    ROW_ORDER = ["매출액","Growth","매출원가","매출원가율",
                 "매출총이익","매출총이익률","판관비","판관비율",
                 "EBITDA","EBITDA Margin","영업이익","영업이익률",
                 "당기순이익","순이익률",
                 "자산총계","현금성자산","부채총계","총차입금","자본총계"]
    table = {r:{} for r in ROW_ORDER}
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

# ── STEP 1: 기업 검색 ─────────────────────────────────────────────────────────
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
            st.session_state.pop("search_rows", None)
        else:
            with st.spinner(f"기업 정보 조회 중... ({len(res)}개)"):
                rows = []
                for _, row in res.iterrows():
                    info = get_corp_info(row["corp_code"])
                    ok   = info.get("status") == "000"
                    rows.append({
                        "_corp_code": row["corp_code"],
                        "기업명":   row["corp_name"],
                        "대표자":   info.get("ceo_nm","-") if ok else "-",
                        "업종":     get_industry_name(info.get("induty_code","")) if ok else "-",
                        "상장구분": CORP_CLS_MAP.get(info.get("corp_cls",""),"비상장"),
                    })
            st.session_state["search_rows"] = rows
            for k in ("chosen_corp","step2_ready","result"):
                st.session_state.pop(k, None)
    except Exception as e:
        err = str(e)
        if any(x in err for x in ["Timeout","Connect","timed out"]):
            st.error("⏱️ DART 서버 연결 초과. `update_corpcode.py` 실행 후 "
                     "`data/corpcode.csv`를 GitHub에 커밋하세요.")
        else:
            st.error("오류: " + err)

# 결과 테이블 + 라디오 선택
if "search_rows" in st.session_state:
    rows = st.session_state["search_rows"]

    # 헤더 + 구분선으로 테이블처럼 표시
    st.caption(f"🔍 {len(rows)}개 기업 — 행을 클릭해서 선택하세요")

    # 컬럼 헤더
    hc = st.columns([0.3, 2.2, 1.5, 2.5, 1.2])
    for col, header in zip(hc, ["", "기업명", "대표자", "업종", "상장구분"]):
        col.markdown(f"**{header}**")
    st.divider()

    # 각 행을 radio 버튼 + 정보 컬럼으로 표시
    options = [r["_corp_code"] for r in rows]
    labels  = [r["기업명"] for r in rows]

    # 현재 선택된 corp_code 찾기
    prev_code = st.session_state.get("chosen_corp", {}).get("_corp_code")
    default_idx = options.index(prev_code) if prev_code in options else 0

    selected_code = None
    for i, row in enumerate(rows):
        rc = st.columns([0.3, 2.2, 1.5, 2.5, 1.2])
        clicked = rc[0].button("●", key=f"sel_{i}",
                               help="클릭해서 선택",
                               use_container_width=True)
        rc[1].write(row["기업명"])
        rc[2].write(row["대표자"])
        rc[3].write(row["업종"])
        rc[4].write(row["상장구분"])
        if clicked:
            selected_code = row["_corp_code"]

    if selected_code:
        chosen = next(r for r in rows if r["_corp_code"] == selected_code)
        st.session_state["chosen_corp"] = chosen
        st.session_state["step2_ready"] = True
        st.session_state.pop("result", None)
        st.rerun()

    # 현재 선택된 기업 표시
    if st.session_state.get("chosen_corp"):
        ch = st.session_state["chosen_corp"]
        if ch["_corp_code"] in options:
            st.success(f"✅ 선택됨: **{ch['기업명']}** | {ch['대표자']} | {ch['상장구분']}")

st.divider()

# ── STEP 2: 조회 설정 ─────────────────────────────────────────────────────────
if st.session_state.get("step2_ready"):
    corp      = st.session_state["chosen_corp"]
    corp_code = corp["_corp_code"]
    corp_name = corp["기업명"]

    st.markdown(f"#### STEP 2 · 조회 설정  —  **{corp_name}**")

    all_years    = [str(y) for y in range(CURRENT_YEAR, 2009, -1)]
    default_to   = str(CURRENT_YEAR - 1)
    default_from = str(CURRENT_YEAR - 5)

    col_fs, col_yr1, col_yr2 = st.columns([3, 1, 1])
    with col_fs:
        fs_pref = st.radio("재무제표 구분", ["연결 우선","별도 우선"],
                           index=0, horizontal=True,
                           help="연결 없는 연도는 별도로 자동 대체됩니다.")
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

    selected_years = [str(y) for y in range(int(year_from), int(year_to)+1)]
    st.caption(f"📅 {year_from}년 ~ {year_to}년 ({len(selected_years)}개 연도)")

    if st.button("📊 재무제표 출력", type="primary", use_container_width=True):
        year_data, year_fstype, all_debug = {}, {}, {}
        prog = st.progress(0, text="데이터 수집 중...")
        for i, year in enumerate(selected_years):
            result = analyze(corp_code, year, fs_preference)
            d, fs_used, err, dbg = result if len(result) == 4 else (*result, [])
            all_debug[year] = dbg
            if d is not None:
                year_data[year]   = d
                year_fstype[year] = fs_used
            else:
                st.warning(f"{year}년: {err}")
            prog.progress((i+1)/len(selected_years), text=f"{year}년 수집 완료")
        prog.empty()
        if year_data:
            fs_set = set(year_fstype.values())
            st.session_state["result"] = {
                "year_data":   year_data, "year_fstype": year_fstype,
                "corp_name":   corp_name, "corp_code":   corp_code,
                "mixed_fs":    any("연결" in f for f in fs_set) and any("별도" in f for f in fs_set),
                "debug_log":   all_debug,
            }
        else:
            # 데이터 없음 + 디버그 정보 표시
            st.error("조회된 데이터가 없습니다.")
            for yr, log in all_debug.items():
                if log:
                    with st.expander(f"🔍 {yr}년 디버그 로그"):
                        st.code("\n".join(log))

st.divider()

# ── STEP 3: 결과 ──────────────────────────────────────────────────────────────
if "result" in st.session_state:
    r = st.session_state["result"]
    year_data    = r["year_data"];    year_fstype = r["year_fstype"]
    sel_name     = r["corp_name"];    corp_code_r = r["corp_code"]
    mixed_fs     = r["mixed_fs"];     years_sorted = sorted(year_data.keys())

    st.markdown(f"#### STEP 3 · 결과  —  **{sel_name}**")

    fs_detail = "  |  ".join([f"{y}: {year_fstype[y]}" for y in years_sorted])
    if mixed_fs:
        st.warning(f"⚠️ **연결/별도 혼재** — 일부 연도 자동 대체\n\n📋 **연도별 기준:** {fs_detail}")
    else:
        st.info(f"📋 조회 기준: **{list(year_fstype.values())[0]}** (전 연도 동일)")

    # 문서파싱 연도 표시
    doc_years = [y for y,f in year_fstype.items() if "문서파싱" in f]
    if doc_years:
        st.warning(f"📄 {', '.join(doc_years)}년은 XBRL 데이터가 없어 공시 HTML 문서를 파싱했습니다. "
                   "수치 정확성을 반드시 원문 감사보고서와 대조 확인하세요.")
        debug_log = r.get("debug_log", {})
        if debug_log:
            with st.expander("🔍 문서 파싱 디버그 로그"):
                for yr in doc_years:
                    if yr in debug_log and debug_log[yr]:
                        st.markdown(f"**{yr}년**")
                        st.code("\n".join(debug_log[yr]))

    info = get_corp_info(corp_code_r)
    if info.get("status") == "000":
        with st.expander("기업 기본 정보", expanded=False):
            c1,c2,c3,c4 = st.columns(4)
            c1.metric("기업명",  info.get("corp_name","-"))
            c2.metric("대표자",  info.get("ceo_nm","-"))
            c3.metric("설립일",  info.get("est_dt","-"))
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
            st.markdown(f"**── {year}년 ({year_fstype.get(year,'-')}) ──**")
            ca,cb,cc = st.columns(3)
            with ca:
                st.markdown("**EBITDA**")
                st.code(d.get("_ebitda_calc") or "데이터 부족", language=None)
            with cb:
                st.markdown("**현금성자산**")
                st.code(d.get("_cash_calc") or "데이터 부족", language=None)
            with cc:
                st.markdown("**총차입금**")
                st.code(d.get("_debt_calc") or "데이터 부족", language=None)
            dp = d.get("_debt_parts",[])
            if dp:
                st.dataframe(
                    pd.DataFrame([{"항목":k,"금액(억원)":fmt_uk(to_uk(v))} for k,v in dp]
                                 + [{"항목":"합계","금액(억원)":fmt_uk(to_uk(sum(v for _,v in dp)))}]),
                    use_container_width=False, hide_index=True)

    st.markdown("##### 최종 요약 재무제표 (단위: 억원)")
    summary_df = build_table(year_data)
    st.dataframe(summary_df, use_container_width=True, hide_index=True, height=700)

    with st.expander("교차 검증", expanded=False):
        vrows = [{"연도":y, "재무제표 종류":year_fstype.get(y,"-"),
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
        fig.add_trace(go.Bar(name=acc, x=years_sorted, y=vals, marker_color=color,
            text=[f"{v:,.0f}" if v is not None else "-" for v in vals],
            textposition="outside"))
    fig.update_layout(barmode="group", yaxis_title="억원",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        height=400, plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, use_container_width=True)

    st.divider()
    csv = summary_df.to_csv(index=False, encoding="utf-8-sig")
    st.download_button("⬇️ CSV 다운로드", csv, sel_name+"_재무제표.csv", "text/csv")
