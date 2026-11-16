"""匯率與外資判讀的模擬測試。執行：python tests/test_macro.py"""
import datetime
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
    return {"fx": fx, "foreign": fr}


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
    test_verdict()
    test_missing_days()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} 項通過")
    sys.exit(1 if failed else 0)
