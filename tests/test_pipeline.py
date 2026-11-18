"""模擬測試：解析 → 評分 → 排名 → 輸出。執行：python tests/test_pipeline.py"""
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import fetch  # noqa: E402
import score  # noqa: E402
import fixtures  # noqa: E402
import monthly  # noqa: E402

CFG = json.loads((ROOT / "config" / "scoring.json").read_text(encoding="utf-8"))
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("通過" if cond else "失敗"), "-", name, detail)


def test_helpers():
    check("num 處理千分位", fetch.num("1,234.5") == 1234.5)
    check("num 處理空值與破折號", fetch.num("") is None and fetch.num("--") is None and fetch.num(None) is None)
    row = {"﻿公司代號": "2330", "營業毛利（毛損）": "5", "本期淨利（淨損）": "7", "繼續營業單位本期淨利（淨損）": "9"}
    check("pick 去除 BOM", fetch.pick(row, fetch.CODE) == "2330")
    check("pick 開頭比對，不誤抓繼續營業單位淨利", fetch.pick(row, fetch.F["net_income"]) == "7")
    check("pick 找不到回傳 None", fetch.pick(row, ["不存在"]) is None)


def test_parse(raw):
    cs = fetch.build_companies(raw)
    codes = {c["code"] for c in cs}
    check("排除 ETF（0050）", "0050" not in codes)
    check("排除存託憑證（9105）、保留 99 開頭股票（9999）", "9105" not in codes and "9999" in codes)
    check("公司數量", len(cs) > 1700, f"({len(cs)})")
    bom = [c for c in cs if c["code"] == "1101"]
    check("BOM 欄位的公司仍能解析財報", bom and bom[0]["revenue"] is not None)
    nof = [c for c in cs if c["code"] == "9999"][0]
    check("無財報公司不崩潰且欄位為 None", nof["revenue"] is None and nof["pe"] is None)
    check("市值換算（億）", all(c["mcap"] is None or c["mcap"] > 0 for c in cs))
    nt = [c for c in cs if c["price"] is None]
    check("無成交價（--）解析為 None", len(nt) > 0 and all(c["mcap"] is None for c in nt))
    tp = [c for c in cs if c["market"] == "上櫃" and c["pe"] is not None]
    check("上櫃欄位名稱（PriceEarningRatio 等）可解析", len(tp) > 100)
    gp = [c for c in cs if c["gross_profit"] is not None]
    check("全形括號欄位（營業毛利（毛損））可解析", len(gp) > 1000)
    rep, errs = fetch.diagnose(raw, cs)
    check("欄位檢查：正常資料無錯誤", not errs, str(errs))
    check("欄位檢查：記錄實際對應欄位", rep["上市"]["fields"]["net_income"]["matched_key"] == "本期淨利（淨損）")
    # 欄位改名時必須被抓出來
    import copy
    bad = copy.deepcopy(raw)
    for r in bad["上市"]["income"]:
        r["稅後淨利"] = r.pop("本期淨利（淨損）")
    _, errs2 = fetch.diagnose(bad, fetch.build_companies(bad))
    check("欄位檢查：淨利欄位改名時會報錯", any("net_income" in e for e in errs2), str(errs2[:1]))
    bad2 = copy.deepcopy(raw)
    for r in bad2["上櫃"]["valuation"]:
        r["StockNo"] = r.pop("SecuritiesCompanyCode")
    _, errs3 = fetch.diagnose(bad2, fetch.build_companies(bad2))
    check("欄位檢查：代號欄位對不上時會報錯", any("valuation" in e for e in errs3), str(errs3[:1]))
    prev = [{"code": "9999", "fs_format": "ci", "year": "115", "season": 2, "net_income": 5.0, "equity": 50.0, "revenue": 9.0},
            {"code": "1102", "fs_format": "ci", "year": "115", "season": 2, "net_income": 1.0, "equity": 1.0}]
    cur = [dict(c) for c in cs if c["code"] in ("9999", "1102")]
    n = fetch.carry_forward(cur, prev)
    by = {c["code"]: c for c in cur}
    check("缺財報時沿用上一次財報並標記", n == 1 and by["9999"]["stale"] and by["9999"]["net_income"] == 5.0 and by["9999"]["season"] == 2)
    check("本次有財報時不被舊資料覆蓋", not by["1102"].get("stale") and by["1102"]["net_income"] != 1.0 and by["1102"]["equity"] != 1.0)
    return cs


