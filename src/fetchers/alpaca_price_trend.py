"""
Alpaca Market Data API を使った米国株の株価トレンド一次スクリーニング（Stage B）。

有料エリア「米国小型株1銘柄/日ローテーション」用の候補選定は2段階に分かれる。
  Stage A: Woodstockの銘柄一覧(tradable_symbols)を取得 → output/us_tradable_symbols_raw.json に保存済み
  Stage B（本スクリプト）: Alpacaの直接APIで全銘柄の過去2年の株価トレンドを計算し、
           右肩上がりの銘柄だけに絞った候補リストを作る（完全自動・レート制限なし）
  Stage C: Stage Bの候補（数十銘柄程度）だけ、Woodstockのget_fundamentals（Claude経由でしか
           呼べない）で決算・時価総額をチェックし、最終候補を確定する（このスクリプトの対象外）

Stage Aの銘柄一覧には多数のETF・レバレッジ商品が混ざっているが、ここでは除外しない
（ETFは決算データを持たないため、Stage Cのfundamentalsチェックで自然に弾かれる）。

使い方:
  1. .env に ALPACA_API_KEY_ID / ALPACA_SECRET_KEY をセット
  2. python src/fetchers/alpaca_price_trend.py
"""

import os
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from trend_utils import linear_trend

load_dotenv()

DATA_BASE_URL = "https://data.alpaca.markets"
HEADERS = {
    "APCA-API-KEY-ID": os.environ.get("ALPACA_API_KEY_ID", ""),
    "APCA-API-SECRET-KEY": os.environ.get("ALPACA_SECRET_KEY", ""),
}

LOOKBACK_DAYS = 760  # 約2年強（週末・休場日を考慮して余裕を持たせる）
TREND_MIN_R2 = 0.3  # jquants_screener.pyと同じ「一貫性」の目安値
MIN_PRICE_POINTS = 200  # 上場間もない銘柄など、データが少なすぎるものは除外
SHORTLIST_SIZE = 60  # Stage C（Woodstock fundamentals）に回す候補数の上限
BATCH_SYMBOLS = 200  # 1リクエストにまとめて渡すシンボル数（レコード数上限でページングされる）

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_symbols():
    path = PROJECT_ROOT / "output" / "us_tradable_symbols_raw.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def fetch_bars(symbols, start, end):
    """
    指定シンボル群の日足バーを取得し、{symbol: [bar, ...]} で返す（日付昇順）。
    レコード数上限によるページングを追い切って全件取得する。
    """
    result = {}
    page_token = None
    while True:
        params = {
            "symbols": ",".join(symbols),
            "timeframe": "1Day",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "limit": 10000,
            "feed": "iex",
            "adjustment": "split",
        }
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(f"{DATA_BASE_URL}/v2/stocks/bars", headers=HEADERS, params=params)
        resp.raise_for_status()
        body = resp.json()
        for sym, bars in body.get("bars", {}).items():
            result.setdefault(sym, []).extend(bars)
        page_token = body.get("next_page_token")
        if not page_token:
            break
    return result


def screen(symbols, verbose=True):
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=LOOKBACK_DAYS)

    all_bars = {}
    for i in range(0, len(symbols), BATCH_SYMBOLS):
        batch = symbols[i:i + BATCH_SYMBOLS]
        bars = fetch_bars(batch, start, end)
        all_bars.update(bars)
        if verbose:
            print(f"...{min(i + BATCH_SYMBOLS, len(symbols))}/{len(symbols)} 銘柄分の株価取得済み")

    candidates = []
    for sym, bars in all_bars.items():
        if len(bars) < MIN_PRICE_POINTS:
            continue
        bars = sorted(bars, key=lambda b: b["t"])
        closes = [b["c"] for b in bars]
        slope, r_squared = linear_trend(closes)
        if slope <= 0 or r_squared < TREND_MIN_R2:
            continue
        price_change_rate = (closes[-1] - closes[0]) / closes[0]
        candidates.append({
            "symbol": sym,
            "latest_close": closes[-1],
            "trend_slope": round(slope, 4),
            "trend_r2": round(r_squared, 3),
            "price_change_rate": round(price_change_rate, 4),
            "period_start": bars[0]["t"][:10],
            "period_end": bars[-1]["t"][:10],
            "data_points": len(bars),
        })

    candidates.sort(key=lambda c: c["price_change_rate"], reverse=True)
    return candidates[:SHORTLIST_SIZE]


def save_results(candidates, path=None):
    if not path:
        out_dir = PROJECT_ROOT / "output"
        out_dir.mkdir(exist_ok=True)
        path = out_dir / "us_trend_candidates.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(candidates, f, ensure_ascii=False, indent=2)
    return path


if __name__ == "__main__":
    if not HEADERS["APCA-API-KEY-ID"] or not HEADERS["APCA-API-SECRET-KEY"]:
        raise SystemExit(".env に ALPACA_API_KEY_ID / ALPACA_SECRET_KEY を設定してください")

    symbols = load_symbols()
    print(f"対象銘柄数: {len(symbols)}")
    print(f"条件: 株価トレンド右肩上がり(R2>={TREND_MIN_R2}) / 上位{SHORTLIST_SIZE}件を抽出")

    results = screen(symbols)

    print(f"\nトレンド条件合致銘柄数（上位{SHORTLIST_SIZE}件抽出後）: {len(results)}")
    out_path = save_results(results)
    print(f"結果を保存しました: {out_path}")
    print("この結果をもとに、Stage C（Woodstockでの決算・時価総額チェック）に進んでください。")
