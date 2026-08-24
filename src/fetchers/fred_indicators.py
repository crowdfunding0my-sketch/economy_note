"""
FRED（セントルイス連銀）APIを使った経済指標の日次基本データ取得。

役割: 平常時の基本データ収集源（毎日実行）。CPI等の物価指標に加え、
S&P500・NASDAQ・ダウ平均などのコア指数も同じAPIでまとめて取得する。
指標発表当日の速報値・予想値との突合は bls_indicators.py（発表日のみ）を使う。

取得する系列:
- SP500 / NASDAQCOM / DJIA：コア指数の終値（日次）
- CPIAUCSL：CPI 全品目（季節調整済み、月次）
- CPILFESL：コアCPI（食品・エネルギー除く、季節調整済み、月次）
- UNRATE：失業率（月次）
- PAYEMS：非農業部門雇用者数（月次、雇用統計の目玉指標）
- GACDFSA066MSFRBPHI / GACDISA066MSFRBNY：フィラデルフィア連銀・NY連銀(Empire State)の
  製造業景況感調査（月次、PMIの代替指標）。

【PMI（ISM・S&P Global）について】
正式なISM PMI・S&P Global PMIは無料APIが存在しない（ISMは2016年にFREDへのデータ提供を終了。
S&P Globalは購読契約が必要）ため、本ツールでは代わりに地区連銀の製造業サーベイ（無料・FRED）を
「PMI発表前に市場が参照する先行指標」として採用している。ただし尺度が異なる点に注意：
PMIは50が拡大/縮小の境目だが、これらの地区連銀指数は**0が境目**のディフュージョンインデックス。

使い方:
  1. .env に FRED_API_KEY をセット
  2. python src/fetchers/fred_indicators.py
"""

import os
import json
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 系列定義: (系列ID, 表示名, 頻度)。頻度は "daily" か "monthly"。
SERIES = [
    ("SP500", "S&P500", "daily"),
    ("NASDAQCOM", "NASDAQ総合指数", "daily"),
    ("DJIA", "NYダウ平均", "daily"),
    ("CPIAUCSL", "CPI（総合）", "monthly"),
    ("CPILFESL", "コアCPI（食品・エネルギー除く）", "monthly"),
    ("UNRATE", "失業率", "monthly"),
    ("PAYEMS", "非農業部門雇用者数", "monthly"),
    ("GACDFSA066MSFRBPHI", "フィラデルフィア連銀製造業景況指数（PMI代替）", "monthly"),
    ("GACDISA066MSFRBNY", "NY連銀 Empire State製造業指数（PMI代替）", "monthly"),
]


def fetch_observations(series_id, limit=20):
    """指定系列の直近limit件を新しい順で取得（欠測(.)を除く）"""
    resp = requests.get(FRED_URL, params={
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "sort_order": "desc",
        "limit": limit,
    })
    resp.raise_for_status()
    obs = resp.json().get("observations", [])
    return [o for o in obs if o.get("value") not in (None, ".")]


def summarize_daily(observations):
    """日次系列: 最新値・前営業日比を計算"""
    if len(observations) < 2:
        return None
    latest, prev = observations[0], observations[1]
    latest_val = float(latest["value"])
    prev_val = float(prev["value"])
    change_rate = (latest_val - prev_val) / prev_val
    return {
        "latest_date": latest["date"],
        "latest_value": latest_val,
        "prev_date": prev["date"],
        "prev_value": prev_val,
        "change_rate": round(change_rate, 5),
    }


def summarize_monthly(observations):
    """
    月次系列: 最新値・前月比・前年同月比を計算。
    政府機関閉鎖等でデータが欠測している月は fetch_observations 側で除外済みのため、
    「12個前」という配列位置ではなく、日付そのもの（年を1つ戻した同月）で前年同月を探す。
    """
    if len(observations) < 2:
        return None
    latest = observations[0]
    prev_month = observations[1]
    latest_val = float(latest["value"])
    prev_val = float(prev_month["value"])

    latest_date = datetime.strptime(latest["date"], "%Y-%m-%d")
    target_year_ago = latest_date.replace(year=latest_date.year - 1).strftime("%Y-%m-%d")
    year_ago = next((o for o in observations if o["date"] == target_year_ago), None)

    # 地区連銀の景況指数など0をまたぐ系列は前月比%が0除算・無意味な値になりうるため、
    # 分母が0の場合はNoneにする（表示側はpt差[mom_diff/yoy_diff]を使う）
    result = {
        "latest_date": latest["date"],
        "latest_value": latest_val,
        "prev_month_date": prev_month["date"],
        "mom_diff": round(latest_val - prev_val, 5),  # ptの差分（失業率・景況指数など「率」でない系列の表示用）
        "mom_change_rate": round((latest_val - prev_val) / prev_val, 5) if prev_val != 0 else None,
    }
    if year_ago:
        year_ago_val = float(year_ago["value"])
        result["year_ago_date"] = year_ago["date"]
        result["yoy_diff"] = round(latest_val - year_ago_val, 5)
        result["yoy_change_rate"] = round((latest_val - year_ago_val) / year_ago_val, 5) if year_ago_val != 0 else None
    else:
        result["year_ago_date"] = None
        result["yoy_diff"] = None
        result["yoy_change_rate"] = None  # 前年同月のデータが欠測（データ取得件数を増やすか要確認）
    return result


def fetch_all():
    result = {}
    for series_id, label, freq in SERIES:
        obs = fetch_observations(series_id)
        summary = summarize_daily(obs) if freq == "daily" else summarize_monthly(obs)
        result[series_id] = {"label": label, "frequency": freq, "summary": summary}
    return result


def save_result(result, path=None):
    if not path:
        out_dir = PROJECT_ROOT / "output"
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f"fred_indicators_{datetime.now().strftime('%Y%m%d')}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return path


def run(verbose=True):
    if not FRED_API_KEY:
        raise SystemExit(".env に FRED_API_KEY を設定してください")

    result = fetch_all()
    if verbose:
        for series_id, data in result.items():
            print(f"[{series_id}] {data['label']}: {data['summary']}")

    out_path = save_result(result)
    if verbose:
        print(f"\n結果を保存しました: {out_path}")
    return result, out_path


if __name__ == "__main__":
    run()
