"""
有料エリア「米国成長モメンタム株 1銘柄/日ローテーション」の記事下書き生成スクリプト。

前提:
- 候補15銘柄は output/us_premium_rotation_candidates.json に確定済み
  （選定ロジック: src/fetchers/alpaca_price_trend.py の出力を、Woodstockのget_fundamentalsで
  「売上高が前期比増加」「時価総額150億ドル以下」の2条件を満たすもの、株価上昇率順に絞り込んだもの。
  この銘柄選定自体はClaude経由でしか自動化できない[Woodstockの制約]ため、本スクリプトは
  「候補リストから今日の1銘柄を選び、最新ニュースを付加して下書きを作る」部分のみを担当する）

- ローテーションの周回位置は output/rotation_state.json に保存し、実行するたびに1つずつ進める
  （15銘柄で1周、一周後は先頭に戻る）

使い方:
  1. .env に ALPHAVANTAGE_API_KEY をセット
  2. python src/formatters/us_stock_rotation.py
"""

import os
import json
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

ALPHAVANTAGE_API_KEY = os.environ.get("ALPHAVANTAGE_API_KEY", "")
ALPHAVANTAGE_URL = "https://www.alphavantage.co/query"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CANDIDATES_PATH = PROJECT_ROOT / "output" / "us_premium_rotation_candidates.json"
STATE_PATH = PROJECT_ROOT / "output" / "rotation_state.json"
DRAFT_DIR = PROJECT_ROOT / "output" / "drafts"


def load_candidates():
    with open(CANDIDATES_PATH, encoding="utf-8") as f:
        return json.load(f)["candidates"]


def load_state():
    if STATE_PATH.exists():
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"next_index": 0}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def pick_todays_stock(candidates, state):
    index = state.get("next_index", 0) % len(candidates)
    stock = candidates[index]
    state["next_index"] = (index + 1) % len(candidates)
    state["last_picked_symbol"] = stock["symbol"]
    state["last_picked_date"] = datetime.now(timezone.utc).date().isoformat()
    return stock


def fetch_news(symbol, limit=5):
    """Alpha VantageのNEWS_SENTIMENTで直近ニュースを取得"""
    if not ALPHAVANTAGE_API_KEY:
        return []
    resp = requests.get(ALPHAVANTAGE_URL, params={
        "function": "NEWS_SENTIMENT",
        "tickers": symbol,
        "limit": limit,
        "apikey": ALPHAVANTAGE_API_KEY,
    })
    resp.raise_for_status()
    body = resp.json()
    # Alpha Vantage側がlimitパラメータを無視して返すことがあるため、念のためクライアント側でも切り詰める
    return body.get("feed", [])[:limit]


def format_draft(stock, news_items):
    symbol = stock["symbol"]
    growth_pct = stock["revenue_growth_rate"] * 100
    price_change_pct = stock["price_change_rate_2y"] * 100
    mktcap_b = stock["market_cap_usd"] / 1_000_000_000

    lines = [
        f"### {symbol}（{stock.get('company_hint', '')}）",
        "",
        "**直近決算**",
        "",
        f"- 売上高：{stock['revenue_curr']:,.0f}ドル（前期 {stock['revenue_prev']:,.0f}ドル、{growth_pct:+.1f}%）",
        f"- EPS（直近期）：{stock['eps_curr']}",
        f"- 時価総額：約{mktcap_b:.2f}億ドル",
        f"- 株価（過去2年）：{price_change_pct:+.1f}%（トレンドの一貫性 R²={stock['trend_r2']}）",
        "",
        "> ※本銘柄は「割安小型株」ではなく「売上成長・株価モメンタム」を基準に選定しています。"
        "現時点で黒字化していない場合があります。",
        "",
        "**最近のニュース**",
        "",
    ]
    if news_items:
        for item in news_items:
            title = item.get("title", "")
            source = item.get("source", "")
            time_published = item.get("time_published", "")
            url = item.get("url", "")
            lines.append(f"- [{title}]({url})（{source}, {time_published}）")
    else:
        lines.append("- （ニュース取得なし、または該当なし）")

    lines += [
        "",
        "**今後の展望**",
        "",
        "（直近決算・ニュースを踏まえたコメントをここに追記）",
        "",
    ]
    return "\n".join(lines)


def run(verbose=True):
    candidates = load_candidates()
    state = load_state()
    stock = pick_todays_stock(candidates, state)

    if verbose:
        print(f"本日の銘柄: {stock['symbol']}")
    news_items = fetch_news(stock["symbol"])
    if verbose:
        print(f"取得ニュース件数: {len(news_items)}")

    draft = format_draft(stock, news_items)

    DRAFT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DRAFT_DIR / f"us_pick_{datetime.now(timezone.utc).strftime('%Y%m%d')}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(draft)

    save_state(state)
    if verbose:
        print(f"下書きを保存しました: {out_path}")
    return stock, out_path


if __name__ == "__main__":
    run()
    print(f"次回のローテーション位置: {state['next_index']} / {len(candidates)}")
