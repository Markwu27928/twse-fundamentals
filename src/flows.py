#!/usr/bin/env python3
"""資金流向：個股三大法人買賣超＋融資融券，輸出 data/flows.json（每檔摘要）與 data/flow_diag.json（診斷）。

資料來源（每個交易日一份，依序嘗試，通過核對才採用）：
  上市 三大法人：證交所 T86；融資融券：證交所 MI_MARGN（備援：開放資料，只有最新一天）
  上櫃 三大法人、融資融券：櫃買中心網站（依日期）；備援：櫃買開放資料（只有最新一天）

正確性核對（欄位對錯位置時等式不會成立）：
  法人：外資 ＋ 投信 ＋ 自營商 ＝ 三大法人合計（容許 ±1 股誤差）
  融資：今日餘額 − 前日餘額 ＝ 買進 − 賣出 − 現金償還
核對通過率低於 MIN_CHECK 的來源不採用。

歷史保留最近 KEEP_DAYS 個交易日於 data/flow_history.json，每次只補抓缺的日期。
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
HIST = ROOT / "data" / "flow_history.json"
OUT = ROOT / "data" / "flows.json"
DIAG = ROOT / "data" / "flow_diag.json"
TZ = ZoneInfo("Asia/Taipei")
UA = {"User-Agent": "Mozilla/5.0 (fundamentals-bot)", "Accept": "application/json"}
KEEP_DAYS = 20
LOOKBACK_CAL_DAYS = 16
MIN_ROWS = 200
MIN_CHECK = 0.95
PREFERRED = {}  # (市場, 種類) -> 上次成功的來源序號（執行期間有效，並存入歷史）


def urls(market, kind, d):
    """d: datetime.date -> 依序嘗試的網址。"""
    ymd, roc = d.strftime("%Y%m%d"), f"{d.year - 1911}/{d:%m/%d}"
    slash = f"{d.year}%2F{d:%m}%2F{d:%d}"
    if market == "上市" and kind == "inst":
        return [f"https://www.twse.com.tw/rwd/zh/fund/T86?date={ymd}&selectType=ALLBUT0999&response=json",
                f"https://www.twse.com.tw/fund/T86?response=json&date={ymd}&selectType=ALLBUT0999"]
    if market == "上市" and kind == "margin":
        return [f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={ymd}&selectType=ALL&response=json",
                f"https://www.twse.com.tw/exchangeReport/MI_MARGN?response=json&date={ymd}&selectType=ALL",
                "OPEN:https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN"]
    if market == "上櫃" and kind == "inst":
        return [f"https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW&date={slash}&response=json",
                f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&o=json&se=EW&t=D&d={roc}",
                "OPEN:https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading"]
    if market == "上櫃" and kind == "margin":
        return [f"https://www.tpex.org.tw/www/zh-tw/margin/balance?date={slash}&response=json",
                f"https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/margin_bal_result.php?l=zh-tw&o=json&d={roc}",
                "OPEN:https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance"]
    return []


# ---------- 通用表格解析 ----------

_FW = str.maketrans({"（": "(", "）": ")", "％": "%", "－": "-", "　": ""})


def norm(s):
    s = re.sub(r"<[^>]*>", "", str(s))
    return re.sub(r"\s+", "", s.translate(_FW))


def num(v):
    if v is None:
        return None
    s = re.sub(r"<[^>]*>", "", str(v)).replace(",", "").strip()
    if s in ("", "-", "--", "---", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def extract_tables(j):
    """回傳 [(欄位名清單, 資料列清單), ...]；支援 {fields,data}、{tables:[...]}、
    {aaData/iTotalRecords, 有欄位名時}、以及開放資料的 list-of-dict。"""
    out = []
    if isinstance(j, list):
        if j and isinstance(j[0], dict):
            keys = list(j[0].keys())
            out.append((keys, [[r.get(k) for k in keys] for r in j]))
        return out
    if not isinstance(j, dict):
        return out
    if j.get("fields") and j.get("data"):
        out.append((j["fields"], j["data"]))
    for t in j.get("tables") or []:
        if isinstance(t, dict) and t.get("fields") and t.get("data"):
            f = t["fields"]
            # 有分組表頭（groups）時，把分組名稱加在欄位前，例如「融資」「融券」
            groups = t.get("groups") or []
            if groups and all(isinstance(g, dict) for g in groups):
                f = list(f)
                for g in groups:
                    start, span, title = g.get("start"), g.get("span"), g.get("title")
                    if isinstance(start, int) and isinstance(span, int) and title:
                        for i in range(start, min(start + span, len(f))):
                            f[i] = f"{title}{f[i]}"
            out.append((f, t["data"]))
    if j.get("aaData") and j.get("fields"):
        out.append((j["fields"], j["aaData"]))
    return out


def unique_names(fields):
    """重複欄位名加上序號：買進、買進#2…（MI_MARGN 的融資、融券欄位同名）。"""
    seen, out = {}, []
    for f in fields:
        n = norm(f)
        seen[n] = seen.get(n, 0) + 1
        out.append(n if seen[n] == 1 else f"{n}#{seen[n]}")
    return out


