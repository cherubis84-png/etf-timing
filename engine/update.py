"""전체 파이프라인: 수집 → 흐름(국면) 판정 → 자산별 같은 국면 과거 통계 → 해설 → data.json

사용:  python engine/update.py          (실데이터)
       python engine/update.py --demo   (합성 데이터로 화면·로직 점검)
"""
import json
import math
import os
import sys
import warnings
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import fetch  # noqa: E402
import regime as R  # noqa: E402
import track  # noqa: E402
from config import ASSETS, DIMS, H_LABEL, HISTORY_WEEKS, HORIZONS, START  # noqa: E402

warnings.filterwarnings("ignore")
np.seterr(all="ignore")
ROOT = os.path.join(os.path.dirname(__file__), "..")
DEMO = "--demo" in sys.argv
DIM = {d["id"]: d for d in DIMS}

FRED_IDS = ["CFNAIMA3", "CPIAUCSL", "CPILFESL", "PCEPILFE", "GDPC1", "GDPNOW", "CP",
            "DGS3MO", "DGS2", "DGS10", "DGS30", "DFF", "DFEDTARU", "DFEDTARL", "FEDTARMD", "FEDTARMDLR",
            "VIXCLS", "BAMLH0A0HYM2", "T10YIE", "DFII10", "NFCI", "UNRATE", "ICSA", "RECPROUSM156N"]


# ═════════════════════ 공용 ═════════════════════
def clean(o):
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not math.isfinite(o) else round(float(o), 4)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (pd.Timestamp, np.datetime64)):
        return str(pd.Timestamp(o).date())
    return o


def last(s):
    if s is None:
        return None, None
    s = s.dropna()
    return (float(s.iloc[-1]), s.index[-1]) if len(s) else (None, None)


def st_name(dim_id, state):
    return f"{DIM[dim_id]['name']} {DIM[dim_id]['states'][int(state)]}"


def _jong(word):
    ch = word.strip()[-1]
    return (ord(ch) - 0xAC00) % 28 if "가" <= ch <= "힣" else 0


def iga(word):
    return "이" if _jong(word) else "가"


def ro(word):
    return "로" if _jong(word) in (0, 8) else "으로"


# ═════════════════════ 1. 수집 ═════════════════════
def collect():
    if DEMO:
        import demo_data
        return demo_data.make()
    from concurrent.futures import ThreadPoolExecutor
    if not os.environ.get("FRED_API_KEY", "").strip():
        fetch.log("⚠ FRED_API_KEY가 없습니다. GitHub 서버에서는 FRED 웹 CSV가 자주 막히니 키를 등록하세요.")
    fetch.log("▶ 거시 지표 수집")
    with ThreadPoolExecutor(6) as ex:
        raw = dict(zip(FRED_IDS, ex.map(fetch.fred, FRED_IDS)))
    raw["GPRD"] = fetch.gpr_daily()
    raw["FEARGREED"] = fetch.cnn_fear_greed()
    fs = fetch.factset()
    fetch.log("▶ 가격 수집")
    syms = []
    for a in ASSETS:
        for src in a["src"]:
            if src[0] == "yahoo" and src[1] not in syms:
                syms.append(src[1])
    px = fetch.prices(syms)
    raw["DXY"] = px.get("DX-Y.NYB")
    return raw, px, fs


# ═════════════════════ 2. 근거 문장 ═════════════════════
def fed_extras(raw):
    lo, _ = last(raw.get("DFEDTARL"))
    hi, _ = last(raw.get("DFEDTARU"))
    out = {"lo": lo, "hi": hi}
    dots = raw.get("FEDTARMD")
    yr = pd.Timestamp.today().year
    if dots is not None and len(dots.dropna()):
        d = dots.dropna()
        by_year = {ix.year: float(v) for ix, v in d.items()}
        out["dot_this"] = by_year.get(yr)
        out["dot_next"] = by_year.get(yr + 1)
        out["dot_next2"] = by_year.get(yr + 2)
    lr, _ = last(raw.get("FEDTARMDLR"))
    out["dot_lr"] = lr
    if lo is not None and hi is not None:
        mid = (lo + hi) / 2
        for k in ("dot_this", "dot_next"):
            if out.get(k) is not None:
                out[k + "_cuts"] = round((mid - out[k]) / 0.25, 1)
    return out


