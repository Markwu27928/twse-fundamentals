#!/usr/bin/env python3
"""匯率（美元兌新台幣）與外資買賣超，輸出 docs/macro.json，用於網頁標題判讀外資匯入或匯出。

資料來源：
  外資買賣超：證交所「三大法人買賣金額統計表」BFI82U（外資及陸資＋外資自營商，上市市場）
  匯率：免費每日參考匯率（currency-api，備援 open.er-api.com）；有日期版本，可回補歷史

判讀規則（可在 config/macro.json 調整門檻）：
  資金訊號：近 N 日外資累計買賣超 ≥ +門檻 → +1；≤ −門檻 → −1；其餘 0
  匯率訊號：美元兌台幣上漲（台幣貶值）超過門檻 → −1（匯出壓力）；下跌（台幣升值）超過門檻 → +1；其餘 0
  兩者相加：≥2 明顯匯入、1 偏匯入、0 中性（或訊號分歧）、−1 偏匯出、≤−2 明顯匯出

這只是市場資金流向的參考指標，不是外資實際匯出入金額（官方匯出入統計為每月公布）。
macro 失敗不影響股票評分；失敗時保留上一次的歷史資料並在網頁顯示「暫缺」。
"""
import datetime
import json
import pathlib
import re
import sys
import time
from zoneinfo import ZoneInfo

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
HIST = ROOT / "data" / "macro_history.json"
OUT = ROOT / "docs" / "macro.json"
TZ = ZoneInfo("Asia/Taipei")
UA = {"User-Agent": "Mozilla/5.0 (fundamentals-bot)", "Accept": "application/json"}

BFI82U = "https://www.twse.com.tw/rwd/zh/fund/BFI82U?type=day&dayDate={d}&response=json"
FX_URLS = [
    "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{d}/v1/currencies/usd.json",
    "https://{d}.currency-api.pages.dev/v1/currencies/usd.json",
]
FX_LATEST_FALLBACK = "https://open.er-api.com/v6/latest/USD"


def get_json(url, retries=3):
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, headers=UA, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"{url.split('?')[0]} 失敗：{last}")


def num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def parse_foreign(j):
    """BFI82U 回應 -> (日期 'YYYY-MM-DD', 外資買賣超（億元）, 明細)；無資料回傳 None。"""
    if not isinstance(j, dict) or j.get("stat") != "OK" or not j.get("data"):
        return None
    fields = j.get("fields") or []
    try:
        ni = next(i for i, f in enumerate(fields) if "買賣差額" in f)
    except StopIteration:
        raise RuntimeError(f"找不到『買賣差額』欄位，實際欄位：{fields}")
    parts = {}
    for row in j["data"]:
        name = str(row[0])
        if name.startswith("外資及陸資") or name.startswith("外資自營商"):
            v = num(row[ni])
            if v is None:
                raise RuntimeError(f"外資買賣差額無法轉成數字：{row}")
            parts[name] = v / 1e8
    if not any(k.startswith("外資及陸資") for k in parts):
        raise RuntimeError(f"找不到外資及陸資列：{[r[0] for r in j['data']]}")
    d = str(j.get("date", ""))
    if not re.fullmatch(r"\d{8}", d):
        raise RuntimeError(f"日期格式異常：{d}")
    return f"{d[:4]}-{d[4:6]}-{d[6:]}", round(sum(parts.values()), 2), {k: round(v, 2) for k, v in parts.items()}


def fetch_foreign(date_yyyymmdd):
    return parse_foreign(get_json(BFI82U.format(d=date_yyyymmdd)))


def parse_fx(j):
    """currency-api 回應 {date, usd:{twd}} -> (日期, 匯率)。"""
    if isinstance(j, dict) and isinstance(j.get("usd"), dict) and num(j["usd"].get("twd")):
        return str(j["date"]), round(num(j["usd"]["twd"]), 4)
    raise RuntimeError("回應中沒有 usd.twd")


def fetch_fx(date="latest"):
    errs = []
    for u in FX_URLS:
        try:
            return parse_fx(get_json(u.format(d=date), retries=2)) + ("currency-api",)
        except Exception as e:  # noqa: BLE001
            errs.append(str(e))
    if date == "latest":
        try:
            j = get_json(FX_LATEST_FALLBACK, retries=2)
            rate = num((j.get("rates") or {}).get("TWD"))
            d = datetime.datetime.fromtimestamp(j["time_last_update_unix"], datetime.timezone.utc).astimezone(TZ).strftime("%Y-%m-%d")
            if rate:
                return d, round(rate, 4), "open.er-api"
        except Exception as e:  # noqa: BLE001
            errs.append(str(e))
    raise RuntimeError("；".join(errs))


def sign_label(total):
    if total >= 2:
        return "明顯匯入"
    if total == 1:
        return "偏匯入"
    if total <= -2:
        return "明顯匯出"
    if total == -1:
        return "偏匯出"
    return "中性"


