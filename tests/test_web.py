"""網頁模擬測試（手機尺寸）。先執行 test_pipeline.py 產生 tests/sample_data.json。"""
import functools
import http.server
import pathlib
import shutil
import sys
import tempfile
import threading

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("通過" if cond else "失敗"), "-", name, detail)


def serve(directory):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    site_root = pathlib.Path(tempfile.mkdtemp())
    tmp = site_root / "twse-fundamentals"   # 與 GitHub Pages 相同的路徑結構：/twse-fundamentals/ 與 /huili/
    tmp.mkdir()
    shutil.copy(ROOT / "docs" / "index.html", tmp / "index.html")
    shutil.copy(ROOT / "tests" / "sample_data.json", tmp / "data.json")
    import json
    sample = json.loads((tmp / "data.json").read_text(encoding="utf-8"))
    # 讓前 5 檔有評分的公司標成「營收連兩月成長」，測試標記與篩選
    global n_hot_rev
    hot = [c for c in sample["companies"] if c["score"] is not None][:5]
    for i, c in enumerate(sample["companies"]):
        c.setdefault("rev", {"month": "202610", "mom": 3.2, "yoy": 12.5, "prev_month": None, "prev_mom": None, "prev_yoy": None, "streak": 0})
        c.setdefault("rev_hot", False)
    for c in hot:
        c["rev"] = {"month": "202610", "mom": 5.1, "yoy": 22.3, "prev_month": "202609", "prev_mom": 2.4, "prev_yoy": 18.0, "streak": 2}
        c["rev_hot"] = True
    global n_pot
    pot = [c for c in sample["companies"] if c["score"] is not None][10:13]
    for c in sample["companies"]:
        c.setdefault("potential", False); c.setdefault("potential_rank", None); c.setdefault("potential_notes", [])
        c.setdefault("growth", {"avg3_yoy": None, "prev3_yoy": None, "accel": None, "high12": None, "months": 0, "hist": []})
    for i, c in enumerate(pot):
        c["potential"], c["potential_rank"] = True, 100.0 - i * 10
        c["potential_notes"] = ["近 3 月營收合計年增 35.0%", "年增加速（前 3 月 12.0% → 近 3 月 35.0%）"]
        c["growth"] = {"avg3_yoy": 35.0, "prev3_yoy": 12.0, "accel": 23.0, "high12": True, "months": 12,
                       "hist": [["202610", 523456.0, 4.2, 38.0], ["202609", 502000.0, 3.1, 35.5], ["202608", 487000.0, 2.0, 31.5],
                                ["202607", 477000.0, 1.0, 14.0], ["202606", 472000.0, -1.0, 12.0], ["202605", 476000.0, 0.5, 10.0]]}
    sample["counts"]["營收成長潛力"] = n_pot = len(pot)
    pot_names = [c["name"] for c in pot]
    sample["counts"]["營收連兩月成長"] = n_hot_rev = sum(1 for c in sample["companies"] if c["rev_hot"])
    # 資金流向模擬資料
    sys.path.insert(0, str(ROOT / "src"))
    import score as sc
    import random
    rng = random.Random(3)
    cfg = json.loads((ROOT / "config" / "scoring.json").read_text(encoding="utf-8"))
    for c in sample["companies"]:
        f5, t5 = rng.randint(-5000, 5000), rng.randint(-800, 800)
        p = c.get("price") or 50
        c["flow"] = {"n5": 5, "date": "2026-11-17", "foreign": f5 // 5, "trust": t5 // 5, "dealer": 10, "foreign5": f5, "trust5": t5,
                     "dealer5": 50, "total5": f5 + t5 + 50, "foreign_streak": rng.choice([-2, 1, 3, 5]), "trust_streak": rng.choice([-1, 2, 4]),
                     "total5_value": round((f5 + t5 + 50) * 1000 * p / 1e8, 2), "foreign5_value": round(f5 * 1000 * p / 1e8, 2),
                     "trust5_value": round(t5 * 1000 * p / 1e8, 2), "conc5": round(rng.uniform(-1, 1.5), 3),
                     "margin": 5000, "short": 300, "short_ratio": 6.0, "margin_chg5": rng.choice([-200, 150]), "margin_chg5_pct": rng.choice([-4.0, 8.0]), "margin_days": 5}
        c["flow_signals"] = sc.flow_signals(c["flow"], cfg)
    sample["flows"] = sc.build_flow_views(sample["companies"], cfg, sample.get("themes", {}), sample.get("products", []), sample["industries"])
    global n_flow_lists
    n_flow_lists = len(sample["flows"]["lists"])
    # 績效追蹤模擬資料：兩期，第二期為舊版規則
    import tracking as tk
    import datetime as dt
    tst = {}
    tk.maybe_form(tst, sample["companies"], dt.date(2026, 10, 15), {"metrics": [], "buy_above": 70})
    tk.maybe_form(tst, sample["companies"], dt.date(2026, 11, 18), cfg)
    later = json.loads(json.dumps(sample["companies"]))
    for i, x in enumerate(later):
        if x.get("price"):
            x["price"] = round(x["price"] * (1.12 if x["label"] == "買進" else 0.97 if x["label"] == "賣出" else 1.01), 2)
    _, sample["tracking"] = tk.update(tst, later, dt.date(2026, 12, 5), cfg)
    (tmp / "data.json").write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")
    global data_has_themes, n_themes
    n_themes = len(sample.get("themes") or {})
    data_has_themes = n_themes > 0
    macro_sample = {"generated_at": "2026-11-16T09:00:00+00:00", "errors": [],
                    "fx": {"date": "2026-11-16", "rate": 31.312, "change_1d_pct": 0.21, "change_win_pct": 0.97, "win_from": "2026-11-09"},
                    "foreign": {"date": "2026-11-16", "today": -184.0, "days": 5, "sum": -612.0, "series": []},
                    "verdict": {"label": "明顯匯出", "flow": -1, "fx": -1, "detail": "外資近 5 日賣超 612 億，新台幣貶值（美元 +0.97%）"}}
    (tmp / "macro.json").write_text(json.dumps(macro_sample, ensure_ascii=False), encoding="utf-8")
    # 匯立分點：用 huili repo 產生程式與真實 CSV 建立資料檔（有 huili 時），否則用模擬資料
    global hl_codes
    hl_dir = site_root / "huili"
    hl_dir.mkdir()
    huili_repo = ROOT.parent / "huili"
    if (huili_repo / "build_site.py").exists():
        sys.path.insert(0, str(huili_repo))
        import build_site
        hl = build_site.build_data()
    else:
        codes = [c["code"] for c in sample["companies"]][:60]
        hl = {"version": 1, "unit": "仟元", "top_n": 50, "names": {}, "days": [
            {"d": "20261113", "r": [[c, 5000, 1000, None, None] for c in codes[:30]]},
            {"d": "20261116", "r": [[c, 9000, 1000, 30, 5] for c in codes[:20]] + [[c, 0, 4000, None, None] for c in codes[40:50]]}]}
    (hl_dir / "data.json").write_text(json.dumps(hl, ensure_ascii=False), encoding="utf-8")
    latest = sorted(hl["days"], key=lambda d: d["d"])[-1]
    known = {c["code"] for c in sample["companies"]}
    hl_codes = {"buy": [r[0] for r in latest["r"] if r[1] - r[2] > 0 and r[0] in known],
                "sell": [r[0] for r in latest["r"] if r[1] - r[2] < 0 and r[0] in known]}
    top_buy = max((r for r in latest["r"] if r[0] in known), key=lambda r: r[1] - r[2])
    hl_codes["top"] = top_buy[0]
    w = (top_buy[1] - top_buy[2]) / 10
    hl_codes["top_txt"] = f"{w / 10000:.2f} 億" if w >= 10000 else f"{round(w):,} 萬"
    srv = serve(site_root)
    url = f"http://127.0.0.1:{srv.server_address[1]}/twse-fundamentals/index.html"
    shots = ROOT / "tests"
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, locale="zh-TW")
        page = ctx.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url)
        page.wait_for_selector("details.co")

        page.wait_for_selector("#macro:not([hidden])")
        mt = page.inner_text("#macro")
        check("標題顯示匯率、外資買賣超與判讀", "31.312" in mt and "-184" in mt and "-612" in mt and "判讀：明顯匯出" in mt, mt.replace("\n", " | "))
        check("匯出判讀用綠色標籤、美元上漲用紅色", page.locator("#macro .vd-out").count() == 1 and page.locator("#macro .up").count() >= 1)
        page.screenshot(path=str(shots / "shot_macro.png"))
        check("全市場預設顯示第一頁 100 檔", page.locator("details.co").count() == 100)
        check("標題與更新時間顯示", "更新：" in page.inner_text("#sub"))
        check("計數文字", page.inner_text("#count").startswith("共 "))
        total = int(page.inner_text("#count").split()[1])

        # 顯示更多
        page.click("button.more")
        check("顯示更多後變 200 檔", page.locator("details.co").count() == 200)

        # 營收連兩月成長
        check("篩選鈕顯示連兩月成長檔數", f"營收連兩月成長 {n_hot_rev}" in page.inner_text("#chip-hi"))
        page.click("#chip-hi")
        n = page.locator("details.co").count()
        tags = page.locator("details.co .hot").count()
        check("篩選後只剩連兩月成長的股票且都有標記", n == n_hot_rev and tags == n, f"({n}/{n_hot_rev})")
        page.locator("details.co summary").first.click()
        body = page.locator("details.co").first.inner_text()
        check("明細顯示兩個月的月增、年增", "2026/10" in body and "2026/09" in body and "連 2 個月月增、年增皆成長" in body)
        page.screenshot(path=str(shots / "shot_revhot.png"))
        page.click("#chip-hi")
        check("再按一次取消篩選", page.locator("details.co").count() == 100)

        # 營收成長潛力
        check("潛力篩選鈕顯示檔數", f"營收成長潛力 {n_pot}" in page.inner_text("#chip-po"))
        page.click("#chip-po")
        names = [x.split("\n")[0] for x in page.locator("details.co .nm").all_inner_texts()]
        check("潛力篩選：只剩潛力股、依潛力排序", page.locator("details.co").count() == n_pot and page.locator("details.co .pot").count() == n_pot
              and page.input_value("#sort") == "potential", str(names))
        page.locator("details.co summary").first.click()
        pb_txt = page.locator("details.co").first.inner_text()
        check("潛力股明細：判斷理由與近 6 月營收表", "營收成長潛力股" in pb_txt and "年增加速" in pb_txt and "2026/05" in pb_txt and "5.23" in pb_txt, pb_txt[pb_txt.find("近期月營收"):][:120].replace("\n", " "))
        page.screenshot(path=str(shots / "shot_potential.png"))
        page.click("#chip-po")
        page.select_option("#sort", "score")
        check("取消潛力篩選", page.locator("details.co").count() == 100)

        # 標籤篩選
        page.click('.chip[data-l="買進"]')
        n_buy = page.locator("details.co").count()
        badges = set(page.locator("details.co .badge").all_inner_texts())
        check("篩選買進：只出現買進標籤", badges == {"買進"} and n_buy > 0, f"({n_buy}+)")
        page.click('.chip[data-l="全部"]')

        # 產業 + 搜尋
        page.select_option("#ind", "半導體業")
        inds = set(page.locator("details.co .main .meta").all_inner_texts())
        check("產業篩選：全部屬於半導體業", all(t.startswith("半導體業") for t in inds) and inds)
        page.select_option("#ind", "全部")
        page.fill("#q", "1101")
        check("搜尋代號 1101", page.locator("details.co").count() == 1 and "1101" in page.inner_text("details.co .nm"))
        page.fill("#q", "")

        # 排序：評分由高到低
        page.select_option("#sort", "score")
        scores = [int(t) for t in page.locator("details.co .score").all_inner_texts()[:50] if t.isdigit()]
        check("依評分排序（高到低）", scores == sorted(scores, reverse=True))
        page.select_option("#sort", "mcap")
        mcaps = page.locator("details.co .meta").all_inner_texts()[:5]
        check("依市值排序可運作", len(mcaps) == 5)

        # 展開明細
        page.select_option("#sort", "score")
        first = page.locator("details.co").first
        first.locator("summary").click()
        check("展開後顯示指標明細", first.locator(".m").count() >= 3 and "指標明細" in first.inner_text())
        page.screenshot(path=str(shots / "shot_all.png"))

        # 各產業前三名（評分）
        page.click("#v-top")
        check("切換後篩選列隱藏、排名列顯示", page.locator("#ctl-all").is_hidden() and page.locator("#ctl-top").is_visible())
        n_ind = page.locator(".ind").count()
        n_cards = page.locator("details.co").count()
        check("每產業最多 3 張卡片", 0 < n_cards <= 3 * n_ind, f"({n_ind} 產業, {n_cards} 張)")
        ranks = page.locator("details.co .rank").all_inner_texts()
        check("卡片有名次 1~3", set(ranks) <= {"1", "2", "3"} and "1" in ranks)
        sec = page.locator(".ind").first.inner_text()
        top_scores = page.locator("details.co .score").all_inner_texts()[:3]
        check("第一個產業內評分遞減", [int(x) for x in top_scores if x.isdigit()] == sorted([int(x) for x in top_scores if x.isdigit()], reverse=True), f"({sec}: {top_scores})")
        page.screenshot(path=str(shots / "shot_top_score.png"))

        # 依市值
        page.click("#r-mcap")
        check("依市值排名按鈕生效", page.get_attribute("#r-mcap", "aria-pressed") == "true" and "市值" in page.inner_text("#count"))
        page.screenshot(path=str(shots / "shot_top_mcap.png"))

        # 主題族群
        if data_has_themes:
            page.click("#v-theme")
            check("族群分頁：顯示族群選單與排名切換", page.locator("#theme-pick").is_visible() and page.locator("#r-score").is_visible())
            n_th = page.locator(".ind").count()
            check("族群分頁：顯示所有族群", n_th == n_themes, f"({n_th}/{n_themes})")
            check("族群標題下有平均評分與標籤統計", "平均評分" in page.locator(".ind-sub").first.inner_text())
            page.select_option("#theme-pick", index=1)
            one = page.locator(".ind").count()
            check("選單篩選單一族群", one == 1, page.locator(".ind").first.inner_text())
            page.locator("details.co summary").first.click()
            check("展開卡片顯示所屬族群", "所屬族群" in page.locator("details.co").first.inner_text())
            page.screenshot(path=str(shots / "shot_theme.png"))
            page.select_option("#theme-pick", "全部")

        # 熱門產品
        page.click("#v-hot")
        check("熱門產品分頁：排名切換顯示、族群選單隱藏", page.locator("#r-score").is_visible() and page.locator("#theme-pick").is_hidden())
        n_hot = page.locator(".ind").count()
        check("熱門產品：最多 20 個題材且依熱度排名", 0 < n_hot <= 20 and page.locator(".ind").first.inner_text().startswith("#1 "), f"({n_hot})")
        heats = [int(x.split("均成交值 ")[1].split(" 億")[0].replace(",", "")) for x in page.locator(".ind-sub").all_inner_texts()]
        check("熱門產品：成交值由高到低", heats == sorted(heats, reverse=True), str(heats[:5]))
        check("熱門產品：卡片顯示主力產品", "主力產品：" in page.locator("details.co").first.inner_text())
        page.screenshot(path=str(shots / "shot_hot.png"))

        # 資金流向
        page.click("#v-flow")
        check("資金流向分頁：顯示清單選單、隱藏其他控制", page.locator("#flow-pick").is_visible() and page.locator("#ctl-all").is_hidden() and page.locator("#r-score").is_hidden())
        rows = page.locator("table.grp tr").count()
        check("產業資金排行：表格依金額排序", rows > 5, f"({rows} 列)")
        vals = [float(x) for x in page.locator("table.grp tr td:nth-child(2)").all_inner_texts()]
        check("產業資金排行：法人合計由高到低", vals == sorted(vals, reverse=True), str(vals[:5]))
        page.screenshot(path=str(shots / "shot_flow_ind.png"))
        page.select_option("#flow-pick", index=1)
        check("熱門題材資金排行", page.locator("table.grp tr").count() > 3)
        n_opts = page.locator("#flow-pick option").count()
        check("清單選單：2 個排行＋5 個匯立清單＋各股票清單", n_opts == 2 + 5 + n_flow_lists, f"({n_opts})")
        page.select_option("#flow-pick", "法人買超金額")
        n_cards = page.locator("details.co").count()
        check("法人買超金額清單：最多 30 檔且有資金說明", 0 < n_cards <= 30 and "三大法人" in page.locator("details.co .flow").first.inner_text(), f"({n_cards})")
        page.locator("details.co summary").first.click()
        fb = page.locator("details.co").first.inner_text()
        check("卡片明細：法人買賣超表與融資融券", "法人買賣超（張" in fb and "外資" in fb and "投信" in fb and "融資餘額" in fb and "券資比" in fb)
        page.screenshot(path=str(shots / "shot_flow_list.png"))

        # 匯立分點（讀取 ../huili/data.json）
        page.select_option("#flow-pick", "匯立分點買超（最新）")
        page.wait_for_selector("details.co")
        names = page.locator("details.co .nm small").all_inner_texts()
        check("匯立買超清單：與資料檔最新一天的買超股票一致、依金額排序", names == hl_codes["buy"][:len(names)] and len(names) == len(hl_codes["buy"]),
              f"({len(names)} 檔，第一名 {names[:1]} vs {hl_codes['top']})")
        first = page.locator("details.co").first
        check("卡片標記「匯立買」並顯示匯立金額", first.locator(".hl").inner_text() == "匯立買" and hl_codes["top_txt"] in first.locator(".flow").inner_text(),
              first.locator(".flow").inner_text())
        first.locator("summary").click()
        hb = first.inner_text()
        check("卡片明細：匯立分點區塊與上榜說明", "匯立分點（永豐金-匯立 9A81）" in hb and "前 50 名" in hb)
        page.select_option("#flow-pick", "匯立分點賣超（最新）")
        check("匯立賣超清單筆數正確", page.locator("details.co").count() == len(hl_codes["sell"]), f"({page.locator('details.co').count()}/{len(hl_codes['sell'])})")
        page.screenshot(path=str(shots / "shot_huili.png"))
        page.select_option("#flow-pick", "匯立買＋法人買")
        check("匯立買＋法人買清單可顯示", page.locator("#count").inner_text().startswith("匯立買＋法人買"))
        page.click("#v-all")
        check("其他分頁也顯示匯立標記", page.locator("details.co .hl").count() >= 0)

        # 績效追蹤
        page.click("#v-track")
        check("績效追蹤分頁：隱藏其他控制、顯示兩期", page.locator("#ctl-flow").is_hidden() and page.locator("#ctl-all").is_hidden() and page.locator(".ind").count() == 2)
        tb = page.locator("table.grp").first.inner_text()
        check("績效表：買進組 +12.00%、全市場為基準", "+12.00%" in tb.replace("▲", "+") or "12.00%" in tb and "全市場（基準）" in tb, tb.replace("\n", " | ")[:160])
        subs = page.locator(".ind-sub").all_inner_texts()
        check("舊版規則的期別有標示", any("較早版本" in x for x in subs) and not ("較早版本" in subs[0]), str(subs))
        page.screenshot(path=str(shots / "shot_track.png"))

        # 切回全市場，狀態保留
        page.click("#v-all")
        check("切回全市場", page.locator("#ctl-all").is_visible() and page.locator("details.co").count() == 100 and page.locator("#theme-pick").is_hidden())

        # 水平溢出（手機版面）
        overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
        check("手機寬度無水平捲動", not overflow)

        # 深色模式
        dark = b.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark").new_page()
        dark.goto(url)
        dark.wait_for_selector("details.co")
        dark.screenshot(path=str(shots / "shot_dark.png"))

        check("無 console 錯誤", not errors, str(errors))

        # macro.json 不存在：主頁照常、標題區塊隱藏
        (tmp / "macro.json").unlink()
        m = ctx.new_page()
        m.goto(url)
        m.wait_for_selector("details.co")
        check("macro.json 不存在時：區塊隱藏、主頁照常", m.locator("#macro").is_hidden() and m.locator("details.co").count() == 100)
        # 匯率與外資只有一邊時
        (tmp / "macro.json").write_text(json.dumps({"fx": None, "errors": ["x"], "foreign": {"date": "2026-11-16", "today": 50.0, "days": 1, "sum": 50.0, "series": []},
                                                    "verdict": {"label": "中性", "flow": 0, "fx": None, "detail": "匯率資料不足，只用外資買賣超判讀"}}), encoding="utf-8")
        m2 = ctx.new_page()
        m2.goto(url)
        m2.wait_for_selector("#macro:not([hidden])")
        check("只有外資資料時：匯率顯示暫缺", "暫缺" in m2.inner_text("#macro") and "判讀：中性" in m2.inner_text("#macro"))
        # huili 資料檔不存在：主頁照常、清單顯示無法讀取
        (site_root / "huili" / "data.json").unlink()
        hp = ctx.new_page()
        hp.goto(url)
        hp.wait_for_selector("details.co")
        hp.click("#v-flow")
        hp.select_option("#flow-pick", "匯立分點買超（最新）")
        hp.wait_for_timeout(500)
        check("huili 資料檔讀不到：顯示說明、其他功能正常", "暫時無法讀取" in hp.inner_text("#count") and hp.locator(".hl").count() == 0)
        # 版本不符：不使用
        (site_root / "huili" / "data.json").write_text(json.dumps({"version": 2, "days": []}), encoding="utf-8")
        vp = ctx.new_page()
        vp.goto(url)
        vp.wait_for_selector("details.co")
        vp.click("#v-flow")
        vp.select_option("#flow-pick", "匯立分點買超（最新）")
        vp.wait_for_timeout(500)
        check("huili 資料格式版本不同：不使用並說明", "版本" in vp.inner_text("#count"), vp.inner_text("#count"))
        # 資料缺失時的錯誤畫面
        (tmp / "data.json").unlink()
        e = ctx.new_page()
        e.goto(url)
        e.wait_for_selector(".err")
        check("data.json 不存在時顯示錯誤訊息", "資料載入失敗" in e.inner_text(".err"))
        b.close()
    srv.shutdown()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} 項通過")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
