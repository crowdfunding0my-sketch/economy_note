"""
記事のFRB発表セクションの上に挿入する「今日の市場ニュース一言」用データ取得。

【データソースの選定理由】
ユーザーから「トランプ大統領と習近平氏の会談を受けて円高が進んだ」のような、市場を動かした
その日のエピソードを一言添えたいという要望を受けたが、取得元は未定だった。検討した候補：

- Alpha Vantage NEWS_SENTIMENT（topics=economy_macro等）：既に契約済みで追加コスト無しだが、
  実際に試すと個別企業のIPO・決算記事が中心で、地政学・為替のような「市場を動かした一言エピソード」
  にはなりにくかった（英語なので機械翻訳も必要）。
- NHKニュース経済カテゴリRSS（採用）：`https://www3.nhk.or.jp/rss/news/cat5.xml`。
  無料・認証不要・日本語ネイティブ（機械翻訳の失敗リスクが無い）。ユーザーの例（米雇用統計や
  要人発言を受けた円高進行など）に近い見出しが実際に頻出することを確認済み。
  経済カテゴリ全体には市場と直接関係ない記事（企業の不祥事・製品リコール等）も混在するため、
  円・ドル・株価・金利など市場関連キーワードを含む見出しをMARKET_KEYWORDSで絞り込む。

該当する見出しが見つからない場合はNoneを返す（その日は該当セクションを省略するだけで、
記事生成自体は止めない）。

使い方:
  python src/fetchers/market_news.py
"""

import sys
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

from http_utils import get_with_retry

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

JST = ZoneInfo("Asia/Tokyo")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "output"

NHK_ECONOMY_RSS = "https://www3.nhk.or.jp/rss/news/cat5.xml"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# 経済カテゴリ全体から「市場を動かしたニュース」に絞り込むためのキーワード
MARKET_KEYWORDS = [
    "円", "ドル", "ユーロ", "株価", "株式", "日経平均", "相場", "為替",
    "利上げ", "利下げ", "金利", "FRB", "FOMC", "日銀", "米国", "貿易", "関税",
]


def fetch_market_headline():
    """NHK経済カテゴリRSSから、市場関連キーワードを含む直近の見出しを1件返す"""
    resp = get_with_retry(NHK_ECONOMY_RSS, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    for item in root.findall("./channel/item"):
        title = item.findtext("title", default="")
        if any(kw in title for kw in MARKET_KEYWORDS):
            return {
                "title": title,
                "link": item.findtext("link", default="").strip(),
                "pub_date": item.findtext("pubDate", default=""),
            }
    return None


def run(verbose=True):
    try:
        headline = fetch_market_headline()
    except Exception as e:
        if verbose:
            print(f"[WARN] NHKニュースの取得に失敗しました: {e}")
        headline = None

    if verbose:
        if headline:
            print(f"今日の市場ニュース: {headline['title']}")
        else:
            print("該当する市場ニュースの見出しが見つかりませんでした。")

    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / f"market_news_{datetime.now(JST).strftime('%Y%m%d')}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(headline, f, ensure_ascii=False, indent=2)
    if verbose:
        print(f"結果を保存しました: {out_path}")
    return headline


if __name__ == "__main__":
    run()
