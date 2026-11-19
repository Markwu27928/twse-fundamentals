#!/usr/bin/env python3
"""技術指標：RSI(14)、布林通道(20, 2)、MACD(12, 26, 9)，輸出 data/technical.json。

歷史收盤價：
  上市：證交所每日收盤行情 MI_INDEX（依日期，一次全部個股）
  上櫃：櫃買中心每日收盤行情（依日期；新網站 JSON，備援舊網站 JSON）
  每個交易日存成 data/prices/YYYYMMDD.json：{"上市": {代號: 收盤}, "上櫃": {...}}，只新增不改寫
核對：最新一天的收盤價必須與主程式抓到的收盤價（data/companies.json）一致 ≥ 95%，否則判定欄位對應錯誤。

限制：使用未還原股價。除權息、減資會造成價格跳空，可能讓指標出現假訊號（例如除息後看似跌破布林下軌）。
程式會偵測單日跌幅超過 JUMP_PCT 且當天沒有跌停的情況，標示「近期價格跳空」並停用該股的技術面加減分。
"""
import datetime
import json
import pathlib
import re
import sys
import time
from zoneinfo import ZoneInfo

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
PDIR = ROOT / "data" / "prices"
OUT = ROOT / "data" / "technical.json"
DIAG = ROOT / "data" / "technical_diag.json"
TZ = ZoneInfo("Asia/Taipei")
UA = {"User-Agent": "Mozilla/5.0 (fundamentals-bot)", "Accept": "application/json"}
TARGET_DAYS = 80          # 保留的交易日數（MACD 需要 26＋9 天，再加上暖機）
MIN_DAYS = 60             # 少於這麼多天不計算 MACD／RSI（指數平滑需要暖機）
LOOKBACK_CAL = 130
MIN_ROWS = {"上市": 600, "上櫃": 400}
MIN_MATCH = 0.95
JUMP_PCT = -11.0          # 台股跌停 10%；單日跌幅超過 11% 只可能是除權息、減資等，不是正常交易
MAX_FETCH_PER_RUN = 90


# ---------- 抓取 ----------

def _num(v):
    s = re.sub(r"<[^>]*>", "", str(v)).replace(",", "").strip()
    try:
        x = float(s)
    except ValueError:
        return None
    return x if x > 0 else None


def _code(v):
    c = re.sub(r"\s+", "", re.sub(r"<[^>]*>", "", str(v)))
    return c if re.fullmatch(r"[1-9]\d{3}", c) and not c.startswith("91") else None


