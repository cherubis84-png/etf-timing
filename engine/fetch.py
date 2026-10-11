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


_FAILS = {}          # 호스트별 연속 실패 횟수 — 막힌 사이트는 빨리 건너뛰기


def _get(url, tries=2, **kw):
    from urllib.parse import urlparse
    host = urlparse(url).netloc
    if _FAILS.get(host, 0) >= 6:
        raise RuntimeError(f"{host} 연속 실패로 건너뜀")
    extra = kw.pop("headers", {})
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers={**UA, **extra}, timeout=(8, 25), **kw)
            if r.status_code == 200:
                _FAILS[host] = 0
                return r
            last = f"HTTP {r.status_code}"
            if r.status_code in (401, 403, 404):
                break
        except Exception as e:  # noqa: BLE001
            last = str(e)[:120]
        time.sleep(1.5 * (i + 1))
    _FAILS[host] = _FAILS.get(host, 0) + 1
    raise RuntimeError(f"{url[:80]} 실패: {last}")


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
    """FRED 공식 API(키 필요)를 먼저 쓰고, 안 되면 웹 CSV로. GitHub 서버에선 웹 CSV가 자주 막힘."""
    key = os.environ.get("FRED_API_KEY", "").strip()

    def via_api():
        r = _get("https://api.stlouisfed.org/fred/series/observations",
                 params={"series_id": series_id, "api_key": key, "file_type": "json"})
        obs = r.json()["observations"]
        return pd.Series({pd.Timestamp(o["date"]): o["value"] for o in obs})

    def via_csv():
        r = _get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}", tries=1)
        df = pd.read_csv(io.StringIO(r.text))
        return pd.Series(df.iloc[:, 1].values, index=pd.to_datetime(df.iloc[:, 0]))

    def go():
        if key:
            try:
                return pd.to_numeric(via_api(), errors="coerce")
            except Exception as e:  # noqa: BLE001
                log(f"    FRED API 실패({series_id}): {e} → 웹 CSV 시도")
        return pd.to_numeric(via_csv(), errors="coerce")

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
        h = {"Referer": "https://edition.cnn.com/", "Origin": "https://edition.cnn.com", "Accept": "application/json"}
        try:
            r = _get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", headers=h)
        except Exception:  # noqa: BLE001
            r = _get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata/"
                     + (pd.Timestamp.today() - pd.Timedelta(days=365)).strftime("%Y-%m-%d"), headers=h)
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
        last_err = ""
        for back in range(0, 22):
            d = today - pd.Timedelta(days=back)
            if d.weekday() not in (3, 4):          # 보통 금요일(휴일 전엔 목요일) 발행
                continue
            url = base.format(d.strftime("%m%d%y"))
            r = None
            try:   # 브라우저 흉내(curl_cffi) — 일반 요청은 GitHub 서버에서 404로 막힘
                from curl_cffi import requests as creq
                rr = creq.get(url, impersonate="chrome", timeout=30)
                if rr.status_code == 200 and rr.content[:4] == b"%PDF":
                    r = rr
                else:
                    last_err = f"HTTP {rr.status_code}"
            except Exception as e:  # noqa: BLE001
                last_err = str(e)[-60:]
            if r is None:
                try:
                    r = _get(url, tries=1)
                except Exception as e:  # noqa: BLE001
                    last_err = str(e)[-60:]
                    continue
            text = " ".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(r.content)).pages[:6])
            text = re.sub(r"\s+", " ", text)
            out = {"date": str(d.date()), "growth": {}}
            for per, g in re.findall(r"For (Q[1-4] \d{4}|CY ?\d{4}), analysts are projecting earnings growth (?:rate )?of (-?[\d.]+)%", text):
                out["growth"][per.replace("CY", "CY ").replace("  ", " ")] = float(g)
            m = re.search(r"(?:blended|estimated) (?:\(year-over-year\) )?earnings growth rate for the S&P 500 is (-?[\d.]+)%", text)
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
        raise RuntimeError(f"최근 3주 보고서를 받지 못함 — 마지막 오류: {last_err}")
    except Exception as e:  # noqa: BLE001
        if os.path.exists(path):
            log(f"  ⚠ FactSet: 수집 실패 → 직전 값 사용 ({e})")
            return json.load(open(path))
        log(f"  ✗ FactSet: 수집 실패 ({e})")
        return None


# ── 가격 ─────────────────────────────────────────────────────
# 1순위 yfinance(브라우저 흉내로 Yahoo 차단을 피함, 여러 종목 한 번에)
# 2순위 Yahoo 직접 호출(천천히 하나씩)
# 3순위 FRED에 있는 대체 시계열(지수·원유·달러·코인)
# 그래도 없으면 data/cache 의 직전 값
FRED_PROXY = {"^GSPC": "SP500", "^NDX": "NASDAQ100", "CL=F": "DCOILWTICO",
              "DX-Y.NYB": "DTWEXBGS", "BTC-USD": "CBBTCUSD", "ETH-USD": "CBETHUSD"}


def _yf_batch(symbols):
    out = {}
    try:
        import yfinance as yf
    except Exception as e:  # noqa: BLE001
        log(f"    yfinance 사용 불가: {e}")
        return out
    for i in range(0, len(symbols), 8):
        chunk = symbols[i:i + 8]
        try:
            df = yf.download(chunk, period="max", interval="1d", auto_adjust=True,
                             progress=False, threads=False, group_by="column")
            close = df["Close"] if "Close" in df else df
            if isinstance(close, pd.Series):
                close = close.to_frame(chunk[0])
            for sym in chunk:
                if sym in close.columns:
                    ser = pd.to_numeric(close[sym], errors="coerce").dropna()
                    if len(ser) > 30:
                        ser.index = pd.DatetimeIndex(ser.index).tz_localize(None).normalize()
                        out[sym] = ser
        except Exception as e:  # noqa: BLE001
            log(f"    yfinance 실패({','.join(chunk)}): {str(e)[:100]}")
        time.sleep(2)
    return out


def _yahoo_one(symbol):
    now = int(datetime.now(timezone.utc).timestamp())
    r = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
             params={"period1": 0, "period2": now, "interval": "1d", "events": "div,split"})
    res = r.json()["chart"]["result"][0]
    ts = pd.to_datetime(res["timestamp"], unit="s").normalize()
    ind = res["indicators"]
    vals = ind.get("adjclose", [{}])[0].get("adjclose") or ind["quote"][0]["close"]
    return pd.Series(vals, index=ts, dtype="float64")


def prices(symbols):
    """여러 종목 가격을 한 번에. 반환 {종목: 일별 종가 Series 또는 None}"""
    got = _yf_batch(list(symbols))
    log(f"  yfinance: {len(got)}/{len(symbols)}개 성공")
    result = {}
    for sym in symbols:
        def go(sym=sym):
            if sym in got:
                return got[sym]
            try:
                time.sleep(1.0)
                return _yahoo_one(sym)
            except Exception as e:  # noqa: BLE001
                if sym in FRED_PROXY:
                    log(f"    {sym}: Yahoo 실패 → FRED {FRED_PROXY[sym]} 로 대체")
                    s = fred(FRED_PROXY[sym])
                    if s is not None and len(s.dropna()) > 30:
                        return s.dropna()
                raise e
        result[sym] = cached(f"px_{sym}", go)
    return result


def price(symbol):
    return prices([symbol])[symbol]