def find(names, *must, exclude=()):
    for i, n in enumerate(names):
        if all(m in n for m in must) and not any(x in n for x in exclude):
            return i
    return None


def code_index(names):
    for cand in ("證券代號", "股票代號", "代號", "SecuritiesCompanyCode", "Code", "StockNo"):
        i = find(names, cand)
        if i is not None:
            return i
    return None


# ---------- 三大法人 ----------

def map_inst(names):
    """回傳各欄位索引：foreign（可能是兩欄相加）、trust、dealer、total。"""
    net = ("買賣超", "淨買", "差額", "Difference", "NetBuy", "Net")
    def any_net(n):
        return any(k in n for k in net)
    cols = [n for n in names]
    m = {}
    # 外資：優先「合計」欄，否則 不含外資自營商 ＋ 外資自營商
    # 外資合計欄：含外資字樣、是買賣超、不是「不含外資自營商」的分項、也不是外資自營商本身
    fx_total = [i for i, n in enumerate(cols) if ("外資" in n or "外陸資" in n or "Foreign" in n) and any_net(n)
                and "不含" not in n and "自營商" not in n and ("合計" in n or "Total" in n or "外資及陸資" in n or "外陸資" in n)
                and "Dealer" not in n]
    fx_main = [i for i, n in enumerate(cols) if ("外陸資" in n or "外資及陸資" in n) and any_net(n) and "不含" in n]
    fx_dealer = [i for i, n in enumerate(cols) if "外資自營商" in n and "不含" not in n and any_net(n)]
    if fx_total:
        m["foreign"] = [fx_total[0]]
    elif fx_main:
        m["foreign"] = [fx_main[0]] + fx_dealer[:1]
    else:
        plain = [i for i, n in enumerate(cols) if ("外資" in n or "外陸資" in n) and any_net(n) and "自營商" not in n]
        if plain:
            m["foreign"] = [plain[0]]
    t = [i for i, n in enumerate(cols) if "投信" in n and any_net(n)]
    if t:
        m["trust"] = [t[0]]
    d_total = [i for i, n in enumerate(cols) if "自營商" in n and "外資自營商" not in n and any_net(n)
               and "自行買賣" not in n and "避險" not in n]
    d_parts = [i for i, n in enumerate(cols) if "自營商" in n and "外資自營商" not in n and any_net(n)
               and ("自行買賣" in n or "避險" in n)]
    if d_total:
        m["dealer"] = [d_total[0]]
    elif d_parts:
        m["dealer"] = d_parts[:2]
    tot = [i for i, n in enumerate(cols) if ("三大法人" in n or "合計" == n[:2]) and any_net(n)]
    if tot:
        m["total"] = [tot[0]]
    # 櫃買中心格式：欄位名只有「買賣超股數」重複 7 組、沒有身分名稱。依網頁順序：
    # 外資及陸資(不含外資自營商)、外資自營商、外資及陸資合計、投信、自營商(自行買賣)、自營商(避險)、自營商合計
    if not {"foreign", "trust", "dealer"} <= set(m):
        generic = [i for i, n in enumerate(cols) if re.sub(r"#\d+$", "", n) in ("買賣超股數", "買賣超", "買賣超(股)")]
        if len(generic) == 7:
            g = generic
            m = {"foreign": [g[2]], "trust": [g[3]], "dealer": [g[6]],
                 "_fx_parts": [g[0], g[1]], "_dl_parts": [g[4], g[5]]}
            if tot:
                m["total"] = [tot[0]]
    return m


