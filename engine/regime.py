"""국면(레짐) 엔진.

흐름
1. 지표를 주 단위로 정렬(발표 지연 반영) → 8가지 '흐름'의 상태 판정 (예: 물가 상승 / 금리 상승 / 성장 견조)
2. 자산마다, 과거 데이터에서 어떤 흐름이 이후 수익률을 실제로 갈랐는지 측정 → 영향 큰 흐름부터 순서화
   (영향이 통계적으로 약한 흐름은 그 자산 판단에서 제외 = 객관적 가중)
3. 현재와 '같은 흐름 조합'이었던 과거 주들을 찾아 이후 1주·1개월·1분기 수익률 통계
   표본이 부족하면 영향이 가장 약한 흐름부터 하나씩 빼며 조합을 넓힘
4. 각 흐름이 곧 바뀔 확률과, 바뀌면 자산 전망이 어떻게 달라지는지(하락 전환 경고)
5. 워크포워드(그 시점까지의 데이터만 사용)로 예측력 검증
"""
import numpy as np
import pandas as pd

from config import (DIMS, DRIVER_T, HORIZONS, MIN_EPISODES, MIN_WEEKS, REFIT_WEEKS, WF_START)


# ═════════════════════════ 1. 주간 정렬 ═════════════════════════
def to_weekly(s, lag_days=0, how="last"):
    if s is None or len(s.dropna()) == 0:
        return None
    s = s.dropna().copy()
    s.index = pd.DatetimeIndex(s.index) + pd.Timedelta(days=lag_days)
    w = s.resample("W-FRI").mean() if how == "mean" else s.resample("W-FRI").last()
    return w.ffill()


def yoy(s, periods):
    """월/분기 원시 시계열의 전년비(%) — 주간 변환 전에 계산."""
    if s is None:
        return None
    s = s.dropna()
    return (s / s.shift(periods) - 1) * 100


def build_indicators(raw, grid):
    """raw: {FRED ID 또는 키: 원시 시계열}. 반환: 주간 지표 표(그 주에 알 수 있었던 값)."""
    g = lambda k: raw.get(k)  # noqa: E731
    W = {}

    def put(name, s, lag, how="last"):
        w = to_weekly(s, lag, how)
        if w is not None:
            W[name] = w.reindex(grid, method="ffill")

    put("cfnai", g("CFNAIMA3"), 55)
    put("cpi_yoy", yoy(g("CPIAUCSL"), 12), 45)
    put("core_yoy", yoy(g("CPILFESL"), 12), 45)
    put("pce_core_yoy", yoy(g("PCEPILFE"), 12), 60)
    put("gdp_yoy", yoy(g("GDPC1"), 4), 120)
    gdp = g("GDPC1")
    if gdp is not None:
        put("gdp_qoq", ((gdp.dropna() / gdp.dropna().shift(1)) ** 4 - 1) * 100, 120)
    put("cp_yoy", yoy(g("CP"), 4), 150)
    for k, n in [("DGS3MO", "y3m"), ("DGS2", "y2"), ("DGS10", "y10"), ("DGS30", "y30"), ("DFF", "dff"),
                 ("VIXCLS", "vix"), ("BAMLH0A0HYM2", "hy"), ("T10YIE", "bei"), ("DFII10", "real10")]:
        put(n, g(k), 1)
    put("dxy", g("DXY"), 0)
    put("nfci", g("NFCI"), 7)
    put("unrate", g("UNRATE"), 35)
    put("claims", g("ICSA"), 6)
    put("recession", g("RECPROUSM156N"), 65)
    put("fg", g("FEARGREED"), 1)
    if g("GPRD") is not None:
        w = to_weekly(g("GPRD"), 3, how="mean")
        W["gpr"] = w.rolling(4, min_periods=1).mean().reindex(grid, method="ffill")
    return pd.DataFrame(W, index=grid)


def rolling_pct(x, window=260, minp=104):
    def f(a):
        a = a[np.isfinite(a)]
        return (a[:-1] < a[-1]).mean() * 100 if len(a) > 1 else np.nan
    return x.rolling(window, min_periods=minp).apply(f, raw=True)


