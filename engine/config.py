"""국면(레짐) 엔진 설정 — 이 파일에서 자산·ETF·흐름 판정 기준을 바꿀 수 있습니다."""

# ─────────────────────────────────────────────────────────────
# 1) 자산과 대표 ETF
#    src: 수익률 계산에 쓸 시계열, 앞쪽이 우선(상장 전 기간은 뒤쪽 지수·합성 시계열로 채움)
#      ("yahoo", 티커) | ("bond", FRED 금리 시리즈, 듀레이션)  ← 금리로 채권 수익률 근사
# ─────────────────────────────────────────────────────────────
ASSETS = [
    # 주식
    {"id": "us_large",  "name": "미국 대형주",      "cat": "주식", "src": [("yahoo", "SPY"), ("yahoo", "^GSPC")], "etfs": ["SPY", "VOO", "IVV"]},
    {"id": "us_tech",   "name": "기술주·나스닥100", "cat": "주식", "src": [("yahoo", "QQQ"), ("yahoo", "^NDX")], "etfs": ["QQQ", "XLK", "VGT"]},
    {"id": "semis",     "name": "반도체",          "cat": "주식", "src": [("yahoo", "SMH"), ("yahoo", "^SOX")], "etfs": ["SMH", "SOXX"]},
    {"id": "us_small",  "name": "미국 소형주",      "cat": "주식", "src": [("yahoo", "IWM"), ("yahoo", "^RUT")], "etfs": ["IWM", "IJR"]},
    {"id": "fin",       "name": "금융",            "cat": "섹터", "src": [("yahoo", "XLF")], "etfs": ["XLF", "KBE"]},
    {"id": "energy",    "name": "에너지",          "cat": "섹터", "src": [("yahoo", "XLE")], "etfs": ["XLE", "VDE"]},
    {"id": "health",    "name": "헬스케어",        "cat": "섹터", "src": [("yahoo", "XLV")], "etfs": ["XLV", "VHT"]},
    {"id": "discr",     "name": "경기소비재",      "cat": "섹터", "src": [("yahoo", "XLY")], "etfs": ["XLY", "VCR"]},
    {"id": "staples",   "name": "필수소비재",      "cat": "섹터", "src": [("yahoo", "XLP")], "etfs": ["XLP", "VDC"]},
    {"id": "indus",     "name": "산업재",          "cat": "섹터", "src": [("yahoo", "XLI")], "etfs": ["XLI", "VIS"]},
    {"id": "util",      "name": "유틸리티",        "cat": "섹터", "src": [("yahoo", "XLU")], "etfs": ["XLU", "VPU"]},
    {"id": "materials", "name": "소재",            "cat": "섹터", "src": [("yahoo", "XLB")], "etfs": ["XLB", "VAW"]},
    {"id": "reit",      "name": "부동산·리츠",    "cat": "섹터", "src": [("yahoo", "XLRE"), ("yahoo", "IYR")], "etfs": ["XLRE", "VNQ"]},
    {"id": "dev_intl",  "name": "선진국 주식(미국 외)", "cat": "해외", "src": [("yahoo", "EFA")], "etfs": ["EFA", "VEA"]},
    {"id": "em",        "name": "신흥국 주식",      "cat": "해외", "src": [("yahoo", "EEM")], "etfs": ["EEM", "VWO"]},
    # 채권
    {"id": "ust_short", "name": "단기 국채",        "cat": "채권", "src": [("yahoo", "SHY"), ("bond", "DGS2", 1.9)],  "etfs": ["SHY", "SGOV", "BIL"]},
    {"id": "ust_mid",   "name": "중기 국채",        "cat": "채권", "src": [("yahoo", "IEF"), ("bond", "DGS10", 7.5)], "etfs": ["IEF", "VGIT"]},
    {"id": "ust_long",  "name": "장기 국채",        "cat": "채권", "src": [("yahoo", "TLT"), ("bond", "DGS30", 17.0)], "etfs": ["TLT", "EDV", "TMF"]},
    {"id": "tips",      "name": "물가연동 국채",    "cat": "채권", "src": [("yahoo", "TIP")], "etfs": ["TIP", "SCHP"]},
    {"id": "ig",        "name": "투자등급 회사채",  "cat": "채권", "src": [("yahoo", "LQD")], "etfs": ["LQD", "VCIT"]},
    {"id": "hy",        "name": "하이일드 회사채",  "cat": "채권", "src": [("yahoo", "HYG")], "etfs": ["HYG", "JNK"]},
    # 원자재·통화·코인
    {"id": "gold",      "name": "금",              "cat": "원자재·통화", "src": [("yahoo", "GLD"), ("yahoo", "GC=F")], "etfs": ["GLD", "IAU", "GLDM"]},
    {"id": "silver",    "name": "은",              "cat": "원자재·통화", "src": [("yahoo", "SLV"), ("yahoo", "SI=F")], "etfs": ["SLV", "SIVR"]},
    {"id": "oil",       "name": "원유",            "cat": "원자재·통화", "src": [("yahoo", "USO"), ("yahoo", "CL=F")], "etfs": ["USO", "BNO"]},
    {"id": "dollar",    "name": "달러",            "cat": "원자재·통화", "src": [("yahoo", "UUP"), ("yahoo", "DX-Y.NYB")], "etfs": ["UUP"]},
    {"id": "btc",       "name": "비트코인",         "cat": "코인", "src": [("yahoo", "IBIT"), ("yahoo", "BTC-USD")], "etfs": ["IBIT", "FBTC", "BITO"]},
    {"id": "eth",       "name": "이더리움",         "cat": "코인", "src": [("yahoo", "ETHA"), ("yahoo", "ETH-USD")], "etfs": ["ETHA", "FETH"]},
]

