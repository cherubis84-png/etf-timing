"""데이터 수집. 실패하면 data/cache 의 이전 값을 그대로 사용합니다."""
import io
import os
import re
import time
from datetime import datetime, timezone

import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
                    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15"}
CACHE = os.path.join(os.path.dirname(__file__), "..", "data", "cache")
os.makedirs(CACHE, exist_ok=True)
LOG = []


def log(msg):
    print(msg, flush=True)
    LOG.append(msg)


def _get(url, tries=3, **kw):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers={**UA, **kw.pop("headers", {})}, timeout=40, **kw)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            last = str(e)
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"{url} 실패: {last}")


def _cache_path(key):
    return os.path.join(CACHE, re.sub(r"[^A-Za-z0-9_-]", "_", key) + ".csv")


def _save(key, s):
    s.dropna().to_frame("value").to_csv(_cache_path(key), index_label="date")


def _load(key):
    p = _cache_path(key)
    if not os.path.exists(p):
        return None
    df = pd.read_csv(p, parse_dates=["date"], index_col="date")
    return df["value"]


def cached(key, fn, merge=False):
    """fn()으로 받아오고 캐시에 저장. 실패 시 캐시 사용. merge=True면 과거 캐시와 합쳐 이력을 늘림."""
    try:
        s = fn().dropna().sort_index()
        s = s[~s.index.duplicated(keep="last")]
        if merge:
            old = _load(key)
            if old is not None:
                s = pd.concat([old[~old.index.isin(s.index)], s]).sort_index()
        _save(key, s)
        log(f"  ✓ {key}: {len(s)}개, 마지막 {s.index[-1].date()}")
        return s
    except Exception as e:  # noqa: BLE001
        old = _load(key)
        if old is not None:
            log(f"  ⚠ {key}: 수집 실패 → 캐시 사용 ({e})")
            return old
        log(f"  ✗ {key}: 수집 실패, 캐시 없음 ({e})")
        return None


# ── FRED ─────────────────────────────────────────────────────
def fred(series_id):
    key = os.environ.get("FRED_API_KEY", "").strip()

    def go():
        if key:
            r = _get("https://api.stlouisfed.org/fred/series/observations",
                     params={"series_id": series_id, "api_key": key, "file_type": "json"})
            obs = r.json()["observations"]
            s = pd.Series({pd.Timestamp(o["date"]): o["value"] for o in obs})
        else:
            r = _get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}")
            df = pd.read_csv(io.StringIO(r.text))
            s = pd.Series(df.iloc[:, 1].values, index=pd.to_datetime(df.iloc[:, 0]))
        return pd.to_numeric(s, errors="coerce")

    return cached(f"fred_{series_id}", go)


# ── 지정학적 리스크 (Caldara & Iacoviello) ─────────────────────
def gpr_daily():
    def go():
        urls = ["https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls"]
        try:
            page = _get("https://www.matteoiacoviello.com/gpr.htm").text
            for m in re.findall(r'href="([^"]*daily_recent[^"]*\.xls)"', page):
                urls.append(m if m.startswith("http") else "https://www.matteoiacoviello.com/" + m.lstrip("/"))
        except Exception:  # noqa: BLE001
            pass
        err = None
        for u in urls:
            try:
                df = pd.read_excel(io.BytesIO(_get(u).content))
                cols = {c.lower(): c for c in df.columns}
                if "date" in cols:
                    idx = pd.to_datetime(df[cols["date"]], errors="coerce")
                else:
                    idx = pd.to_datetime(df[cols["day"]].astype(str), format="%Y%m%d", errors="coerce")
                return pd.Series(pd.to_numeric(df[cols["gprd"]], errors="coerce").values, index=idx).dropna()
            except Exception as e:  # noqa: BLE001
                err = e
        raise RuntimeError(err)

    return cached("gpr_daily", go)


# ── CNN 공포·탐욕 (비공식 엔드포인트, 실패 가능) ───────────────
def cnn_fear_greed():
    def go():
        r = _get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata/2018-01-01",
                 headers={"Referer": "https://edition.cnn.com/", "Origin": "https://edition.cnn.com"})
        pts = r.json()["fear_and_greed_historical"]["data"]
        s = pd.Series({pd.Timestamp(p["x"], unit="ms").normalize(): float(p["y"]) for p in pts})
        return s
    # CNN은 1년치 정도만 주므로 매번 캐시에 이어 붙여 이력을 쌓음
    return cached("cnn_feargreed", go, merge=True)


