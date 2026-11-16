#!/usr/bin/env python3
"""從證交所／櫃買中心開放資料抓取全市場財報，輸出標準化的 data/companies.json。

資料來源（皆為官方開放資料，不需金鑰）：
  基本資料、損益表、資產負債表、月營收、本益比/淨值比/殖利率、收盤價

正確性保護：
  1. 欄位名稱統一正規化（去空白、BOM，全形括號轉半形）後比對
  2. 每次執行都輸出 data/diagnostics.json：各來源筆數、實際欄位名稱、各欄位填值比例
  3. 關鍵欄位填值比例低於門檻就以非零狀態結束，不覆蓋舊資料
"""
import datetime
import json
import pathlib
import re
import sys
import time

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "companies.json"
DIAG = ROOT / "data" / "diagnostics.json"

TWSE = "https://openapi.twse.com.tw/v1"
TPEX = "https://www.tpex.org.tw/openapi/v1"
# 一般業(ci)以外的財報格式：異業、銀行、證券期貨、金控、保險
EXTRA_FORMATS = ["mim", "basi", "bd", "fh", "ins"]

SOURCES = {
    "上市": {
        "basic": f"{TWSE}/opendata/t187ap03_L",
        "income": f"{TWSE}/opendata/t187ap06_L_ci",
        "balance": f"{TWSE}/opendata/t187ap07_L_ci",
        **{f"income_{t}": f"{TWSE}/opendata/t187ap06_L_{t}" for t in EXTRA_FORMATS},
        **{f"balance_{t}": f"{TWSE}/opendata/t187ap07_L_{t}" for t in EXTRA_FORMATS},
        "revenue": f"{TWSE}/opendata/t187ap05_L",
        "valuation": f"{TWSE}/exchangeReport/BWIBBU_ALL",
        "price": f"{TWSE}/exchangeReport/STOCK_DAY_ALL",
    },
    "上櫃": {
        "basic": f"{TPEX}/mopsfin_t187ap03_O",
        "income": f"{TPEX}/mopsfin_t187ap06_O_ci",
        "balance": f"{TPEX}/mopsfin_t187ap07_O_ci",
        **{f"income_{t}": f"{TPEX}/mopsfin_t187ap06_O_{t}" for t in EXTRA_FORMATS},
        **{f"balance_{t}": f"{TPEX}/mopsfin_t187ap07_O_{t}" for t in EXTRA_FORMATS},
        "revenue": f"{TPEX}/mopsfin_t187ap05_O",
        "valuation": f"{TPEX}/tpex_mainboard_peratio_analysis",
        "price": f"{TPEX}/tpex_mainboard_daily_close_quotes",
    },
}
# 證交所開放資料（openapi）的股價、估值有時落後數個交易日；以證交所網站盤後資料當備援，取日期較新者
TWSE_RWD = {
    "price": [
        "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL?response=json",
        "https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=json",
        "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&type=ALLBUT0999",
    ],
    "valuation": [
        "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?response=json&selectType=ALL",
    ],
}
REQUIRED = {"basic", "income"}
MIN_COMPANIES = 500  # 低於此數量視為資料異常，不覆蓋舊資料