def parse_inst(j):
    """回傳 (資料 {代號: [外資, 投信, 自營商]}（股）, 診斷)。"""
    best = None
    for fields, rows in extract_tables(j):
        names = unique_names(fields)
        ci = code_index(names)
        m = map_inst(names)
        if ci is None or not {"foreign", "trust", "dealer"} <= set(m):
            continue
        data, ok, checked = {}, 0, 0
        for r in rows:
            if len(r) < len(names):
                continue
            code = norm(r[ci])
            if not re.fullmatch(r"[1-9]\d{3}", code) or code.startswith("91"):
                continue
            vals = {k: [num(r[i]) for i in idx] for k, idx in m.items()}
            if any(v is None for k in ("foreign", "trust", "dealer") for v in vals[k]):
                continue
            f, t, d = sum(vals["foreign"]), sum(vals["trust"]), sum(vals["dealer"])
            data[code] = [f, t, d]
            if "total" in m and vals["total"][0] is not None:
                checked += 1
                good = abs(f + t + d - vals["total"][0]) <= 1
                # 依位置對應時，再核對 外資分項相加＝外資合計、自營商分項相加＝自營商合計
                for part, whole in (("_fx_parts", f), ("_dl_parts", d)):
                    if part in vals:
                        ps = vals[part]
                        good = good and all(x is not None for x in ps) and abs(sum(ps) - whole) <= 1
                ok += good
        diag = {"fields": names, "map": {k: [names[i] for i in v] for k, v in m.items()}, "rows": len(data),
                "check": round(ok / checked, 4) if checked else None}
        if best is None or len(data) > best[1]["rows"]:
            best = (data, diag)
    return best if best else ({}, {"error": "找不到含代號與外資、投信、自營商買賣超的表格",
                                  "tables": [unique_names(f) for f, _ in extract_tables(j)][:3]})


# ---------- 融資融券 ----------