def evidence(dim_id, W, V, raw, fed, fs):
    w = W.iloc[-1]
    g = lambda k: w.get(k, np.nan)  # noqa: E731
    f = lambda x, d=2: "–" if x is None or not np.isfinite(x) else f"{x:.{d}f}"  # noqa: E731
    sg = lambda x, d=2: "–" if x is None or not np.isfinite(x) else f"{x:+.{d}f}"  # noqa: E731
    if dim_id == "growth":
        gn, gd = last(raw.get("GDPNOW"))
        t = f"CFNAI 3개월 평균 {sg(g('cfnai'))} (0=추세 성장), 실질 GDP 전기비 연율 {f(g('gdp_qoq'), 1)}%, 전년비 {f(g('gdp_yoy'), 1)}%"
        if gn is not None:
            t += f", GDPNow {gn:.1f}%"
        return t + f", 실업률 {f(g('unrate'), 1)}%"
    if dim_id == "inflation":
        prev = W["core_yoy"].iloc[-14] if "core_yoy" in W and len(W) > 14 else np.nan
        return (f"CPI {f(g('cpi_yoy'), 1)}%, 근원 CPI {f(g('core_yoy'), 1)}% (3개월 전 {f(prev, 1)}%), "
                f"근원 PCE {f(g('pce_core_yoy'), 1)}%, 기대인플레 {f(g('bei'))}%")
    if dim_id == "rates":
        return (f"10년물 {f(g('y10'))}% (13주 {sg(V['rates'].iloc[-1])}%p), 2년물 {f(g('y2'))}%, "
                f"3개월물 {f(g('y3m'))}%, 30년물 {f(g('y30'))}%")
    if dim_id == "fed":
        t = f"기준금리 {f(fed.get('lo'))}–{f(fed.get('hi'))}%, 2년물−기준금리 {sg(g('y2') - g('dff'))}%p"
        if fed.get("dot_this") is not None:
            t += f", 점도표 중간값 올해 말 {f(fed['dot_this'], 3)}%"
        if fed.get("dot_next") is not None:
            t += f" · 내년 말 {f(fed['dot_next'], 3)}%"
        return t
    if dim_id == "dollar":
        return f"달러 인덱스 {f(g('dxy'), 1)} (13주 {sg(V['dollar'].iloc[-1], 1)}%)"
    if dim_id == "risk":
        t = f"VIX {f(g('vix'), 1)} (4주 평균 {f(V['risk'].iloc[-1], 1)}), 하이일드 스프레드 {f(g('hy'))}%p, NFCI {sg(g('nfci'))}"
        if np.isfinite(g("fg")):
            t += f", 공포·탐욕 {g('fg'):.0f}"
        return t
    if dim_id == "earnings":
        t = f"미국 기업이익(GDP 통계) 전년비 {sg(g('cp_yoy'), 1)}%"
        if fs and fs.get("growth"):
            parts = [f"{k} {v:+.1f}%" for k, v in list(fs["growth"].items())[:4]]
            t += f", 애널리스트 S&P 500 EPS 성장 예상: {', '.join(parts)}"
            if fs.get("fpe"):
                t += f", 선행 PER {fs['fpe']:.1f}배"
        return t
    if dim_id == "geo":
        return f"지정학 리스크 지수(GPR) 4주 평균 {f(g('gpr'), 0)}, 과거 5년 중 {f(V['geo'].iloc[-1], 0)} 백분위"
    return ""