# 欄位名稱候選（正規化後比對；依序先完全比對，全部失敗才比對開頭）
CODE = ["公司代號", "Code", "SecuritiesCompanyCode", "CompanyCode", "股票代號", "證券代號"]
NAME = ["公司簡稱", "CompanyAbbreviation", "公司名稱", "Name", "CompanyName"]
F = {
    "industry": ["產業別", "SecuritiesIndustryCode", "IndustryCode"],
    "capital": ["實收資本額", "實收資本額(元)", "Paidin.Capital.NTDollars", "PaidInCapital"],
    "shares": ["已發行普通股數或TDR原股發行股數", "IssueShares"],
    "par": ["普通股每股面額", "ParValueOfCommonStock"],
    "year": ["年度", "Year"],
    "season": ["季別", "Season", "Quarter"],
    "revenue": ["營業收入", "營業收入合計", "收入", "收益", "淨收益", "收益合計", "Revenue", "OperatingRevenue"],
    "gross_profit": ["營業毛利(毛損)淨額", "營業毛利(毛損)", "GrossProfit"],
    "op_income": ["營業利益(損失)", "營業利益", "OperatingIncome"],
    "net_income": ["本期淨利(淨損)", "本期稅後淨利(淨損)", "本期稅後淨利", "NetIncome"],
    "eps": ["基本每股盈餘(元)", "基本每股盈餘", "EPS"],
    "total_assets": ["資產總額", "資產總計", "TotalAssets"],
    "total_liab": ["負債總額", "負債總計", "TotalLiabilities"],
    "cur_assets": ["流動資產", "CurrentAssets"],
    "cur_liab": ["流動負債", "CurrentLiabilities"],
    "equity": ["權益總額", "權益總計", "TotalEquity"],
    "rev_yoy": ["累計營業收入-前期比較增減(%)", "累計營業收入-前期比較增減",
                "營業收入-去年同月增減(%)", "營業收入-去年同月增減"],
    "pe": ["PEratio", "PriceEarningRatio", "本益比"],
    "pb": ["PBratio", "PriceBookRatio", "股價淨值比"],
    "dy": ["DividendYield", "YieldRatio", "殖利率(%)", "殖利率"],
    "price": ["ClosingPrice", "Close", "收盤價"],
    "trade_value": ["TradeValue", "TransactionAmount", "成交金額"],
    "change": ["Change", "漲跌價差"],
}
# 每個欄位來自哪個來源（診斷用）
FIELD_SOURCE = {
    "industry": "basic", "capital": "basic", "shares": "basic", "par": "basic",
    "year": "income", "season": "income", "revenue": "income", "gross_profit": "income",
    "op_income": "income", "net_income": "income", "eps": "income",
    "total_assets": "balance", "total_liab": "balance", "cur_assets": "balance",
    "cur_liab": "balance", "equity": "balance",
    "rev_yoy": "revenue", "pe": "valuation", "pb": "valuation", "dy": "valuation", "price": "price",
    "trade_value": "price", "change": "price",
}
# 關鍵欄位：在「該來源有資料的公司」中，填值比例必須達到此門檻，否則判定欄位對應錯誤
CRITICAL = {"industry": 0.95, "capital": 0.95, "revenue": 0.9, "net_income": 0.9, "eps": 0.9,
            "total_assets": 0.9, "equity": 0.9, "pe": 0.5, "price": 0.8, "trade_value": 0.8}

_FW = str.maketrans({"（": "(", "）": ")", "％": "%", "－": "-", "　": ""})


def norm_key(k):
    return re.sub(r"\s+", "", str(k).lstrip("﻿").translate(_FW))


def pick(row, candidates):
    """依候選欄位名取值：先完全比對，再比對開頭。"""
    keys = {norm_key(k): k for k in row}
    for c in candidates:
        c = norm_key(c)
        if c in keys:
            return row[keys[c]]
    for c in candidates:
        c = norm_key(c)
        for k, orig in keys.items():
            if k.startswith(c):
                return row[orig]
    return None


def matched_key(rows, candidates):
    """回傳實際對應到的原始欄位名（診斷用）。"""
    for r in rows[:50]:
        keys = {norm_key(k): k for k in r}
        for c in candidates:
            if norm_key(c) in keys:
                return keys[norm_key(c)]
        for c in candidates:
            for k, orig in keys.items():
                if k.startswith(norm_key(c)):
                    return orig
    return None


def num(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "--", "---", "N/A", "null", "None", "除權息", "除息", "除權"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fetch_json(url, retries=4):
    last = None
    for i in range(retries):
        try:
            r = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (fundamentals-bot)", "Accept": "application/json"},
                timeout=90,
            )
            r.raise_for_status()
            r.encoding = "utf-8"
            data = r.json()
            if not isinstance(data, list):
                raise ValueError("非預期的回傳格式（不是清單）")
            return data
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"{url} 抓取失敗：{last}")


