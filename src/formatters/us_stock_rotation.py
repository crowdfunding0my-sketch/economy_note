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
import re
import sys
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from deep_translator import GoogleTranslator

# 下書きファイル名・ローテーション記録はarticle_builder.pyが読みに来る日付と一致させる必要があるため、
# 読者の生活時間である日本時間(JST)基準にする（UTC基準だとJST朝の実行時に前日扱いになってしまう）。
JST = ZoneInfo("Asia/Tokyo")

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
    state["last_picked_date"] = datetime.now(JST).date().isoformat()
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


def fetch_overview(symbol, retries=1, retry_wait_sec=20):
    """Alpha VantageのOVERVIEWでアナリスト目標株価・レーティング・予想PER等を取得（「今後の展望」の元データ）。
    レート制限（5リクエスト/分）に引っかかった場合はNote/Informationキー付きのJSONが返るだけで
    HTTPエラーにはならないため、"Symbol"キーの有無で成否を判定し、失敗時は少し待って1回だけ再試行する。"""
    if not ALPHAVANTAGE_API_KEY:
        return {}
    for attempt in range(retries + 1):
        resp = requests.get(ALPHAVANTAGE_URL, params={
            "function": "OVERVIEW",
            "symbol": symbol,
            "apikey": ALPHAVANTAGE_API_KEY,
        })
        resp.raise_for_status()
        body = resp.json()
        if body.get("Symbol"):
            return body
        if attempt < retries:
            time.sleep(retry_wait_sec)
    return {}