MOPS_HTML = """<html><body><table><tr><th>產業別：半導體業</th></tr></table>
<table border=1>
<tr><th rowspan=2>公司代號</th><th rowspan=2>公司名稱</th><th colspan=5>營業收入</th><th colspan=3>累計營業收入</th><th rowspan=2>備註</th></tr>
<tr><th>當月營收</th><th>上月營收</th><th>去年當月營收</th><th>上月比較增減(%)</th><th>去年同月增減(%)</th>
<th>當月累計營收</th><th>去年累計營收</th><th>前期比較增減(%)</th></tr>
<tr><td>2330</td><td>台積電</td><td>330,000,000</td><td>300,000,000</td><td>250,000,000</td><td>10.00</td><td>32.00</td><td>1</td><td>1</td><td>1</td><td>-</td></tr>
<tr><td>2303</td><td>聯電</td><td>20,000,000</td><td>21,000,000</td><td>19,000,000</td><td>-4.76</td><td>5.26</td><td>1</td><td>1</td><td>1</td><td>-</td></tr>
<tr><td>合計</td><td></td><td>1</td><td>1</td><td>1</td><td>1</td><td>1</td><td>1</td><td>1</td><td>1</td><td></td></tr>
</table></body></html>""" + "<!--" + "x" * 6000 + "-->"


def test_monthly():
    rows = monthly.parse_mops_html(MOPS_HTML)
    check("觀測站彙總表：解析兩層表頭與千分位", rows.get("2330") == {"rev": 330000000.0, "mom": 10.0, "yoy": 32.0} and rows["2303"]["mom"] == -4.76, str(rows))
    check("觀測站彙總表：排除『合計』等非公司列", set(rows) == {"2330", "2303"})
    bad = MOPS_HTML.replace("上月比較增減(%)", "環比").replace("去年同月增減(%)", "同比")
    try:
        monthly.parse_mops_html(bad)
        ok = False
    except RuntimeError:
        ok = True
    check("觀測站彙總表：欄位改名時報錯而不是空白通過", ok)
    check("月份換算：開放資料 11510 → 202610、跨年上一月", monthly.parse_open_ym("11510") == "202610" and monthly.prev_ym("202601") == "202512")
    h = {}
    monthly.merge(h, "202610", {"A": {"rev": 1, "mom": 5, "yoy": 10}, "B": {"rev": 1, "mom": 5, "yoy": -1}, "C": {"rev": 1, "mom": 2, "yoy": 3}})
    monthly.merge(h, "202609", {"A": {"rev": 1, "mom": 1, "yoy": 2}, "B": {"rev": 1, "mom": 9, "yoy": 9}, "C": {"rev": 1, "mom": -1, "yoy": 3}})
    monthly.merge(h, "202608", {"A": {"rev": 1, "mom": 3, "yoy": 3}})
    check("連續成長：A 連 3 個月、B 當月年增為負 0、C 上月月增為負只算 1",
          monthly.streak(h["A"], "202610") == 3 and monthly.streak(h["B"], "202610") == 0 and monthly.streak(h["C"], "202610") == 1)
    h2 = {"D": {"202610": {"mom": 1, "yoy": 1}, "202608": {"mom": 1, "yoy": 1}}}
    check("連續成長：月份中斷不能接續計算", monthly.streak(h2["D"], "202610") == 1)
    for i in range(30):
        monthly.merge(h, f"20{24 + i // 12}{i % 12 + 1:02d}", {"A": {"rev": 1, "mom": 1, "yoy": 1}})
    check("歷史：每檔只保留 24 個月", len(h["A"]) == 24)
    cs = [{"code": "A", "rev_month": "202610"}, {"code": "Z", "rev_month": "202610"}, {"code": "C", "rev_month": None}]
    hh = {"A": {"202610": {"mom": 5, "yoy": 10}, "202609": {"mom": 1, "yoy": 2}}}
    import fetch as fe
    fe.apply_revenue_history(cs, hh)
    check("寫回公司：上月月增年增與連續月數", cs[0]["rev_prev_month"] == "202609" and cs[0]["rev_prev_mom"] == 1 and cs[0]["rev_streak"] == 2)
    check("寫回公司：沒有歷史或沒有月份時為 0 且不崩潰", cs[1]["rev_streak"] == 0 and cs[1]["rev_prev_month"] is None and cs[2]["rev_streak"] == 0)