def _rwd_rows(fields, data, roc):
    rows = []
    for row in data:
        r = dict(zip(fields, row), Date=roc)
        # MI_INDEX 格式：漲跌方向在「漲跌(+/-)」（含 HTML），「漲跌價差」不帶正負號
        sign_key = next((k for k in r if k.startswith("漲跌(")), None)
        if sign_key is not None and "漲跌價差" in r:
            neg = "-" in re.sub(r"<[^>]*>", "", str(r[sign_key]))
            diff = str(r["漲跌價差"]).replace(",", "").lstrip("+-")
            r["漲跌價差"] = ("-" if neg else "") + diff
        rows.append(r)
    return rows


def fetch_twse_rwd(url):
    """證交所網站盤後 JSON 轉成與開放資料相同的「每列一個 dict」，Date 用民國年。
    支援 {fields, data} 與 {tables:[{fields, data}]}（MI_INDEX）兩種格式。"""
    last = None
    for i in range(3):
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (fundamentals-bot)",
                                           "Accept": "application/json"}, timeout=90)
            r.raise_for_status()
            j = r.json()
            break
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(5 * (i + 1))
    else:
        raise RuntimeError(f"回應不是 JSON 或連線失敗：{last}")
    if j.get("stat") not in (None, "OK"):
        raise RuntimeError(f"無資料：{j.get('stat')}")
    d = str(j.get("date", ""))
    roc = f"{int(d[:4]) - 1911}{d[4:]}" if re.fullmatch(r"\d{8}", d) else d
    if j.get("data") and j.get("fields"):
        return _rwd_rows(j["fields"], j["data"], roc)
    for t in j.get("tables") or []:
        f = t.get("fields") or []
        if "證券代號" in f and "收盤價" in f and t.get("data"):
            return _rwd_rows(f, t["data"], roc)
    raise RuntimeError("回應中找不到個股資料表")


def latest_date(rows):
    ds = [str(r.get("Date", "")) for r in rows[:50] if r.get("Date")]
    return max(ds) if ds else ""


def clean_code(v):
    return re.sub(r"\s+", "", str(v or ""))


def index_rows(rows):
    out = {}
    for r in rows:
        c = pick(r, CODE)
        if c is not None:
            out[clean_code(c)] = r
    return out


def build_companies(raw):
    """raw: {市場: {來源名: 資料列清單}} -> 標準化公司清單（純函式，方便測試）。"""
    companies = []
    for market, src in raw.items():
        idx = {k: index_rows(src.get(k, [])) for k in ("revenue", "valuation", "price")}
        # 財報：一般業優先，其餘格式（異業、金融業）補上；記錄採用的格式
        fmt_of = {}
        for kind in ("income", "balance"):
            merged = {}
            for t in ["ci"] + EXTRA_FORMATS:
                key = kind if t == "ci" else f"{kind}_{t}"
                for code, row in index_rows(src.get(key, [])).items():
                    if code not in merged:
                        merged[code] = row
                        if kind == "income":
                            fmt_of[code] = t
            idx[kind] = merged
        for b in src.get("basic", []):
            code = clean_code(pick(b, CODE))
            # 只留一般股票：排除 0 開頭（ETF 等）與 91 開頭（存託憑證 TDR）
            if not re.fullmatch(r"[1-9]\d{3}", code) or code.startswith("91"):
                continue
            i, a, r, v, p = (idx[k].get(code, {}) for k in ("income", "balance", "revenue", "valuation", "price"))
            capital = num(pick(b, F["capital"]))
            shares = num(pick(b, F["shares"]))
            par = num(re.sub(r"[^\d.]", "", str(pick(b, F["par"]) or "")) or None)
            if not shares and capital:
                shares = capital / (par if par else 10)
            price = num(pick(p, F["price"]))
            chg = num(pick(p, F["change"]))
            prev_close = price - chg if price is not None and chg is not None else None
            mcap = shares * price / 1e8 if shares and price else None  # 單位：億元
            season = num(pick(i, F["season"]))
            companies.append({
                "code": code,
                "name": str(pick(b, NAME) or code).strip(),
                "market": market,
                "industry": str(pick(b, F["industry"]) or "").strip(),
                "fs_format": fmt_of.get(code),
                "capital": capital,
                "shares": shares,
                "price": price,
                "price_date": str(pick(p, ["Date"]) or "") or None,
                "mcap": mcap,
                "trade_value": num(pick(p, F["trade_value"])),
                "change": chg,
                "change_pct": round(chg / prev_close * 100, 2) if prev_close else None,
                "year": str(pick(i, F["year"]) or "").strip() or None,
                "season": int(season) if season else None,
                "revenue": num(pick(i, F["revenue"])),
                "gross_profit": num(pick(i, F["gross_profit"])),
                "op_income": num(pick(i, F["op_income"])),
                "net_income": num(pick(i, F["net_income"])),
                "eps": num(pick(i, F["eps"])),
                "total_assets": num(pick(a, F["total_assets"])),
                "total_liab": num(pick(a, F["total_liab"])),
                "cur_assets": num(pick(a, F["cur_assets"])),
                "cur_liab": num(pick(a, F["cur_liab"])),
                "equity": num(pick(a, F["equity"])),
                "rev_yoy": num(pick(r, F["rev_yoy"])),
                "pe": num(pick(v, F["pe"])),
                "pb": num(pick(v, F["pb"])),
                "dy": num(pick(v, F["dy"])),
                "_has": {k: code in idx[k] for k in idx},
            })
    return companies


