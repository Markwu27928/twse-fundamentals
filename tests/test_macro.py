"""匯率與外資判讀的模擬測試。執行：python tests/test_macro.py"""
import datetime
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import macro  # noqa: E402

CFG = {"days": 5, "foreign_threshold_100m": 300, "fx_window_calendar_days": 7, "fx_threshold_pct": 0.5}
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("通過" if cond else "失敗"), "-", name, detail)


def bfi(date, foreign, dealer):
    return {"stat": "OK", "date": date, "title": "x",
            "fields": ["單位名稱", "買進金額", "賣出金額", "買賣差額"],
            "data": [["自營商(自行買賣)", "1,000", "2,000", "-1,000"],
                     ["投信", "5", "3", "2"],
                     ["外資及陸資(不含外資自營商)", "9", "9", f"{foreign:,}"],
                     ["外資自營商", "0", "0", f"{dealer:,}"],
                     ["合計", "1", "1", "0"]]}


def test_parse():
    r = macro.parse_foreign(bfi("20261116", -8_500_000_000, 100_000_000))
    check("外資：外資及陸資＋外資自營商，換算成億元", r[0] == "2026-11-16" and r[1] == -84.0, str(r))
    check("外資：休市（沒有資料）回傳 None", macro.parse_foreign({"stat": "很抱歉，沒有符合條件的資料!"}) is None)
    try:
        macro.parse_foreign({"stat": "OK", "date": "20261116", "fields": ["a", "b"], "data": [["x", "1"]]})
        ok = False
    except RuntimeError as e:
        ok = "買賣差額" in str(e)
    check("外資：欄位改名時報錯而不是亂算", ok)
    try:
        macro.parse_foreign({"stat": "OK", "date": "20261116", "fields": ["單位名稱", "買賣差額"], "data": [["投信", "1"]]})
        ok = False
    except RuntimeError as e:
        ok = "外資" in str(e)
    check("外資：找不到外資列時報錯", ok)
    check("匯率：解析 currency-api 格式", macro.parse_fx({"date": "2026-11-16", "usd": {"twd": 31.2345, "jpy": 150}}) == ("2026-11-16", 31.2345))
    try:
        macro.parse_fx({"date": "2026-11-16", "usd": {"jpy": 150}})
        ok = False
    except RuntimeError:
        ok = True
    check("匯率：沒有台幣時報錯", ok)


def hist(fx, fr):
    return {"fx_cbc": fx, "fx_ref": {}, "foreign": fr}


CBC_HTML = """<html><body><h2>新臺幣/美元 銀行間收盤匯率</h2>
<table><tr><th>日期</th><th>NTD/USD</th></tr>
<tr><td>2026/11/17</td><td>31.420</td></tr>
<tr><td>2026/11/16</td><td>31.312</td></tr>
<tr><td>2026/11/13</td><td>31.010</td></tr>
<tr><td>2026/11/12</td><td>30.985</td></tr>
<tr><td>2026/11/11</td><td>31.002</td></tr>
<tr><td>2026/11/10</td><td>31.050</td></tr>
</table><p>更新日期：2026/11/17 共 3409 筆 2026/11/17 16:05</p></body></html>"""


def test_cbc():
    r = macro.parse_cbc(CBC_HTML)
    check("央行收盤匯率：解析日期與匯率", r.get("2026-11-17") == 31.42 and r.get("2026-11-10") == 31.05 and len(r) == 6, str(r))
    check("央行收盤匯率：不把『16:05』等時間或筆數誤當匯率", all(20 <= v <= 45 for v in r.values()))
    try:
        macro.parse_cbc("<html>系統維護中</html>")
        ok = False
    except RuntimeError:
        ok = True
    check("央行頁面格式改變時報錯", ok)
    today = datetime.date(2026, 11, 17)
    h = {"fx_cbc": r, "fx_ref": {"2026-11-17": 31.40, "2026-11-16": 31.30}, "foreign": {"2026-11-17": -100.0}}
    s = macro.summarize(h, CFG, today)
    check("有央行資料時用央行收盤價並標示來源", s["fx"]["source"] == "cbc" and s["fx"]["rate"] == 31.42 and "央行" in s["fx"]["source_label"])
    check("央行單日變動：31.420 對 31.312", s["fx"]["change_1d_pct"] == round((31.42 / 31.312 - 1) * 100, 2))
    old = {k: v for k, v in r.items() if k <= "2026-11-10"}
    s2 = macro.summarize({"fx_cbc": old, "fx_ref": {"2026-11-17": 31.40}, "foreign": {"2026-11-17": -900.0}}, CFG, today)
    check("官方資料超過 5 天沒更新：匯率顯示暫缺、不用其他報價替代", s2["fx"] is None, str(s2["fx"]))
    check("匯率暫缺時：只用外資判讀並說明", s2["verdict"]["label"] == "偏匯出" and "匯率資料不足" in s2["verdict"]["detail"])

    # 央行開放資料 API：格式未知，測多種可能格式
    rows = [("2026/11/17", "31.420"), ("2026/11/16", "31.312"), ("2026/11/13", "31.010"), ("2026/11/12", "30.985"), ("2026/11/11", "31.002")]
    j1 = json.dumps([{"日期": d, "NTD/USD": v} for d, v in rows], ensure_ascii=False)
    j2 = json.dumps({"result": {"records": [{"Date": d.replace("/", ""), "Value": float(v)} for d, v in rows]}})
    j3 = json.dumps({"data": [[d, v] for d, v in rows]})
    txt = "日期,NTD/USD\n" + "\n".join(f"{d},{v}" for d, v in rows)
    for name, c in (("JSON 物件陣列（中文欄位）", j1), ("巢狀 JSON（西元 8 碼日期、數值）", j2), ("JSON 二維陣列", j3), ("CSV 純文字", txt)):
        r2 = macro.parse_cbc_api(c)
        check(f"央行 API 格式：{name}", r2.get("2026-11-17") == 31.42 and r2.get("2026-11-11") == 31.002 and len(r2) == 5, str(r2))
    try:
        macro.parse_cbc_api(json.dumps({"message": "error"}))
        ok = False
    except RuntimeError:
        ok = True
    check("央行 API 回傳錯誤訊息時報錯", ok)
    j4 = json.dumps([{"日期": d, "NTD/USD": v, "筆數": 3409} for d, v in rows], ensure_ascii=False)
    check("央行 API：不把其他數字欄位誤當匯率", len(macro.parse_cbc_api(j4)) == 5)



