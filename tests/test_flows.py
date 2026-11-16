"""資金流向模擬測試。執行：python tests/test_flows.py"""
import copy
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import flows  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("通過" if cond else "失敗"), "-", name, detail)


T86_FIELDS = ["證券代號", "證券名稱", "外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)",
              "外陸資買賣超股數(不含外資自營商)", "外資自營商買進股數", "外資自營商賣出股數", "外資自營商買賣超股數",
              "投信買進股數", "投信賣出股數", "投信買賣超股數", "自營商買賣超股數", "自營商買進股數(自行買賣)",
              "自營商賣出股數(自行買賣)", "自營商買賣超股數(自行買賣)", "自營商買進股數(避險)", "自營商賣出股數(避險)",
              "自營商買賣超股數(避險)", "三大法人買賣超股數"]


def t86_row(code, f, fd, t, d):
    return [code, f"股{code}", "0", "0", f"{f:,}", "0", "0", f"{fd:,}", "0", "0", f"{t:,}", f"{d:,}",
            "0", "0", f"{d - 100:,}", "0", "0", "100", f"{f + fd + t + d:,}"]


def t86(n=300, wrong_total=False):
    rows = [t86_row(str(1100 + i), 1000 * (i - 150), 10 * i, 500 * (i % 7 - 3), -200 * (i % 5)) for i in range(n)]
    rows.append(["0050", "ETF", *(["1"] * 17)])
    if wrong_total:
        for r in rows:
            r[-1] = "999,999"
    return {"stat": "OK", "date": "20261116", "fields": T86_FIELDS, "data": rows}


MARGN_FIELDS = ["代號", "名稱", "買進", "賣出", "現金償還", "前日餘額", "今日餘額", "次一營業日限額",
                "買進", "賣出", "現券償還", "前日餘額", "今日餘額", "次一營業日限額", "資券互抵", "註記"]


def margn_row(code, prev, buy, sell, repay, sprev, sbal):
    bal = prev + buy - sell - repay
    return [code, f"股{code}", f"{buy:,}", f"{sell:,}", f"{repay:,}", f"{prev:,}", f"{bal:,}", "99,999",
            "1", "2", "0", f"{sprev:,}", f"{sbal:,}", "99,999", "0", ""]


def margn(n=300, groups=True, swap=False):
    rows = [margn_row(str(1100 + i), 5000 + i, 100 + i % 9, 80, 5, 300, 290 + i % 4) for i in range(n)]
    t = {"title": "融資融券彙總", "fields": MARGN_FIELDS, "data": rows}
    if groups:
        t["groups"] = [{"start": 2, "span": 6, "title": "融資"}, {"start": 8, "span": 6, "title": "融券"}]
    if swap:  # 欄位順序錯置：把前日餘額與今日餘額對調
        t["fields"] = MARGN_FIELDS[:5] + ["今日餘額", "前日餘額"] + MARGN_FIELDS[7:]
    return {"stat": "OK", "tables": [{"title": "信用交易統計", "fields": ["項目", "買進"], "data": [["融資(交易單位)", "1"]]}, t]}


def test_inst():
    data, diag = flows.parse_inst(t86())
    check("法人：解析上市 T86、排除 ETF", len(data) == 300 and "0050" not in data, f"({len(data)})")
    v = data["1110"]
    check("法人：外資＝不含外資自營商＋外資自營商", v[0] == 1000 * (10 - 150) + 100, str(v))
    check("法人：投信、自營商（合計欄，不重複加自行買賣與避險）", v[1] == 500 * (10 % 7 - 3) and v[2] == -200 * (10 % 5), str(v))
    check("法人：核對 外資＋投信＋自營商＝合計 全部通過", diag["check"] == 1.0, str(diag["map"]))
    _, bad = flows.parse_inst(t86(wrong_total=True))
    check("法人：合計對不上時核對率為 0", bad["check"] == 0.0)
    # 櫃買新網站格式（推測）：tables + 外資及陸資合計欄
    tp_fields = ["代號", "名稱", "外資及陸資(不含外資自營商)-買進股數", "外資及陸資(不含外資自營商)-賣出股數",
                 "外資及陸資(不含外資自營商)-買賣超股數", "外資自營商-買賣超股數", "外資及陸資-買賣超股數",
                 "投信-買賣超股數", "自營商(自行買賣)-買賣超股數", "自營商(避險)-買賣超股數", "自營商-買賣超股數", "三大法人買賣超股數合計"]
    rows = [[str(5000 + i), "x", "0", "0", "1,000", "10", "1,010", "-300", "5", "-7", "-2", "708"] for i in range(250)]
    d2, g2 = flows.parse_inst({"stat": "ok", "tables": [{"fields": tp_fields, "data": rows}]})
    check("法人：櫃買格式（有外資合計欄時不重複相加）", d2["5000"] == [1010.0, -300.0, -2.0] and g2["check"] == 1.0, f"{d2.get('5000')} {g2['map']}")