STATEMENT_FIELDS = ["fs_format", "year", "season", "revenue", "gross_profit", "op_income", "net_income", "eps",
                    "total_assets", "total_liab", "cur_assets", "cur_liab", "equity"]


def carry_forward(companies, previous):
    """本次開放資料缺財報的公司，沿用上一次抓到的財報（標記 stale）。回傳沿用家數。"""
    prev = {p["code"]: p for p in previous}
    n = 0
    for c in companies:
        if c.get("fs_format"):
            continue
        p = prev.get(c["code"])
        if p and p.get("fs_format") and (p.get("net_income") is not None or p.get("equity") is not None):
            for k in STATEMENT_FIELDS:
                c[k] = p.get(k)
            c["stale"] = True
            n += 1
    return n


def diagnose(raw, companies):
    """各來源實際欄位與各欄位填值比例；回傳 (報告, 錯誤清單)。"""
    report, errors = {}, []
    for market, src in raw.items():
        m = {"sources": {}, "fields": {}}
        for name, rows in src.items():
            m["sources"][name] = {
                "rows": len(rows),
                "keys": list(rows[0].keys()) if rows else [],
                "sample": rows[0] if rows else None,
                "code_key": matched_key(rows, CODE) if rows else None,
            }
        cs = [c for c in companies if c["market"] == market]
        m["companies"] = len(cs)
        for field, cands in F.items():
            if field == "par":
                continue
            s = FIELD_SOURCE[field]
            base = cs if s == "basic" else [c for c in cs if c["_has"].get(s)]
            filled = sum(1 for c in base if c.get(field) not in (None, ""))
            rate = filled / len(base) if base else 0.0
            m["fields"][field] = {
                "source": s, "matched_key": matched_key(src.get(s, []), cands),
                "filled": filled, "base": len(base), "rate": round(rate, 3),
            }
            need = CRITICAL.get(field)
            if need and src.get(s) and rate < need:
                errors.append(f"{market}/{field}：填值比例 {rate:.0%} 低於 {need:.0%}（對應欄位：{m['fields'][field]['matched_key']}）")
        for s in ("income", "balance", "revenue", "valuation", "price"):
            if src.get(s):
                hit = sum(1 for c in cs if c["_has"].get(s))
                m["sources"][s]["matched_companies"] = hit
                if hit == 0:
                    errors.append(f"{market}/{s}：{len(src[s])} 筆資料，但沒有任何一筆對到公司代號（代號欄位對應錯誤）")
        chs = [c["change"] for c in cs if c.get("change") is not None]
        m["change_sign"] = {"up": sum(x > 0 for x in chs), "down": sum(x < 0 for x in chs), "flat": sum(x == 0 for x in chs)}
        if len(chs) > 200 and m["change_sign"]["down"] == 0:
            errors.append(f"{market}/change：{len(chs)} 筆漲跌沒有任何負值，漲跌欄位可能缺少正負號")
        fmts = {}
        for c in cs:
            fmts[c.get("fs_format") or "無財報"] = fmts.get(c.get("fs_format") or "無財報", 0) + 1
        m["fs_formats"] = fmts
        m["no_statement"] = [f"{c['code']} {c['name']}" for c in cs if not c.get("fs_format")]
        report[market] = m
    return report, errors