# ─────────────────────────────────────────────────────────────
# 2) 국면을 이루는 '흐름' 8가지
#    값 > 기준선 이면 states[0], 아니면 states[1].
#    band: 기준선 근처 잔떨림 방지(이 폭 안에서는 직전 상태 유지)
#    lag: 발표 지연(일) — 과거 시점에 실제로 알 수 있었던 값만 쓰기 위함
# ─────────────────────────────────────────────────────────────
DIMS = [
    {"id": "growth",    "name": "성장",       "states": ["견조", "둔화"],     "th": -0.35, "band": 0.10,
     "desc": "시카고연은 경기지수(CFNAI) 3개월 평균. 0 = 추세 성장, -0.35 미만이면 둔화"},
    {"id": "inflation", "name": "물가 흐름",   "states": ["상승", "하락"],     "th": 0.0,   "band": 0.05,
     "desc": "CPI·근원 CPI 전년비 상승률이 3개월 전보다 높은지(가속) 낮은지(둔화)"},
    {"id": "rates",     "name": "국채금리 흐름", "states": ["상승", "하락"],    "th": 0.0,   "band": 0.10,
     "desc": "10년 국채금리 13주 변화"},
    {"id": "fed",       "name": "연준 정책",   "states": ["긴축·동결", "완화"], "th": -0.25, "band": 0.05,
     "desc": "2년 국채금리 − 기준금리. 시장이 인하를 반영하면(−0.25%p 이하) 또는 최근 반년 인하 시 완화"},
    {"id": "dollar",    "name": "달러 흐름",   "states": ["강세", "약세"],     "th": 0.0,   "band": 0.5,
     "desc": "달러 인덱스(DXY) 13주 변화율"},
    {"id": "risk",      "name": "위험심리",    "states": ["불안", "안정"],     "th": 20.0,  "band": 1.0,
     "desc": "VIX 4주 평균 20 초과면 불안"},
    {"id": "earnings",  "name": "기업이익",    "states": ["증가", "감소"],     "th": 0.0,   "band": 1.0,
     "desc": "미국 기업이익(FRED CP) 전년비 증가율"},
    {"id": "geo",       "name": "지정학 리스크", "states": ["높음", "보통"],    "th": 80.0,  "band": 3.0,
     "desc": "GPR 지수 4주 평균의 과거 5년 백분위 80 초과면 높음"},
]

# ─────────────────────────────────────────────────────────────
# 3) 통계 파라미터
# ─────────────────────────────────────────────────────────────
HORIZONS = {"1w": 1, "1m": 4, "1q": 13}            # 이번 주 / 이번 달 / 이번 분기
H_LABEL = {"1w": "이번 주", "1m": "이번 달", "1q": "이번 분기"}
START = "1990-01-05"                               # VIX 시작 시점
MIN_WEEKS = {"1w": 52, "1m": 52, "1q": 78}         # 같은 조합 최소 표본(주)
MIN_EPISODES = 3                                   # 같은 조합이 나타난 서로 다른 시기 수
DRIVER_T = 1.0                                     # 자산별로 '영향 있는 흐름'으로 인정할 최소 t값
REFIT_WEEKS = 52                                   # 워크포워드 재학습 주기
WF_START = "2000-01-07"                            # 검증 시작
HISTORY_WEEKS = 156