def mk_hist(yoys, revs=None, start="202610"):
    seq = monthly.months_back(start, len(yoys))
    revs = revs or [200000.0] * len(yoys)
    return {ym: {"rev": r, "mom": 1.0, "yoy": y} for ym, y, r in zip(seq, yoys, revs)}


def test_growth():
    # 近 3 月年增 30/28/26、前 3 月 10/12/14，營收相同 → 合計年增介於各月之間
    h = mk_hist([30, 28, 26, 10, 12, 14, 5, 5, 5, 5, 5, 5], revs=[500000.0] + [200000.0] * 11)
    g = monthly.growth_profile(h, "202610")
    exp3 = round((900000 / (500000 / 1.30 + 200000 / 1.28 + 200000 / 1.26) - 1) * 100, 2)
    check("成長輪廓：近 3 月以營收合計計算年增", g["avg3_yoy"] == exp3 and 10 < g["prev3_yoy"] < 14 and g["accel"] == round(exp3 - g["prev3_yoy"], 2), str({k: g[k] for k in ("avg3_yoy", "prev3_yoy", "accel")}))
    check("成長輪廓：12 個月新高、近 3 月皆為正、近 6 月明細", g["high12"] is True and g["all3_pos"] is True and len(g["hist"]) == 6 and g["hist"][0][0] == "202610")
    # 單月暴衝：平均法會變成 3000% 以上，合計法只反映實際營收倍數
    spike = monthly.growth_profile(mk_hist([9900, 5, 5, 5, 5, 5], revs=[1000000.0, 100000.0, 100000.0, 100000, 100000, 100000]), "202610")
    check("合計年增：單月暴衝不被放大（遠低於各月平均）", spike["avg3_yoy"] < 500, f"({spike['avg3_yoy']}%)")
    check("合計年增：年增 ≤ −100% 無法還原時為 None", monthly._agg_yoy([(100.0, -100.0), (100.0, 5.0)]) is None)
    g5 = monthly.growth_profile(mk_hist([30, 28, 26, 10, 12]), "202610")
    check("成長輪廓：不足 6 個月不算加速、不足 12 個月不判新高", g5["accel"] is None and g5["high12"] is None and g5["avg3_yoy"] is not None)
    gap = mk_hist([30, 28, 26, 10, 12, 14]); del gap["202609"]
    check("成長輪廓：中間缺月時不算年增", monthly.growth_profile(gap, "202610")["avg3_yoy"] is None)

    base = dict(code="X", industry="24", rev_cur=500000.0, rev_yoy_m=30.0, op_income=1.0)
    strong = monthly.growth_profile(mk_hist([45, 40, 38, 10, 12, 14, 5, 5, 5, 5, 5, 5], revs=[500000.0] + [200000.0] * 11), "202610")
    ok, rank, notes = score.growth_potential(dict(base, growth=strong), CFG)
    check("潛力股：高成長＋加速＋新高＋每月皆正 → 符合", ok and "最新月營收創近 12 個月新高" in notes, f"{rank} {notes}")
    check("潛力股：建材營造業不適用", not score.growth_potential(dict(base, industry="14", growth=strong), CFG)[0])
    nohigh = monthly.growth_profile(mk_hist([45, 40, 38, 10, 12, 14, 5, 5, 5, 5, 5, 5], revs=[200000.0] * 11 + [900000.0]), "202610")
    check("潛力股：未創 12 個月新高 → 不符合", not score.growth_potential(dict(base, growth=nohigh), CFG)[0])
    dip = monthly.growth_profile(mk_hist([80, -5, 60, 10, 12, 14, 5, 5, 5, 5, 5, 5], revs=[500000.0] + [200000.0] * 11), "202610")
    check("潛力股：近 3 個月有一個月年增為負 → 不符合", not score.growth_potential(dict(base, growth=dip), CFG)[0])
    slow = monthly.growth_profile(mk_hist([42, 41, 40, 34, 34, 34, 5, 5, 5, 5, 5, 5], revs=[200001.0] + [200000.0] * 11), "202610")
    check("潛力股：加速不到 10 個百分點 → 不符合", not score.growth_potential(dict(base, growth=slow), CFG)[0], f"(加速 {slow['accel']})")
    low = monthly.growth_profile(mk_hist([20, 19, 18, 1, 1, 1, 5, 5, 5, 5, 5, 5], revs=[500000.0] + [200000.0] * 11), "202610")
    check("潛力股：加速但成長不到 25% → 不符合", not score.growth_potential(dict(base, growth=low), CFG)[0])
    check("潛力股：月營收不到 1 億 → 不符合（基期過小）", not score.growth_potential(dict(base, rev_cur=50000.0, growth=strong), CFG)[0])
    check("潛力股：本業虧損 → 不符合", not score.growth_potential(dict(base, op_income=-1.0, growth=strong), CFG)[0])
    check("潛力股：最新月份年增轉負 → 不符合", not score.growth_potential(dict(base, rev_yoy_m=-2.0, growth=strong), CFG)[0])
    ok5, _, n5 = score.growth_potential(dict(base, growth=g5), CFG)
    check("潛力股：歷史不足 6 個月 → 不符合並說明原因", not ok5 and any("資料不足" in x for x in n5), str(n5))
    ok0, r0, _ = score.growth_potential(dict(base, growth=None), CFG)
    check("潛力股：沒有營收歷史 → 不符合、不崩潰", not ok0 and r0 is None)