def _to_float(value):
    """Alpha Vantageは未提供の値を"-"や"None"の文字列で返すことがあるため、それらをNoneに正規化する"""
    try:
        if value in (None, "", "-", "None"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def build_outlook(stock, overview):
    """アナリスト予想・レーティング（Alpha Vantage OVERVIEW）をもとに「今後の展望」を組み立てる。
    文章はAIが書くのではなく、取得した数値をそのまま構成している。"""
    lines = []

    target = _to_float(overview.get("AnalystTargetPrice"))
    latest_close = stock.get("latest_close")
    if target and latest_close:
        upside_pct = (target / latest_close - 1) * 100
        lines.append(f"- アナリスト目標株価：{target:,.2f}ドル（現在値 {latest_close:,.2f}ドルから{upside_pct:+.1f}%）")

    ratings = [
        ("強気買い", _to_float(overview.get("AnalystRatingStrongBuy"))),
        ("買い", _to_float(overview.get("AnalystRatingBuy"))),
        ("中立", _to_float(overview.get("AnalystRatingHold"))),
        ("売り", _to_float(overview.get("AnalystRatingSell"))),
        ("強気売り", _to_float(overview.get("AnalystRatingStrongSell"))),
    ]
    if any(v for _, v in ratings):
        rating_text = "・".join(f"{label}{int(v)}" for label, v in ratings if v)
        lines.append(f"- アナリスト評価：{rating_text}")

    forward_pe = _to_float(overview.get("ForwardPE"))
    trailing_pe = _to_float(overview.get("TrailingPE"))
    if forward_pe:
        pe_text = f"- 予想PER：{forward_pe:.1f}倍"
        if trailing_pe:
            pe_text += f"（実績PER {trailing_pe:.1f}倍）"
        lines.append(pe_text)

    rev_growth = _to_float(overview.get("QuarterlyRevenueGrowthYOY"))
    earn_growth = _to_float(overview.get("QuarterlyEarningsGrowthYOY"))
    if rev_growth is not None:
        growth_text = f"- 直近四半期の売上成長率（前年比）：{rev_growth*100:+.1f}%"
        if earn_growth is not None:
            growth_text += f"、利益成長率（前年比）：{earn_growth*100:+.1f}%"
        lines.append(growth_text)

    if not lines:
        return "アナリストによる予想データが取得できませんでした（カバレッジ対象外の可能性があります）。"

    lines.append("")
    lines.append("> アナリスト予想はAlpha Vantageが集計した市場コンセンサスで、カバレッジの薄い銘柄では件数が少なく参考程度になる場合があります。")
    return "\n".join(lines)


def _trim_description(text, target_len=200):
    """Alpha VantageのDescriptionは、後半に「業界のリーダーとしての地位を確立」のような
    定型的な誇張表現（PR文）が続くことが多く、機械翻訳するとその部分が特に不自然になりやすい。
    target_len文字以降で最初に来る文の区切り（". "）までを採用し、末尾の誇張表現を落とす。"""
    if not text:
        return text
    boundaries = [m.end() for m in re.finditer(r"\. ", text)]
    cutoff = next((b for b in boundaries if b >= target_len), None)
    if cutoff is None:
        return text
    return text[:cutoff].rstrip()


def translate_to_japanese(text):
    """deep-translator（Google翻訳の無料・非公式エンドポイント経由）で英語テキストを日本語に翻訳する。
    非公式APIのため失敗しうる前提でNoneを返し、呼び出し側で原文（英語）にフォールバックする。"""
    if not text:
        return None
    try:
        return GoogleTranslator(source="en", target="ja").translate(text)
    except Exception:
        return None


def build_business_section(stock, overview):
    """「事業内容」セクションを組み立てる。

    2026-09-10追加: 従来はAlpha Vantage OVERVIEWのSector/Industry/Descriptionのみに
    依存していたが、OVERVIEWはレート制限（5req/分）で無言のまま空データが返ることがあり
    （HTTPエラーにならないため検知しづらい）、その場合「事業内容」セクションが記事に
    一切表示されない不具合があった（ユーザー指摘で発覚）。
    us_premium_rotation_candidates.jsonに候補選定時（Stage C）から埋め込んである
    50〜100字程度の固定説明文（business_desc）を常に表示する土台とし、
    Alpha Vantageからより詳しい分野・説明文が取得できた場合は追加情報として補う構成にした。
    """
    lines = ["**事業内容**", ""]

    business_desc = stock.get("business_desc")
    if business_desc:
        lines.append(business_desc)
        lines.append("")

    sector = overview.get("Sector")
    industry = overview.get("Industry")
    description = overview.get("Description")
    if not (sector or industry or description):
        return "\n".join(lines) if business_desc else None

    field_en = " / ".join(p for p in [sector, industry] if p)
    if field_en:
        field_ja = translate_to_japanese(field_en)
        lines.append(f"- 分野：{field_ja or field_en}")
        lines.append("")

    if description:
        description = _trim_description(description)
        desc_ja = translate_to_japanese(description)
        lines.append(desc_ja or description)
        lines.append("")
        if desc_ja:
            lines.append("> 事業内容はAlpha Vantageの英語情報を機械翻訳したものです。ニュアンスが原文と異なる場合があります。")
        else:
            lines.append("> 翻訳に失敗したため、Alpha Vantageの英語原文をそのまま表示しています。")

    return "\n".join(lines)


def format_draft(stock, news_items, overview):
    symbol = stock["symbol"]
    growth_pct = stock["revenue_growth_rate"] * 100
    price_change_pct = stock["price_change_rate_2y"] * 100
    mktcap_b = stock["market_cap_usd"] / 1_000_000_000

    lines = [
        f"### {symbol}（{stock.get('company_hint', '')}）",
        "",
    ]

    business_section = build_business_section(stock, overview)
    if business_section:
        lines += [business_section, ""]

    lines += [
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
        build_outlook(stock, overview),
        "",
    ]
    return "\n".join(lines)


def _build_and_save_draft(stock, verbose=True):
    if verbose:
        print(f"本日の銘柄: {stock['symbol']}")
    news_items = fetch_news(stock["symbol"])
    if verbose:
        print(f"取得ニュース件数: {len(news_items)}")
    overview = fetch_overview(stock["symbol"])
    if verbose and not overview.get("Symbol"):
        print("警告: アナリスト予想データを取得できませんでした（レート制限、または本当にカバレッジ対象外の可能性）")

    draft = format_draft(stock, news_items, overview)

    DRAFT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DRAFT_DIR / f"us_pick_{datetime.now(JST).strftime('%Y%m%d')}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(draft)
    return out_path


def retry_today(verbose=True):
    """本日すでに選定済みの銘柄について、下書きだけを再生成する（ローテーションは進めない）。
    「今後の展望」がフォールバック文言になった際の手動リカバリー用。"""
    candidates = load_candidates()
    state = load_state()
    symbol = state.get("last_picked_symbol")
    if not symbol:
        raise RuntimeError("本日選定済みの銘柄がありません（先にrun()を実行してください）")
    stock = next((c for c in candidates if c["symbol"] == symbol), None)
    if stock is None:
        raise RuntimeError(f"候補リストに{symbol}が見つかりません")
    out_path = _build_and_save_draft(stock, verbose=verbose)
    if verbose:
        print(f"下書きを再生成しました（ローテーションは変更なし）: {out_path}")
    return stock, out_path


def run(verbose=True):
    candidates = load_candidates()
    state = load_state()
    stock = pick_todays_stock(candidates, state)

    out_path = _build_and_save_draft(stock, verbose=verbose)

    save_state(state)
    if verbose:
        print(f"下書きを保存しました: {out_path}")
        print(f"次回のローテーション位置: {state['next_index']} / {len(candidates)}")
    return stock, out_path


if __name__ == "__main__":
    if "--retry" in sys.argv:
        retry_today()
    else:
        run()