# ── S&P 500 실적 예상 (FactSet Earnings Insight 주간 보고서) ─────
def factset():
    """최근 FactSet 보고서에서 분기·연간 EPS 성장 예상과 선행 PER을 읽음. 실패 시 직전 값 유지."""
    import json
    path = os.path.join(CACHE, "factset.json")
    base = ("https://advantage.factset.com/hubfs/Website/Resources%20Section/"
            "Research%20Desk/Earnings%20Insight/EarningsInsight_{}.pdf")
    today = pd.Timestamp.today().normalize()
    try:
        from pypdf import PdfReader
        for back in range(0, 22):
            d = today - pd.Timedelta(days=back)
            if d.weekday() not in (3, 4):          # 보통 금요일(휴일 전엔 목요일) 발행
                continue
            try:
                r = _get(base.format(d.strftime("%m%d%y")), tries=1)
            except Exception:  # noqa: BLE001
                continue
            text = " ".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(r.content)).pages[:6])
            text = re.sub(r"\s+", " ", text)
            out = {"date": str(d.date()), "growth": {}}
            for per, g in re.findall(r"For (Q[1-4] \d{4}|CY ?\d{4}), analysts are projecting earnings growth (?:rate )?of (-?[\d.]+)%", text):
                out["growth"][per.replace("CY", "CY ").replace("  ", " ")] = float(g)
            m = re.search(r"blended (?:\(year-over-year\) )?earnings growth rate for the S&P 500 is (-?[\d.]+)%", text)
            if m:
                out["blended"] = float(m.group(1))
            m = re.search(r"forward 12-month P/E ratio for the S&P 500 is ([\d.]+)", text)
            if m:
                out["fpe"] = float(m.group(1))
            if out["growth"] or "fpe" in out:
                with open(path, "w") as f:
                    json.dump(out, f, ensure_ascii=False)
                log(f"  ✓ FactSet 실적 예상 {out['date']}: {out['growth']}")
                return out
        raise RuntimeError("최근 3주 보고서를 찾지 못함")
    except Exception as e:  # noqa: BLE001
        if os.path.exists(path):
            log(f"  ⚠ FactSet: 수집 실패 → 직전 값 사용 ({e})")
            return json.load(open(path))
        log(f"  ✗ FactSet: 수집 실패 ({e})")
        return None


# ── 가격 (Yahoo → Stooq 백업), 배당 반영 수정주가 ───────────────
def price(symbol):
    def yahoo():
        now = int(datetime.now(timezone.utc).timestamp())
        r = _get(f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}",
                 params={"period1": 0, "period2": now, "interval": "1d", "events": "div,split"})
        res = r.json()["chart"]["result"][0]
        ts = pd.to_datetime(res["timestamp"], unit="s").normalize()
        ind = res["indicators"]
        vals = ind.get("adjclose", [{}])[0].get("adjclose") or ind["quote"][0]["close"]
        return pd.Series(vals, index=ts, dtype="float64")

    def stooq():
        special = {"^GSPC": "^spx", "^NDX": "^ndx", "^RUT": "^rut", "^SOX": "^sox", "DX-Y.NYB": "dx.f",
                   "GC=F": "gc.f", "SI=F": "si.f", "CL=F": "cl.f"}
        code = special.get(symbol) or symbol.lower().replace("-usd", "usd")
        if code == symbol.lower() and not code.endswith("usd"):
            code += ".us"
        r = _get(f"https://stooq.com/q/d/l/?s={code}&i=d")
        df = pd.read_csv(io.StringIO(r.text))
        return pd.Series(df["Close"].values, index=pd.to_datetime(df["Date"]))

    def go():
        try:
            return yahoo()
        except Exception as e:  # noqa: BLE001
            log(f"    Yahoo 실패({symbol}): {e} → Stooq 시도")
            return stooq()

    return cached(f"px_{symbol}", go)
