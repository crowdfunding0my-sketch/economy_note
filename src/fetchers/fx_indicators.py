"""
有料エリア「ドルインデックス・ドル円・クロス円動向」用のデータ取得。

一般的なFXレート（ドル円・クロス円・ドルインデックス）はFREDから、
「他ではなかなか得られない情報」として、CFTC（米商品先物取引委員会）が毎週発表する
Traders in Financial Futures（TFF）レポートから**円先物のレバレッジド・マネー
（ヘッジファンド等の投機筋）のネットポジション**を取得する。個人投資家向けの相場記事では
ほとんど扱われないが、プロのFXトレーダーは「スマートマネーがどちらに賭けているか」の
参考指標として重視するデータ。

【データソースメモ】
- FRED（為替レート）:
  - DTWEXBGS: 名目広義ドル指数（26通貨バスケット。一般に言われるICE DXY(6通貨)とは構成が異なる点に注意。
    "ドルインデックス相当"として使う）
  - DEXJPUS: ドル円（1ドル＝何円）
  - DEXUSEU / DEXUSUK: ユーロドル・ポンドドル（クロス円の算出に使用。ドル円との掛け算で
    ユーロ円・ポンド円を導出する）
  - 為替系列は発表に1〜2週間程度のラグがある点に注意（記事内で「時点」を明記すること）。
- CFTC（publicreporting.cftc.gov、Socrata Open Data API、認証不要・無料）:
  - Traders in Financial Futures（TFF）レポートの円先物（JAPANESE YEN, CME）データセット
    （gpe5-46if）から、lev_money_positions_long/short（レバレッジド・マネーの買い/売り建玉数）を取得。
  - ネットポジション = long - short。プラスならネットロング（円高方向への賭け）、
    マイナスならネットショート（円安方向への賭け）。
  - レポートは毎週金曜発表、直近火曜時点のデータ（実質数日〜1週間のラグ）。

使い方:
  1. .env に FRED_API_KEY をセット（CFTC側はAPIキー不要）
  2. python src/fetchers/fx_indicators.py
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
CFTC_URL = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"

PROJECT_ROOT = Path(__file__).resolve().parents[2]

FRED_SERIES = {
    "DTWEXBGS": "ドルインデックス相当（FRED広義ドル指数、26通貨バスケット）",
    "DEXJPUS": "ドル円",
    "DEXUSEU": "ユーロドル（クロス円算出用）",
    "DEXUSUK": "ポンドドル（クロス円算出用）",
}


def fetch_fred_observations(series_id, limit=15):
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


def summarize_fred_series(observations):
    if len(observations) < 2:
        return None
    latest, prev = observations[0], observations[1]
    latest_val = float(latest["value"])
    prev_val = float(prev["value"])
    return {
        "latest_date": latest["date"],
        "latest_value": latest_val,
        "prev_date": prev["date"],
        "prev_value": prev_val,
        "change_rate": round((latest_val - prev_val) / prev_val, 5),
    }


def fetch_fx_rates():
    result = {}
    for series_id, label in FRED_SERIES.items():
        obs = fetch_fred_observations(series_id)
        result[series_id] = {"label": label, "summary": summarize_fred_series(obs)}
    return result


def derive_cross_yen(fx_rates):
    """USD/JPYとEUR/USD・GBP/USDから、EUR/JPY・GBP/JPYを算出する"""
    usdjpy = (fx_rates.get("DEXJPUS") or {}).get("summary")
    eurusd = (fx_rates.get("DEXUSEU") or {}).get("summary")
    gbpusd = (fx_rates.get("DEXUSUK") or {}).get("summary")

    crosses = {}
    if usdjpy and eurusd:
        latest = usdjpy["latest_value"] * eurusd["latest_value"]
        prev = usdjpy["prev_value"] * eurusd["prev_value"]
        crosses["EURJPY"] = {
            "label": "ユーロ円（算出値）",
            "latest_value": round(latest, 3),
            "prev_value": round(prev, 3),
            "change_rate": round((latest - prev) / prev, 5),
        }
    if usdjpy and gbpusd:
        latest = usdjpy["latest_value"] * gbpusd["latest_value"]
        prev = usdjpy["prev_value"] * gbpusd["prev_value"]
        crosses["GBPJPY"] = {
            "label": "ポンド円（算出値）",
            "latest_value": round(latest, 3),
            "prev_value": round(prev, 3),
            "change_rate": round((latest - prev) / prev, 5),
        }
    return crosses


def fetch_jpy_futures_positioning():
    """
    CFTC TFFレポートから円先物のレバレッジド・マネー ネットポジションを取得（直近2週分）。
    market_and_exchange_names は完全一致で指定する。ワイルドカード（like '%JAPANESE YEN%'）だと
    別契約の「EURO FX/JAPANESE YEN XRATE」（ユーロ円クロス先物）まで拾ってしまい、
    同じ日付で異なる契約の行が混ざって集計を誤るバグがあったため（実機で確認済み）。
    """
    resp = requests.get(CFTC_URL, params={
        "$where": "market_and_exchange_names = 'JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE'",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": 2,
    }, timeout=20)
    resp.raise_for_status()
    rows = resp.json()
    if len(rows) < 2:
        return None

    def net_position(row):
        return int(row["lev_money_positions_long"]) - int(row["lev_money_positions_short"])

    latest, prev = rows[0], rows[1]
    latest_net = net_position(latest)
    prev_net = net_position(prev)
    return {
        "report_date": latest["report_date_as_yyyy_mm_dd"][:10],
        "prev_report_date": prev["report_date_as_yyyy_mm_dd"][:10],
        "lev_money_long": int(latest["lev_money_positions_long"]),
        "lev_money_short": int(latest["lev_money_positions_short"]),
        "net_position": latest_net,
        "prev_net_position": prev_net,
        "net_position_change": latest_net - prev_net,
        "direction": "ネットロング（円高方向）" if latest_net >= 0 else "ネットショート（円安方向）",
    }


def save_result(result, path=None):
    if not path:
        out_dir = PROJECT_ROOT / "output"
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f"fx_indicators_{datetime.now().strftime('%Y%m%d')}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return path


def run(verbose=True):
    if not FRED_API_KEY:
        raise SystemExit(".env に FRED_API_KEY を設定してください")

    fx_rates = fetch_fx_rates()
    cross_yen = derive_cross_yen(fx_rates)

    try:
        jpy_positioning = fetch_jpy_futures_positioning()
    except Exception as e:
        if verbose:
            print(f"CFTCデータの取得に失敗しました: {e}")
        jpy_positioning = None

    result = {"fx_rates": fx_rates, "cross_yen": cross_yen, "jpy_futures_positioning": jpy_positioning}

    if verbose:
        for sid, data in fx_rates.items():
            print(f"[{sid}] {data['label']}: {data['summary']}")
        for pair, data in cross_yen.items():
            print(f"[{pair}] {data['label']}: {data}")
        if jpy_positioning:
            print(f"[CFTC円先物] {jpy_positioning}")

    out_path = save_result(result)
    if verbose:
        print(f"\n結果を保存しました: {out_path}")
    return result, out_path


if __name__ == "__main__":
    run()
