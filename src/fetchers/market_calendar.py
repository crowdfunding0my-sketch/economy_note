"""
日米市場の休場日判定。

用途：前日が日米ともに市場が休みだった日は、前営業日までの情報を焼き直すだけの
記事になってしまうため、main.pyの実行自体をスキップする（記事を作らない＝アップしない）。

判定方法:
- 米国市場：Alpaca証券取引カレンダーAPI（`/v2/calendar`）。NYSEの祝日・短縮取引日を
  直接反映した一次情報で、こちらで祝日リストを保守する必要がない。
- 日本市場：`jpholiday`ライブラリ（内閣府の祝日法に基づく計算、外部通信不要）で祝日判定し、
  土日と合わせて休日とする。東証の大納会・大発会（12/31, 1/2, 1/3）は祝日ではないが
  東証休場日のため、固定で追加している。

【スキップ判定は「当日」ではなく「前日」基準（2026-09-05変更）】
FREDの株価指数データには約1日の遅延があり、当日朝7時の記事は実質「前日(JST)に終値がついた
セッション」を報じる形になる。そのため:
- 土曜7時の記事は金曜の終値を報じるので生成する（金曜は取引日 → 前日が休場ではない）。
- 月曜7時の記事は、日曜が休場のため金曜と同じ終値しか無く、生成しても土曜の記事と内容が
  重複してしまうのでスキップする（前日＝日曜が休場）。
- 火曜7時の記事は月曜の終値を報じるので生成する（月曜は取引日）。
「前日が両市場とも休場だったか」で判定することで、この重複を避けつつ、休場明け直後の
「まだ新しい終値が無い日」だけを正しくスキップできる。

使い方:
  python src/fetchers/market_calendar.py
"""

import os
from datetime import date, timedelta

import jpholiday
import requests
from dotenv import load_dotenv

from http_utils import get_with_retry

load_dotenv()

ALPACA_TRADING_URL = "https://paper-api.alpaca.markets"
ALPACA_HEADERS = {
    "APCA-API-KEY-ID": os.environ.get("ALPACA_API_KEY_ID", ""),
    "APCA-API-SECRET-KEY": os.environ.get("ALPACA_SECRET_KEY", ""),
}

# 祝日ではないが東証が休場する日（大納会・大発会）。年をまたいでも変わらない固定日。
JP_EXTRA_MARKET_HOLIDAYS_MMDD = {(12, 31), (1, 2), (1, 3)}


def is_us_market_open(target_date):
    """指定日がNYSEの取引日かどうかをAlpacaの取引カレンダーAPIで判定する"""
    date_str = target_date.isoformat()
    resp = get_with_retry(
        f"{ALPACA_TRADING_URL}/v2/calendar",
        headers=ALPACA_HEADERS,
        params={"start": date_str, "end": date_str},
        timeout=20,
    )
    resp.raise_for_status()
    return len(resp.json()) > 0


def is_jp_market_open(target_date):
    """指定日が東証の取引日かどうかを判定する（土日・祝日・大納会/大発会を除く平日）"""
    if target_date.weekday() >= 5:  # 5=土曜, 6=日曜
        return False
    if (target_date.month, target_date.day) in JP_EXTRA_MARKET_HOLIDAYS_MMDD:
        return False
    if jpholiday.is_holiday(target_date):
        return False
    return True


def both_markets_closed(target_date=None):
    """日米どちらの市場も休みの日かどうかを判定する"""
    target_date = target_date or date.today()
    return not is_jp_market_open(target_date) and not is_us_market_open(target_date)


def should_skip_generation(target_date=None):
    """target_date（省略時は本日）の記事生成をスキップすべきかを判定する（main.py用）。
    「前日が日米とも休場だったか」で判定する（当日ではない）。理由はモジュールdocstring参照：
    FREDのデータ遅延により、当日の記事は実質「前日に終値がついたセッション」を報じるため、
    前日に新しい終値が無い（＝前日も休場だった）場合だけ、内容が重複するのでスキップする。"""
    target_date = target_date or date.today()
    day_before = target_date - timedelta(days=1)
    return both_markets_closed(day_before)


if __name__ == "__main__":
    today = date.today()
    jp_open = is_jp_market_open(today)
    us_open = is_us_market_open(today)
    print(f"{today}: 日本市場={'営業日' if jp_open else '休場'} / 米国市場={'営業日' if us_open else '休場'}")
    print(f"→ 日米ともに休場: {not jp_open and not us_open}")
    print(f"→ 記事生成をスキップすべきか（前日基準）: {should_skip_generation(today)}")