# ═════════════════════════ 2. 흐름 상태 ═════════════════════════
def dim_values(W):
    c = lambda k: W[k] if k in W else pd.Series(np.nan, index=W.index)  # noqa: E731
    V = pd.DataFrame(index=W.index)
    V["growth"] = c("cfnai")
    infl = pd.concat([c("cpi_yoy"), c("core_yoy")], axis=1).mean(axis=1, skipna=True)
    V["inflation"] = infl - infl.shift(13)
    V["rates"] = c("y10") - c("y10").shift(13)
    fed = c("y2") - c("dff")
    cutting = (c("dff") - c("dff").shift(26)) <= -0.25
    V["fed"] = fed.where(~cutting, np.minimum(fed, -0.5))
    V["dollar"] = (c("dxy") / c("dxy").shift(13) - 1) * 100
    V["risk"] = c("vix").rolling(4, min_periods=2).mean()
    V["earnings"] = c("cp_yoy")
    V["geo"] = rolling_pct(c("gpr")) if "gpr" in W else np.nan
    return V


def dim_states(V):
    """0 = states[0], 1 = states[1]. 기준선 근처(band)에서는 직전 상태 유지."""
    S = pd.DataFrame(index=V.index, dtype="float64")
    for d in DIMS:
        th, band, prev, out = d["th"], d["band"], None, []
        for v in V[d["id"]].values:
            if not np.isfinite(v):
                out.append(np.nan)
                prev = None
                continue
            if prev is None:
                s = 0 if v > th else 1
            elif v > th + band:
                s = 0
            elif v < th - band:
                s = 1
            else:
                s = prev
            out.append(s)
            prev = s
        S[d["id"]] = out
    return S


def quad_of(growth, infl):
    if not (np.isfinite(growth) and np.isfinite(infl)):
        return None
    return {(0, 0): "리플레이션", (0, 1): "골디락스", (1, 0): "스태그플레이션 위험", (1, 1): "둔화·디스인플레이션"}[(int(growth), int(infl))]


QUAD_DESC = {
    "리플레이션": "성장과 물가가 함께 오르는 국면",
    "골디락스": "성장은 견조하고 물가는 식어가는 국면",
    "스태그플레이션 위험": "성장은 둔화되는데 물가는 오르는 국면",
    "둔화·디스인플레이션": "성장과 물가가 함께 식어가는 국면",
}


# ═════════════════════════ 3. 자산 수익률 ═════════════════════════
def asset_returns(asset, px, raw, grid):
    """주간 로그수익률(우선순위대로 이어 붙임)과 표시용 가격."""
    rets, disp = [], None
    for src in asset["src"]:
        if src[0] == "yahoo":
            s = px.get(src[1])
            if s is None or len(s.dropna()) < 30:
                continue
            w = to_weekly(s).reindex(grid)
            w = w.where(w.index >= s.dropna().index[0])
            rets.append(np.log(w / w.shift(1)))
            if disp is None:
                disp = s.dropna()
        elif src[0] == "bond":
            y = raw.get(src[1])
            if y is None:
                continue
            yw = to_weekly(y).reindex(grid) / 100
            r = yw.shift(1) / 52 - src[2] * (yw - yw.shift(1))
            rets.append(np.log1p(r))
    if not rets:
        return None, None
    out = rets[0].copy()
    for r in rets[1:]:
        out = out.fillna(r)
    return out, disp


def fwd_returns(r, h):
    """t 시점 이후 h주 누적 로그수익률."""
    return r[::-1].rolling(h, min_periods=h).sum()[::-1].shift(-1)


# ═════════════════════════ 4. 통계 도구 ═════════════════════════
def episodes(index_pos, gap=8):
    """연속된 주 묶음(서로 다른 시기)으로 나눔."""
    if len(index_pos) == 0:
        return []
    runs, start, prev = [], index_pos[0], index_pos[0]
    for p in index_pos[1:]:
        if p - prev > gap:
            runs.append((start, prev))
            start = p
        prev = p
    runs.append((start, prev))
    return runs