def print_report(report, errors):
    for market, m in report.items():
        print(f"\n==== {market}（{m['companies']} 家）財報格式：{m['fs_formats']} ====")
        print("無財報：", "、".join(m["no_statement"]))
        for name, s in m["sources"].items():
            print(f"[{name}] {s['rows']} 筆，代號欄位={s['code_key']}，對到公司={s.get('matched_companies', '-')}")
            print("   欄位：", "、".join(s["keys"]))
        for f, d in m["fields"].items():
            print(f"  {f:13s} <- {str(d['matched_key']):28s} {d['filled']}/{d['base']} ({d['rate']:.0%})")
    for e in errors:
        print("[錯誤]", e, file=sys.stderr)


def main():
    raw = {}
    for market, urls in SOURCES.items():
        raw[market] = {}
        for name, url in urls.items():
            try:
                raw[market][name] = fetch_json(url)
                print(f"[ok] {market}/{name}: {len(raw[market][name])} 筆")
            except RuntimeError as e:
                if name in REQUIRED:
                    print(f"[錯誤] 必要來源失敗：{e}", file=sys.stderr)
                    sys.exit(1)
                print(f"[警告] 選用來源失敗，略過：{e}", file=sys.stderr)
                raw[market][name] = []

    chosen = {}
    for name, urls in TWSE_RWD.items():
        try:
            alt, errs = None, []
            for url in urls:
                try:
                    alt = fetch_twse_rwd(url)
                    break
                except Exception as e:  # noqa: BLE001
                    errs.append(f"{url.split('?')[0].rsplit('/', 1)[-1]}: {e}")
                    time.sleep(3)
            if alt is None:
                raise RuntimeError("；".join(errs))
            time.sleep(3)
            a_date, o_date = latest_date(alt), latest_date(raw["上市"].get(name, []))
            print(f"[比較] 上市/{name}：開放資料 {o_date or '無'}，網站盤後 {a_date}（{len(alt)} 筆）")
            if a_date > o_date:
                raw["上市"][name] = alt
                chosen[name] = f"網站盤後 {a_date}"
            else:
                chosen[name] = f"開放資料 {o_date}"
        except Exception as e:  # noqa: BLE001
            chosen[name] = f"開放資料（備援失敗：{e}）"
            print(f"[警告] 上市/{name} 備援來源失敗，沿用開放資料：{e}", file=sys.stderr)

    companies = build_companies(raw)
    report, errors = diagnose(raw, companies)
    report["上市"]["twse_source"] = chosen
    print_report(report, errors)
    DIAG.parent.mkdir(parents=True, exist_ok=True)
    DIAG.write_text(json.dumps({
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "errors": errors, "markets": report,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    if len(companies) < MIN_COMPANIES:
        errors.append(f"只取得 {len(companies)} 家公司，低於 {MIN_COMPANIES}")
    if errors:
        print("\n欄位檢查未通過，不覆蓋舊資料。詳見 data/diagnostics.json", file=sys.stderr)
        sys.exit(1)

    for c in companies:
        c.pop("_has", None)
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))["companies"]
            print(f"缺財報、沿用上一次資料：{carry_forward(companies, prev)} 家")
        except (ValueError, KeyError) as e:
            print(f"[警告] 讀取上一次資料失敗，不沿用：{e}", file=sys.stderr)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps({
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "companies": companies,
    }, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
    print(f"\n完成：{len(companies)} 家公司 -> {OUT}")


if __name__ == "__main__":
    main()
