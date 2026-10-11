"""--demo 용 합성 데이터. 실제 시장과 무관하며 화면·로직 점검 전용입니다.
잠재 경기(L)·물가(P) 사이클이 지표와 자산 수익률을 함께 움직이도록 만들어,
엔진이 '흐름 조합 → 이후 수익률' 관계를 찾아내는지 확인합니다."""
import numpy as np
import pandas as pd

from config import ASSETS

ETF_START = {"SPY": "1993-01-29", "QQQ": "1999-03-10", "SMH": "2000-06-05", "IWM": "2000-05-26",
             "XLRE": "2015-10-08", "IYR": "2000-06-19", "EFA": "2001-08-17", "EEM": "2003-04-14",
             "SHY": "2002-07-26", "IEF": "2002-07-26", "TLT": "2002-07-26", "TIP": "2003-12-05",
             "LQD": "2002-07-26", "HYG": "2007-04-11", "GLD": "2004-11-18", "SLV": "2006-04-28",
             "USO": "2006-04-10", "UUP": "2007-03-01", "IBIT": "2024-01-11", "ETHA": "2024-07-23",
             "GC=F": "2000-08-30", "SI=F": "2000-08-30", "CL=F": "2000-08-23", "BTC-USD": "2014-09-17",
             "ETH-USD": "2017-11-09", "^SOX": "1994-05-04", "^RUT": "1987-09-10", "^NDX": "1985-10-01"}


