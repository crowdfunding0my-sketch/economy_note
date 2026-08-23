"""
FRB（連邦準備制度理事会）の公式RSSフィードから金融政策関連の発表文を取得する。

「一次情報の原則」（解説記事より先にFRB・BLS等の一次発表そのものを参照する）を実現するための
ソース。BLSと異なり federalreserve.gov はボット対策で自動アクセスを拒否しないことを実機で
確認済み（200 OK）。RSS 2.0形式のクリーンなXMLなので標準ライブラリのxml.etree.ElementTreeで
十分パース可能（追加ライブラリ不要）。

【Reuters/APについて】
Reuters Connect・AP News APIはいずれも企業向けの営業窓口経由の契約が必要で、料金非公開の
エンタープライズ向けサービス。個人運営のnote自動投稿ツールで使える自己登録型の無料/安価な
プランは存在しないため、Reuters/APの直接利用は見送る。一次情報としてはFRB公式発表（本ファイル）を、
ニュース全般はAlpha Vantageのニュース集約（src/formatters/us_stock_rotation.py）を使う方針とする。

取得する系列:
- press_monetary.xml：金融政策関連の発表（FOMC声明・議事要旨など、最も相場への影響が大きい）

使い方:
  python src/fetchers/fed_press_releases.py
"""

import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests

FEED_URL = "https://www.federalreserve.gov/feeds/press_monetary.xml"
HEADERS = {"User-Agent": "Mozilla/5.0"}

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = PROJECT_ROOT / "output" / "fed_press_release_state.json"


def fetch_items(limit=10):
    resp = requests.get(FEED_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    items = []
    for item in root.findall("./channel/item")[:limit]:
        items.append({
            "title": item.findtext("title", default=""),
            "link": item.findtext("link", default="").strip(),
            "pub_date": item.findtext("pubDate", default=""),
            "category": item.findtext("category", default=""),
        })
    return items


def load_state():
    if STATE_PATH.exists():
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def detect_new_items(items):
    """
    前回実行時に見た最新のlinkと比較し、新着（前回以降に追加された）発表文だけを返す。
    初回実行時は「新着」の判定ができないため空リストを返す（次回実行以降、正しく機能する）。
    """
    state = load_state()
    last_seen_link = state.get("last_seen_link")

    new_items = []
    for item in items:
        if item["link"] == last_seen_link:
            break
        new_items.append(item)

    if items:
        state["last_seen_link"] = items[0]["link"]
        state["last_checked_at"] = datetime.now(timezone.utc).isoformat()
        save_state(state)

    if last_seen_link is None:
        return []  # 初回実行（比較対象なし）
    return new_items


def save_result(items, new_items, path=None):
    if not path:
        out_dir = PROJECT_ROOT / "output"
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f"fed_press_releases_{datetime.now().strftime('%Y%m%d')}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"items": items, "new_items": new_items}, f, ensure_ascii=False, indent=2)
    return path


def run(verbose=True):
    items = fetch_items()
    new_items = detect_new_items(items)

    if verbose:
        print(f"直近の発表（最大10件）:")
        for item in items:
            flag = "【新着】" if item in new_items else ""
            print(f"{flag}- {item['title']}（{item['pub_date']}）")
        if new_items:
            print(f"\n前回チェック以降の新着: {len(new_items)}件")
        else:
            print("\n新着なし")

    out_path = save_result(items, new_items)
    if verbose:
        print(f"\n結果を保存しました: {out_path}")
    return items, new_items, out_path


if __name__ == "__main__":
    run()
