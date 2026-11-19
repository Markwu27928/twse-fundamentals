"""技術指標模擬測試。執行：python tests/test_technical.py（需要 pandas，用來做獨立比對）"""
import json
import math
import pathlib
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import technical as T  # noqa: E402
import score  # noqa: E402

results = []
CFG = json.loads((ROOT / "config" / "scoring.json").read_text(encoding="utf-8"))


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("通過" if cond else "失敗"), "-", name, detail)


RSI_CLOSES = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00]


def test_rsi_reference():
    # 依定義手算：前 14 個變動中，上漲合計 3.34、下跌合計 1.40 → 平均 3.34/14、1.40/14
    ag, al = 3.34 / 14, 1.40 / 14
    first = 100 - 100 / (1 + ag / al)
    check("RSI 第一個值與依定義手算一致（70.46）", abs(T.rsi(RSI_CLOSES[:15]) - first) < 1e-9 and round(first, 2) == 70.46, f"{T.rsi(RSI_CLOSES[:15]):.4f}")
    # 第二天下跌 0.28：平均漲幅 = (ag*13 + 0)/14，平均跌幅 = (al*13 + 0.28)/14（Wilder 平滑）
    ag2, al2 = ag * 13 / 14, (al * 13 + 0.28) / 14
    check("RSI 第二個值與 Wilder 平滑手算一致", abs(T.rsi(RSI_CLOSES[:16]) - (100 - 100 / (1 + ag2 / al2))) < 1e-9)
    check("RSI：資料不足 15 天回傳 None", T.rsi(RSI_CLOSES[:14]) is None)
    check("RSI：只漲不跌為 100", T.rsi([float(i) for i in range(1, 30)]) == 100.0)


def series(n=200, seed=5):
    rng = random.Random(seed)
    xs, p = [], 100.0
    for _ in range(n):
        p *= 1 + rng.gauss(0.0005, 0.02)
        xs.append(round(p, 2))
    return xs


def test_vs_pandas():
    import pandas as pd
    xs = series()
    s = pd.Series(xs)
    mid, sd = s.rolling(20).mean().iloc[-1], s.rolling(20).std(ddof=0).iloc[-1]
    bb = T.bollinger(xs)
    check("布林通道與 pandas 獨立計算一致（中線、上軌、下軌）",
          abs(bb["mid"] - mid) < 1e-9 and abs(bb["upper"] - (mid + 2 * sd)) < 1e-9 and abs(bb["lower"] - (mid - 2 * sd)) < 1e-9)
    check("布林 %B 計算", abs(bb["pct_b"] - (xs[-1] - bb["lower"]) / (bb["upper"] - bb["lower"])) < 1e-12)
    # pandas 的 EMA（adjust=False）以第一個值為起點；本程式以前 n 日平均為起點，200 天後差異應趨近於 0
    ef = s.ewm(span=12, adjust=False).mean()
    es = s.ewm(span=26, adjust=False).mean()
    line = ef - es
    sig = line.ewm(span=9, adjust=False).mean()
    m = T.macd(xs)
    check("MACD 線、訊號線與 pandas 獨立計算一致（相對誤差 < 0.1%）",
          abs(m["macd"] - line.iloc[-1]) <= 1e-3 * abs(line.iloc[-1]) + 1e-6 and abs(m["signal"] - sig.iloc[-1]) <= 1e-3 * abs(sig.iloc[-1]) + 1e-6,
          f"本程式 {m['macd']:.5f}/{m['signal']:.5f}，pandas {line.iloc[-1]:.5f}/{sig.iloc[-1]:.5f}")
    # RSI 與 pandas（Wilder＝ewm alpha 1/14，以前 14 日平均為起點）
    d = s.diff().iloc[1:]
    g, l = d.clip(lower=0).tolist(), (-d.clip(upper=0)).tolist()
    ag, al = sum(g[:14]) / 14, sum(l[:14]) / 14
    for gi, li in zip(g[14:], l[14:]):
        ag, al = ag + (gi - ag) / 14, al + (li - al) / 14
    ref = 100 - 100 / (1 + ag / al)
    check("RSI 與另一種寫法的 Wilder 平滑一致", abs(T.rsi(xs) - ref) < 1e-9)


def test_cross():
    # 先跌後漲：MACD 柱狀體由負轉正
    xs = [100 - i * 0.5 for i in range(60)] + [70 + i * 1.5 for i in range(1, 30)]
    found = None
    for k in range(36, len(xs) + 1):
        m = T.macd(xs[:k])
        if m and m["cross"] == "golden" and m["cross_ago"] == 0:
            found = k
            break
    check("MACD：下跌轉上漲時出現黃金交叉（當天 cross_ago=0）", found is not None, f"(第 {found} 天)")
    ys = [70 + i * 0.5 for i in range(60)] + [100 - i * 1.5 for i in range(1, 30)]
    dead = any((T.macd(ys[:k]) or {}).get("cross") == "dead" for k in range(36, len(ys) + 1))
    check("MACD：上漲轉下跌時出現死亡交叉", dead)
    check("MACD：資料不足 35 天回傳 None", T.macd(xs[:30]) is None)
    # 有雜訊的真實型態：先跌後漲
    import random
    rng = random.Random(9)
    zs, p = [], 100.0
    for i in range(95):
        drift = 0.0 if i < 35 else -0.015 if i < 60 else 0.012   # 盤整 → 急跌 → 反彈
        p *= 1 + drift + rng.gauss(0, 0.004)
        zs.append(p)
    neg = (T.macd(zs[:60]) or {}).get("hist", 0) < 0
    hits = [k for k in range(60, 96) if (T.macd(zs[:k]) or {}).get("cross") == "golden" and T.macd(zs[:k])["cross_ago"] == 0]
    check("MACD：有雜訊的急跌後反彈，柱狀體先轉負、反彈後抓到黃金交叉", neg and bool(hits), f"(急跌末柱狀體為負：{neg}；交叉在第 {hits[:1]} 天)")