def make(seed=11):
    rng = np.random.default_rng(seed)
    d = pd.bdate_range("1975-01-01", "2026-10-09")
    n = len(d)
    S = pd.Series

    def ar(phi, sd, mu=0.0):
        x = np.zeros(n)
        e = rng.normal(0, sd, n)
        for i in range(1, n):
            x[i] = mu + phi * (x[i - 1] - mu) + e[i]
        return x

    L = ar(0.998, 0.063)                      # 경기 약화 정도(≈표준정규)
    P = ar(0.999, 0.03, 0) + 0.3 * -L         # 물가 압력
    infl = 3.0 + 1.2 * P                      # 연 물가상승률(%)
    core = 2.8 + 0.9 * P + ar(0.99, 0.02)
    dff_t = np.clip(2.2 + 1.4 * (infl - 2.5) - 1.1 * L, 0.05, None)
    dff = S(dff_t, d).ewm(span=60).mean().values
    fut = S(dff, d).shift(-120).ffill().values
    y2 = 0.4 * dff + 0.6 * fut + 0.25 + ar(0.95, 0.03)
    y10 = 0.55 * dff + 1.6 + 0.3 * P + ar(0.997, 0.03)
    dxy = 95 * np.exp(np.cumsum(rng.normal(0, .004, n) + 2e-5 * (y2 - 2.5)))
    vix = 16 * np.exp(0.25 * L + ar(0.97, 0.05))

    def idx_from_rate(rate_pct, freq):
        m = S(rate_pct, d).resample(freq).mean()
        k = 12 if freq == "MS" else 4
        return 100 * np.cumprod(1 + m / 100 / k)

    raw = {
        "CFNAIMA3": S(-0.55 * L + ar(0.9, 0.05), d).resample("MS").mean(),
        "CPIAUCSL": idx_from_rate(infl + ar(0.95, 0.15), "MS"),
        "CPILFESL": idx_from_rate(core, "MS"),
        "PCEPILFE": idx_from_rate(core - 0.3, "MS"),
        "GDPC1": idx_from_rate(2.6 - 1.6 * L + ar(0.9, 0.2), "QS"),
        "CP": idx_from_rate(6 - 6 * L + ar(0.95, 0.4), "QS"),
        "DGS3MO": S(dff + 0.05, d), "DGS2": S(y2, d), "DGS10": S(y10, d), "DGS30": S(y10 + 0.4, d),
        "DFF": S(dff, d), "VIXCLS": S(vix, d)[d >= "1990-01-02"],
        "BAMLH0A0HYM2": S(3.6 + 1.2 * L, d)[d >= "2023-10-01"],
        "T10YIE": S(2.2 + 0.25 * P, d)[d >= "2003-01-02"], "DFII10": S(y10 - 2.2 - 0.25 * P, d)[d >= "2003-01-02"],
        "NFCI": S(-0.4 + 0.25 * L, d).resample("W-FRI").last(),
        "UNRATE": S(4.5 + 0.9 * L, d).resample("MS").mean(),
        "ICSA": S(230000 * np.exp(0.12 * L), d).resample("W-SAT").last(),
        "RECPROUSM156N": S(100 / (1 + np.exp(-(2.5 * L - 3))), d).resample("MS").last(),
        "DXY": S(dxy, d)[d >= "1985-01-01"],
        "GPRD": S(100 * np.exp(ar(0.99, 0.12) + rng.normal(0, .25, n)), d)[d >= "1985-01-01"],
        "FEARGREED": S(np.clip(50 - 14 * L + ar(0.95, 4), 0, 100), d)[d >= "2021-01-01"],
        "GDPNOW": S([2.4], [pd.Timestamp("2026-07-01")]),
    }
    hi = np.ceil(dff[-1] * 4) / 4
    raw["DFEDTARU"] = S([hi], [d[-1]])
    raw["DFEDTARL"] = S([hi - 0.25], [d[-1]])
    raw["FEDTARMD"] = S([hi - 0.125 - 0.25, hi - 0.125 - 0.75, hi - 0.125 - 1.0],
                        pd.to_datetime(["2026-01-01", "2027-01-01", "2028-01-01"]))
    raw["FEDTARMDLR"] = S([3.0], [d[-1]])

    # 자산: 잠재 경기 약화(L)·금리 변화·달러가 이후 수익률에 영향
    dy10 = np.r_[0, np.diff(y10)]
    lagL = np.roll(L, 5)
    lagdy = S(dy10, d).rolling(60).sum().shift(5).fillna(0).values      # 지난 3개월 금리 흐름
    lagdx = S(np.log(dxy), d).diff(60).shift(5).fillna(0).values
    eq = 3.5e-4 - 5e-4 * lagL - 2e-3 * lagdy * (lagL > 0.6)
    tech = eq - 1.5e-3 * lagdy
    px = {}

    def make_px(r, sym):
        p = S(100 * np.exp(np.cumsum(r)), d)
        px[sym] = p[p.index >= ETF_START.get(sym, "1998-12-22")]

    for a in ASSETS:
        for src in a["src"]:
            if src[0] != "yahoo":
                continue
            sym, cat, aid = src[1], a["cat"], a["id"]
            if aid in ("ust_short", "ust_mid", "ust_long", "tips"):
                D = {"ust_short": 1.9, "ust_mid": 7.5, "ust_long": 17, "tips": 7}[aid]
                r = y10 / 100 / 252 - D * dy10 / 100 * (0.5 if aid == "ust_short" else 1) + rng.normal(0, 3e-4, n)
            elif aid == "dollar":
                r = np.r_[0, np.diff(np.log(dxy))] + 4e-5
            elif aid in ("gold", "silver"):
                r = 2e-4 - 8e-3 * lagdx - 1e-3 * lagdy + rng.normal(0, .010 if aid == "gold" else .017, n)
            elif aid == "oil":
                r = 1e-4 - 6e-4 * lagL + 3e-3 * np.roll(P, 5) * 1e-1 + rng.normal(0, .02, n)
            elif aid in ("btc", "eth"):
                r = 4e-4 - 2e-3 * lagL + rng.normal(0, .03, n)
            elif aid in ("us_tech", "semis"):
                r = tech + rng.normal(0, .014, n)
            elif aid in ("util", "staples", "health"):
                r = 3e-4 - 1e-4 * lagL - 1e-3 * lagdy + rng.normal(0, .009, n)
            elif aid in ("ig", "hy"):
                r = 2e-4 - 2e-4 * lagL - (6 if aid == "ig" else 3) * dy10 / 100 + rng.normal(0, .003, n)
            elif aid in ("dev_intl", "em"):
                r = eq - 6e-3 * lagdx + rng.normal(0, .013, n)
            else:
                r = eq + rng.normal(0, .011, n)
            make_px(r, sym)
    fs = {"date": "2026-10-09", "blended": 7.4, "fpe": 21.8,
          "growth": {"Q4 2026": 9.1, "Q1 2027": 11.8, "CY 2026": 11.2, "CY 2027": 13.5}}
    return raw, px, fs