def map_margin(names):
    """融資：買進、賣出、現金償還、前日餘額、今日餘額；融券：今日餘額、前日餘額。
    欄位名有『融資／融券』前綴就用前綴；沒有時同名欄位第一次出現為融資、第二次為融券。"""
    def idx(*must, group=None, nth=1):
        hits = [i for i, n in enumerate(names) if all(m in n for m in must) and (group is None or group in n)]
        if group is None:
            hits = [i for i in hits if "融資" not in names[i] and "融券" not in names[i]] or hits
        return hits[nth - 1] if len(hits) >= nth else None
    m = {}
    prefixed = any("融資" in n for n in names) and any("融券" in n for n in names)
    eng = any("Margin" in n for n in names)
    if prefixed:
        m = {"mb": idx("買進", group="融資"), "ms": idx("賣出", group="融資"), "mr": idx("現金償還", group="融資"),
             "mprev": idx("前日餘額", group="融資"), "mbal": idx("今日餘額", group="融資") or idx("餘額", group="融資", nth=2),
             "sbal": idx("今日餘額", group="融券") or idx("餘額", group="融券", nth=2), "sprev": idx("前日餘額", group="融券")}
    elif any(n.startswith("資餘額") for n in names) and any(n.startswith("券餘額") for n in names):
        # 櫃買中心格式：前資餘額(張)、資買、資賣、現償、資餘額、前券餘額(張)、券賣、券買、券償、券餘額
        def ex(name):
            return next((i for i, n in enumerate(names) if n == name or n.startswith(name + "(")), None)
        m = {"mb": ex("資買"), "ms": ex("資賣"), "mr": ex("現償"), "mprev": ex("前資餘額"), "mbal": ex("資餘額"),
             "sprev": ex("前券餘額"), "sbal": ex("券餘額")}
    elif eng:
        def e(*must):
            return next((i for i, n in enumerate(names) if all(x.lower() in n.lower() for x in must)), None)
        m = {"mb": e("Margin", "Purchase") if e("Margin", "Purchase") is not None else e("MarginPurchase"),
             "ms": e("Margin", "Sale"), "mr": e("Margin", "Redemption"),
             "mprev": e("Margin", "Previous"), "mbal": e("Margin", "Balance", "Today") or e("MarginPurchaseBalance"),
             "sbal": e("Short", "Balance", "Today") or e("ShortSaleBalance"), "sprev": e("Short", "Previous")}
    else:
        m = {"mb": idx("買進"), "ms": idx("賣出"), "mr": idx("償還"), "mprev": idx("前日餘額"),
             "mbal": idx("今日餘額"), "sprev": idx("前日餘額", nth=2), "sbal": idx("今日餘額", nth=2)}
    return {k: v for k, v in m.items() if v is not None}


def parse_margin(j):
    """回傳 (資料 {代號: [融資餘額, 融券餘額]}（張）, 診斷)。"""
    best = None
    for fields, rows in extract_tables(j):
        names = unique_names(fields)
        ci = code_index(names)
        m = map_margin(names)
        if ci is None or not {"mbal", "sbal"} <= set(m):
            continue
        data, ok, checked = {}, 0, 0
        for r in rows:
            if len(r) < len(names):
                continue
            code = norm(r[ci])
            if not re.fullmatch(r"[1-9]\d{3}", code) or code.startswith("91"):
                continue
            v = {k: num(r[i]) for k, i in m.items()}
            if v["mbal"] is None or v["sbal"] is None:
                continue
            data[code] = [v["mbal"], v["sbal"]]
            if all(v.get(k) is not None for k in ("mb", "ms", "mr", "mprev")):
                checked += 1
                ok += abs((v["mbal"] - v["mprev"]) - (v["mb"] - v["ms"] - v["mr"])) <= 0.5
        diag = {"fields": names, "map": {k: names[i] for k, i in m.items()}, "rows": len(data),
                "check": round(ok / checked, 4) if checked else None}
        if best is None or len(data) > best[1]["rows"]:
            best = (data, diag)
    return best if best else ({}, {"error": "找不到含代號與融資、融券餘額的表格",
                                  "tables": [unique_names(f) for f, _ in extract_tables(j)][:3]})


# ---------- 抓取 ----------

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


def open_date(j):
    """開放資料（list-of-dict）的資料日期（民國年 7 碼或西元 8 碼）-> 'YYYY-MM-DD'。"""
    if not isinstance(j, list) or not j:
        return None
    for k in ("Date", "日期", "資料日期"):
        v = re.sub(r"\D", "", str(j[0].get(k, "")))
        if len(v) == 7:
            return f"{int(v[:3]) + 1911}-{v[3:5]}-{v[5:]}"
        if len(v) == 8:
            return f"{v[:4]}-{v[4:6]}-{v[6:]}"
    return None


def no_data(j):
    """交易所回傳「查無資料」（休市或尚未公布）。"""
    if isinstance(j, dict):
        stat = str(j.get("stat", "")).lower()
        if stat and stat not in ("ok",) and not j.get("tables") and not j.get("data") and not j.get("aaData"):
            return True
        if j.get("totalCount") == 0 or j.get("iTotalRecords") == 0:
            return True
        if j.get("tables") is not None and all(not (t or {}).get("data") for t in j["tables"]):
            return True
    return False


