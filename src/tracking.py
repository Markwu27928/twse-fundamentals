"""評分績效追蹤（模擬持股，不是真實交易）。

每月月營收公布截止（10 日）後的第一次執行，建立一組「期別」：記錄當時各分組的股票與收盤價。
之後每次執行以最新收盤價計算各組等權報酬，並與「全市場等權平均」比較，用來驗證評分是否真的有效。

分組：評分買進／觀望／賣出、評分前 20%／後 20%、營收成長潛力、全市場等權（比較基準）。
限制：
  - 報酬只計價差，未含股利（除息會讓報酬略為低估）、未扣交易成本
  - 減資、分割等造成的股價跳動不會調整；單檔報酬超過 ±MAX_ABS_RET 視為異常並排除
  - 停牌或下市沒有新價格的股票，以最後一次取得的價格計算
"""
import datetime
import hashlib
import json

FORM_DAY = 11          # 每月 11 日以後的第一次執行建立新期別
MAX_ABS_RET = 3.0      # 單檔報酬 > +300% 或 < -75% 視為資料異常（減資、分割等）
MIN_RET = -0.75
GROUPS = ["買進", "觀望", "賣出", "評分前20%", "評分後20%", "成長潛力", "RSI超賣", "跌破布林下軌", "MACD黃金交叉", "全市場"]


def rules_version(cfg):
    keep = {k: cfg.get(k) for k in ("metrics", "buy_above", "sell_below", "bonuses", "revenue_streak", "growth_potential", "technical")}
    return hashlib.sha1(json.dumps(keep, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:8]


def groups_of(out):
    """依目前評分結果分組：{組名: [代號]}。只納入有價格的股票。"""
    xs = [x for x in out if x.get("price")]
    scored = sorted([x for x in xs if x.get("score") is not None], key=lambda x: -x["score"])
    n = len(scored) // 5
    g = {
        "買進": [x["code"] for x in xs if x.get("label") == "買進"],
        "觀望": [x["code"] for x in xs if x.get("label") == "觀望"],
        "賣出": [x["code"] for x in xs if x.get("label") == "賣出"],
        "評分前20%": [x["code"] for x in scored[:n]],
        "評分後20%": [x["code"] for x in scored[-n:]] if n else [],
        "成長潛力": [x["code"] for x in xs if x.get("potential")],
        # 技術訊號單獨分組，驗證技術指標在台股是否有效（只看有財報評分的股票）
        "RSI超賣": [x["code"] for x in scored if "RSI超賣" in (x.get("tech_signals") or [])],
        "跌破布林下軌": [x["code"] for x in scored if "跌破布林下軌" in (x.get("tech_signals") or [])],
        "MACD黃金交叉": [x["code"] for x in scored if "MACD黃金交叉" in (x.get("tech_signals") or [])],
        "全市場": [x["code"] for x in scored],
    }
    return g


def maybe_form(state, out, today, cfg):
    """本月尚未建立期別、且已過 FORM_DAY（或完全沒有期別）時，建立新期別。回傳是否建立。"""
    key = today.strftime("%Y-%m")
    cohorts = state.setdefault("cohorts", [])
    if any(c["id"] == key for c in cohorts):
        return False
    if cohorts and today.day < FORM_DAY:
        return False
    prices = {x["code"]: x["price"] for x in out if x.get("price")}
    g = groups_of(out)
    cohorts.append({
        "id": key, "formed": today.isoformat(), "rules": rules_version(cfg),
        "entry": {c: prices[c] for c in g["全市場"] if c in prices} | {c: prices[c] for gg in g.values() for c in gg if c in prices},
        "groups": g,
    })
    return True


def evaluate(state, out, today):
    """以最新價格計算每個期別各組的等權報酬（%）、相對全市場的超額報酬、勝率。"""
    prices = {x["code"]: x["price"] for x in out if x.get("price")}
    last = state.setdefault("last_price", {})
    last.update(prices)
    res = []
    for ch in state.get("cohorts", []):
        rets, bad = {}, []
        for code, p0 in ch["entry"].items():
            p1 = last.get(code)
            if not p0 or not p1:
                continue
            r = p1 / p0 - 1
            if r > MAX_ABS_RET or r < MIN_RET:
                bad.append(code)
                continue
            rets[code] = r
        groups = {}
        base = None
        for name in GROUPS:
            rs = [rets[c] for c in ch["groups"].get(name, []) if c in rets]
            if not rs:
                continue
            groups[name] = {"n": len(rs), "ret": round(sum(rs) / len(rs) * 100, 2)}
        if "全市場" in groups:
            base = groups["全市場"]["ret"]
            for name, gv in groups.items():
                rs = [rets[c] for c in ch["groups"].get(name, []) if c in rets]
                gv["excess"] = round(gv["ret"] - base, 2)
                gv["win"] = round(sum(1 for r in rs if r * 100 > base) / len(rs) * 100, 1)
        days = (today - datetime.date.fromisoformat(ch["formed"])).days
        res.append({"id": ch["id"], "formed": ch["formed"], "days": days, "rules": ch["rules"],
                    "groups": groups, "excluded": bad[:20], "excluded_n": len(bad)})
    return sorted(res, key=lambda r: r["formed"], reverse=True)


def update(state, out, today, cfg):
    formed = maybe_form(state, out, today, cfg)
    summary = evaluate(state, out, today)
    return formed, {"current_rules": rules_version(cfg), "cohorts": summary,
                    "note": "模擬持股：各組等權、只計價差（未含股利與交易成本）。和「全市場」等權平均比較，超額報酬為正代表該組跑贏市場。"}