def test_archetypes():
    for c, expect in fixtures.archetypes():
        _, _, s, label, cov, flags, up, dn = score.score_company(c, CFG)
        check(f"樣本 {c['code']} 預期 {expect}", label == expect, f"(得 {label}，分數 {s and round(s,1)}，旗標 {flags})")
    c = fixtures.archetypes()[0][0]
    _, _, s, label, cov, *_ = score.score_company(dict(c, gross_profit=None, op_income=None, equity=None,
                                                       net_income=None, total_assets=None, cur_assets=None,
                                                       rev_yoy=None, pe=None, pb=None, dy=None), CFG)
    check("全缺資料 → 資料不足", label == "資料不足" and s is None)
    mid = dict(c, gross_profit=2.0e7, op_income=8e6, net_income=7e6, rev_yoy=8.0, rev_yoy_m=8.0, rev_mom=3.0, pe=20.0, pb=2.2, dy=3.0)
    _, _, s0, *_ = score.score_company(dict(mid, rev_streak=1), CFG)
    m2, _, s2, l2, _, _, up2, _ = score.score_company(dict(mid, rev_streak=2), CFG)
    check("營收連兩月成長：加分並列入優勢第一項", round(s2 - s0, 6) == CFG["revenue_streak"]["bonus"] and up2 and up2[0].startswith("月營收連 2 個月"), f"({s0:.1f}→{s2:.1f}) {up2[:1]}")
    check("單月營收月增、年增納入評分指標", "rev_mom" in m2 and "rev_yoy_m" in m2)
    _, _, s9, *_ = score.score_company(dict(c, rev_streak=3, rev_yoy_m=40.0, rev_mom=20.0), CFG)
    check("加分後分數上限 100", s9 <= 100.0, f"({s9})")
    # 研究依據的新指標
    base = dict(mid, mcap=100.0, rev_sum12=5_000_000.0, total_assets=1e8, season=3)
    m3 = score.derive(base)
    check("營收市價比＝近 12 月營收 ÷ 市值", abs(m3["rev_to_price"] - 5_000_000 * 1000 / 1e10) < 1e-9, str(m3.get("rev_to_price")))
    check("毛利占資產＝累計毛利年化 ÷ 總資產", abs(m3["gp_assets"] - 2.0e7 * 4 / 3 / 1e8 * 100) < 1e-9, str(m3.get("gp_assets")))
    m4 = score.derive(dict(base, rev_sum12=None))
    check("沒有 12 個月月營收時，用財報累計營收年化", abs(m4["rev_to_price"] - 1e8 * 4 / 3 * 1000 / 1e10) < 1e-9)
    m5 = score.derive(dict(base, industry="17", fs_format="basi"))
    check("金融業不評營收市價比、毛利占資產", "rev_to_price" not in m5 and "gp_assets" not in m5)
    _, _, b0, *_ = score.score_company(dict(base, growth={"high12": False}), CFG)
    _, _, b1, _, _, _, up1, _ = score.score_company(dict(base, growth={"high12": True}), CFG)
    check("營收創 12 個月新高加分並列入優勢", round(b1 - b0, 6) == CFG["bonuses"]["rev_high12"] and any("新高" in t for t in up1))
    sh = 100_000_000  # 發行 1 億股
    buy_flow = {"n5": 5, "foreign5": 300, "trust5": 50}      # 35 萬股＝0.35%
    sell_flow = {"n5": 5, "foreign5": -3000, "trust5": -500}
    _, _, f0, *_ = score.score_company(dict(base, shares=sh), CFG, None)
    _, _, f1, *_ = score.score_company(dict(base, shares=sh), CFG, buy_flow)
    _, _, f2, *_ = score.score_company(dict(base, shares=sh), CFG, sell_flow)
    check("外資＋投信買超達門檻加分", round(f1 - f0, 6) == CFG["bonuses"]["inst_buy"]["points"])
    check("法人賣超不扣分（研究：賣超沒有預測力）", f2 == f0)
    _, _, sf, lf, _, ff, _, _ = score.score_company(dict(fixtures.archetypes()[2][0], rev_streak=2), CFG)
    check("營收連兩月成長但本期虧損：仍不給買進", lf != "買進", f"({lf}, {ff})")
    fin = dict(c, industry="17", fs_format="basi", revenue=None, gross_profit=None, op_income=None,
               total_liab=9.3e7, cur_assets=None, cur_liab=None)
    m, _, s, label, cov, flags, *_ = score.score_company(fin, CFG)
    check("金融業不評負債比、流動比、毛利率", not ({"debt_ratio", "current_ratio", "gross_margin"} & set(m)), str(sorted(m)))
    check("金融業負債比 93% 不觸發風險旗標", not flags, str(flags))
    check("金融業涵蓋度只計適用指標（可達 100%）", cov == 1.0, f"(cov={cov:.2f}, {label})")
    nost = dict(c, revenue=None, gross_profit=None, op_income=None, net_income=None, eps=None, equity=None,
                total_assets=None, total_liab=None, cur_assets=None, cur_liab=None)
    _, _, s, label, cov, *_ = score.score_company(nost, CFG)
    check("沒有財報、只有估值 → 資料不足", label == "資料不足" and s is None, f"(cov={cov:.2f})")
    thin = dict(c, gross_profit=None, op_income=None, pe=None, pb=None, dy=None)
    _, _, s, label, cov, *_ = score.score_company(thin, CFG)
    check("涵蓋度不足時不給買進（最高觀望）", label != "買進" and cov < CFG["buy_min_coverage"], f"(分數 {s and round(s,1)}，cov={cov:.2f}，{label})")