# ═════════════════════ 3. 자산 처리 ═════════════════════
def run_asset(a, px, raw, S, V, trans, now):
    r, disp = R.asset_returns(a, px, raw, S.index)
    if r is None or r.notna().sum() < 104:
        return None
    cur = {k: S[k].iloc[now] for k in S.columns}
    out = {"id": a["id"], "name": a["name"], "cat": a["cat"], "etfs": a["etfs"], "h": {}, "rel": {}}
    preds = {}
    order_by_h = {}
    for hk, h in HORIZONS.items():
        fwd = R.fwd_returns(r, h)
        drivers = R.rank_drivers(S, fwd, h, now)
        order = [d["dim"] for d in drivers if abs(d["t"]) >= R.DRIVER_T]
        order_by_h[hk] = order
        st = R.cond_stats(S, fwd, h, order, now, cur, hk)
        if st:
            st["signal"] = R.signal_of(st)
        out["h"][hk] = st
        if hk == "1q":
            out["drivers"] = [{**d, "name": DIM[d["dim"]]["name"], "states": DIM[d["dim"]]["states"]}
                              for d in drivers]
        pred = R.walk_forward(S, fwd, h, hk)
        preds[hk] = pred
        ic = R.spearman(pred.values, fwd.values)
        out["rel"][hk] = ic
        # 예측 부호 적중률 (표본 외)
        m = pred.notna() & fwd.notna()
        out["rel"][hk + "_hit"] = float(((pred[m] > 0) == (fwd[m] > 0)).mean() * 100) if m.sum() > 50 else None
    ics = [out["rel"][k] for k in ("1m", "1q") if out["rel"].get(k) is not None and np.isfinite(out["rel"][k])]
    out["rel"]["label"] = R.reliability(np.mean(ics) if ics else None)
    out["rel"]["since"] = str(preds["1m"].dropna().index[0].date()) if preds["1m"].notna().any() else None

    # 시나리오: 곧 바뀔 수 있는 흐름이 바뀌면?
    scen, warns = [], []
    for dk, tr in trans.items():
        p = tr.get("flip_13w")
        if p is None or p < 25:
            continue
        sc = {"dim": dk, "to": DIM[dk]["states"][1 - tr["state"]], "prob": p}
        for hk in ("1m", "1q"):
            if dk not in order_by_h[hk]:
                continue
            fwd = R.fwd_returns(r, HORIZONS[hk])
            st2 = R.cond_stats(S, fwd, HORIZONS[hk], order_by_h[hk], now, cur, hk, flip=dk)
            if st2:
                sc[hk] = {"median": st2["median"], "hit": st2["hit"], "signal": R.signal_of(st2)}
        if "1m" in sc or "1q" in sc:
            scen.append(sc)
            base, alt = out["h"].get("1q"), sc.get("1q")
            if base and alt:
                up_now, up_alt = base["median"] > 0 and base["hit"] >= 50, alt["median"] > 0 and alt["hit"] >= 50
                nm = DIM[dk]["name"]
                if up_now and not up_alt:
                    warns.append({"type": "down", "text": f"‘{nm}’{iga(nm)} {sc['to']}{ro(sc['to'])} 바뀌면 하락 우위로 전환될 수 있습니다. "
                                  f"13주 안에 바뀔 확률 {p:.0f}%, 바뀌면 분기 기대 {alt['median']:+.1f}% (현재 {base['median']:+.1f}%)."})
                elif not up_now and up_alt:
                    warns.append({"type": "up", "text": f"‘{nm}’{iga(nm)} {sc['to']}{ro(sc['to'])} 바뀌면 상승 반전 가능성이 커집니다. "
                                  f"13주 안에 바뀔 확률 {p:.0f}%, 바뀌면 분기 기대 {alt['median']:+.1f}%."})
    m1, mq = out["h"].get("1m"), out["h"].get("1q")
    if m1 and mq:
        if m1["median"] > 0 and mq["median"] < 0:
            warns.append({"type": "down", "text": "이번 달은 우호적이지만 분기 기준으로는 약세 우위입니다 — 1~3개월 사이 하락 전환에 대비하세요."})
        elif m1["median"] < 0 and mq["median"] > 0:
            warns.append({"type": "up", "text": "단기(1개월)는 약세 우위지만 분기 기준으로는 반등 우위입니다 — 분할 매수 구간이 될 수 있습니다."})
    out["scenarios"], out["warnings"] = scen, warns

    # 가격 표시
    if disp is not None and len(disp):
        out["price"], out["price_date"] = float(disp.iloc[-1]), disp.index[-1]
        out["price_sym"] = next(s[1] for s in a["src"] if s[0] == "yahoo")
        out["chg1w"] = (disp.iloc[-1] / disp.iloc[-6] - 1) * 100 if len(disp) > 6 else None
        dw = R.to_weekly(disp).reindex(S.index)
        out["hist"] = [[d, v] for d, v in dw.iloc[-HISTORY_WEEKS:].items()]
    return out, preds, r


