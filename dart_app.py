import re
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

# ── 전역 CSS ──────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* 활성 버튼 (기간/단위) */
.btn-active > button, .btn-active > button:hover {
    background:#e74c3c !important; color:#fff !important; font-weight:700 !important;
    border:1px solid #c0392b !important;
}
/* 기업선택 행 버튼 */
.row-btn > button {
    background:#f5f6fa !important; color:#212529 !important;
    border:none !important; border-bottom:1px solid #dfe6e9 !important;
    border-radius:0 !important; text-align:left !important;
    padding:5px 10px !important; font-size:0.83rem !important;
    height:32px !important; width:100% !important;
}
.row-btn > button:hover { background:#e74c3c !important; color:#fff !important; }
.row-sel > button, .row-sel > button:hover {
    background:#e74c3c !important; color:#fff !important; font-weight:700 !important;
    border:none !important; border-bottom:1px solid #c0392b !important;
    border-radius:0 !important; text-align:left !important;
    padding:5px 10px !important; font-size:0.83rem !important;
    height:32px !important; width:100% !important;
}
/* 재무표 우측정렬 */
table.fin-table { width:100%; border-collapse:collapse; font-size:0.82rem; }
table.fin-table th { background:#2c3e50; color:#fff; padding:6px 10px;
    text-align:center; white-space:nowrap; }
table.fin-table th:first-child { text-align:left; min-width:160px; }
table.fin-table td { padding:5px 10px; border-bottom:1px solid #eee;
    text-align:right; white-space:nowrap; }
table.fin-table td:first-child { text-align:left; color:#333; }
table.fin-table tr.hl td { background:#f8f9fa; font-weight:700; }
table.fin-table tr.sub td { color:#888; font-style:italic; }
table.fin-table tr:hover td { background:#fff3cd44; }
</style>
""", unsafe_allow_html=True)

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
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
            if attempt < max_retries: time.sleep(attempt * 5)
        except requests.exceptions.HTTPError as e: raise e
    raise last_exc

_INDUTY_MAP = {
    "10":"식료품 제조업","20":"화학물질·화학제품 제조업","21":"의약품 제조업",
    "22":"고무·플라스틱 제조업","24":"1차 금속 제조업","25":"금속가공제품 제조업",
    "26":"전자부품·컴퓨터·통신장비 제조업","27":"의료·정밀·광학기기 제조업",
    "28":"전기장비 제조업","29":"기타 기계·장비 제조업",
    "30":"자동차·트레일러 제조업","35":"전기·가스·증기 공급업",
    "41":"종합 건설업","42":"전문직별 공사업",
    "46":"도매·상품중개업","47":"소매업","49":"육상 운송업",
    "55":"숙박업","56":"음식점·주점업","60":"방송업","61":"통신업",
    "62":"컴퓨터 프로그래밍·시스템 통합업","63":"정보서비스업",
    "64":"금융업","65":"보험·연금업","68":"부동산업",
    "70":"연구개발업","71":"전문 서비스업","85":"교육 서비스업",
    "86":"보건업","87":"사회복지 서비스업",
}
def get_industry_name(code):
    c = str(code).strip()
    return _INDUTY_MAP.get(c[:2], c) if c and c != "-" else "-"

CORP_CLS_MAP = {"Y":"유가증권","K":"코스닥","N":"코넥스","E":"기타(비상장)"}

# ── 로그인 ────────────────────────────────────────────────────────────────────
def check_password():
    if st.session_state.get("authenticated"): return True
    st.title("DART 재무 분석")
    _, col, _ = st.columns([1, 2, 1])
    with col:
        st.subheader("🔒 Login")
        with st.form("login_form"):
            pw = st.text_input("Password", type="password", placeholder="비밀번호 입력 후 Enter")
            if st.form_submit_button("Login", use_container_width=True, type="primary"):
                if pw == st.secrets["APP_PASSWORD"]:
                    st.session_state.authenticated = True; st.rerun()
                else: st.error("비밀번호가 틀렸습니다.")
    return False

if not check_password(): st.stop()

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
        if s.startswith("(") and s.endswith(")"): s = "-" + s[1:-1]
        return int(s)
    except: return None

def _clean_num(s):
    s = str(s or "").strip().replace(",","").replace(" ","").replace("\xa0","")
    if not s or s in ("-","–","—"): return None
    if s.startswith("(") and s.endswith(")"): s = "-" + s[1:-1]
    try: return int(float(s))
    except: return None

def _detect_unit(text):
    """단위 감지 → 원(KRW) 기준 승수 반환"""
    t = text.replace(" ","").replace("\xa0","").replace("　","")
    if any(p in t for p in ("단위:억원","(억원)","[억원]")): return 100_000_000
    if any(p in t for p in ("단위:백만원","(백만원)","[백만원]","단위:1,000,000원")): return 1_000_000
    if any(p in t for p in ("단위:천원","(천원)","[천원]","단위:1,000원","단위:1000원")): return 1_000
    if any(p in t for p in ("단위:원","(단위:원)")): return 1
    return 1

# ── 단위 변환 ── (원 기준 저장값 → 표시 단위)
_UNIT_MAP = {"천원":1_000, "백만원":1_000_000, "억원":100_000_000, "십억원":1_000_000_000}

def _fmt_val(val_won, disp_unit):
    """원(KRW) 단위 값 → 표시 단위로 변환 후 포맷"""
    if val_won is None: return "-"
    v = val_won / disp_unit
    if abs(v) < 0.005: return "0"
    if abs(v) < 1:     return f"{v:.2f}"
    return f"{v:,.0f}"

def _fmt_pct(val): return "-" if val is None else f"{val:.1f}%"

def _sp(a, b):
    if a is not None and b and b != 0: return _fmt_pct(a / b * 100)
    return "-"

# ── 파생 지표 계산 (값은 항상 원(KRW) 단위로 저장) ───────────────────────────
def compute_derived(raw):
    """모든 값이 원(KRW) 단위라고 가정"""
    op, dep, amd = raw.get("영업이익"), raw.get("감가상각비"), raw.get("무형자산상각비")
    ebitda, ebitda_calc = None, ""
    if op is not None:
        parts = [("영업이익", op)]
        if dep: parts.append(("감가상각비", dep))
        if amd: parts.append(("무형자산상각비", amd))
        ebitda = sum(v for _,v in parts)
        ebitda_calc = " + ".join([f"{k}({v/1e8:,.1f}억)" for k,v in parts]) + f" = {ebitda/1e8:,.1f}억"

    cash, stfi = raw.get("현금및현금성자산"), raw.get("단기금융상품")
    c_parts = [(k,v) for k,v in [("현금및현금성자산",cash),("단기금융상품",stfi)] if v]
    cash_total = sum(v for _,v in c_parts) if c_parts else None
    cash_calc  = (" + ".join([f"{k}({v/1e8:,.1f}억)" for k,v in c_parts]) + f" = {cash_total/1e8:,.1f}억") if c_parts else ""

    debt_keys = ["단기차입금","유동성장기차입금","유동성사채","단기리스부채","장기차입금","사채","장기리스부채"]
    debt_parts = [(k,raw[k]) for k in debt_keys if raw.get(k)]
    total_debt = sum(v for _,v in debt_parts) if debt_parts else None
    debt_calc  = (" + ".join([f"{k}({v/1e8:,.1f}억)" for k,v in debt_parts]) + f" = {total_debt/1e8:,.1f}억") if debt_parts else ""

    raw.update({"EBITDA":ebitda,"현금성자산":cash_total,"총차입금":total_debt,
                "_ebitda_calc":ebitda_calc,"_cash_calc":cash_calc,
                "_debt_calc":debt_calc,"_debt_parts":debt_parts})
    return raw

# ── DART XBRL API ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=86400)
def get_corp_list():
    import os
    csv_path = os.path.join(os.path.dirname(__file__), "data", "corpcode.csv")
    if os.path.exists(csv_path):
        df = pd.read_csv(csv_path, dtype=str).fillna("")
        df["stock_code"] = df["stock_code"].str.strip(); return df
    url = "https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key=" + API_KEY
    r = requests_get_with_retry(url, timeout=120, max_retries=4)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    root = ET.fromstring(z.read("CORPCODE.xml"))
    return pd.DataFrame([{"corp_code":item.findtext("corp_code",""),
                           "corp_name":item.findtext("corp_name",""),
                           "stock_code":item.findtext("stock_code","").strip()}
                          for item in root.findall("list")])

def search_corp(name, df):
    return df[df["corp_name"].str.contains(name, na=False)].reset_index(drop=True)

@st.cache_data(ttl=3600)
def get_corp_info(corp_code):
    try:
        r = requests_get_with_retry("https://opendart.fss.or.kr/api/company.json",
                                    params={"crtfc_key":API_KEY,"corp_code":corp_code},timeout=30)
        return r.json()
    except: return {}

@st.cache_data(ttl=3600)
def get_fs(corp_code, year, report_code, fs_div):
    try:
        r = requests_get_with_retry(
            "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
            params={"crtfc_key":API_KEY,"corp_code":corp_code,
                    "bsns_year":year,"reprt_code":report_code,"fs_div":fs_div},
            timeout=60)
        data = r.json()
        if data.get("status") != "000": return None, data.get("message","fail")
        return pd.DataFrame(data["list"]), None
    except Exception as e: return None, str(e)

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

# ── 비상장 기업 문서 파싱 ─────────────────────────────────────────────────────
@st.cache_data(ttl=1800)
def find_filing(corp_code, year):
    bgn, end = f"{year}0101", f"{int(year)+2}0630"
    for pblntf_ty in ["F","A",""]:
        try:
            r = requests_get_with_retry(
                "https://opendart.fss.or.kr/api/list.json",
                params={"crtfc_key":API_KEY,"corp_code":corp_code,
                        "bgn_de":bgn,"end_de":end,
                        "pblntf_ty":pblntf_ty,"page_count":100},
                timeout=30)
            data = r.json()
            if data.get("status") != "000" or not data.get("list"): continue
            items = data["list"]
            for kw in ["감사보고서","사업보고서","연결감사보고서","재무제표"]:
                for item in items:
                    if kw in item.get("report_nm",""):
                        return item["rcept_no"], item.get("report_nm","")
            return items[0]["rcept_no"], items[0].get("report_nm","")
        except: continue
    return None, None

@st.cache_data(ttl=3600)
def download_zip(rcept_no):
    meta = {"status":"not_tried","files":[],"html":0,"xml":0,"pdf":0,"error":None}
    try:
        r = requests_get_with_retry(
            "https://opendart.fss.or.kr/api/document.xml",
            params={"crtfc_key":API_KEY,"rcept_no":rcept_no}, timeout=120)
        if r.content[:4] == b"<?xm" and b"<r>" in r.content[:400]:
            meta["status"]="api_error"; meta["error"]=r.content[:400].decode("utf-8","ignore")
            return None,[],[],meta
        z = zipfile.ZipFile(io.BytesIO(r.content))
        files = z.namelist(); meta["files"]=files; meta["status"]="ok"
        html_files=[f for f in files if f.lower().endswith((".html",".htm"))]
        xml_files =[f for f in files if f.lower().endswith(".xml")
                    and not any(f.lower().endswith(x) for x in (".xsd",))]
        pdf_files =[f for f in files if f.lower().endswith(".pdf")]
        meta["html"]=len(html_files); meta["xml"]=len(xml_files); meta["pdf"]=len(pdf_files)
        html_combined=""
        for fn in html_files[:15]:
            for enc in ["utf-8","cp949","euc-kr"]:
                try: html_combined+=z.read(fn).decode(enc,errors="ignore")+"\n"; break
                except: continue
        xml_list=[]
        for fn in xml_files[:5]:
            try: xml_list.append((fn,z.read(fn)))
            except: continue
        pdf_list=[]
        for fn in sorted(pdf_files)[:5]:
            try: pdf_list.append(z.read(fn))
            except: continue
        return html_combined or None, xml_list, pdf_list, meta
    except zipfile.BadZipFile as e:
        meta["status"]="bad_zip"; meta["error"]=str(e); return None,[],[],meta
    except Exception as e:
        meta["status"]="exception"; meta["error"]=str(e); return None,[],[],meta

_FIN_KWDS = {
    "매출액":["매출액","수익(매출액)","영업수익","총매출액"],
    "매출원가":["매출원가","제품매출원가"],
    "매출총이익":["매출총이익"],
    "판관비":["판매비와관리비","판매비및관리비","판관비"],
    "영업이익":["영업이익","영업손익","영업이익(손실)"],
    "당기순이익":["당기순이익","당기순손익"],
    "자산총계":["자산총계","자산합계"],
    "현금및현금성자산":["현금및현금성자산","현금과예금"],
    "단기금융상품":["단기금융상품"],
    "부채총계":["부채총계","부채합계"],
    "자본총계":["자본총계","자본합계"],
    "단기차입금":["단기차입금"],
    "유동성장기차입금":["유동성장기차입금","유동성장기부채"],
    "장기차입금":["장기차입금"],
    "사채":["사채"],
    "감가상각비":["감가상각비"],
    "무형자산상각비":["무형자산상각비"],
}

def _extract_from_html(text, log=None):
    def L(m):
        if log: log.append(m)
    try:
        from bs4 import BeautifulSoup
    except: L("beautifulsoup4 미설치"); return {}
    unit = _detect_unit(text)
    L(f"    단위감지: {unit:,}원")
    results = {}
    for parser in ["lxml","html.parser"]:
        try:
            soup = BeautifulSoup(text, parser)
            for table in soup.find_all("table"):
                tbl_txt = table.get_text()
                if sum(1 for kw in ["매출","자산","부채","자본","이익"] if kw in tbl_txt) < 2: continue
                for row in table.find_all("tr"):
                    cells = row.find_all(["td","th"])
                    if len(cells) < 2: continue
                    label = re.sub(r"[\s\xa0\u3000①②③④⑤]","",cells[0].get_text(strip=True))
                    for key,kwds in _FIN_KWDS.items():
                        if key in results: continue
                        if any(label==kw.replace(" ","") or label.startswith(kw.replace(" ","")) for kw in kwds):
                            for cell in cells[1:6]:
                                v = _clean_num(cell.get_text())
                                if v is not None and v != 0:
                                    results[key] = v * unit   # 원 단위로 저장
                                    L(f"    ✅ {key}: {v:,} × {unit:,} = {v*unit:,}원")
                                    break
            if results: L(f"    테이블파싱: {len(results)}개"); break
        except: continue
    return results

def _extract_from_text(text, log=None):
    def L(m):
        if log: log.append(m)
    # XML/HTML 태그 제거
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"[ \t]+", " ", clean)
    unit  = _detect_unit(text)
    L(f"    단위감지(텍스트): {unit:,}원")
    preview = [ln.strip() for ln in clean.splitlines() if ln.strip()][:12]
    L("    미리보기:")
    for p in preview: L(f"      {p[:80]}")
    NUM = re.compile(r"\(?\d[\d,]{2,}\)?")
    results = {}
    for key, kwds in _FIN_KWDS.items():
        if key in results: continue
        for kw in kwds:
            kw_plain = re.escape(kw.replace(" ",""))
            m = re.search(kw_plain, clean.replace(" ",""))
            if not m: continue
            window = clean.replace(" ","")[m.end():m.end()+300]
            for raw_n in NUM.findall(window):
                v = _clean_num(raw_n)
                if v is not None and abs(v) >= 100:
                    results[key] = v * unit   # 원 단위로 저장
                    L(f"    ✅ {key}: {raw_n} × {unit:,} = {v*unit:,}원")
                    break
            if key in results: break
    L(f"    → {len(results)}개: {list(results.keys())}")
    return results

def parse_document(html_str, xml_list, pdf_list, log=None):
    def L(m):
        if log: log.append(m)
    results = {}
    if html_str:
        L("  [1] HTML 테이블")
        results = _extract_from_html(html_str, log)
        if "매출액" in results: return results
    for fname, xbytes in xml_list:
        L(f"  [2] XML: {fname} ({len(xbytes):,}bytes)")
        for enc in ["utf-8","cp949","euc-kr"]:
            try:
                xtext = xbytes.decode(enc, errors="ignore")
                if len(xtext) < 100: continue
                r = _extract_from_html(xtext, log)
                if "매출액" in r: L("  ✅ XML HTML파싱 성공"); return r
                r = _extract_from_text(xtext, log)
                if len(r) > len(results): results = r
                if "매출액" in results: L("  ✅ XML 텍스트파싱 성공"); return results
                break
            except: continue
    if pdf_list:
        L(f"  [3] PDF ({len(pdf_list)}개)")
        try:
            import pdfplumber
            for i, pb in enumerate(pdf_list):
                L(f"    PDF#{i+1}: {len(pb):,}bytes")
                try:
                    r = {}; full_txt = ""
                    with pdfplumber.open(io.BytesIO(pb)) as pdf:
                        for page in pdf.pages[:80]:
                            full_txt += (page.extract_text() or "") + "\n"
                            for tbl in (page.extract_tables() or []):
                                for row in tbl:
                                    if not row or len(row) < 2: continue
                                    lbl = str(row[0] or "").strip().replace(" ","")
                                    for key, kwds in _FIN_KWDS.items():
                                        if key in r: continue
                                        for kw in kwds:
                                            if lbl==kw.replace(" ","") or lbl.startswith(kw.replace(" ","")):
                                                for cell in row[1:5]:
                                                    v = _clean_num(cell)
                                                    if v and abs(v)>0: r[key]=v; break
                                                break
                    if r:
                        unit = _detect_unit(full_txt)
                        r = {k: v*unit for k,v in r.items()}   # 원 단위로 변환
                    if len(r) > len(results): results = r
                    if "매출액" in results: L("  ✅ PDF파싱 성공"); return results
                except Exception as e: L(f"    PDF#{i+1} 오류: {e}")
        except ImportError: L("  ❌ pdfplumber 미설치")
    return results

def analyze_from_document(corp_code, year):
    log = [f"=== {year}년 감사보고서 파싱 ==="]
    rcept_no, report_nm = find_filing(corp_code, year)
    if not rcept_no: log.append("❌ 공시 없음"); return None,None,"DART 공시 없음",log
    log.append(f"✅ {report_nm} (rcept_no={rcept_no})")
    html_str, xml_list, pdf_list, meta = download_zip(rcept_no)
    log.append(f"ZIP: {meta['status']}, html={meta['html']}, xml={meta['xml']}, pdf={meta['pdf']}, files={meta['files'][:4]}")
    if meta.get("error"): log.append(f"  오류: {str(meta['error'])[:200]}")
    raw = parse_document(html_str, xml_list, pdf_list, log)
    if not raw or "매출액" not in raw:
        log.append(f"❌ 실패 — 추출항목: {list(raw.keys())}")
        return None,None,"재무데이터 파싱 실패",log
    raw = compute_derived(raw)
    label = f"별도(감사보고서·{report_nm})" if report_nm else "별도(감사보고서)"
    log.append(f"✅ 완료: 매출액={raw.get('매출액',0)/1e8:.1f}억원")
    return raw, label, None, log

# ── 메인 분석 (CFS/OFS 엄격하게 적용) ────────────────────────────────────────
def analyze(corp_code, year, fs_preference="CFS"):
    """
    fs_preference="CFS": 연결 우선 → CFS만 시도, 없으면 OFS
    fs_preference="OFS": 별도 우선 → OFS만 시도, 없으면 CFS
    """
    # XBRL 시도 순서: 선호 타입 먼저, 그 다음 반대 타입
    if fs_preference == "CFS":
        fs_order = [("CFS","연결재무제표"), ("OFS","별도재무제표")]
    else:
        fs_order = [("OFS","별도재무제표"), ("CFS","연결재무제표")]

    df, used_fs_type = None, None
    for rcode in ["11011","11012","11013","11014"]:
        for fs_div, fs_label in fs_order:
            d, _ = get_fs(corp_code, year, rcode, fs_div)
            if d is not None and not d.empty:
                suffix = {"11011":"","11012":"(반기)","11013":"(1분기)","11014":"(3분기)"}.get(rcode,"")
                df, used_fs_type = d, fs_label+suffix; break
        if df is not None: break

    if df is not None:
        raw = {}
        for nm, ids in {**IS_IDS, **BS_IDS, **CF_IDS}.items():
            raw[nm] = find_val(df, ids) or find_by_name(df, nm)
        # XBRL 값은 원(KRW) 단위로 반환됨
        return compute_derived(raw), used_fs_type, None, []

    return analyze_from_document(corp_code, year)

# ── 재무표 생성 (HTML) ────────────────────────────────────────────────────────
def build_html_table(year_data, disp_unit, disp_label):
    """원 단위 저장값 → disp_unit으로 변환하여 HTML 테이블 반환"""
    years = sorted(year_data.keys())

    ROW_DEFS = [
        ("매출액",      "매출액",      "hl"),
        ("Growth",     "Growth",      "sub"),
        ("매출원가",    "매출원가",    ""),
        ("매출원가율",  "매출원가율",  "sub"),
        ("매출총이익",  "매출총이익",  "hl"),
        ("매출총이익률","매출총이익률","sub"),
        ("판관비",      "판관비",      ""),
        ("판관비율",    "판관비율",    "sub"),
        ("EBITDA",     "EBITDA",      "hl"),
        ("EBITDA M",   "EBITDA Margin","sub"),
        ("영업이익",    "영업이익",    "hl"),
        ("영업이익률",  "영업이익률",  "sub"),
        ("당기순이익",  "당기순이익",  "hl"),
        ("순이익률",    "순이익률",    "sub"),
        ("자산총계",    "자산총계",    "hl"),
        ("현금성자산",  "현금성자산",  "sub"),
        ("부채총계",    "부채총계",    "hl"),
        ("총차입금",    "총차입금",    "sub"),
        ("자본총계",    "자본총계",    "hl"),
    ]

    fv = lambda v: _fmt_val(v, disp_unit)
    sp = lambda a, b: _sp(a, b)

    rows_data = {}
    for i, year in enumerate(years):
        d = year_data[year]
        rv=d.get("매출액"); cg=d.get("매출원가"); gp=d.get("매출총이익")
        sg=d.get("판관비"); op=d.get("영업이익"); ni=d.get("당기순이익")
        eb=d.get("EBITDA"); ast=d.get("자산총계"); cs=d.get("현금성자산")
        lb=d.get("부채총계"); db=d.get("총차입금"); eq=d.get("자본총계")
        pr = year_data[years[i-1]].get("매출액") if i>0 else None
        rv_d = rv/disp_unit if rv else None
        pr_d = pr/disp_unit if pr else None
        rows_data[year] = {
            "매출액":       fv(rv),
            "Growth":       _fmt_pct((rv_d/pr_d-1)*100) if (rv_d and pr_d and pr_d!=0) else "-",
            "매출원가":     fv(cg),   "매출원가율":   sp(cg,rv),
            "매출총이익":   fv(gp),   "매출총이익률": sp(gp,rv),
            "판관비":       fv(sg),   "판관비율":     sp(sg,rv),
            "EBITDA":       fv(eb),   "EBITDA Margin":sp(eb,rv),
            "영업이익":     fv(op),   "영업이익률":   sp(op,rv),
            "당기순이익":   fv(ni),   "순이익률":     sp(ni,rv),
            "자산총계":     fv(ast),  "현금성자산":   fv(cs),
            "부채총계":     fv(lb),   "총차입금":     fv(db),
            "자본총계":     fv(eq),
        }

    # HTML 생성
    header = "".join(f"<th>{y}</th>" for y in years)
    html = f"""<div style="overflow-x:auto">
<table class="fin-table">
<thead><tr><th>(단위:{disp_label})</th>{header}</tr></thead>
<tbody>"""
    for key, label, cls in ROW_DEFS:
        tr_cls = f' class="{cls}"' if cls else ""
        cells  = "".join(f"<td>{rows_data[y].get(key,'-')}</td>" for y in years)
        html  += f"<tr{tr_cls}><td>{label}</td>{cells}</tr>\n"
    html += "</tbody></table></div>"
    return html

# ══════════════════════════════════════════════════════════════════════════════
# UI
# ══════════════════════════════════════════════════════════════════════════════
st.title("📊 DART 재무 분석")
st.caption("금융감독원 전자공시(DART) 기반 요약 재무제표 자동 생성")
with st.sidebar:
    if st.button("로그아웃"): st.session_state.authenticated=False; st.rerun()

# ── STEP 1: 기업 검색 ─────────────────────────────────────────────────────────
st.markdown("#### STEP 1 · 기업 검색")
with st.form("search_form"):
    c1,c2 = st.columns([5,1])
    with c1: q = st.text_input("기업명", placeholder="예: 삼성전자, 이노켐", label_visibility="collapsed")
    with c2: search_btn = st.form_submit_button("검색 🔍", use_container_width=True, type="primary")

if search_btn and q:
    try:
        with st.spinner("기업 목록 로딩 중..."): corp_df = get_corp_list()
        res = search_corp(q, corp_df).head(50).reset_index(drop=True)
        if res.empty:
            st.warning("검색 결과 없습니다."); st.session_state.pop("search_rows",None)
        else:
            with st.spinner(f"기업 정보 조회 중... ({len(res)}개)"):
                rows = []
                for _, row in res.iterrows():
                    info = get_corp_info(row["corp_code"])
                    ok   = info.get("status") == "000"
                    rows.append({"_corp_code":row["corp_code"],"기업명":row["corp_name"],
                                 "대표자":info.get("ceo_nm","-") if ok else "-",
                                 "업종":get_industry_name(info.get("induty_code","")) if ok else "-",
                                 "상장구분":CORP_CLS_MAP.get(info.get("corp_cls",""),"비상장")})
            st.session_state["search_rows"]=rows; st.session_state["sel_idx"]=-1
            for k in ("chosen_corp","step2_ready","result"): st.session_state.pop(k,None)
    except Exception as e:
        err=str(e)
        if any(x in err for x in ["Timeout","Connect","timed out"]):
            st.error("⏱️ DART 서버 연결 초과.")
        else: st.error("오류: "+err)

# ── 기업 선택: 버튼 방식 ──────────────────────────────────────────────────────
if "search_rows" in st.session_state:
    rows    = st.session_state["search_rows"]
    sel_idx = st.session_state.get("sel_idx", -1)
    if sel_idx is None: sel_idx = -1

    # 헤더
    h1,h2,h3,h4 = st.columns([2.5,1.5,3.5,1.2])
    for hcol,htxt in zip([h1,h2,h3,h4],["기업명","대표자","업종","상장구분"]):
        hcol.markdown(f"<div style='background:#2c3e50;color:#fff;padding:6px 10px;"
                      f"font-weight:600;font-size:0.83rem;text-align:center'>{htxt}</div>",
                      unsafe_allow_html=True)

    for i, row in enumerate(rows):
        is_sel = (i == sel_idx)
        dc = "row-sel" if is_sel else "row-btn"
        r1,r2,r3,r4 = st.columns([2.5,1.5,3.5,1.2])
        clicked = False
        for rcol, val, k in [(r1,row["기업명"],f"cn{i}"),
                              (r2,row["대표자"],f"cr{i}"),
                              (r3,row["업종"],  f"ci{i}"),
                              (r4,row["상장구분"],f"cs{i}")]:
            with rcol:
                st.markdown(f'<div class="{dc}">', unsafe_allow_html=True)
                if st.button(val, key=k, use_container_width=True): clicked=True
                st.markdown('</div>', unsafe_allow_html=True)
        if clicked:
            st.session_state["sel_idx"]=i; st.session_state["chosen_corp"]=row
            st.session_state["step2_ready"]=True; st.session_state.pop("result",None)
            st.rerun()

    if 0 <= sel_idx < len(rows):
        ch = rows[sel_idx]
        st.success(f"✅ 선택됨: **{ch['기업명']}** | {ch['대표자']} | {ch['상장구분']}")

st.divider()

# ── STEP 2: 조회 설정 ─────────────────────────────────────────────────────────
if st.session_state.get("step2_ready"):
    corp      = st.session_state["chosen_corp"]
    corp_code = corp["_corp_code"]
    corp_name = corp["기업명"]
    st.markdown(f"#### STEP 2 · 조회 설정  —  **{corp_name}**")

    all_years = [str(y) for y in range(CURRENT_YEAR, 2009, -1)]
    latest    = CURRENT_YEAR - 1

    # session_state 초기화
    ss = st.session_state
    if "sel_yr_from"      not in ss: ss["sel_yr_from"]      = str(latest-4)
    if "sel_yr_to"        not in ss: ss["sel_yr_to"]        = str(latest)
    if "active_period"    not in ss: ss["active_period"]    = None
    if "active_unit"      not in ss: ss["active_unit"]      = "억원"
    if "active_fs"        not in ss: ss["active_fs"]        = "연결"

    # ── 4컬럼 컨트롤 패널 ──────────────────────────────────────────────────────
    col_unit, col_fs, col_period, col_yr = st.columns([1.5, 1.5, 1.8, 2.5])

    # 1) 단위 선택
    with col_unit:
        st.markdown("**단위**")
        for ulabel in ["천원","백만원","억원","십억원"]:
            is_u = (ss["active_unit"] == ulabel)
            st.markdown(f'<div class="{"btn-active" if is_u else ""}">', unsafe_allow_html=True)
            if st.button(ulabel, key=f"u_{ulabel}", use_container_width=True):
                ss["active_unit"] = ulabel; st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

    # 2) 연결/별도
    with col_fs:
        st.markdown("**재무제표 구분**")
        for fslabel, fskey in [("연결","연결"),("별도","별도")]:
            is_f = (ss["active_fs"] == fskey)
            st.markdown(f'<div class="{"btn-active" if is_f else ""}">', unsafe_allow_html=True)
            if st.button(fslabel, key=f"fs_{fskey}", use_container_width=True):
                ss["active_fs"] = fskey; st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)
        fs_preference = "CFS" if ss["active_fs"] == "연결" else "OFS"

    # 3) 기간 선택 버튼
    with col_period:
        st.markdown("**기간 선택**")
        for plabel, yrs in [("5년",5),("10년",10),("20년",20)]:
            is_p = (ss["active_period"] == plabel)
            st.markdown(f'<div class="{"btn-active" if is_p else ""}">', unsafe_allow_html=True)
            if st.button(plabel, key=f"p_{plabel}", use_container_width=True):
                ss["active_period"] = plabel
                ss["sel_yr_from"]   = str(max(2010, latest - yrs + 1))
                ss["sel_yr_to"]     = str(latest)
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

    # 4) 상세 기간 (selectbox)
    with col_yr:
        st.markdown("**상세 기간**")
        yfrom_idx = all_years.index(ss["sel_yr_from"]) if ss["sel_yr_from"] in all_years else len(all_years)-1
        year_from = st.selectbox("시작", all_years, index=yfrom_idx, key="sb_from",
                                  label_visibility="visible")
        yto_idx   = all_years.index(ss["sel_yr_to"]) if ss["sel_yr_to"] in all_years else 0
        year_to   = st.selectbox("종료", all_years, index=yto_idx, key="sb_to",
                                  label_visibility="visible")
        # 수동 선택 시 active_period 해제
        if year_from != ss["sel_yr_from"] or year_to != ss["sel_yr_to"]:
            ss["sel_yr_from"] = year_from; ss["sel_yr_to"] = year_to
            ss["active_period"] = None

    if int(year_from) > int(year_to):
        st.warning("⚠️ 시작 연도가 종료 연도보다 큽니다."); st.stop()

    selected_years = [str(y) for y in range(int(year_from), int(year_to)+1)]
    st.caption(f"📅 {year_from}년 ~ {year_to}년 ({len(selected_years)}개 연도) | "
               f"단위: {ss['active_unit']} | {ss['active_fs']}재무제표 우선")

    if st.button("📊 재무제표 출력", type="primary", use_container_width=True):
        year_data, year_fstype, all_debug = {}, {}, {}
        prog = st.progress(0)
        for i, year in enumerate(selected_years):
            prog.progress((i+1)/len(selected_years), text=f"{year}년 수집 중...")
            result = analyze(corp_code, year, fs_preference)
            d, fs_used, err, dbg = result if len(result)==4 else (*result,[])
            all_debug[year] = dbg
            if d is not None: year_data[year]=d; year_fstype[year]=fs_used
            else: st.warning(f"{year}년: {err}")
        prog.empty()
        if year_data:
            fs_set = set(year_fstype.values())
            st.session_state["result"] = {
                "year_data":year_data, "year_fstype":year_fstype,
                "corp_name":corp_name, "corp_code":corp_code,
                "mixed_fs":any("연결" in f for f in fs_set) and any("별도" in f for f in fs_set),
                "debug_log":all_debug,
            }
        else:
            st.error("조회된 데이터가 없습니다.")
            for yr,lg in all_debug.items():
                if lg:
                    with st.expander(f"🔍 {yr}년 디버그"):
                        st.code("\n".join(lg))

st.divider()

# ── STEP 3: 결과 ──────────────────────────────────────────────────────────────
if "result" in st.session_state:
    r = st.session_state["result"]
    year_data    = r["year_data"]; year_fstype  = r["year_fstype"]
    sel_name     = r["corp_name"]; corp_code_r  = r["corp_code"]
    mixed_fs     = r["mixed_fs"];  years_sorted = sorted(year_data.keys())

    # 현재 단위 설정
    active_unit  = st.session_state.get("active_unit","억원")
    disp_unit    = _UNIT_MAP.get(active_unit, 100_000_000)

    st.markdown(f"#### STEP 3 · 결과  —  **{sel_name}**")

    fs_detail = " | ".join([f"{y}: {year_fstype[y]}" for y in years_sorted])
    if mixed_fs:
        st.warning(f"⚠️ **연결/별도 혼재**\n\n📋 {fs_detail}")
    else:
        st.info(f"📋 {list(year_fstype.values())[0]} (전 연도 동일)")

    # 감사보고서 파싱 안내 (경고 아닌 정보로)
    doc_years = [y for y,f in year_fstype.items() if "감사보고서" in f]
    if doc_years:
        st.info(f"📄 {', '.join(doc_years)}년: XBRL 미제출 기업 → 감사보고서 문서 기반 조회 (수치 원문 대조 권장)")
        dbg = r.get("debug_log",{})
        if any(dbg.get(y) for y in doc_years):
            with st.expander("🔍 문서 파싱 상세 로그"):
                for y in doc_years:
                    if dbg.get(y): st.markdown(f"**{y}년**"); st.code("\n".join(dbg[y]))

    info = get_corp_info(corp_code_r)
    if info.get("status") == "000":
        with st.expander("기업 기본 정보", expanded=False):
            c1,c2,c3,c4 = st.columns(4)
            c1.metric("기업명",info.get("corp_name","-")); c2.metric("대표자",info.get("ceo_nm","-"))
            c3.metric("설립일",info.get("est_dt","-")); c4.metric("결산월",(info.get("acc_mt") or "-")+"월")

    with st.expander("1단계: 원재료 데이터 수집 (원 단위)", expanded=False):
        raw_items=(list(IS_IDS.keys())+
                   ["현금및현금성자산","단기금융상품","감가상각비","무형자산상각비"]+
                   ["단기차입금","유동성장기차입금","유동성사채","단기리스부채",
                    "장기차입금","사채","장기리스부채","자산총계","부채총계","자본총계"])
        raw_rows=[]
        for item in raw_items:
            rd={"계정":item}
            for y in years_sorted:
                v=year_data[y].get(item)
                rd[y]=f"{v:,.0f}" if v is not None else "미조회"
            raw_rows.append(rd)
        st.dataframe(pd.DataFrame(raw_rows), use_container_width=True, hide_index=True)

    with st.expander("2단계: EBITDA / 현금성자산 / 총차입금", expanded=False):
        for year in years_sorted:
            d = year_data[year]
            st.markdown(f"**── {year}년 ({year_fstype.get(year,'-')}) ──**")
            ca,cb,cc = st.columns(3)
            with ca: st.markdown("**EBITDA**"); st.code(d.get("_ebitda_calc") or "부족",language=None)
            with cb: st.markdown("**현금성자산**"); st.code(d.get("_cash_calc") or "부족",language=None)
            with cc: st.markdown("**총차입금**"); st.code(d.get("_debt_calc") or "부족",language=None)
            dp = d.get("_debt_parts",[])
            if dp:
                st.dataframe(
                    pd.DataFrame([{"항목":k,"금액(억원)":f"{v/1e8:,.1f}"} for k,v in dp]
                                 +[{"항목":"합계","금액(억원)":f"{sum(v for _,v in dp)/1e8:,.1f}"}]),
                    use_container_width=False, hide_index=True)

    # ── 재무표 (HTML, 우측정렬) ─────────────────────────────────────────────
    st.markdown(f"##### 최종 요약 재무제표 (단위: {active_unit})")
    table_html = build_html_table(year_data, disp_unit, active_unit)
    st.markdown(table_html, unsafe_allow_html=True)

    with st.expander("교차 검증", expanded=False):
        vrows=[{"연도":y,"재무제표":year_fstype.get(y,"-"),
                "매출액(억원)":f"{year_data[y].get('매출액',0)/1e8:,.1f}" if year_data[y].get('매출액') else "-",
                "영업이익(억원)":f"{year_data[y].get('영업이익',0)/1e8:,.1f}" if year_data[y].get('영업이익') else "-",
                "EBITDA(억원)":f"{year_data[y].get('EBITDA',0)/1e8:,.1f}" if year_data[y].get('EBITDA') else "-"}
               for y in years_sorted]
        st.dataframe(pd.DataFrame(vrows), use_container_width=True, hide_index=True)

    st.markdown("##### 손익 추이")
    fig = go.Figure()
    for acc, color in zip(["매출액","EBITDA","영업이익","당기순이익"],
                           ["#1f77b4","#9467bd","#2ca02c","#ff7f0e"]):
        vals = [year_data[y].get(acc,0)/disp_unit if year_data[y].get(acc) is not None else None
                for y in years_sorted]
        fig.add_trace(go.Bar(name=acc, x=years_sorted, y=vals, marker_color=color,
            text=[f"{v:,.1f}" if v is not None else "-" for v in vals],
            textposition="outside"))
    fig.update_layout(barmode="group", yaxis_title=active_unit,
        legend=dict(orientation="h",yanchor="bottom",y=1.02),
        height=400, plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, use_container_width=True)

    st.divider()
    # CSV 다운로드용 DataFrame
    csv_rows = []
    ROW_KEYS = ["매출액","매출원가","매출총이익","판관비","영업이익","당기순이익",
                "EBITDA","자산총계","현금성자산","부채총계","총차입금","자본총계"]
    for key in ROW_KEYS:
        rd = {"계정":key}
        for y in years_sorted:
            v = year_data[y].get(key)
            rd[y] = f"{v/disp_unit:,.1f}" if v is not None else "-"
        csv_rows.append(rd)
    csv_df = pd.DataFrame(csv_rows)
    csv = csv_df.to_csv(index=False, encoding="utf-8-sig")
    st.download_button(f"⬇️ CSV 다운로드 (단위:{active_unit})", csv,
                       sel_name+"_재무제표.csv", "text/csv")