def rank_drivers(S, fwd, h, upto):
    """upto 시점까지 결과가 확인된 표본으로, 각 흐름의 두 상태 간 이후 수익률 차이(t값)."""
    last = upto - h
    if last < 52:
        return []
    St, y = S.iloc[: last + 1], fwd.iloc[: last + 1]
    res = []
    for d in DIMS:
        k = d["id"]
        m = St[k].notna() & y.notna()
        a, b = y[m & (St[k] == 0)], y[m & (St[k] == 1)]
        if len(a) < 26 or len(b) < 26:
            continue
        se = np.sqrt(a.var() / max(len(a) / h, 2) + b.var() / max(len(b) / h, 2))
        t = (a.mean() - b.mean()) / se if se > 0 else 0.0
        res.append({"dim": k, "t": float(t), "diff": float(np.expm1(a.mean()) * 100 - np.expm1(b.mean()) * 100),
                    "mean0": float(np.expm1(a.mean()) * 100), "mean1": float(np.expm1(b.mean()) * 100)})
    res.sort(key=lambda r: -abs(r["t"]))
    return res


def cond_stats(S, fwd, h, order, now, cur, hkey, flip=None):
    """현재 조합(cur)과 같은 과거 주들의 이후 h주 수익률 통계. 표본 부족 시 약한 흐름부터 제외."""
    last = now - h
    base_mask = fwd.notna().values & (np.arange(len(fwd)) <= last)
    y = fwd.values
    cur = dict(cur)
    if flip:
        cur[flip] = 1 - cur[flip]
    order = [k for k in order if np.isfinite(cur.get(k, np.nan))]
    base = y[base_mask]
    for k in range(len(order), -1, -1):
        dims = order[:k]
        m = base_mask.copy()
        for dk in dims:
            m &= (S[dk].values == cur[dk])
        pos = np.where(m)[0]
        eps = episodes(pos)
        if k > 0 and (len(pos) < MIN_WEEKS[hkey] or len(eps) < MIN_EPISODES):
            continue
        if len(pos) < 10:
            return None
        v = np.expm1(y[pos]) * 100
        sd = v.std(ddof=1)
        t = v.mean() / (sd / np.sqrt(max(len(v) / h, 2))) if sd > 0 else 0.0
        ep_list = []
        for a, b in eps:
            seg = np.expm1(y[pos[(pos >= a) & (pos <= b)]]) * 100
            ep_list.append({"start": S.index[a], "end": S.index[b], "weeks": int(((pos >= a) & (pos <= b)).sum()),
                            "avg": float(seg.mean())})
        bm = float(np.expm1(base).mean() * 100) if len(base) else 0.0
        t_ex = (v.mean() - bm) / (sd / np.sqrt(max(len(v) / h, 2))) if sd > 0 else 0.0
        return {
            "dims": [{"id": dk, "state": int(cur[dk])} for dk in dims],
            "t_ex": float(t_ex),
            "n": int(len(v)), "episodes": len(eps),
            "mean": float(v.mean()), "median": float(np.median(v)),
            "p25": float(np.percentile(v, 25)), "p75": float(np.percentile(v, 75)),
            "hit": float((v > 0).mean() * 100), "t": float(t),
            "ep_pos": float(np.mean([e["avg"] > 0 for e in ep_list]) * 100),
            "base_mean": float((np.expm1(base) * 100).mean()) if len(base) else None,
            "eps": sorted(ep_list, key=lambda e: e["start"], reverse=True)[:8],
        }
    return None


def signal_of(st):
    if not st:
        return "데이터 부족"
    hit, t, med = st["hit"], st["t"], st["median"]
    tx = st.get("t_ex", 0)
    if hit >= 62 and t >= 2 and med > 0 and tx >= 1:
        return "강한 상승"
    if hit >= 55 and med > 0:
        return "상승 우위"
    if hit <= 38 and t <= -2 and med < 0 and tx <= -1:
        return "강한 하락"
    if hit <= 45 and med < 0:
        return "하락 우위"
    return "중립"