# ═════════════════════ 4. 포트폴리오 검증 ═════════════════════
def backtest(preds, rets):
    P = pd.DataFrame({k: v["1m"] for k, v in preds.items()})
    Rn = pd.DataFrame({k: np.expm1(v.shift(-1)) for k, v in rets.items()})   # 다음 주 수익률
    P = P[P.notna().any(axis=1)]
    Rn = Rn.reindex(P.index)
    top, ew, spy = [], [], []
    for t in P.index:
        p, rr = P.loc[t], Rn.loc[t]
        avail = p.notna() & rr.notna()
        if avail.sum() < 5:
            continue
        cand = p[avail & (p > 0)].sort_values(ascending=False).head(3)
        top.append(rr[cand.index].mean() if len(cand) else 0.0)
        ew.append(rr[avail].mean())
        spy.append(rr.get("us_large", np.nan))
    if len(top) < 52:
        return None
    def stats(x):
        x = pd.Series(x).fillna(0)
        eq = (1 + x).cumprod()
        return {"cagr": (eq.iloc[-1] ** (52 / len(x)) - 1) * 100, "mdd": (eq / eq.cummax() - 1).min() * 100}
    return {"since": P.index[0], "weeks": len(top), "top3": stats(top), "ew": stats(ew), "spy": stats(spy)}


# ═════════════════════ 5. 해설 ═════════════════════
def narrative(cur, W, V, trans, fed, fs, ranked):
    out = []
    quad = R.quad_of(cur["growth"], cur["inflation"])
    if quad:
        out.append(f"현재는 ‘{quad}’ 국면 — {R.QUAD_DESC[quad]}입니다.")
    g, rt = cur.get("growth"), cur.get("rates")
    if np.isfinite(g) and np.isfinite(rt):
        out.append({
            (0, 0): "국채금리가 오르고 있지만 경기가 견조해 금리 부담을 흡수할 수 있는 환경입니다.",
            (1, 0): "경기가 둔화되는데 금리까지 올라 위험자산에 부담이 큰 환경입니다.",
            (0, 1): "경기가 견조한 가운데 금리가 내려 주식 밸류에이션에 우호적인 환경입니다.",
            (1, 1): "금리 하락이 경기 둔화 우려를 반영하는 성격이 강한 환경입니다.",
        }[(int(g), int(rt))])
    if np.isfinite(cur.get("fed", np.nan)):
        if cur["fed"] == 1:
            spr = W["y2"].iloc[-1] - W["dff"].iloc[-1] if "y2" in W and "dff" in W else np.nan
            if np.isfinite(spr) and spr <= -0.25:
                s = f"시장은 연준의 금리 인하를 반영하고 있습니다(2년물이 기준금리보다 {abs(spr):.2f}%p 낮음)."
            else:
                s = "연준이 최근 반년 사이 기준금리를 내린 완화 국면입니다."
        else:
            s = "연준은 긴축·동결 기조이며 시장도 큰 폭의 인하를 반영하지 않고 있습니다."
        if fed.get("dot_this") is not None and fed.get("dot_next") is not None:
            cuts = fed.get("dot_next_cuts")
            s += f" 점도표 중간값은 올해 말 {fed['dot_this']:.2f}%, 내년 말 {fed['dot_next']:.2f}%"
            s += f"로 지금보다 약 {cuts:.0f}회(0.25%p 기준) 인하를 시사합니다." if cuts and cuts >= 0.5 else (
                 f"로 추가 인상 가능성을 시사합니다." if cuts and cuts <= -0.5 else "로 현 수준 유지를 시사합니다.")
        out.append(s)
    bits = []
    for k in ("dollar", "risk", "earnings", "geo"):
        if np.isfinite(cur.get(k, np.nan)):
            bits.append(f"{DIM[k]['name']} {DIM[k]['states'][int(cur[k])]}")
    if bits:
        out.append(", ".join(bits) + " 흐름입니다.")
    if fs and fs.get("growth"):
        cy = [(k, v) for k, v in fs["growth"].items() if k.startswith("CY")]
        if len(cy) >= 2:
            out.append(f"애널리스트들은 S&P 500 주당순이익이 {cy[0][0][3:]}년 {cy[0][1]:+.1f}%, {cy[1][0][3:]}년 {cy[1][1]:+.1f}% 늘 것으로 예상합니다"
                       + (f"(선행 PER {fs['fpe']:.1f}배)." if fs.get("fpe") else "."))
    ups = [a for a in ranked["1m"] if a["sig"] in ("강한 상승", "상승 우위")][:3]
    dns = sorted([a for a in ranked["1m"] if a["sig"] in ("강한 하락", "하락 우위")],
                 key=lambda x: (x["rel"] not in ("높음", "보통"), x["median"]))[:2]
    if ups:
        out.append("과거 같은 흐름 조합에서 이번 달 유리했던 자산: "
                   + ", ".join(f"{a['name']}({a['median']:+.1f}%)" for a in ups) + ".")
    if dns:
        out.append("불리했던 자산: " + ", ".join(f"{a['name']}({a['median']:+.1f}%)" for a in dns) + ".")
    soon = sorted([(k, t) for k, t in trans.items() if (t.get("flip_13w") or 0) >= 50], key=lambda x: -x[1]["flip_13w"])
    for k, t in soon[:2]:
        nm, to = DIM[k]["name"], DIM[k]["states"][1 - t["state"]]
        out.append(f"‘{nm}’{iga(nm)} 기준선에 가까워 13주 안에 {to}{ro(to)} 바뀔 확률이 "
                   f"{t['flip_13w']:.0f}%입니다 — 바뀌면 자산 전망도 달라집니다.")
    return quad, out