def fetch_one(market, kind, d):
    """回傳 (資料 or None, 診斷, 狀態)；狀態：ok / nodata / fail。"""
    parser = parse_inst if kind == "inst" else parse_margin
    tried, saw_nodata = [], False
    cands = urls(market, kind, d)
    pi = PREFERRED.get((market, kind))
    if pi is not None and 0 <= pi < len(cands):
        cands = [cands[pi]] + [c for i, c in enumerate(cands) if i != pi]
    for u in cands:
        is_open = u.startswith("OPEN:")
        url = u[5:] if is_open else u
        try:
            j = get_json(url)
        except Exception as e:  # noqa: BLE001
            tried.append({"url": url, "error": str(e)})
            time.sleep(2)
            continue
        if is_open:
            od = open_date(j)
            if od != d.isoformat():
                tried.append({"url": url, "skip": f"開放資料日期 {od} 不是 {d}"})
                continue
        if no_data(j):
            saw_nodata = True
            tried.append({"url": url, "nodata": True, "stat": j.get("stat") if isinstance(j, dict) else None})
            time.sleep(2)
            continue
        data, diag = parser(j)
        diag["url"] = url
        tried.append(diag)
        if diag.get("check") is None and data:
            diag["rejected"] = "無法核對（缺合計或前日餘額欄位），不採用"
        if len(data) >= MIN_ROWS and diag.get("check") is not None and diag["check"] >= MIN_CHECK:
            # 記住可用的來源，下次優先嘗試
            PREFERRED[(market, kind)] = urls(market, kind, d).index(u)
            return data, tried, "ok"
        time.sleep(2)
    return None, tried, "nodata" if saw_nodata else "fail"


# ---------- 指標 ----------

def summarize(hist, companies):
    """每檔股票的資金流向摘要。法人單位：張；金額：億元（以最新收盤價估算）。"""
    inst_days = sorted(set().union(*[set(hist["inst"].get(m, {})) for m in hist["inst"]])) if hist["inst"] else []
    out = {}
    for c in companies:
        code, mk = c["code"], c["market"]
        idays = sorted(hist["inst"].get(mk, {}))
        mdays = sorted(hist["margin"].get(mk, {}))
        series = [hist["inst"][mk][d].get(code) for d in idays]
        series = [(d, v) for d, v in zip(idays, series) if v is not None]
        r = {"days": len(series)}
        price, shares = c.get("price"), c.get("shares")
        if series:
            last5 = series[-5:]
            f5 = sum(v[0] for _, v in last5) / 1000
            t5 = sum(v[1] for _, v in last5) / 1000
            d5 = sum(v[2] for _, v in last5) / 1000
            ft, tt, dt = (x / 1000 for x in series[-1][1])
            r.update({
                "date": series[-1][0], "n5": len(last5),
                "foreign": round(ft), "trust": round(tt), "dealer": round(dt),
                "foreign5": round(f5), "trust5": round(t5), "dealer5": round(d5), "total5": round(f5 + t5 + d5),
                "foreign_streak": _streak([v[0] for _, v in series]),
                "trust_streak": _streak([v[1] for _, v in series]),
            })
            if price:
                r["total5_value"] = round((f5 + t5 + d5) * 1000 * price / 1e8, 2)
                r["foreign5_value"] = round(f5 * 1000 * price / 1e8, 2)
                r["trust5_value"] = round(t5 * 1000 * price / 1e8, 2)
            if shares:
                r["conc5"] = round((f5 + t5 + d5) * 1000 / shares * 100, 3)
        mser = [(d, hist["margin"][mk][d].get(code)) for d in mdays]
        mser = [(d, v) for d, v in mser if v is not None]
        if mser:
            mb, sb = mser[-1][1]
            r.update({"margin": round(mb), "short": round(sb), "margin_date": mser[-1][0],
                      "short_ratio": round(sb / mb * 100, 1) if mb > 0 else None})
            if len(mser) >= 2:
                base = mser[-6][1][0] if len(mser) >= 6 else mser[0][1][0]
                r["margin_chg5"] = round(mb - base)
                r["margin_chg5_pct"] = round((mb / base - 1) * 100, 1) if base > 0 else None
                r["margin_days"] = min(len(mser) - 1, 5)
        out[code] = r
    return out, inst_days