def test_score_and_rank(cs):
    with tempfile.TemporaryDirectory() as d:
        src = pathlib.Path(d) / "companies.json"
        dst = pathlib.Path(d) / "data.json"
        src.write_text(json.dumps({"generated_at": "2026-11-16T09:00:00+00:00", "companies": cs}, ensure_ascii=False))
        score.main(src, dst)
        out = json.loads(dst.read_text(encoding="utf-8"))
    by = {c["code"]: c for c in out["companies"]}
    check("輸出筆數等於輸入", len(out["companies"]) == len(cs))
    check("標籤只有四種", {c["label"] for c in out["companies"]} <= {"買進", "觀望", "賣出", "資料不足"})
    check("三種標籤皆有出現", all(out["counts"].get(l, 0) > 0 for l in ("買進", "觀望", "賣出")), str(out["counts"]))
    check("分數在 0–100", all(c["score"] is None or 0 <= c["score"] <= 100 for c in out["companies"]))
    check("資料不足者分數為 None", all((c["label"] == "資料不足") == (c["score"] is None) for c in out["companies"]))
    check("買進者無風險旗標", all(not c["flags"] for c in out["companies"] if c["label"] == "買進"))
    check("每個產業名稱皆已轉成中文", all(not i.isdigit() for i in out["industries"]), str(out["industries"][:5]))
    check("金融保險業存在", "金融保險業" in out["industries"])
    ok_top = True
    for kind in ("score", "mcap"):
        for ind, codes in out["top"][kind].items():
            if len(codes) > 3 or any(by[c]["industry"] != ind for c in codes):
                ok_top = False
    check("各產業前三名：最多 3 檔且屬於該產業", ok_top)
    # 前三名依評分，確實是該產業最高分
    ok_rank = True
    for ind, codes in out["top"]["score"].items():
        grp = sorted([c["score"] for c in out["companies"] if c["industry"] == ind and c["score"] is not None], reverse=True)
        if [by[c]["score"] for c in codes] != grp[:3]:
            ok_rank = False
    check("依評分前三名與全產業排序一致", ok_rank)
    ok_cap = True
    for ind, codes in out["top"]["mcap"].items():
        grp = sorted([c["mcap"] for c in out["companies"] if c["industry"] == ind and c["mcap"] is not None], reverse=True)
        if [by[c]["mcap"] for c in codes] != grp[:3]:
            ok_cap = False
    check("依市值前三名與全產業排序一致", ok_cap)
    th = score.build_themes([dict(c) for c in out["companies"]], {"測試族": ["1101", "1102", "1101", "0000"]})["測試族"]
    check("族群：去除重複代號、列出找不到的代號", th["missing"] == ["0000"] and len(th["mcap"]) == 2)
    sc = [by[c]["score"] for c in th["score"] if by[c]["score"] is not None]
    check("族群：依評分排序", sc == sorted(sc, reverse=True))
    # 熱度：近 N 日平均成交值
    hist = {}
    score.update_heat_history([{"code": "A", "trade_value": 1e8, "price_date": "1151110"}], hist, 5)
    score.update_heat_history([{"code": "A", "trade_value": 3e8, "price_date": "1151111"}], hist, 5)
    heat = score.update_heat_history([{"code": "A", "trade_value": 3e8, "price_date": "1151111"}], hist, 5)
    check("熱度：同一天重跑不重複計入，取多日平均", heat["A"] == 2.0 and len(hist["A"]) == 2, str(heat))
    for i in range(12):
        score.update_heat_history([{"code": "A", "trade_value": 1e8, "price_date": f"11512{i:02d}"}], hist, 5)
    check("熱度：歷史最多保留 10 天", len(hist["A"]) == 10)
    # 熱門題材排序與取前 N 名
    cs3 = out["companies"]
    pc = {"show_top": 2, "themes": {f"T{i}": {cs3[i]["code"]: "產品", cs3[i + 10]["code"]: "產品", "0000": "x"} for i in range(4)}}
    ht = {cs3[i]["code"]: float(i) for i in range(4)}
    hot, pool = score.build_products([dict(c) for c in cs3], pc, ht)
    check("熱門題材：依熱度排序並只取前 N 名", [t["name"] for t in hot] == ["T3", "T2"] and pool == 4, str([t["name"] for t in hot]))
    check("熱門題材：記錄主力產品與找不到的代號", hot[0]["items"][cs3[3]["code"]] == "產品" and hot[0]["missing"] == ["0000"])
    size_kb = len(json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode()) / 1024
    check("輸出檔大小合理（< 3MB）", size_kb < 3072, f"({size_kb:.0f} KB)")
    return out


if __name__ == "__main__":
    test_helpers()
    raw = fixtures.make_raw()
    cs = test_parse(raw)
    test_monthly()
    test_growth()
    test_archetypes()
    out = test_score_and_rank(cs)
    # 把模擬結果留給網頁測試使用
    (ROOT / "tests" / "sample_data.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} 項通過")
    sys.exit(1 if failed else 0)
