"""績效追蹤模擬測試。執行：python tests/test_tracking.py"""
import copy
import datetime
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import tracking  # noqa: E402

results = []
CFG = {"metrics": [{"key": "x"}], "buy_above": 80, "sell_below": 35}


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("通過" if cond else "失敗"), "-", name, detail)


def universe():
    out = []
    for i in range(100):
        label = "買進" if i < 10 else "賣出" if i >= 90 else "觀望"
        out.append({"code": str(1000 + i), "price": 100.0, "score": 100 - i, "label": label, "potential": i in (0, 1, 50)})
    out.append({"code": "9999", "price": None, "score": 50, "label": "觀望"})  # 沒有價格不納入
    out.append({"code": "8888", "price": 20.0, "score": None, "label": "資料不足"})
    return out


def test_form():
    st = {}
    d = datetime.date(2026, 11, 18)
    check("第一次執行：不論日期都建立期別", tracking.maybe_form(st, universe(), d, CFG) and st["cohorts"][0]["id"] == "2026-11")
    check("同月份不重複建立", not tracking.maybe_form(st, universe(), datetime.date(2026, 11, 25), CFG) and len(st["cohorts"]) == 1)
    check("下個月 11 日以前不建立", not tracking.maybe_form(st, universe(), datetime.date(2026, 12, 10), CFG))
    check("下個月 11 日以後建立新期別", tracking.maybe_form(st, universe(), datetime.date(2026, 12, 11), CFG) and st["cohorts"][-1]["id"] == "2026-12")
    g = st["cohorts"][0]["groups"]
    check("分組：買進 10、賣出 10、前後 20% 各 20、全市場 100（排除無價格、無分數）",
          len(g["買進"]) == 10 and len(g["賣出"]) == 10 and len(g["評分前20%"]) == 20 and len(g["評分後20%"]) == 20 and len(g["全市場"]) == 100,
          str({k: len(v) for k, v in g.items()}))
    check("無價格的股票不納入、進場價有記錄", "9999" not in st["cohorts"][0]["entry"] and st["cohorts"][0]["entry"]["1000"] == 100.0)
    check("記錄評分規則版本", st["cohorts"][0]["rules"] == tracking.rules_version(CFG))


def test_eval():
    st = {}
    tracking.maybe_form(st, universe(), datetime.date(2026, 11, 18), CFG)
    later = universe()
    for x in later:
        i = int(x["code"]) - 1000
        if x["price"] and 0 <= i < 100:
            x["price"] = 110.0 if i < 10 else 95.0 if i >= 90 else 100.0  # 買進 +10%、賣出 -5%
    res = tracking.evaluate(st, later, datetime.date(2026, 12, 18))[0]
    g = res["groups"]
    check("買進組報酬 +10%", g["買進"]["ret"] == 10.0, str(g["買進"]))
    check("賣出組報酬 -5%", g["賣出"]["ret"] == -5.0)
    base = (10 * 10 - 5 * 10) / 100
    check("全市場等權報酬與超額報酬", g["全市場"]["ret"] == round(base, 2) and g["買進"]["excess"] == round(10 - base, 2), f"base={g['全市場']['ret']}")
    check("勝率：買進組 100% 跑贏、賣出組 0%", g["買進"]["win"] == 100.0 and g["賣出"]["win"] == 0.0)
    check("持有天數", res["days"] == 30)
    # 異常報酬（減資、分割）排除
    weird = copy.deepcopy(later)
    for x in weird:
        if x["code"] == "1000":
            x["price"] = 900.0
    res2 = tracking.evaluate(st, weird, datetime.date(2026, 12, 19))[0]
    check("單檔報酬異常（+800%）排除並列出", "1000" in res2["excluded"] and res2["groups"]["買進"]["n"] == 9)
    # 停牌：沒有新價格時沿用最後價格
    gone = [x for x in later if x["code"] != "1001"]
    st2 = copy.deepcopy(st)
    tracking.evaluate(st2, later, datetime.date(2026, 12, 18))
    r3 = tracking.evaluate(st2, gone, datetime.date(2026, 12, 20))[0]
    check("停牌股沿用最後價格計算", r3["groups"]["買進"]["n"] == 10 and r3["groups"]["買進"]["ret"] == 10.0)


def test_update():
    st = {}
    f, s = tracking.update(st, universe(), datetime.date(2026, 11, 18), CFG)
    check("update：建立並回傳摘要", f and s["cohorts"][0]["id"] == "2026-11" and s["cohorts"][0]["groups"]["買進"]["ret"] == 0.0)
    cfg2 = dict(CFG, buy_above=75)
    check("規則改變時版本號不同", tracking.rules_version(cfg2) != tracking.rules_version(CFG))


if __name__ == "__main__":
    test_form()
    test_eval()
    test_update()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} 項通過")
    sys.exit(1 if failed else 0)
