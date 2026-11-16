"""產生仿開放資料格式的模擬資料（欄位名稱、全形括號、千分位逗號、民國年），供測試完整流程。

注意：這是依我們對官方欄位的理解所做的模擬，不是真實 API 回應；
首次在 GitHub Actions 實跑時，仍需確認欄位名稱與實際資料一致。
"""
import random

IND_CODES = ["01", "02", "03", "05", "10", "14", "15", "17", "21", "22", "24", "25", "26", "27", "28", "29", "30", "31"]


def fmt(v, d=0):
    return f"{v:,.{d}f}"


def make_raw(n_twse=1100, n_tpex=700, seed=7):
    rng = random.Random(seed)
    raw = {m: {k: [] for k in ("basic", "income", "balance", "revenue", "valuation", "price")}
           for m in ("上市", "上櫃")}
    code = 1100
    for market, n in (("上市", n_twse), ("上櫃", n_tpex)):
        src = raw[market]
        tpex = market == "上櫃"
        for _ in range(n):
            code += 1
            cid = str(code)
            ind = rng.choice(IND_CODES)
            fin = ind == "17"
            capital = rng.choice([5e8, 1e9, 3e9, 8e9, 3e10, 2.6e11])
            price = round(rng.uniform(8, 900), 2)
            # 基本資料
            src["basic"].append({"公司代號": cid, "公司簡稱": f"測試{cid}", "產業別": ind, "實收資本額": fmt(capital)})
            # 股價 / 估值（上櫃欄位名稱不同）
            no_trade = rng.random() < 0.03
            pcode = "SecuritiesCompanyCode" if tpex else "Code"
            src["price"].append({"Date": "1151116", pcode: cid, ("Close" if tpex else "ClosingPrice"): "--" if no_trade else fmt(price, 2),
                                 ("TransactionAmount" if tpex else "TradeValue"): "0" if no_trade else str(int(rng.uniform(1e6, 5e10))),
                                 "Change": "0.00" if no_trade else f"{rng.uniform(-0.05, 0.05) * price:+.2f}"})
            pe = round(rng.uniform(5, 60), 2)
            val = {pcode: cid}
            if tpex:
                val.update({"PriceEarningRatio": "" if rng.random() < 0.1 else str(pe),
                            "PriceBookRatio": str(round(rng.uniform(0.5, 6), 2)),
                            "YieldRatio": str(round(rng.uniform(0, 8), 2))})
            else:
                val.update({"PEratio": "" if rng.random() < 0.1 else str(pe),
                            "PBratio": str(round(rng.uniform(0.5, 6), 2)),
                            "DividendYield": str(round(rng.uniform(0, 8), 2))})
            src["valuation"].append(val)
            # 月營收（含金融業）
            src["revenue"].append({"公司代號": cid, "資料年月": "11510", "營業收入-當月營收": str(int(rng.uniform(1e4, 1e7))),
                                   "營業收入-上月比較增減(%)": str(round(rng.uniform(-20, 25), 2)),
                                   "營業收入-去年同月增減(%)": str(round(rng.uniform(-30, 50), 2)),
                                   "累計營業收入-前期比較增減(%)": str(round(rng.uniform(-30, 50), 2))})
            if fin:  # 金融業不在 *_ci 端點
                continue
            rev = rng.uniform(5e5, 5e8)
            loss = rng.random() < 0.12
            gm = rng.uniform(0.05, 0.5)
            opm = rng.uniform(-0.1, 0.25) if loss else rng.uniform(0.0, 0.25)
            nim = opm * 0.8
            season = rng.choice([1, 2, 3])
            assets = rev * rng.uniform(1.5, 5)
            liab = assets * rng.uniform(0.2, 0.9)
            ca = assets * rng.uniform(0.3, 0.7)
            cl = liab * rng.uniform(0.3, 0.9)
            src["income"].append({
                "出表日期": "1141114", "年度": "114", "季別": str(season), "公司代號": cid,
                "營業收入": fmt(rev), "營業毛利（毛損）": fmt(rev * gm),
                "營業利益（損失）": fmt(rev * opm), "本期淨利（淨損）": fmt(rev * nim),
                "基本每股盈餘（元）": str(round(rev * nim / (capital / 10 / 1000), 2)),
            })
            src["balance"].append({
                "公司代號": cid, "資產總額": fmt(assets), "負債總額": fmt(liab),
                "流動資產": fmt(ca), "流動負債": fmt(cl), "權益總額": fmt(assets - liab),
            })
    # 邊界案例
    raw["上市"]["basic"].append({"公司代號": "0050", "公司簡稱": "ETF", "產業別": "", "實收資本額": "1"})  # ETF 應被排除
    raw["上市"]["basic"].append({"公司代號": "9105", "公司簡稱": "泰金寶-DR", "產業別": "91", "實收資本額": "1"})
    raw["上市"]["basic"].append({"公司代號": "9999", "公司簡稱": "無任何財報", "產業別": "99", "實收資本額": "1,000,000,000"})
    # 第一列含 BOM 的欄位名稱
    raw["上市"]["income"][0] = {("﻿" + k if k == "公司代號" else k): v for k, v in raw["上市"]["income"][0].items()}
    return raw


def archetypes():
    """已知答案的樣本：(原始標準化公司資料, 預期標籤)。"""
    base = dict(code="9001", name="x", market="上市", industry="24", price=100.0, mcap=500.0,
                year="114", season=3, revenue=1e8, gross_profit=4.5e7, op_income=2.4e7, net_income=2.0e7,
                eps=5.0, total_assets=1e8, total_liab=3.5e7, cur_assets=6e7, cur_liab=2.5e7, equity=6.5e7,
                rev_yoy=25.0, rev_yoy_m=30.0, rev_mom=15.0, pe=10.0, pb=1.1, dy=6.0)
    strong = dict(base)
    weak = dict(base, code="9002", rev_yoy_m=-20.0, rev_mom=-15.0, gross_profit=5e6, op_income=-1e7, net_income=-1.5e7, eps=-1.2,
                total_liab=9e7, equity=1e7, cur_assets=4e7, cur_liab=8e7, rev_yoy=-25.0, pe=None, pb=5.0, dy=0.0)
    # 高分但本期虧損：不得被標為買進
    flagged = dict(base, code="9003", net_income=-1e6, eps=-0.1, pe=None)
    return [(strong, "買進"), (weak, "賣出"), (flagged, "觀望")]