def summarize(hist, cfg, today):
    """由歷史資料算出匯率變動、外資累計與判讀。回傳 macro.json 內容（缺資料的欄位為 None）。"""
    days = cfg["days"]
    out = {"fx": None, "foreign": None, "verdict": None}

    fx = hist.get("fx", {})
    if fx:
        ds = sorted(fx)
        last = ds[-1]
        prev = next((d for d in reversed(ds[:-1])), None)
        target = (datetime.date.fromisoformat(last) - datetime.timedelta(days=cfg["fx_window_calendar_days"])).isoformat()
        base_d = next((d for d in reversed(ds[:-1]) if d <= target), ds[0] if len(ds) > 1 else None)
        out["fx"] = {
            "date": last, "rate": fx[last],
            "change_1d_pct": round((fx[last] / fx[prev] - 1) * 100, 2) if prev else None,
            "change_win_pct": round((fx[last] / fx[base_d] - 1) * 100, 2) if base_d else None,
            "win_from": base_d,
        }

    fr = hist.get("foreign", {})
    if fr:
        ds = sorted(fr)
        recent = ds[-days:]
        out["foreign"] = {
            "date": ds[-1], "today": fr[ds[-1]], "days": len(recent), "sum": round(sum(fr[d] for d in recent), 1),
            "series": [{"date": d, "net": fr[d]} for d in recent],
        }

    f, x = out["foreign"], out["fx"]
    if f:
        flow = 1 if f["sum"] >= cfg["foreign_threshold_100m"] else -1 if f["sum"] <= -cfg["foreign_threshold_100m"] else 0
        fxs = None
        if x and x["change_win_pct"] is not None:
            c = x["change_win_pct"]
            fxs = -1 if c >= cfg["fx_threshold_pct"] else 1 if c <= -cfg["fx_threshold_pct"] else 0
        total = flow + (fxs or 0)
        label = sign_label(total)
        if label == "中性" and fxs is not None and flow != 0 and flow == -fxs:
            label = "訊號分歧"
        parts = [f"外資近 {f['days']} 日{'買超' if f['sum'] >= 0 else '賣超'} {abs(f['sum']):,.0f} 億"]
        if fxs is not None:
            parts.append("新台幣" + ("貶值" if fxs == -1 else "升值" if fxs == 1 else "持平") + f"（美元 {x['change_win_pct']:+.2f}%）")
        else:
            parts.append("匯率資料不足，只用外資買賣超判讀")
        out["verdict"] = {"label": label, "flow": flow, "fx": fxs, "detail": "，".join(parts)}
    return out


def trading_days_missing(hist, today, lookback=14):
    """需要補抓的外資日期（平日、尚未記錄、未被標為休市）。今天不標休市（可能尚未公布）。"""
    have = set(hist.get("foreign", {})) | set(hist.get("closed", []))
    res = []
    for i in range(lookback, -1, -1):
        d = today - datetime.timedelta(days=i)
        if d.weekday() >= 5 or d.isoformat() in have:
            continue
        res.append(d)
    return res


def main():
    cfg = json.loads((ROOT / "config" / "macro.json").read_text(encoding="utf-8"))
    hist = json.loads(HIST.read_text(encoding="utf-8")) if HIST.exists() else {}
    hist.setdefault("fx", {}), hist.setdefault("foreign", {}), hist.setdefault("closed", [])
    today = datetime.datetime.now(TZ).date()
    errors = []

    # 外資買賣超
    for d in trading_days_missing(hist, today):
        try:
            r = fetch_foreign(d.strftime("%Y%m%d"))
            if r:
                hist["foreign"][r[0]] = r[1]
                print(f"[外資] {r[0]} 買賣超 {r[1]:+,.1f} 億  {r[2]}")
            elif d != today:
                hist["closed"].append(d.isoformat())
                print(f"[外資] {d} 無資料（休市）")
        except Exception as e:  # noqa: BLE001
            errors.append(f"外資 {d}：{e}")
            print(f"[警告] 外資 {d}：{e}", file=sys.stderr)
        time.sleep(3)

    # 匯率：最新值 + 回補近 10 天歷史
    try:
        d, rate, src = fetch_fx("latest")
        hist["fx"][d] = rate
        print(f"[匯率] {d} USD/TWD {rate}（{src}）")
    except Exception as e:  # noqa: BLE001
        errors.append(f"匯率最新值：{e}")
        print(f"[警告] 匯率最新值：{e}", file=sys.stderr)
    for i in range(1, 11):
        d = (today - datetime.timedelta(days=i)).isoformat()
        if d in hist["fx"]:
            continue
        try:
            dd, rate, _ = fetch_fx(d)
            hist["fx"][dd] = rate
        except Exception as e:  # noqa: BLE001
            errors.append(f"匯率 {d}：{e}")
            break  # 歷史版本取不到就不再逐日重試
    for k in ("fx", "foreign"):
        for old in sorted(hist[k])[:-60]:
            del hist[k][old]
    hist["closed"] = sorted(set(hist["closed"]))[-60:]

    result = summarize(hist, cfg, today)
    result["generated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    result["errors"] = errors
    result["config"] = {k: cfg[k] for k in ("days", "foreign_threshold_100m", "fx_threshold_pct", "fx_window_calendar_days")}

    HIST.parent.mkdir(parents=True, exist_ok=True)
    HIST.write_text(json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf-8")
    OUT.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps(result["verdict"], ensure_ascii=False), "| fx:", result["fx"], "| 外資:", result["foreign"] and {k: result["foreign"][k] for k in ("date", "today", "sum", "days")})
    if result["fx"] is None or result["foreign"] is None:
        print("::warning::匯率或外資資料暫缺，詳見 docs/macro.json 的 errors", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
