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
    check("本次有財報時不被舊資料覆蓋", not by["1102"].get("stale") and by["1102"]["season"] != 2)
    return cs


def test_archetypes():
    for c, expect in fixtures.archetypes():
        _, _, s, label, cov, flags, up, dn = score.score_company(c, CFG)
        check(f"樣本 {c['code']} 預期 {expect}", label == expect, f"(得 {label}，分數 {s and round(s,1)}，旗標 {flags})")
    c = fixtures.archetypes()[0][0]
    _, _, s, label, cov, *_ = score.score_company(dict(c, gross_profit=None, op_income=None, equity=None,
                                                       net_income=None, total_assets=None, cur_assets=None,
                                                       rev_yoy=None, pe=None, pb=None, dy=None), CFG)
    check("全缺資料 → 資料不足", label == "資料不足" and s is None)
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
    size_kb = len(json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode()) / 1024
    check("輸出檔大小合理（< 3MB）", size_kb < 3072, f"({size_kb:.0f} KB)")
    return out


if __name__ == "__main__":
    test_helpers()
    raw = fixtures.make_raw()
    cs = test_parse(raw)
    test_archetypes()
    out = test_score_and_rank(cs)
    # 把模擬結果留給網頁測試使用
    (ROOT / "tests" / "sample_data.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} 項通過")
    sys.exit(1 if failed else 0)