# ═════════════════════ 6. 지표 카드 ═════════════════════
def macro_cards(W, raw, fed, fs):
    cards = []

    def add(cid, name, cat, col=None, unit="%", dec=2, series=None, value=None, asof=None, note=None):
        s = W[col] if col and col in W else series
        hist = []
        if s is not None and value is None:
            s2 = s.dropna()
            if not len(s2):
                return
            value = float(s2.iloc[-1])
            hist = [[d, v] for d, v in s2.iloc[-HISTORY_WEEKS:].items()]
            chg = float(s2.iloc[-1] - s2.iloc[-14]) if len(s2) > 14 else None
        else:
            chg = None
        if value is None:
            return
        raw_key = {"cfnai": "CFNAIMA3", "cpi_yoy": "CPIAUCSL", "core_yoy": "CPILFESL", "pce_core_yoy": "PCEPILFE",
                   "gdp_qoq": "GDPC1", "gdp_yoy": "GDPC1", "cp_yoy": "CP", "unrate": "UNRATE", "claims": "ICSA",
                   "y3m": "DGS3MO", "y2": "DGS2", "y10": "DGS10", "y30": "DGS30", "vix": "VIXCLS", "hy": "BAMLH0A0HYM2",
                   "nfci": "NFCI", "dxy": "DXY", "fg": "FEARGREED", "gpr": "GPRD", "recession": "RECPROUSM156N",
                   "bei": "T10YIE", "real10": "DFII10"}.get(col)
        if asof is None and raw_key:
            _, asof = last(raw.get(raw_key))
            if asof is not None and raw_key in ("GDPC1", "CP"):
                asof = f"{asof.year}년 {(asof.month - 1) // 3 + 1}분기"
            elif asof is not None and raw_key in ("CFNAIMA3", "CPIAUCSL", "CPILFESL", "PCEPILFE", "UNRATE", "RECPROUSM156N"):
                asof = f"{asof.year}년 {asof.month}월"
        cards.append({"id": cid, "name": name, "cat": cat, "value": value, "unit": unit, "dec": dec,
                      "chg13": chg, "asof": asof, "hist": hist, "note": note})

    lo, hi = fed.get("lo"), fed.get("hi")
    if lo is not None:
        add("ff", "기준금리 (상단)", "연준", value=hi, asof=last(raw.get("DFEDTARU"))[1],
            note=f"목표 범위 {lo:.2f}–{hi:.2f}%")
    for k, nm in (("dot_this", "점도표 중간값 · 올해 말"), ("dot_next", "점도표 중간값 · 내년 말"),
                  ("dot_next2", "점도표 중간값 · 2년 뒤"), ("dot_lr", "점도표 중간값 · 장기")):
        if fed.get(k) is not None:
            cuts = fed.get(k + "_cuts")
            add(k, nm, "연준", value=fed[k], asof="최근 FOMC 경제전망",
                note=(f"현재 대비 약 {cuts:.0f}회 인하" if cuts and cuts > 0 else f"현재 대비 약 {abs(cuts):.0f}회 인상" if cuts and cuts < 0 else None)
                if cuts is not None else None)
    add("gdp_qoq", "실질 GDP 성장률 (전기비 연율)", "성장", "gdp_qoq", dec=1)
    add("gdp_yoy", "실질 GDP 성장률 (전년비)", "성장", "gdp_yoy", dec=1)
    gn, gd = last(raw.get("GDPNOW"))
    if gn is not None:
        add("gdpnow", "애틀랜타 연은 GDPNow (분기 성장률 추정)", "성장", value=gn,
            asof=f"{gd.year}년 {(gd.month - 1) // 3 + 1}분기", dec=1)
    add("cfnai", "시카고연은 경기지수 3개월 평균", "성장", "cfnai", unit="", dec=2)
    add("unrate", "실업률", "성장", "unrate", dec=1)
    add("claims", "신규 실업수당 청구", "성장", "claims", unit="건", dec=0)
    add("recession", "경기침체 확률 (FRED)", "성장", "recession", dec=1)
    add("cpi", "CPI 상승률 (전년비)", "물가", "cpi_yoy", dec=1)
    add("core", "근원 CPI 상승률 (전년비)", "물가", "core_yoy", dec=1)
    add("pce", "근원 PCE 상승률 (연준 목표 지표)", "물가", "pce_core_yoy", dec=1)
    add("bei", "10년 기대인플레이션", "물가", "bei")
    add("y3m", "3개월 국채", "금리", "y3m")
    add("y2", "2년 국채", "금리", "y2")
    add("y10", "10년 국채", "금리", "y10")
    add("y30", "30년 국채", "금리", "y30")
    add("real10", "10년 실질금리", "금리", "real10")
    add("dxy", "달러 인덱스 (DXY)", "달러", "dxy", unit="", dec=1)
    add("vix", "VIX 변동성 지수", "위험심리", "vix", unit="", dec=1)
    add("hy", "하이일드 스프레드", "위험심리", "hy", unit="%p")
    add("nfci", "시카고연은 금융여건(NFCI)", "위험심리", "nfci", unit="")
    add("fg", "CNN 공포·탐욕 지수", "위험심리", "fg", unit="", dec=0)
    add("cp", "미국 기업이익 (전년비)", "기업이익", "cp_yoy", dec=1)
    if fs:
        if fs.get("blended") is not None:
            add("fs_blend", "S&P 500 이번 분기 EPS 성장 (실적+예상)", "기업이익", value=fs["blended"], asof=fs["date"], dec=1)
        for k, v in (fs.get("growth") or {}).items():
            add("fs_" + k, f"S&P 500 EPS 성장 예상 · {k}", "기업이익", value=v, asof=fs["date"], dec=1)
        if fs.get("fpe"):
            add("fs_pe", "S&P 500 선행 12개월 PER", "기업이익", value=fs["fpe"], unit="배", asof=fs["date"], dec=1)
    add("gpr", "지정학적 리스크 지수(GPR)", "지정학", "gpr", unit="", dec=0)
    return cards


