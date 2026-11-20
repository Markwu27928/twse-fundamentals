"""台指期三大法人未平倉模擬測試。執行：python tests/test_futures.py"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import futures as F  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("通過" if cond else "失敗"), "-", name, detail)


HEAD = "日期,商品名稱,身份別,多方交易口數,多方交易契約金額(千元),空方交易口數,空方交易契約金額(千元),多空交易口數淨額,多空交易契約金額淨額(千元),多方未平倉口數,多方未平倉契約金額(千元),空方未平倉口數,空方未平倉契約金額(千元),多空未平倉口數淨額,多空未平倉契約金額淨額(千元)"


def row(d, name, who, lo, sh, net=None):
    net = lo - sh if net is None else net
    return f"{d},{name},{who},1,1,1,1,0,0,{lo},1,{sh},1,{net},0"


def csv_text(days):
    lines = [HEAD]
    for d, fx, tr, dl in days:
        lines += [row(d, "臺股期貨", "自營商", *dl), row(d, "臺股期貨", "投信", *tr), row(d, "臺股期貨", "外資及陸資", *fx),
                  row(d, "小型臺指期貨", "外資及陸資", 1000, 99999)]
    return "\n".join(lines)


def test_parse():
    t = csv_text([("20261119", (12000, 45000), (60000, 3000), (8000, 9000))])
    p, dg = F.parse("﻿" + t)
    check("CSV：只取臺股期貨，排除小型臺指", p["2026-11-19"]["外資"] == {"long": 12000, "short": 45000, "net": -33000}, str(p))
    check("CSV：三種身份別都有、核對 100%", set(p["2026-11-19"]) == {"外資", "投信", "自營商"} and dg["check"] == 1.0)
    big5 = t.encode("cp950")
    p2, _ = F.parse(F.decode(big5))
    check("Big5 編碼的 CSV 也能解析", p2 == p)
    j = json.dumps([dict(zip(HEAD.split(","), r.split(","))) for r in t.split("\n")[1:]], ensure_ascii=False)
    p3, dg3 = F.parse(j)
    check("JSON 格式（同欄位名）也能解析", p3 == p and dg3["check"] == 1.0)
    bad = "\n".join([HEAD, row("20261119", "臺股期貨", "外資及陸資", 12000, 45000, net=-3000)])
    p4, dg4 = F.parse(bad)
    check("淨額≠多方−空方時核對失敗、不採用", dg4["check"] == 0.0 and not F.accept(p4, dg4))
    p5, dg5 = F.parse("<html>系統維護</html>")
    check("非資料內容：不採用、不崩潰", not F.accept(p5, dg5))


def hist_of(nets):
    days = ["2026-11-12", "2026-11-13", "2026-11-14", "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20"][-len(nets):]
    return {"fut": {d: {"外資": {"long": 10000, "short": 10000 - n, "net": n}, "投信": {"long": 1, "short": 0, "net": 1}} for d, n in zip(days, nets)}}


def test_summary():
    s = F.summarize(hist_of([-30000, -31000, -32000, -33000, -34000, -35000, -38000]))
    check("淨空單：口數為淨額絕對值", s["position"] == "淨空單" and s["size"] == 38000 and s["date"] == "2026-11-20")
    check("較前日：淨額由 -35,000 到 -38,000 → 空單增加 3,000 口", s["chg1"] == -3000 and s["chg1_label"] == "空單增加")
    check("近 5 日：由 -31,000 到 -38,000 → 空單增加 7,000 口", s["chg5"] == -7000 and s["chg5_label"] == "空單增加" and s["days5"] == 5)
    s2 = F.summarize(hist_of([-38000, -36000]))
    check("淨空單減少（回補）", s2["chg1"] == 2000 and s2["chg1_label"] == "空單減少")
    s3 = F.summarize(hist_of([5000, 8000]))
    check("淨多單時也能顯示（多單增加＝空單減少的方向）", s3["position"] == "淨多單" and s3["chg1_label"] == "空單減少")
    s4 = F.summarize(hist_of([-1000]))
    check("只有一天：沒有增減", s4["chg1"] is None and s4["chg1_label"] is None)
    check("沒有資料：回傳 None", F.summarize({}) is None)
    check("其他身份別一併輸出", F.summarize(hist_of([-1, -2]))["others"] == {"投信": 1})


def test_update():
    calls = []

    def fake_range(a, b):
        calls.append("range")
        return F.parse(csv_text([(f"202611{d:02d}", (1, 100 + d), (1, 0), (1, 0)) for d in range(1, 19)]))

    def fake_latest():
        calls.append("latest")
        return F.parse(csv_text([("20261119", (1, 200), (1, 0), (1, 0))]))
    o1, o2 = F.fetch_range, F.fetch_latest
    F.fetch_range, F.fetch_latest = fake_range, fake_latest
    try:
        import datetime
        h = {}
        note, err = F.update(h, datetime.date(2026, 11, 19))
        check("第一次：回補歷史＋最新", calls == ["range", "latest"] and len(h["fut"]) == 19 and not err, note)
        calls.clear()
        F.update(h, datetime.date(2026, 11, 20))
        check("已有 15 天以上：只抓最新", calls == ["latest"])
    finally:
        F.fetch_range, F.fetch_latest = o1, o2


if __name__ == "__main__":
    test_parse()
    test_summary()
    test_update()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} 項通過")
    sys.exit(1 if failed else 0)
