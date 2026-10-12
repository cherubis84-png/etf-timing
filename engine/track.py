"""전망 vs 실제 비교.

1) 실제 기록 (live): 매 실행 때 앱이 보여준 전망을 data/forecast_log.json 에 저장해 두었다가,
   기간(1주·1개월·1분기)이 지나면 실제 수익률과 맞춰 봅니다. 오늘부터 쌓입니다.
2) 과거 재현 (replay): 과거 각 주에 '그 시점까지의 데이터만으로' 앱과 똑같은 계산을 다시 돌려
   당시 전망을 만들고 실제 결과와 비교합니다. 기록이 쌓이기 전에도 바로 볼 수 있습니다.
   (단, 경제지표는 나중에 수정된 값 기준이라 실제 당시 숫자와 약간 다를 수 있음)
"""
import json
import os

import numpy as np
import pandas as pd

import regime as R
from config import DRIVER_T, HORIZONS

ROOT = os.path.join(os.path.dirname(__file__), "..")
LOG_PATH = os.path.join(ROOT, "data", "cache", "forecast_log.json")   # data/cache 는 워크플로가 자동 저장
REPLAY_WEEKS = {"1w": 52, "1m": 104, "1q": 156}


def _forecast_at(S, fwd, h, hk, t):
    drivers = R.rank_drivers(S, fwd, h, t)
    order = [d["dim"] for d in drivers if abs(d["t"]) >= DRIVER_T]
    cur = {k: S[k].iloc[t] for k in S.columns}
    st = R.cond_stats(S, fwd, h, order, t, cur, hk)
    if st:
        st["signal"] = R.signal_of(st)
    return st


def _row(week, st, actual):
    return [str(week.date()), round(st["median"], 2), round(st["p25"], 2), round(st["p75"], 2),
            None if actual is None else round(actual, 2), st["signal"], round(st["hit"], 0)]


# ─────────────────────── 과거 재현 ───────────────────────
def replay(S, rets):
    """rets: {asset_id: 주간 로그수익률}. 반환 {asset_id: {hk: [[주, 중앙값, p25, p75, 실제, 신호, 상승확률], ...]}}"""
    out = {}
    now = len(S) - 1
    for aid, r in rets.items():
        out[aid] = {}
        for hk, h in HORIZONS.items():
            fwd = R.fwd_returns(r, h)
            rows = []
            first = max(0, now - h - REPLAY_WEEKS[hk] + 1)
            for t in range(first, now - h + 1):
                if not np.isfinite(fwd.iloc[t]):
                    continue
                st = _forecast_at(S, fwd, h, hk, t)
                if st:
                    rows.append(_row(S.index[t], st, float(np.expm1(fwd.iloc[t]) * 100)))
            out[aid][hk] = rows
    return out


# ─────────────────────── 실제 기록 ───────────────────────
def load_log():
    try:
        with open(LOG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return []


def update_log(assets, week, rets):
    """이번 주 전망 저장(같은 주는 최신 값으로 덮어씀) + 기간이 지난 전망의 실제 수익률 채우기."""
    log = load_log()
    wk = str(pd.Timestamp(week).date())
    keep = [e for e in log if e["week"] != wk]
    for a in assets:
        for hk, st in a["h"].items():
            if st:
                keep.append({"week": wk, "asset": a["id"], "hk": hk, "median": round(st["median"], 2),
                             "p25": round(st["p25"], 2), "p75": round(st["p75"], 2),
                             "hit": round(st["hit"], 0), "signal": st["signal"], "actual": None})
    for e in keep:
        if e["actual"] is not None or e["asset"] not in rets:
            continue
        r = rets[e["asset"]]
        h = HORIZONS[e["hk"]]
        try:
            i = r.index.get_loc(pd.Timestamp(e["week"]))
        except KeyError:
            continue
        seg = r.iloc[i + 1: i + 1 + h]
        if len(seg) == h and seg.notna().all():
            e["actual"] = round(float(np.expm1(seg.sum()) * 100), 2)
            e["due"] = str(r.index[i + h].date())
        else:
            e["due"] = str((pd.Timestamp(e["week"]) + pd.Timedelta(weeks=h)).date())
    keep.sort(key=lambda e: (e["week"], e["asset"], e["hk"]))
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(keep, f, ensure_ascii=False, separators=(",", ":"))
    return keep


# ─────────────────────── 요약 통계 ───────────────────────
UP = ("강한 상승", "상승 우위")
DN = ("강한 하락", "하락 우위")


def summarize(rows):
    """rows: [[주, 중앙값, p25, p75, 실제, 신호, 상승확률], ...] (실제값 있는 것만)"""
    rows = [x for x in rows if x[4] is not None]
    if len(rows) < 5:
        return None
    med = np.array([x[1] for x in rows])
    lo = np.array([x[2] for x in rows])
    hi = np.array([x[3] for x in rows])
    act = np.array([x[4] for x in rows])
    sig = [x[5] for x in rows]
    called = np.abs(med) > 0.05
    up = np.array([s in UP for s in sig])
    dn = np.array([s in DN for s in sig])
    return {
        "n": len(rows), "since": rows[0][0],
        "dir_hit": float(((np.sign(med) == np.sign(act)) & called).sum() / max(called.sum(), 1) * 100),
        "band_hit": float(((act >= lo) & (act <= hi)).mean() * 100),
        "mae": float(np.abs(act - med).mean()),
        "bias": float((act - med).mean()),
        "up_n": int(up.sum()), "up_avg": float(act[up].mean()) if up.any() else None,
        "up_hit": float((act[up] > 0).mean() * 100) if up.any() else None,
        "dn_n": int(dn.sum()), "dn_avg": float(act[dn].mean()) if dn.any() else None,
        "dn_hit": float((act[dn] < 0).mean() * 100) if dn.any() else None,
        "all_avg": float(act.mean()),
    }


def build(assets, S, rets, week, names):
    """data.json 과 별도로 track.json 을 만들 내용."""
    log = update_log(assets, week, rets)
    rep = replay(S, rets)
    live_rows = {hk: [] for hk in HORIZONS}
    for e in log:
        live_rows[e["hk"]].append([e["week"], e["median"], e["p25"], e["p75"], e["actual"], e["signal"], e["hit"]])
    out = {"week": str(pd.Timestamp(week).date()), "names": names,
           "replay": rep, "live": log[-2000:], "summary": {}}
    for hk in HORIZONS:
        allrows = [row for aid in rep for row in rep[aid][hk]]
        allrows.sort(key=lambda x: x[0])
        per = {}
        for aid in rep:
            s = summarize(rep[aid][hk])
            if s:
                per[aid] = {k: s[k] for k in ("n", "dir_hit", "band_hit", "mae", "bias")}
        out["summary"][hk] = {"replay": summarize(allrows), "live": summarize(live_rows[hk]), "per_asset": per}
    return out
