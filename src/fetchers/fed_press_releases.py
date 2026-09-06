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
- speeches.xml：FRB高官の講演。ジャクソンホール会議での議長講演等もこちら経由で公開される
  （press_monetary.xmlには載らない）。ただしFOMCメンバー全員分だと週数件ペースで発表される
  ノイズの多いフィードのため、**議長の講演のみ**に絞って取り込む（2026-08-31、ユーザーと合意）。
  議長交代時はFED_CHAIR_LASTNAMEを更新する必要がある。

使い方:
  python src/fetchers/fed_press_releases.py
"""

import sys
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests

# FRBの発表タイトルにはWindowsのコンソール既定コードページ(cp932)で表現できない記号
# （enダッシュ等）が含まれることがあり、print()がそのままだと落ちる。UTF-8に強制する。
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MONETARY_FEED_URL = "https://www.federalreserve.gov/feeds/press_monetary.xml"
SPEECHES_FEED_URL = "https://www.federalreserve.gov/feeds/speeches.xml"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# speeches.xmlのタイトルは "{姓}, {講演タイトル}" の形式。議長交代時はここを更新する。
FED_CHAIR_LASTNAME = "Warsh"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = PROJECT_ROOT / "output" / "fed_press_release_state.json"


def fetch_items(feed_url, limit=10):
    resp = requests.get(feed_url, headers=HEADERS, timeout=20)
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


def fetch_chair_speeches(limit=10):
    """speeches.xmlから議長（FED_CHAIR_LASTNAME）の講演のみを抽出する"""
    items = fetch_items(SPEECHES_FEED_URL, limit=limit * 3)  # 議長以外も混ざるため多めに取得して絞り込む
    prefix = f"{FED_CHAIR_LASTNAME}, "
    return [item for item in items if item["title"].startswith(prefix)][:limit]


def load_state():
    if STATE_PATH.exists():
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def detect_new_items(items, state, state_key):
    """
    前回実行時に見た最新のlinkと比較し、新着（前回以降に追加された）発表文だけを返す。
    初回実行時は「新着」の判定ができないため空リストを返す（次回実行以降、正しく機能する）。
    state辞書はmonetary/speechesで共通のファイルを使うため、state_keyで項目を分けて記録する。
    """
    last_seen_link = state.get(state_key)

    new_items = []
    for item in items:
        if item["link"] == last_seen_link:
            break
        new_items.append(item)

    if items:
        state[state_key] = items[0]["link"]

    if last_seen_link is None:
        return []  # 初回実行（比較対象なし）
    return new_items


def save_result(items, new_items, chair_speeches, new_chair_speeches, path=None):
    if not path:
        out_dir = PROJECT_ROOT / "output"
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f"fed_press_releases_{datetime.now().strftime('%Y%m%d')}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "items": items,
            "new_items": new_items,
            "chair_speeches": chair_speeches,
            "new_chair_speeches": new_chair_speeches,
        }, f, ensure_ascii=False, indent=2)
    return path


def run(verbose=True):
    items = fetch_items(MONETARY_FEED_URL)
    chair_speeches = fetch_chair_speeches()

    state = load_state()
    new_items = detect_new_items(items, state, "last_seen_link")
    new_chair_speeches = detect_new_items(chair_speeches, state, "last_seen_speech_link")
    state["last_checked_at"] = datetime.now(timezone.utc).isoformat()
    save_state(state)

    if verbose:
        print(f"直近の金融政策発表（最大10件）:")
        for item in items:
            flag = "【新着】" if item in new_items else ""
            print(f"{flag}- {item['title']}（{item['pub_date']}）")
        print(f"\n議長（{FED_CHAIR_LASTNAME}）の直近の講演:")
        for item in chair_speeches:
            flag = "【新着】" if item in new_chair_speeches else ""
            print(f"{flag}- {item['title']}（{item['pub_date']}）")
        print(f"\n新着: 金融政策発表{len(new_items)}件 / 議長講演{len(new_chair_speeches)}件")

    out_path = save_result(items, new_items, chair_speeches, new_chair_speeches)
    if verbose:
        print(f"\n結果を保存しました: {out_path}")
    return items, new_items, out_path


if __name__ == "__main__":
    run()
