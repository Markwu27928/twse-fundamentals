#!/usr/bin/env python3
"""讀取 data/companies.json 與 config/scoring.json，產生 docs/data.json。

評分：每個指標依 bad→0、good→100 線性內插，再依權重加總；
缺資料的指標會略過並重新配分，coverage 表示可用權重占比。
標籤：score >= buy_above 且無風險旗標 → 買進；score <= sell_below → 賣出；其餘 → 觀望。
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOSS_PE = 999.0  # 虧損公司本益比以極大值代表，評分時為最差


def interp(v, bad, good):
    if good == bad:
        return 0.0
    return max(0.0, min(100.0, (v - bad) / (good - bad) * 100.0))


FIN_FORMATS = {"basi", "bd", "fh", "ins"}


def is_financial(c):
    return c.get("fs_format") in FIN_FORMATS or c.get("industry") == "17"


def derive(c):
    """由原始財報數字推導各指標。金融業不適用毛利率、負債比、流動比。"""
    m = {}
    fin = is_financial(c)
    rev = c.get("revenue")
    if rev and rev > 0 and not fin:
        if c.get("gross_profit") is not None:
            m["gross_margin"] = c["gross_profit"] / rev * 100
        if c.get("op_income") is not None:
            m["op_margin"] = c["op_income"] / rev * 100
    eq, ni, s = c.get("equity"), c.get("net_income"), c.get("season")
    if eq and eq > 0 and ni is not None and s in (1, 2, 3, 4):
        m["roe"] = ni * (4 / s) / eq * 100  # 累計淨利年化
    ta, tl = c.get("total_assets"), c.get("total_liab")
    if ta and ta > 0 and tl is not None and not fin:
        m["debt_ratio"] = tl / ta * 100
    ca, cl = c.get("cur_assets"), c.get("cur_liab")
    if cl and cl > 0 and ca is not None and not fin:
        m["current_ratio"] = ca / cl * 100
    if c.get("rev_yoy") is not None:
        m["rev_yoy"] = c["rev_yoy"]
    pe, eps = c.get("pe"), c.get("eps")
    if pe is not None and pe > 0:
        m["pe"] = pe
    elif eps is not None and eps <= 0:
        m["pe"] = LOSS_PE
    if c.get("pb") is not None and c["pb"] > 0:
        m["pb"] = c["pb"]
    if c.get("dy") is not None:
        m["dy"] = c["dy"]
    return m


def fmt_metric(key, v, mc):
    if key == "pe" and v >= LOSS_PE:
        return "本益比：虧損"
    return f"{mc['label']}：{v:.1f}{mc['unit']}"


def score_company(c, cfg):
    metrics = derive(c)
    total_w = sum(mc["weight"] for mc in cfg["metrics"])
    got, acc, subs = 0.0, 0.0, {}
    for mc in cfg["metrics"]:
        v = metrics.get(mc["key"])
        if v is None:
            continue
        sub = interp(v, mc["bad"], mc["good"])
        subs[mc["key"]] = sub
        acc += sub * mc["weight"]
        got += mc["weight"]
    coverage = got / total_w if total_w else 0.0

    flags = []
    if c.get("net_income") is not None and c["net_income"] < 0:
        flags.append("本期虧損")
    elif c.get("eps") is not None and c["eps"] <= 0:
        flags.append("每股盈餘為負")
    if c.get("op_income") is not None and c["op_income"] < 0:
        flags.append("本業虧損")
    dmax = cfg.get("red_flags", {}).get("debt_ratio_max")
    if dmax is not None and metrics.get("debt_ratio", 0) > dmax:
        flags.append(f"負債比偏高（>{dmax}%）")

    if got == 0 or coverage < cfg["min_coverage"]:
        return metrics, subs, None, "資料不足", coverage, flags, [], []

    score = acc / got
    if score <= cfg["sell_below"]:
        label = "賣出"
    elif score >= cfg["buy_above"] and not flags and coverage >= cfg.get("buy_min_coverage", 0):
        label = "買進"
    else:
        label = "觀望"

    by_key = {mc["key"]: mc for mc in cfg["metrics"]}
    ranked = sorted(subs.items(), key=lambda kv: kv[1], reverse=True)
    strengths = [fmt_metric(k, metrics[k], by_key[k]) for k, s in ranked if s >= 70][:2]
    weaknesses = [fmt_metric(k, metrics[k], by_key[k]) for k, s in reversed(ranked) if s <= 30][:2]
    return metrics, subs, score, label, coverage, flags, strengths, weaknesses


def industry_name(code, cfg):
    names = cfg["industry_names"]
    if code in names:
        return names[code]
    if code.zfill(2) in names:
        return names[code.zfill(2)]
    return code if code and not code.isdigit() else "其他"


def build(raw_companies, cfg, generated_at):
    out = []
    for c in raw_companies:
        metrics, subs, score, label, cov, flags, up, dn = score_company(c, cfg)
        period = f"{c['year']}Q{c['season']}" if c.get("year") and c.get("season") else None
        out.append({
            "code": c["code"],
            "name": c["name"],
            "market": c["market"],
            "industry": industry_name(c.get("industry", ""), cfg),
            "score": None if score is None else round(score, 1),
            "label": label,
            "coverage": round(cov, 2),
            "mcap": None if c.get("mcap") is None else round(c["mcap"], 2),
            "price": c.get("price"),
            "price_date": c.get("price_date"),
            "financial": is_financial(c),
            "period": period,
            "metrics": {k: round(v, 2) for k, v in metrics.items()},
            "subs": {k: round(v) for k, v in subs.items()},
            "strengths": up,
            "weaknesses": dn,
            "flags": flags,
        })

    top_score, top_mcap = {}, {}
    industries = sorted({x["industry"] for x in out})
    for ind in industries:
        group = [x for x in out if x["industry"] == ind]
        scored = [x for x in group if x["score"] is not None]
        scored.sort(key=lambda x: (-x["score"], -(x["mcap"] or 0)))
        top_score[ind] = [x["code"] for x in scored[:3]]
        sized = [x for x in group if x["mcap"] is not None]
        sized.sort(key=lambda x: -x["mcap"])
        top_mcap[ind] = [x["code"] for x in sized[:3]]

    counts = {}
    for x in out:
        counts[x["label"]] = counts.get(x["label"], 0) + 1
    return {
        "generated_at": generated_at,
        "thresholds": {"buy": cfg["buy_above"], "sell": cfg["sell_below"]},
        "metric_labels": {mc["key"]: {"label": mc["label"], "unit": mc["unit"]} for mc in cfg["metrics"]},
        "counts": counts,
        "industries": industries,
        "companies": out,
        "top": {"score": top_score, "mcap": top_mcap},
    }


def main(src=None, dst=None):
    src = pathlib.Path(src) if src else ROOT / "data" / "companies.json"
    dst = pathlib.Path(dst) if dst else ROOT / "docs" / "data.json"
    cfg = json.loads((ROOT / "config" / "scoring.json").read_text(encoding="utf-8"))
    payload = json.loads(src.read_text(encoding="utf-8"))
    result = build(payload["companies"], cfg, payload["generated_at"])
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"完成：{len(result['companies'])} 家，標籤分布 {result['counts']} -> {dst}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
