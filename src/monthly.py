"""月營收：累積每月的月增率、年增率歷史，計算「連續幾個月月增與年增皆成長」。

資料來源：
  最新月份：證交所／櫃買中心開放資料（t187ap05），在 fetch.py 抓取
  上一個月：公開資訊觀測站「每月營業收入彙總表」HTML（上市 sii、上櫃 otc；國內 0、國外 KY 1）
歷史存於 data/revenue_history.json，每次執行自動補上缺的月份；觀測站暫時連不上時，
只要歷史已有該月份就不受影響。
"""
import io
import re
import time

import requests

HOSTS = [
    "https://mopsov.twse.com.tw/nas/t21/{mkt}/t21sc03_{y}_{m}_{t}.html",
    "https://mops.twse.com.tw/nas/t21/{mkt}/t21sc03_{y}_{m}_{t}.html",
]
UA = {"User-Agent": "Mozilla/5.0 (fundamentals-bot)"}
KEEP_MONTHS = 24


def ym_key(roc_year, month):
    """民國年、月 -> 'YYYYMM'（西元，方便排序）。"""
    return f"{int(roc_year) + 1911}{int(month):02d}"


def parse_open_ym(v):
    """開放資料的資料年月（如 '11510'）-> 'YYYYMM'。"""
    s = re.sub(r"\D", "", str(v or ""))
    if len(s) == 5:
        return ym_key(s[:3], s[3:])
    if len(s) == 6 and s.startswith(("19", "20")):
        return s
    return None


def prev_ym(ym):
    y, m = int(ym[:4]), int(ym[4:])
    return f"{y - 1}12" if m == 1 else f"{y}{m - 1:02d}"


def _num(v):
    try:
        x = float(str(v).replace(",", "").strip())
    except ValueError:
        return None
    return None if x != x else x  # NaN


def decode(content):
    for enc in ("utf-8", "cp950", "big5"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("cp950", errors="replace")


def parse_mops_html(text):
    """觀測站彙總表 -> {代號: {'rev', 'mom', 'yoy'}}。欄位找不到時丟出例外。"""
    import pandas as pd

    try:
        tables = pd.read_html(io.StringIO(text))
    except ValueError:
        return {}
    out = {}
    found_cols = False
    for t in tables:
        cols = []
        for c in t.columns:
            parts = c if isinstance(c, tuple) else (c,)
            cols.append("".join(str(p) for p in parts if not str(p).startswith("Unnamed")))
        idx = {}
        for i, name in enumerate(cols):
            n = re.sub(r"\s+", "", name)
            if "公司代號" in n:
                idx.setdefault("code", i)
            elif "當月營收" in n:
                idx.setdefault("rev", i)
            elif "上月比較增減" in n:
                idx.setdefault("mom", i)
            elif "去年同月增減" in n:
                idx.setdefault("yoy", i)
        if not {"code", "rev", "mom", "yoy"} <= set(idx):
            continue
        found_cols = True
        for row in t.itertuples(index=False):
            code = str(row[idx["code"]]).strip()
            if not re.fullmatch(r"\d{4}", code):
                continue
            out[code] = {"rev": _num(row[idx["rev"]]), "mom": _num(row[idx["mom"]]), "yoy": _num(row[idx["yoy"]])}
    if tables and not found_cols and len(text) > 5000:
        raise RuntimeError("找不到『公司代號／當月營收／上月比較增減／去年同月增減』欄位，頁面格式可能已改變")
    return out


def fetch_month(ym):
    """抓觀測站某月份（'YYYYMM'）上市＋上櫃、國內＋國外的彙總表。回傳 (資料, 來源說明)。"""
    y, m = int(ym[:4]) - 1911, int(ym[4:])
    data, notes = {}, []
    for mkt in ("sii", "otc"):
        for t in (0, 1):
            last = None
            for host in HOSTS:
                url = host.format(mkt=mkt, y=y, m=m, t=t)
                try:
                    r = requests.get(url, headers=UA, timeout=60)
                    if r.status_code == 404:
                        last = "404"
                        continue
                    r.raise_for_status()
                    rows = parse_mops_html(decode(r.content))
                    data.update(rows)
                    notes.append(f"{mkt}_{t}:{len(rows)}筆({url.split('/')[2]})")
                    last = None
                    break
                except Exception as e:  # noqa: BLE001
                    last = str(e)[:120]
                time.sleep(2)
            if last:
                notes.append(f"{mkt}_{t}:失敗({last})")
            time.sleep(2)
    return data, "；".join(notes)


def merge(hist, ym, rows):
    """把某月份資料併入歷史：{代號: {YYYYMM: {rev, mom, yoy}}}，每檔只留最近 KEEP_MONTHS 個月。"""
    n = 0
    for code, v in rows.items():
        if v.get("mom") is None and v.get("yoy") is None:
            continue
        h = hist.setdefault(code, {})
        h[ym] = {k: v.get(k) for k in ("rev", "mom", "yoy")}
        for old in sorted(h)[:-KEEP_MONTHS]:
            del h[old]
        n += 1
    return n


def streak(h, latest):
    """從 latest 往回數，連續幾個月「月增 > 0 且 年增 > 0」（月份必須連續）。"""
    n, ym = 0, latest
    while ym in h:
        v = h[ym]
        if v.get("mom") is not None and v.get("yoy") is not None and v["mom"] > 0 and v["yoy"] > 0:
            n += 1
            ym = prev_ym(ym)
        else:
            break
    return n


def months_back(ym, n):
    """latest 往回 n 個月（含 latest），由新到舊。"""
    out = [ym]
    for _ in range(n - 1):
        out.append(prev_ym(out[-1]))
    return out


def _agg_yoy(items):
    """多個月合計年增：sum(本期營收) / sum(去年同月營收) − 1。去年同月由 營收 ÷ (1 + 年增%) 還原。
    任一月份缺資料、或年增 ≤ −100%（無法還原）時回傳 None。"""
    cur = ly = 0.0
    for rev, yoy in items:
        if rev is None or yoy is None or yoy <= -100:
            return None
        cur += rev
        ly += rev / (1 + yoy / 100)
    if ly <= 0:
        return None
    return round((cur / ly - 1) * 100, 2)


def growth_profile(h, latest):
    """近 3 月／前 3 月「合計」營收年增、加速幅度、近 3 月是否每月年增皆為正、是否創近 12 個月新高、近 6 個月明細。
    以合計計算年增，避免單月暴衝（例如一次認列的大額營收）被平均放大。資料不足的項目為 None。"""
    seq = months_back(latest, 12)
    pairs = [(h.get(ym, {}).get("rev"), h.get(ym, {}).get("yoy")) for ym in seq]
    a3, p3 = _agg_yoy(pairs[:3]), _agg_yoy(pairs[3:6])
    rev = [p[0] for p in pairs]
    known = [r for r in rev if r is not None]
    high12 = None
    if len(known) == 12 and rev[0] is not None:
        high12 = rev[0] >= max(rev[1:])
    y3 = [p[1] for p in pairs[:3]]
    return {
        "avg3_yoy": a3, "prev3_yoy": p3,
        "accel": round(a3 - p3, 2) if a3 is not None and p3 is not None else None,
        "all3_pos": all(y is not None and y > 0 for y in y3) if all(y is not None for y in y3) else None,
        "high12": high12, "months": len(known),
        "hist": [[ym, h[ym].get("rev"), h[ym].get("mom"), h[ym].get("yoy")] for ym in seq[:6] if ym in h],
    }