def get_json(url):
    last = None
    for i in range(2):
        try:
            r = requests.get(url, headers=UA, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(4 * (i + 1))
    raise RuntimeError(str(last)[:160])


def closes_from_tables(j, code_names=("證券代號", "代號", "股票代號"), close_names=("收盤價", "收盤")):
    """從 {tables:[{fields,data}]} 或 {fields,data} 取出 {代號: 收盤價}。"""
    tables = []
    if isinstance(j, dict):
        if j.get("fields") and j.get("data"):
            tables.append((j["fields"], j["data"]))
        for t in j.get("tables") or []:
            if isinstance(t, dict) and t.get("fields") and t.get("data"):
                tables.append((t["fields"], t["data"]))
    best = {}
    for fields, rows in tables:
        names = [re.sub(r"\s+", "", str(f)) for f in fields]
        ci = next((i for i, n in enumerate(names) if n in code_names), None)
        pi = next((i for i, n in enumerate(names) if n in close_names), None)
        if ci is None or pi is None:
            continue
        out = {}
        for r in rows:
            if len(r) <= max(ci, pi):
                continue
            c, p = _code(r[ci]), _num(r[pi])
            if c and p:
                out[c] = p
        if len(out) > len(best):
            best = out
    return best


def closes_from_aadata(j):
    """櫃買舊網站：{"aaData": [[代號, 名稱, 收盤, 漲跌, 開盤, ...], ...]}（沒有欄位名，依位置）。"""
    out = {}
    for r in (j.get("aaData") or []) if isinstance(j, dict) else []:
        if len(r) > 2:
            c, p = _code(r[0]), _num(r[2])
            if c and p:
                out[c] = p
    return out


def fetch_day(market, d):
    """回傳 (收盤價 dict 或 None, 狀態 ok/nodata/fail, 嘗試紀錄)。"""
    ymd, roc = d.strftime("%Y%m%d"), f"{d.year - 1911}/{d:%m/%d}"
    slash = f"{d.year}%2F{d:%m}%2F{d:%d}"
    if market == "上市":
        cands = [("tables", f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={ymd}&type=ALLBUT0999&response=json"),
                 ("tables", f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={ymd}&type=ALLBUT0999")]
    else:
        cands = [("tables", f"https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date={slash}&id=&response=json"),
                 ("aadata", f"https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php?l=zh-tw&o=json&d={roc}")]
    tried, nodata = [], False
    for kind, url in cands:
        try:
            j = get_json(url)
        except Exception as e:  # noqa: BLE001
            tried.append({"url": url, "error": str(e)})
            time.sleep(2)
            continue
        data = closes_from_tables(j) if kind == "tables" else closes_from_aadata(j)
        info = {"url": url, "rows": len(data)}
        if isinstance(j, dict):
            info["stat"] = j.get("stat")
            info["keys"] = list(j.keys())[:8]
            if j.get("tables"):
                info["fields"] = [t.get("fields") for t in j["tables"] if isinstance(t, dict) and t.get("fields")][:3]
        tried.append(info)
        if len(data) >= MIN_ROWS[market]:
            return data, "ok", tried
        if len(data) == 0:
            nodata = True
        time.sleep(2)
    return None, "nodata" if nodata else "fail", tried


# ---------- 指標 ----------

def rsi(closes, n=14):
    """Wilder RSI：第一個平均用前 n 天簡單平均，之後 avg = (prev*(n-1) + 當日)/n。"""
    if len(closes) < n + 1:
        return None
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        ag = (ag * (n - 1) + g) / n
        al = (al * (n - 1) + l) / n
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def bollinger(closes, n=20, k=2.0):
    """布林通道：中線＝n 日均線，上下軌＝中線 ± k 倍母體標準差。%B＝(收盤−下軌)/(上軌−下軌)。"""
    if len(closes) < n:
        return None
    w = closes[-n:]
    mid = sum(w) / n
    sd = (sum((x - mid) ** 2 for x in w) / n) ** 0.5
    up, lo = mid + k * sd, mid - k * sd
    pb = (closes[-1] - lo) / (up - lo) if up > lo else 0.5
    return {"mid": mid, "upper": up, "lower": lo, "pct_b": pb, "width": (up - lo) / mid * 100 if mid else None}


def ema_series(xs, n):
    """EMA：以前 n 個值的簡單平均為起點，alpha＝2/(n+1)。回傳與 xs 等長的序列（前 n-1 個為 None）。"""
    if len(xs) < n:
        return [None] * len(xs)
    a = 2 / (n + 1)
    out = [None] * (n - 1)
    e = sum(xs[:n]) / n
    out.append(e)
    for x in xs[n:]:
        e = a * x + (1 - a) * e
        out.append(e)
    return out


def macd(closes, fast=12, slow=26, sig=9):
    """MACD 線＝EMA12−EMA26；訊號線＝MACD 的 EMA9；柱狀體＝MACD−訊號線。回傳最新值與最近交叉。"""
    if len(closes) < slow + sig:
        return None
    ef, es = ema_series(closes, fast), ema_series(closes, slow)
    line = [f - s for f, s in zip(ef, es) if f is not None and s is not None]
    sigs = ema_series(line, sig)
    hist = [m - s for m, s in zip(line, sigs) if s is not None]
    line_v = line[len(line) - len(hist):]
    if len(hist) < 2:
        return None
    # 最近一次交叉：柱狀體由負轉正（黃金交叉）或由正轉負（死亡交叉），往回最多看 5 天
    # 計算誤差造成的極小值（例如 1e-16）視為 0，避免漏判或誤判交叉
    eps = 1e-9 * max(1.0, abs(closes[-1]))

    def sgn(x):
        return 0 if abs(x) <= eps else (1 if x > 0 else -1)
    cross, ago = None, None
    for i in range(len(hist) - 1, max(len(hist) - 6, 0), -1):
        if sgn(hist[i - 1]) <= 0 < sgn(hist[i]):
            cross, ago = "golden", len(hist) - 1 - i
            break
        if sgn(hist[i - 1]) >= 0 > sgn(hist[i]):
            cross, ago = "dead", len(hist) - 1 - i
            break
    # 柱狀體連續放大（正數）或縮小（負數）的天數
    trend = 0
    for i in range(len(hist) - 1, 0, -1):
        d = hist[i] - hist[i - 1]
        if trend >= 0 and d > eps:
            trend += 1
        elif trend <= 0 and d < -eps:
            trend -= 1
        else:
            break
    return {"macd": line_v[-1], "signal": line_v[-1] - hist[-1], "hist": hist[-1], "cross": cross, "cross_ago": ago,
            "hist_trend": trend}


def analyze(closes):
    """單一股票：給定由舊到新的收盤價，計算指標與訊號。"""
    n = len(closes)
    r = {"days": n, "close": closes[-1] if closes else None}
    if n < 20:
        return r
    bb = bollinger(closes)
    r["bb"] = {k: round(v, 4) for k, v in bb.items() if v is not None}
    # 帶寬在近 60 個交易日中的百分位（0＝最窄）；需要 20＋60 天資料
    widths = [bollinger(closes[: k + 1])["width"] for k in range(max(19, n - 60), n)]
    widths = [w for w in widths if w is not None]
    if len(widths) >= 40 and bb["width"] is not None:
        r["bb"]["width_rank"] = round(sum(1 for w in widths if w < bb["width"]) / len(widths) * 100, 1)
    # 價格跳空偵測：單日跌幅超過跌停（除權息、減資等），指標可能失真
    jumps = [i for i in range(max(1, n - 30), n) if (closes[i] / closes[i - 1] - 1) * 100 < JUMP_PCT]
    r["gap"] = bool(jumps)
    if n >= MIN_DAYS:
        rv = rsi(closes)
        r["rsi"] = round(rv, 2) if rv is not None else None
        rp = rsi(closes[:-5])
        r["rsi_prev5"] = round(rp, 2) if rp is not None else None
        m = macd(closes)
        if m:
            r["macd"] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()}
    return r


# ---------- 主程式 ----------

def trading_days(today, have, closed):
    res = []
    for i in range(LOOKBACK_CAL, -1, -1):
        d = today - datetime.timedelta(days=i)
        if d.weekday() < 5 and d.strftime("%Y%m%d") not in have and d.isoformat() not in closed:
            res.append(d)
    return res


def main():
    PDIR.mkdir(parents=True, exist_ok=True)
    today = datetime.datetime.now(TZ).date()
    diag = {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "runs": [], "errors": []}
    meta_p = PDIR / "_meta.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {"closed": []}
    have = {p.stem for p in PDIR.glob("2*.json")}
    todo = trading_days(today, have, set(meta["closed"]))
    # 已有足夠天數時只補最近缺的日期；第一次執行時由新到舊回補
    todo = sorted(todo, reverse=True)[:MAX_FETCH_PER_RUN]
    for d in todo:
        if len(have) >= TARGET_DAYS + 5 and d < today - datetime.timedelta(days=10):
            continue
        day = {}
        statuses = {}
        for market in ("上市", "上櫃"):
            data, status, tried = fetch_day(market, d)
            statuses[market] = status
            diag["runs"].append({"date": d.isoformat(), "market": market, "status": status, "tried": tried})
            if status == "ok":
                day[market] = data
            time.sleep(2)
        if len(day) == 2:
            (PDIR / f"{d:%Y%m%d}.json").write_text(json.dumps(day, separators=(",", ":")), encoding="utf-8")
            have.add(f"{d:%Y%m%d}")
            print(f"[{d}] 上市 {len(day['上市'])}、上櫃 {len(day['上櫃'])} 檔")
        elif all(s == "nodata" for s in statuses.values()) and d != today:
            meta["closed"].append(d.isoformat())
            print(f"[{d}] 休市")
        elif statuses and d != today:
            diag["errors"].append(f"{d} {statuses}")
            print(f"[警告] {d} {statuses}", file=sys.stderr)
    # 只保留最近 TARGET_DAYS＋10 個交易日的檔案
    files = sorted(PDIR.glob("2*.json"))
    for p in files[: max(0, len(files) - (TARGET_DAYS + 10))]:
        p.unlink()
    meta["closed"] = sorted(set(meta["closed"]))[-80:]
    meta_p.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    # 組成每檔股票的收盤序列
    files = sorted(PDIR.glob("2*.json"))
    days = [p.stem for p in files]
    series = {}
    last_day = {}
    for p in files:
        dd = json.loads(p.read_text(encoding="utf-8"))
        for market, m in dd.items():
            for code, px in m.items():
                series.setdefault(code, []).append(px)
                last_day[code] = (p.stem, market, px)

    # 核對：最新一天的收盤價與主程式抓到的收盤價一致
    companies = json.loads((ROOT / "data" / "companies.json").read_text(encoding="utf-8"))["companies"]
    match, total = {"上市": 0, "上櫃": 0}, {"上市": 0, "上櫃": 0}
    for c in companies:
        pd_ = str(c.get("price_date") or "")
        if not c.get("price") or len(pd_) != 7:
            continue
        ymd = f"{int(pd_[:3]) + 1911}{pd_[3:]}"
        ld = last_day.get(c["code"])
        if not ld or ld[0] != ymd:
            continue
        total[c["market"]] += 1
        match[c["market"]] += abs(ld[2] - c["price"]) < 1e-6
    check = {m: (round(match[m] / total[m], 4) if total[m] else None) for m in total}
    diag["check"] = {"match": match, "total": total, "rate": check}
    print("收盤價核對：", check, total)
    bad = [m for m, v in check.items() if v is not None and v < MIN_MATCH]

    stocks = {code: analyze(px) for code, px in series.items()}
    OUT.write_text(json.dumps({"generated_at": diag["generated_at"], "days": len(days),
                               "first": days[0] if days else None, "last": days[-1] if days else None,
                               "check": check, "stocks": stocks if not bad else {}},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    DIAG.write_text(json.dumps(diag, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"歷史 {len(days)} 個交易日（{days[0] if days else '-'}～{days[-1] if days else '-'}），{len(stocks)} 檔")
    if bad:
        print(f"::error::收盤價核對未通過 {bad}，技術指標不輸出", file=sys.stderr)
        sys.exit(1)
    if len(days) < MIN_DAYS:
        print(f"::warning::歷史只有 {len(days)} 天，RSI、MACD 需要 {MIN_DAYS} 天才計算", file=sys.stderr)


if __name__ == "__main__":
    main()
