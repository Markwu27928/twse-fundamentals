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
    tmp = pathlib.Path(tempfile.mkdtemp())
    shutil.copy(ROOT / "docs" / "index.html", tmp / "index.html")
    shutil.copy(ROOT / "tests" / "sample_data.json", tmp / "data.json")
    srv = serve(tmp)
    url = f"http://127.0.0.1:{srv.server_address[1]}/index.html"
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

        check("全市場預設顯示第一頁 100 檔", page.locator("details.co").count() == 100)
        check("標題與更新時間顯示", "更新：" in page.inner_text("#sub"))
        check("計數文字", page.inner_text("#count").startswith("共 "))
        total = int(page.inner_text("#count").split()[1])

        # 顯示更多
        page.click("button.more")
        check("顯示更多後變 200 檔", page.locator("details.co").count() == 200)

        # 標籤篩選
        page.click('.chip[data-l="買進"]')
        n_buy = page.locator("details.co").count()
        badges = set(page.locator("details.co .badge").all_inner_texts())
        check("篩選買進：只出現買進標籤", badges == {"買進"} and n_buy > 0, f"({n_buy}+)")
        page.click('.chip[data-l="全部"]')

        # 產業 + 搜尋
        page.select_option("#ind", "半導體業")
        inds = set(page.locator("details.co .meta").all_inner_texts())
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

        # 切回全市場，狀態保留
        page.click("#v-all")
        check("切回全市場", page.locator("#ctl-all").is_visible() and page.locator("details.co").count() == 100)

        # 水平溢出（手機版面）
        overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
        check("手機寬度無水平捲動", not overflow)

        # 深色模式
        dark = b.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark").new_page()
        dark.goto(url)
        dark.wait_for_selector("details.co")
        dark.screenshot(path=str(shots / "shot_dark.png"))

        check("無 console 錯誤", not errors, str(errors))

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