def test_verdict():
    base_fx = {"2026-11-09": 31.00, "2026-11-12": 31.05, "2026-11-15": 31.10, "2026-11-16": 31.30}
    # 台幣貶值 0.97% + 外資賣超 → 明顯匯出
    r = macro.summarize(hist(base_fx, {f"2026-11-1{i}": -120.0 for i in range(0, 5)}), CFG, None)
    check("判讀：台幣貶值＋外資大賣 → 明顯匯出", r["verdict"]["label"] == "明顯匯出", r["verdict"]["detail"])
    check("匯率：近一週變動計算（31.30 對 31.00）", abs(r["fx"]["change_win_pct"] - 0.97) < 0.01 and r["fx"]["change_1d_pct"] == round((31.30 / 31.10 - 1) * 100, 2), str(r["fx"]))
    check("外資：只取近 5 日累計", r["foreign"]["days"] == 5 and r["foreign"]["sum"] == -600.0)
    strong = {"2026-11-09": 31.50, "2026-11-16": 31.00}
    r = macro.summarize(hist(strong, {f"2026-11-1{i}": 90.0 for i in range(0, 5)}), CFG, None)
    check("判讀：台幣升值＋外資買超 → 明顯匯入", r["verdict"]["label"] == "明顯匯入", r["verdict"]["detail"])
    r = macro.summarize(hist(strong, {f"2026-11-1{i}": 10.0 for i in range(0, 5)}), CFG, None)
    check("判讀：外資買賣超不大、台幣升值 → 偏匯入", r["verdict"]["label"] == "偏匯入")
    r = macro.summarize(hist(base_fx, {f"2026-11-1{i}": 100.0 for i in range(0, 5)}), CFG, None)
    check("判讀：外資買超但台幣貶值 → 訊號分歧", r["verdict"]["label"] == "訊號分歧", r["verdict"]["detail"])
    flat = {"2026-11-09": 31.00, "2026-11-16": 31.02}
    r = macro.summarize(hist(flat, {f"2026-11-1{i}": 5.0 for i in range(0, 5)}), CFG, None)
    check("判讀：都沒有明顯變化 → 中性", r["verdict"]["label"] == "中性")
    r = macro.summarize(hist({"2026-11-16": 31.0}, {f"2026-11-1{i}": -200.0 for i in range(0, 5)}), CFG, None)
    check("匯率只有一天資料：只用外資判讀並說明", r["verdict"]["label"] == "偏匯出" and "匯率資料不足" in r["verdict"]["detail"] and r["fx"]["change_win_pct"] is None, r["verdict"]["detail"])
    r = macro.summarize(hist({}, {}), CFG, None)
    check("完全沒有資料：不產生判讀、不崩潰", r["verdict"] is None and r["fx"] is None and r["foreign"] is None)
    r = macro.summarize(hist(base_fx, {"2026-11-16": 50.0}), CFG, None)
    check("外資只有 1 天資料：以實際天數累計", r["foreign"]["days"] == 1 and r["foreign"]["sum"] == 50.0)


def test_missing_days():
    today = datetime.date(2026, 11, 16)  # 星期一
    h = {"foreign": {"2026-11-13": 1.0}, "closed": ["2026-11-11"]}
    miss = macro.trading_days_missing(h, today, lookback=7)
    ds = [d.isoformat() for d in miss]
    check("補抓：跳過週末、已記錄與已標休市的日期", "2026-11-14" not in ds and "2026-11-15" not in ds and "2026-11-13" not in ds and "2026-11-11" not in ds, str(ds))
    check("補抓：包含今天與未記錄的平日", "2026-11-16" in ds and "2026-11-12" in ds, str(ds))


if __name__ == "__main__":
    test_parse()
    test_cbc()
    test_verdict()
    test_missing_days()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} 項通過")
    sys.exit(1 if failed else 0)