TPEX_INST = ["代號", "名稱"] + ["買進股數", "賣出股數", "買賣超股數"] * 7 + ["三大法人買賣超股數合計"]


def tpex_inst_row(code, fx, fxd, tr, dself, dhedge, wrong_order=False):
    fx_all, d_all = fx + fxd, dself + dhedge
    groups = [fx, fxd, fx_all, tr, dself, dhedge, d_all]
    if wrong_order:  # 假設網站順序其實不同：投信與外資合計互換
        groups = [fx, fxd, tr, fx_all, dself, dhedge, d_all]
    row = [code, "x"]
    for g in groups:
        row += ["0", "0", f"{g:,}"]
    return row + [f"{fx_all + tr + d_all:,}"]


def test_tpex_inst():
    rows = [tpex_inst_row(str(5000 + i), 1000 * (i - 120), 3 * i, 200 * (i % 5 - 2), 40, -15) for i in range(250)]
    data, diag = flows.parse_inst({"stat": "ok", "tables": [{"fields": TPEX_INST, "data": rows}]})
    check("上櫃法人（真實欄位名、7 組同名）：依位置對應", data["5010"] == [1000 * (10 - 120) + 30, 200 * (10 % 5 - 2), 25] and diag["check"] == 1.0, f"{data.get('5010')} {diag['map']}")
    bad = [tpex_inst_row(str(5000 + i), 1000 * (i - 120), 3 * i, 200 * (i % 5 - 2), 40, -15, wrong_order=True) for i in range(250)]
    _, d2 = flows.parse_inst({"stat": "ok", "tables": [{"fields": TPEX_INST, "data": bad}]})
    check("上櫃法人：若網站順序與假設不同，核對不會通過", d2["check"] < flows.MIN_CHECK, f"({d2['check']})")


TPEX_MARGIN = ["代號", "名稱", "前資餘額(張)", "資買", "資賣", "現償", "資餘額", "資屬證金", "資使用率(%)", "資限額",
               "前券餘額(張)", "券賣", "券買", "券償", "券餘額", "券屬證金", "券使用率(%)", "券限額", "資券相抵(張)", "備註"]


def test_tpex_margin():
    rows = []
    for i in range(250):
        prev, b, s_, r = 3000 + i, 50, 30, 2
        rows.append([str(5000 + i), "x", f"{prev:,}", str(b), str(s_), str(r), f"{prev + b - s_ - r:,}", "0", "1.2", "9,999",
                     "120", "5", "3", "0", "122", "0", "0.1", "9,999", "0", ""])
    data, diag = flows.parse_margin({"stat": "ok", "tables": [{"fields": TPEX_MARGIN, "data": rows}]})
    check("上櫃融資融券（真實欄位名、簡稱）：資餘額不會誤抓前資餘額", data["5000"] == [3018.0, 122.0] and diag["check"] == 1.0, f"{data.get('5000')} {diag['map']}")


def test_unverifiable():
    import datetime
    op = [{"Date": "1151116", "SecuritiesCompanyCode": str(5000 + i), "MarginPurchaseBalancePreviousDay": "100", "ShortSaleBalancePreviousDay": "5"} for i in range(300)]
    orig = flows.get_json
    flows.get_json = lambda url: op if "openapi" in url else {"stat": "ok", "tables": [{"fields": ["x"], "data": [["1"]]}]}
    try:
        data, tried, status = flows.fetch_one("上櫃", "margin", datetime.date(2026, 11, 16))
    finally:
        flows.get_json = orig
    check("無法核對的來源（缺前日餘額或買賣欄位）一律不採用", data is None and status == "fail", str([t.get("rejected") or t.get("error", "")[:20] for t in tried]))


def test_margin():
    data, diag = flows.parse_margin(margn())
    check("融資融券：解析有分組表頭（融資／融券）的格式", len(data) == 300 and data["1100"] == [5000 + 100 - 80 - 5, 290.0], f"{data.get('1100')} {diag['map']}")
    check("融資融券：核對 今日−前日＝買進−賣出−償還 全部通過", diag["check"] == 1.0)
    d2, g2 = flows.parse_margin(margn(groups=False))
    check("融資融券：沒有分組時，同名欄位第一個是融資、第二個是融券", d2["1100"] == data["1100"] and g2["check"] == 1.0, str(g2["map"]))
    _, g3 = flows.parse_margin(margn(swap=True))
    check("融資融券：欄位錯置時核對失敗（不會被採用）", g3["check"] is not None and g3["check"] < flows.MIN_CHECK, f"({g3['check']})")
    op = [{"Date": "1151116", "股票代號": str(1100 + i), "股票名稱": "x", "融資買進": "10", "融資賣出": "5", "融資現金償還": "1",
           "融資前日餘額": "100", "融資今日餘額": "104", "融券今日餘額": "7", "融券前日餘額": "6"} for i in range(250)]
    d4, g4 = flows.parse_margin(op)
    check("融資融券：開放資料 list-of-dict 格式", d4["1100"] == [104.0, 7.0] and g4["check"] == 1.0, str(g4["map"]))
    check("開放資料日期換算", flows.open_date(op) == "2026-11-16")