def _streak(vals):
    """最近連續買超（正數）或賣超（負數）天數。"""
    n = 0
    if not vals:
        return 0
    sign = 1 if vals[-1] > 0 else -1 if vals[-1] < 0 else 0
    if sign == 0:
        return 0
    for v in reversed(vals):
        if (v > 0 and sign > 0) or (v < 0 and sign < 0):
            n += 1
        else:
            break
    return n * sign


def candidate_days(hist, today):
    have_closed = set(hist.get("closed", []))
    res = []
    for i in range(LOOKBACK_CAL_DAYS, -1, -1):
        d = today - datetime.timedelta(days=i)
        if d.weekday() >= 5 or d.isoformat() in have_closed:
            continue
        res.append(d)
    return res


def main():
    hist = json.loads(HIST.read_text(encoding="utf-8")) if HIST.exists() else {}
    hist.setdefault("inst", {}), hist.setdefault("margin", {}), hist.setdefault("closed", [])
    for k, v in (hist.get("preferred") or {}).items():
        mk, kd = k.split("/")
        PREFERRED[(mk, kd)] = v
    today = datetime.datetime.now(TZ).date()
    diag = {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "runs": [], "errors": []}

    for d in candidate_days(hist, today):
        ds = d.isoformat()
        nodata_all = True
        for market in ("上市", "上櫃"):
            for kind in ("inst", "margin"):
                if ds in hist[kind].get(market, {}):
                    nodata_all = False
                    continue
                data, tried, status = fetch_one(market, kind, d)
                diag["runs"].append({"date": ds, "market": market, "kind": kind, "status": status, "tried": tried})
                if status == "ok":
                    hist[kind].setdefault(market, {})[ds] = data
                    nodata_all = False
                    print(f"[{ds}] {market}/{kind}: {len(data)} 檔，核對 {tried[-1].get('check')}，來源 {tried[-1]['url'][:70]}")
                elif status == "fail":
                    nodata_all = False
                    diag["errors"].append(f"{ds} {market}/{kind} 全部來源失敗")
                    print(f"[警告] {ds} {market}/{kind} 全部來源失敗", file=sys.stderr)
                time.sleep(2)
        # 四個來源都回「查無資料」且不是今天（今天可能尚未公布）→ 標為休市
        if nodata_all and d != today:
            hist["closed"].append(ds)
            print(f"[{ds}] 休市")

    for kind in ("inst", "margin"):
        for market in hist[kind]:
            for old in sorted(hist[kind][market])[:-KEEP_DAYS]:
                del hist[kind][market][old]
    hist["closed"] = sorted(set(hist["closed"]))[-60:]
    hist["preferred"] = {f"{mk}/{kd}": v for (mk, kd), v in PREFERRED.items()}
    HIST.write_text(json.dumps(hist, separators=(",", ":")), encoding="utf-8")

    companies = json.loads((ROOT / "data" / "companies.json").read_text(encoding="utf-8"))["companies"]
    per, days = summarize(hist, companies)
    cover = {f"{m}/{k}": sorted(hist[k].get(m, {})) [-5:] for k in ("inst", "margin") for m in ("上市", "上櫃")}
    OUT.write_text(json.dumps({"generated_at": diag["generated_at"], "coverage": cover, "stocks": per},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    diag["coverage"] = cover
    DIAG.write_text(json.dumps(diag, ensure_ascii=False, indent=1), encoding="utf-8")
    print("涵蓋：", json.dumps(cover, ensure_ascii=False))
    missing = [k for k, v in cover.items() if not v]
    if missing:
        print(f"::warning::資金流向缺少來源：{missing}，詳見 data/flow_diag.json", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
