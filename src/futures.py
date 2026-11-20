"""台指期三大法人未平倉（口數）：外資淨空單增加或減少。

資料來源（臺灣期貨交易所）：
  最新一天：OpenAPI /MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate
            （沒有日期參數，只有最新交易日；回傳格式曾在 JSON 與 CSV 之間變動，兩種都處理）
  歷史回補：期交所網站「三大法人-區分各期貨契約-依日期」下載（CSV，單次最多約一個月）
商品：臺股期貨（TX，大台）。身份別：外資（外資及陸資）、投信、自營商。

核對：每列「多空未平倉口數淨額」必須等於「多方未平倉口數 − 空方未平倉口數」，通過率低於 95% 的來源不採用。
"""
import csv
import datetime
import io
import json
import re
import time

import requests

OPENAPI = "https://openapi.taifex.com.tw/v1/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate"
DOWNLOAD = "https://www.taifex.com.tw/cht/3/futContractsDateDown"
UA = {"User-Agent": "Mozilla/5.0 (fundamentals-bot)"}
PRODUCT_NAMES = ("臺股期貨", "台股期貨")
KEEP_DAYS = 60
MIN_CHECK = 0.95


def _num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def _ident(s):
    s = str(s).strip()
    if "外資" in s:
        return "外資"
    if "投信" in s:
        return "投信"
    if "自營" in s:
        return "自營商"
    return None


def _date(v):
    s = re.sub(r"\D", "", str(v))
    if len(s) == 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return None


def decode(content):
    for enc in ("utf-8-sig", "cp950", "big5"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def rows_from_text(text):
    """CSV 或 JSON 文字 -> 資料列（dict，鍵為欄位名）。"""
    t = text.replace("\ufeff", "").strip()
    if t.startswith("[") or t.startswith("{"):
        j = json.loads(t)
        if isinstance(j, dict):
            j = j.get("data") or j.get("Data") or []
        return [r for r in j if isinstance(r, dict)]
    reader = csv.reader(io.StringIO(t))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return []
    head = [re.sub(r"\s+", "", h) for h in rows[0]]
    return [dict(zip(head, r)) for r in rows[1:]]


def _pick(r, *cands):
    keys = {re.sub(r"\s+", "", str(k)).replace("\ufeff", ""): k for k in r}
    for c in cands:
        if c in keys:
            return r[keys[c]]
    for c in cands:
        for k, orig in keys.items():
            if k.startswith(c):
                return r[orig]
    return None


def parse(text):
    """回傳 ({日期: {身份: {long, short, net}}}, 診斷)。只取臺股期貨。"""
    rows = rows_from_text(text)
    out, checked, ok = {}, 0, 0
    for r in rows:
        name = str(_pick(r, "商品名稱", "ContractName", "Contract") or "").strip()
        if name not in PRODUCT_NAMES:
            continue
        d = _date(_pick(r, "日期", "Date"))
        who = _ident(_pick(r, "身份別", "身分別", "Identity", "Item") or "")
        lo = _num(_pick(r, "多方未平倉口數", "OpenInterest(Long)", "LongOpenInterest"))
        sh = _num(_pick(r, "空方未平倉口數", "OpenInterest(Short)", "ShortOpenInterest"))
        net = _num(_pick(r, "多空未平倉口數淨額", "OpenInterest(Net)", "NetOpenInterest"))
        if not d or not who or lo is None or sh is None or net is None:
            continue
        checked += 1
        ok += abs((lo - sh) - net) < 0.5
        out.setdefault(d, {})[who] = {"long": int(lo), "short": int(sh), "net": int(net)}
    diag = {"rows": len(rows), "tx_rows": checked, "check": round(ok / checked, 4) if checked else None,
            "head": list(rows[0].keys())[:16] if rows else []}
    return out, diag


def fetch_latest():
    last = None
    for i in range(3):
        try:
            r = requests.get(OPENAPI, headers=dict(UA, Accept="application/json, text/csv, */*"), timeout=60)
            r.raise_for_status()
            return parse(decode(r.content))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"期交所 OpenAPI 失敗：{last}")


def fetch_range(start, end):
    """期交所網站下載（POST，CSV），用來回補歷史。start/end 為 datetime.date，相差不超過 30 天。"""
    data = {"queryStartDate": start.strftime("%Y/%m/%d"), "queryEndDate": end.strftime("%Y/%m/%d"), "commodityId": "TXF"}
    last = None
    for i in range(2):
        try:
            r = requests.post(DOWNLOAD, data=data, headers=UA, timeout=60)
            r.raise_for_status()
            return parse(decode(r.content))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(4 * (i + 1))
    raise RuntimeError(f"期交所下載失敗：{last}")


def accept(parsed, diag):
    return bool(parsed) and diag.get("check") is not None and diag["check"] >= MIN_CHECK


def update(hist, today):
    """更新 hist["fut"]；回傳 (說明, 錯誤清單)。"""
    fut = hist.setdefault("fut", {})
    notes, errors = [], []
    if len(fut) < 15:
        try:
            p, dg = fetch_range(today - datetime.timedelta(days=30), today)
            if accept(p, dg):
                fut.update(p)
                notes.append(f"歷史回補 {len(p)} 天（核對 {dg['check']}）")
            else:
                errors.append(f"期交所下載未通過核對或無資料：{dg}")
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
    try:
        p, dg = fetch_latest()
        if accept(p, dg):
            fut.update(p)
            notes.append(f"最新 {max(p)}（核對 {dg['check']}）")
        else:
            errors.append(f"期交所 OpenAPI 未通過核對或無資料：{dg}")
    except Exception as e:  # noqa: BLE001
        errors.append(str(e))
    for old in sorted(fut)[:-KEEP_DAYS]:
        del fut[old]
    return "；".join(notes), errors


def summarize(hist, who="外資"):
    """外資台指期淨未平倉：最新口數、較前一交易日增減、近 5 日增減；淨空單增加或減少。"""
    fut = hist.get("fut") or {}
    ds = [d for d in sorted(fut) if who in fut[d]]
    if not ds:
        return None
    d = ds[-1]
    cur = fut[d][who]
    net = cur["net"]
    prev = fut[ds[-2]][who]["net"] if len(ds) >= 2 else None
    base5 = fut[ds[-6]][who]["net"] if len(ds) >= 6 else (fut[ds[0]][who]["net"] if len(ds) >= 2 else None)
    chg1 = net - prev if prev is not None else None
    chg5 = net - base5 if base5 is not None else None

    def short_change(chg):
        """淨部位變動 → 淨空單增減的說法。淨額下降＝偏空加碼（空單增加或多單減少）。"""
        if chg is None or chg == 0:
            return None
        return "空單增加" if chg < 0 else "空單減少"

    return {
        "date": d, "who": who, "net": net, "long": cur["long"], "short": cur["short"],
        "position": "淨空單" if net < 0 else "淨多單" if net > 0 else "多空持平",
        "size": abs(net), "chg1": chg1, "chg5": chg5,
        "chg1_label": short_change(chg1), "chg5_label": short_change(chg5),
        "days5": min(len(ds) - 1, 5),
        "others": {k: v["net"] for k, v in fut[d].items() if k != who},
        "series": [{"date": x, "net": fut[x][who]["net"]} for x in ds[-10:]],
    }