# ═════════════════════ 실행 ═════════════════════
def main():
    raw, px, fs = collect()
    ref = next((px[k] for k in ("SPY", "^GSPC", "QQQ", "^NDX") if px.get(k) is not None),
               next((v for v in px.values() if v is not None), None))
    if ref is None:
        fetch.log("✗ 가격 데이터를 하나도 받지 못해 중단합니다 (이전 data.json 유지). Yahoo 접속이 막혔는지 확인하세요.")
        sys.exit(1)
    end = R.to_weekly(ref).index[-1]
    grid = pd.date_range(pd.Timestamp(START), end, freq="W-FRI")

    fetch.log("▶ 흐름(국면) 판정")
    W = R.build_indicators(raw, grid)
    V = R.dim_values(W)
    S = R.dim_states(V)
    now = len(grid) - 1
    cur = {k: S[k].iloc[now] for k in S.columns}
    trans = R.transition(V, S, now)
    fed = fed_extras(raw)

    dims_out = []
    for d in DIMS:
        k = d["id"]
        t = trans.get(k, {})
        dims_out.append({"id": k, "name": d["name"], "states": d["states"], "desc": d["desc"],
                         "state": None if not np.isfinite(cur[k]) else int(cur[k]),
                         "value": V[k].iloc[now], "th": d["th"], "age": t.get("age"),
                         "flip_4w": t.get("flip_4w"), "flip_13w": t.get("flip_13w"),
                         "evidence": evidence(k, W, V, raw, fed, fs),
                         "hist": [[i, (None if not np.isfinite(s) else int(s))] for i, s in S[k].iloc[-HISTORY_WEEKS:].items()]})
        fetch.log(f"  {d['name']:8s}: {d['states'][int(cur[k])] if np.isfinite(cur[k]) else '-'}  ({V[k].iloc[now]:.2f})")

    fetch.log("▶ 자산별 같은 국면 과거 통계·검증")
    assets, preds, rets = [], {}, {}
    for a in ASSETS:
        res = run_asset(a, px, raw, S, V, trans, now)
        if not res:
            fetch.log(f"  {a['name']}: 데이터 부족")
            continue
        o, p, r = res
        assets.append(o)
        preds[a["id"]], rets[a["id"]] = p, r
        m = o["h"].get("1m") or {}
        fetch.log(f"  {a['name']:12s} 1개월 {m.get('median', float('nan')):+5.1f}% 상승확률 {m.get('hit', float('nan')):4.0f}% "
                  f"{m.get('signal', '-')}  신뢰도 {o['rel']['label']}")

    ranked = {}
    for hk in HORIZONS:
        rows = [{"id": a["id"], "name": a["name"], "median": a["h"][hk]["median"], "t": a["h"][hk]["t"],
                 "sig": a["h"][hk]["signal"], "rel": a["rel"]["label"]} for a in assets if a["h"].get(hk)]
        wt = {"높음": 1.0, "보통": 0.8}
        ranked[hk] = sorted(rows, key=lambda x: (x["rel"] not in wt, -x["median"]))
    quad, story = narrative(cur, W, V, trans, fed, fs, ranked)

    # 국면 이력 (사분면 구간)
    qs = [R.quad_of(g, i) for g, i in zip(S["growth"].values, S["inflation"].values)]
    runs, start = [], None
    for i, q in enumerate(qs):
        if i == 0 or q != qs[i - 1]:
            if start is not None and qs[start]:
                runs.append([grid[start], grid[i - 1], qs[start]])
            start = i
    if qs[start]:
        runs.append([grid[start], grid[-1], qs[start]])

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "week": grid[-1], "demo": DEMO, "h_label": H_LABEL,
        "regime": {"quad": quad, "story": story, "dims": dims_out, "runs": [r for r in runs if r[1] >= pd.Timestamp("2000-01-01")]},
        "ranked": ranked, "assets": assets, "macro": macro_cards(W, raw, fed, fs),
        "backtest": backtest(preds, rets), "log": fetch.LOG[-120:],
    }
    fetch.log("▶ 전망 vs 실제 비교 (실제 기록 + 과거 재현)")
    tr = track.build(assets, S, rets, grid[-1], {a["id"]: a["name"] for a in assets})
    for hk, sm in tr["summary"].items():
        rp = sm["replay"] or {}
        fetch.log(f"  {hk}: 과거 재현 {rp.get('n', 0)}건, 방향 적중 {rp.get('dir_hit', float('nan')):.0f}%, "
                  f"범위 적중 {rp.get('band_hit', float('nan')):.0f}%, 평균 오차 {rp.get('mae', float('nan')):.1f}%p")
    with open(os.path.join(ROOT, "data", "cache", "track.json"), "w", encoding="utf-8") as f:
        json.dump(clean(tr), f, ensure_ascii=False, separators=(",", ":"))
    out["track_summary"] = tr["summary"]

    path = os.path.join(ROOT, "data.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(clean(out), f, ensure_ascii=False, separators=(",", ":"))
    fetch.log(f"✔ data.json 저장 ({os.path.getsize(path) // 1024} KB)")


if __name__ == "__main__":
    main()