# ═════════════════════════ 5. 워크포워드 ═════════════════════════
def walk_forward(S, fwd, h, hkey):
    """각 시점에서 그때까지 결과가 확인된 데이터만으로 계산한 '조건부 기대수익'(표본 외)."""
    N = len(S)
    pred = np.full(N, np.nan)
    y = fwd.values
    yv = np.isfinite(y)
    y0 = np.where(yv, y, 0.0)
    cum_all_c, cum_all_s = np.cumsum(yv), np.cumsum(y0)
    start = int(np.searchsorted(S.index.values, np.datetime64(pd.Timestamp(WF_START))))
    codes = {d["id"]: S[d["id"]].values for d in DIMS}
    for r in range(start, N, REFIT_WEEKS):
        end = min(r + REFIT_WEEKS, N)
        drivers = [x["dim"] for x in rank_drivers(S, fwd, h, r) if abs(x["t"]) >= DRIVER_T]
        best = np.full(end - r, np.nan)
        # 많은 흐름을 쓴 조합부터 채우고, 표본이 모자란 주만 더 넓은 조합으로
        for k in range(len(drivers), 0, -1):
            key = np.zeros(N, dtype=np.int64)
            ok = np.ones(N, dtype=bool)
            for i, dk in enumerate(drivers[:k]):
                c = codes[dk]
                ok &= np.isfinite(c)
                key += np.where(np.isfinite(c), c, 0).astype(np.int64) << i
            key = np.where(ok, key, -1)
            for q in np.unique(key[r:end]):
                if q < 0:
                    continue
                P = np.where(key == q)[0]
                C, Sm = np.cumsum(yv[P]), np.cumsum(y0[P])
                T = P[(P >= r) & (P < end)]
                idx = np.searchsorted(P, T - h, side="right") - 1
                cnt = np.where(idx >= 0, C[np.maximum(idx, 0)], 0)
                sm = np.where(idx >= 0, Sm[np.maximum(idx, 0)], 0.0)
                fill = (cnt >= MIN_WEEKS[hkey]) & np.isnan(best[T - r])
                best[(T - r)[fill]] = sm[fill] / cnt[fill]
        # 조건이 하나도 안 맞으면 전체 평균
        t_idx = np.arange(r, end) - h
        cc = np.where(t_idx >= 0, cum_all_c[np.maximum(t_idx, 0)], 0)
        ss = np.where(t_idx >= 0, cum_all_s[np.maximum(t_idx, 0)], 0.0)
        uncond = np.where(cc > 0, ss / np.maximum(cc, 1), np.nan)
        pred[r:end] = np.where(np.isnan(best), uncond, best)
    return pd.Series(pred, index=S.index)


def spearman(a, b):
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 30:
        return np.nan
    ra, rb = pd.Series(a[m]).rank().values, pd.Series(b[m]).rank().values
    if ra.std() == 0 or rb.std() == 0:
        return np.nan
    return float(np.corrcoef(ra, rb)[0, 1])


def reliability(ic):
    if ic is None or not np.isfinite(ic):
        return "검증 불가"
    return "높음" if ic >= 0.10 else "보통" if ic >= 0.05 else "낮음"


# ═════════════════════════ 6. 흐름 전환 확률 ═════════════════════════
def transition(V, S, now):
    out = {}
    for d in DIMS:
        k = d["id"]
        v, s = V[k].values, S[k].values
        if not np.isfinite(s[now]):
            continue
        cur = s[now]
        age = 0
        while now - age - 1 >= 0 and s[now - age - 1] == cur:
            age += 1
        scale = np.nanstd(V[k].diff(13).values)
        scale = scale if np.isfinite(scale) and scale > 0 else 1.0
        dist = (v[now] - d["th"]) / scale
        bucket = lambda x: np.digitize(np.abs(x), [0.5, 1.0, 2.0])  # noqa: E731
        dists = (v - d["th"]) / scale
        res = {"state": int(cur), "age": int(age + 1), "value": float(v[now]), "dist": float(dist)}
        for hk, h in (("4w", 4), ("13w", 13)):
            flips = []
            b_now = bucket(dist)
            for t in range(0, now - h):
                if s[t] == cur and np.isfinite(dists[t]) and bucket(dists[t]) == b_now:
                    w = s[t + 1: t + h + 1]
                    if np.all(np.isfinite(w)):
                        flips.append(np.any(w != cur))
            res[f"flip_{hk}"] = float(np.mean(flips) * 100) if len(flips) >= 20 else None
        out[k] = res
    return out