def test_nodata():
    check("查無資料判斷：證交所 stat 非 OK", flows.no_data({"stat": "很抱歉，沒有符合條件的資料!"}))
    check("查無資料判斷：tables 都是空的", flows.no_data({"stat": "ok", "tables": [{"fields": ["a"], "data": []}]}))
    check("有資料時不誤判", not flows.no_data(t86()))


def test_summary():
    days = ["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-16", "2026-11-17"]
    # 外資：賣、買、買、買、買、買（連買 5 天）；投信：連賣 2 天
    f = [-1000, 2000, 2000, 2000, 2000, 2000]
    t = [500, 500, 500, 500, -300, -300]
    hist = {"inst": {"上市": {d: {"2330": [f[i], t[i], 100]} for i, d in enumerate(days)}},
            "margin": {"上市": {d: {"2330": [1000 + 10 * i, 50]} for i, d in enumerate(days)}}}
    cs = [{"code": "2330", "market": "上市", "price": 100.0, "shares": 1_000_000}, {"code": "9999", "market": "上市", "price": None, "shares": None}]
    s, _ = flows.summarize(hist, cs)
    r = s["2330"]
    check("摘要：近 5 日外資／投信買超（張）", r["foreign5"] == 10 and r["trust5"] == round((500 * 3 - 600) / 1000) and r["n5"] == 5, str({k: r[k] for k in ("foreign5", "trust5", "dealer5", "total5")}))
    check("摘要：外資連買 5 天、投信連賣 2 天", r["foreign_streak"] == 5 and r["trust_streak"] == -2)
    exp_val = round((10000 + 900 + 500) * 100 / 1e8, 2)
    check("摘要：5 日買超金額（億元）以股價估算", r["total5_value"] == exp_val, f"({r['total5_value']} vs {exp_val})")
    check("摘要：籌碼集中度＝5 日買超股數 ÷ 發行股數", r["conc5"] == round(11400 / 1_000_000 * 100, 3), str(r["conc5"]))
    check("摘要：融資 5 日變化與券資比", r["margin"] == 1050 and r["margin_chg5"] == 50 and r["short_ratio"] == round(50 / 1050 * 100, 1), str({k: r.get(k) for k in ("margin", "margin_chg5", "margin_chg5_pct", "short_ratio")}))
    check("摘要：沒有資料的股票不崩潰", s["9999"]["days"] == 0)
    check("連買天數：最後一天為 0 時為 0", flows._streak([5, 5, 0]) == 0 and flows._streak([]) == 0)


def test_fetch_one(monkey=None):
    calls = []

    def fake(url):
        calls.append(url)
        if "rwd/zh/fund/T86" in url:
            raise RuntimeError("timeout")
        return t86()
    orig = flows.get_json
    flows.get_json = fake
    try:
        import datetime
        data, tried, status = flows.fetch_one("上市", "inst", datetime.date(2026, 11, 16))
    finally:
        flows.get_json = orig
    check("抓取：第一個來源失敗時改用第二個", status == "ok" and len(data) == 300 and len(calls) == 2, str([t.get("url", "")[:40] for t in tried]))

    def wrong(url):
        return t86(wrong_total=True)
    flows.get_json = wrong
    try:
        import datetime
        data, tried, status = flows.fetch_one("上市", "inst", datetime.date(2026, 11, 16))
    finally:
        flows.get_json = orig
    check("抓取：核對失敗的來源不採用", data is None and status == "fail")

    def nod(url):
        return {"stat": "很抱歉，沒有符合條件的資料!"}
    flows.get_json = nod
    try:
        import datetime
        data, tried, status = flows.fetch_one("上市", "inst", datetime.date(2026, 11, 15))
    finally:
        flows.get_json = orig
    check("抓取：休市回傳 nodata", status == "nodata" and data is None)


if __name__ == "__main__":
    test_inst()
    test_tpex_inst()
    test_tpex_margin()
    test_unverifiable()
    test_margin()
    test_nodata()
    test_summary()
    test_fetch_one()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} 項通過")
    sys.exit(1 if failed else 0)