def test_analyze():
    xs = series(80)
    a = T.analyze(xs)
    check("analyze：80 天資料有 RSI、布林、MACD", a.get("rsi") is not None and "bb" in a and "macd" in a and a["days"] == 80)
    b = T.analyze(xs[:40])
    check("analyze：少於 60 天只算布林，不算 RSI、MACD", "bb" in b and "rsi" not in b and "macd" not in b)
    gap = xs[:70] + [xs[69] * 0.8] + [xs[69] * 0.8] * 9
    check("analyze：單日跌幅超過跌停（除權息、減資）標示價格跳空", T.analyze(gap)["gap"] is True and a["gap"] is False)


def test_extra():
    xs = series(85)
    a = T.analyze(xs)
    check("RSI 5 天前的值＝少 5 天資料的 RSI", a["rsi_prev5"] == round(T.rsi(xs[:-5]), 2))
    # 盤整後波動收窄：帶寬百分位應接近 0
    calm = series(60, seed=2) + [series(60, seed=2)[-1] * (1 + 0.0005 * (i % 2)) for i in range(25)]
    ac = T.analyze(calm)
    check("布林帶寬百分位：波動收窄時接近 0（通道收窄）", ac["bb"].get("width_rank") is not None and ac["bb"]["width_rank"] <= 5, str(ac["bb"].get("width_rank")))
    up = [100.0] * 72 + [100 * 1.02 ** i for i in range(1, 14)]      # 盤整後最近 13 天加速上漲
    m = T.macd(up)
    check("MACD 柱狀體連續放大天數為正", m["hist_trend"] > 0, str(m["hist_trend"]))
    dn = [200 - x for x in up]                                        # 上漲情境的鏡像：跌勢加速
    check("MACD 柱狀體連續縮小天數為負", T.macd(dn)["hist_trend"] < 0)
    check("資料只有 30 天：沒有帶寬百分位", "width_rank" not in T.analyze(xs[:30])["bb"])


def test_parse():
    j = {"stat": "OK", "date": "20261117", "tables": [
        {"title": "價格指數", "fields": ["指數", "收盤指數"], "data": [["發行量加權股價指數", "28,000.00"]]},
        {"title": "每日收盤行情", "fields": ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "漲跌(+/-)", "漲跌價差"],
         "data": [["2330", "台積電", "1", "1", "1", "1", "1", "1", "1,450.00", "<p style= color:red>+</p>", "5.00"],
                  ["0050", "元大台灣50", "1", "1", "1", "1", "1", "1", "60.00", "", "0"],
                  ["1101", "台泥", "1", "1", "1", "1", "1", "1", "--", "", "0"]]}]}
    d = T.closes_from_tables(j)
    check("上市 MI_INDEX：取出個股收盤價、排除 ETF 與無成交（--）", d == {"2330": 1450.0}, str(d))
    a = {"aaData": [["6488", "環球晶", "460.50", "+3.00", "455.00"], ["00679B", "x", "30", "0", "30"], ["5347", "世界", "--", "", ""]]}
    check("上櫃舊網站 aaData：依位置取收盤價", T.closes_from_aadata(a) == {"6488": 460.5})
    tp = {"stat": "ok", "tables": [{"fields": ["代號", "名稱", "收盤", "漲跌", "開盤"], "data": [["6488", "環球晶", "460.50", "+3", "455"]]}]}
    check("上櫃新網站 tables：欄位『收盤』", T.closes_from_tables(tp) == {"6488": 460.5})


def test_adjust():
    base = {"days": 80, "gap": False, "rsi": 50.0, "bb": {"pct_b": 0.5}, "macd": {"cross": None, "cross_ago": None}}
    check("技術面：中性時不加減分", score.tech_adjust(base, CFG)[0] == 0)
    p, notes, sig = score.tech_adjust(dict(base, rsi=25.0, bb={"pct_b": -0.1}, macd={"cross": "golden", "cross_ago": 1}), CFG)
    check("技術面：超賣＋跌破下軌＋黃金交叉，合計上限 +5", p == CFG["technical"]["max_abs"] and set(sig) == {"RSI超賣", "跌破布林下軌", "MACD黃金交叉"}, f"{p} {sig}")
    p2, _, s2 = score.tech_adjust(dict(base, rsi=85.0, macd={"cross": "dead", "cross_ago": 0}), CFG)
    check("技術面：過熱＋死亡交叉扣分", p2 == CFG["technical"]["rsi_overheat_pts"] + CFG["technical"]["macd_dead_pts"] and "RSI過熱" in s2)
    p3, _, s3 = score.tech_adjust(dict(base, bb={"pct_b": 1.2}), CFG)
    check("技術面：突破布林上軌不扣分（研究：突破後反而有動能）", p3 == 0 and "突破布林上軌" in s3)
    p4, n4, s4 = score.tech_adjust(dict(base, rsi=20.0, gap=True), CFG)
    check("技術面：價格跳空時不加減分並說明", p4 == 0 and s4 == ["價格跳空"] and "除權息" in n4[0])
    p5, _, _ = score.tech_adjust(dict(base, macd={"cross": "golden", "cross_ago": 4}), CFG)
    check("技術面：交叉超過 3 天不計", p5 == 0)
    check("技術面：沒有資料時不調整", score.tech_adjust(None, CFG) == (0, [], []))


if __name__ == "__main__":
    test_rsi_reference()
    test_vs_pandas()
    test_cross()
    test_analyze()
    test_extra()
    test_parse()
    test_adjust()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} 項通過")
    sys.exit(1 if failed else 0)
