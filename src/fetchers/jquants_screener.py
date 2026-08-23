"""
J-Quants API V2 銘柄スクリーニングスクリプト（有料エリア「小型株特化」向け）

条件（すべてAND）:
  1. 時価総額が SMALL_CAP_MAX_MKTCAP 以下（小型株）
  2. 売上高・営業利益が直近N期(デフォルト3期)連続増加
  3. PER 15倍以下
  4. 過去2年程度の株価が右肩上がり（回帰直線の傾き>0）かつ一貫性がある（決定係数R²が閾値以上）
上記を満たす銘柄の中から、2年間の株価上昇率が高い順に上位15銘柄を抽出する。

対象: グロース市場・スタンダード市場（小型株の比率が高いため。プライムは対象外）

前提:
- J-Quants API V2 に登録済みで、ダッシュボードで発行した API キーを持っていること
  （2025/12/22以降の登録者はV2のみ利用可。V1は2026/6/1に終了済み）
- pip install -r requirements.txt

【重要な制約（Freeプラン）】
- レート制限: 5リクエスト/分。この制限を超えると 429 が返る想定で、
  1リクエストあたり 60 / JQUANTS_REQUESTS_PER_MINUTE 秒のインターバルを空けている。
  Light以上のプランに上げた場合は環境変数 JQUANTS_REQUESTS_PER_MINUTE を
  プランのレート上限に合わせて変更すること（Light=60, Standard=120, Premium=500）。
- データ取得可能期間: 直近12週間〜過去2年12週間分のみ。
  つまり Free プランで取れる「最新株価」は実際には最大12週間（約3ヶ月）前のものになる。
  有料記事として「本日時点のPER」と案内する場合はこの遅延を明記するか、
  プランのアップグレードを検討すること。

使い方:
  1. .env に JQUANTS_API_KEY をセット
  2. python src/fetchers/jquants_screener.py
"""

import os
import time
import csv
import requests
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from trend_utils import linear_trend

load_dotenv()

BASE_URL = "https://api.jquants.com"
API_KEY = os.environ.get("JQUANTS_API_KEY", "")

HEADERS = {"x-api-key": API_KEY}

# スクリーニング条件
MAX_PER = 15.0
CONSECUTIVE_GROWTH_YEARS = 3  # 直近何期分の増収増益を要求するか
SMALL_CAP_MAX_MKTCAP = 50_000  # 時価総額の上限（百万円単位。J-QuantsのMktCapと同じ単位＝500億円）
TREND_MIN_R2 = 0.3  # 株価トレンドの「一貫性」とみなす決定係数R²の下限（要調整の目安値）
MIN_PRICE_POINTS = 200  # トレンド判定に必要な最低営業日数（目安：約10ヶ月分）
TOP_N = 15  # 最終的に記事に載せる銘柄数

# Freeプラン=5req/分が前提。上位プランに変更したら環境変数で上書きする。
REQUESTS_PER_MINUTE = int(os.environ.get("JQUANTS_REQUESTS_PER_MINUTE", "5"))
REQUEST_INTERVAL_SEC = 60.0 / REQUESTS_PER_MINUTE

# 小型株の比率が高い市場区分（プライムは大型株中心のため対象外）
TARGET_MARKETS = ["グロース", "スタンダード"]

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _get(url, params=None):
    resp = requests.get(url, headers=HEADERS, params=params)
    time.sleep(REQUEST_INTERVAL_SEC)
    return resp


def get_listed_equities(market_filters=None):
    """上場銘柄一覧を取得。market_filters例: ['グロース', 'スタンダード']"""
    url = f"{BASE_URL}/v2/equities/master"
    resp = _get(url)
    resp.raise_for_status()
    data = resp.json().get("data", [])
    if market_filters:
        data = [
            d for d in data
            if any(m in d.get("MktNm", "") for m in market_filters)
        ]
    return data


def get_financial_summary(code):
    """指定銘柄の財務情報サマリー(四半期・通期含む)を取得し、開示日昇順で返す"""
    url = f"{BASE_URL}/v2/fins/summary"
    params = {"code": code}
    results = []
    pagination_key = None
    while True:
        if pagination_key:
            params["pagination_key"] = pagination_key
        resp = _get(url, params)
        if resp.status_code != 200:
            break
        body = resp.json()
        results.extend(body.get("data", []))
        pagination_key = body.get("pagination_key")
        if not pagination_key:
            break
    results.sort(key=lambda d: d.get("DiscDate", ""))
    return results


def get_price_history(code):
    """
    株価の日次履歴を取得（Freeプランでは直近12週間〜過去2年12週間分のみ、
    すなわち実質「過去2年弱の値動き」が取れる。この制約が今回の
    「過去2年の株価推移で判定する」という設計とちょうど噛み合う）。
    日付昇順で返す。
    """
    url = f"{BASE_URL}/v2/equities/bars/daily"
    results = []
    pagination_key = None
    params = {"code": code}
    while True:
        if pagination_key:
            params["pagination_key"] = pagination_key
        resp = _get(url, params)
        if resp.status_code != 200:
            break
        body = resp.json()
        results.extend(body.get("data", []))
        pagination_key = body.get("pagination_key")
        if not pagination_key:
            break
    results.sort(key=lambda d: d.get("Date", ""))
    return results


def annual_records(financials):
    """通期(FY)決算のレコードだけを抽出"""
    return [
        r for r in financials
        if r.get("CurPerType") == "FY"
        and r.get("Sales") not in (None, "")
        and r.get("OP") not in (None, "")
    ]


def is_consecutive_growth(fy_records, years=CONSECUTIVE_GROWTH_YEARS):
    """直近years期、売上高・営業利益が前期比で増加し続けているか判定"""
    if len(fy_records) < years + 1:
        return False
    recent = fy_records[-(years + 1):]
    for i in range(1, len(recent)):
        prev, curr = recent[i - 1], recent[i]
        try:
            sales_growth = float(curr["Sales"]) > float(prev["Sales"])
            op_growth = float(curr["OP"]) > float(prev["OP"])
        except (TypeError, ValueError):
            return False
        if not (sales_growth and op_growth):
            return False
    return True


def latest_eps(fy_records):
    """最新期のEPS(1株利益、当期純利益ベース)を取得"""
    for r in reversed(fy_records):
        eps = r.get("EPS")
        if eps not in (None, ""):
            try:
                return float(eps)
            except ValueError:
                continue
    return None


def screen(codes, verbose=True):
    """
    スクリーニングを実行し、条件（小型株・増収増益・PER15以下・株価が右肩上がり）を
    すべて満たす銘柄を、2年間の株価上昇率が高い順に並べて返す（上位TOP_N件）。
    """
    candidates = []
    for i, code in enumerate(codes):
        try:
            financials = get_financial_summary(code)
            fy = annual_records(financials)

            if not is_consecutive_growth(fy):
                continue

            eps = latest_eps(fy)
            if not eps or eps <= 0:
                continue

            history = get_price_history(code)
            if len(history) < MIN_PRICE_POINTS:
                continue

            latest = history[-1]
            price = latest.get("C")
            mktcap = latest.get("MktCap")
            if not price or not mktcap:
                continue

            if mktcap > SMALL_CAP_MAX_MKTCAP:
                continue

            per = price / eps
            if per > MAX_PER:
                continue

            closes = [h["C"] for h in history if h.get("C") is not None]
            slope, r_squared = linear_trend(closes)
            if slope <= 0 or r_squared < TREND_MIN_R2:
                continue

            first_price = closes[0]
            price_change_rate = (price - first_price) / first_price

            candidates.append({
                "code": code,
                "price": price,
                "eps": round(eps, 2),
                "per": round(per, 2),
                "mktcap_million_yen": mktcap,
                "trend_slope": round(slope, 4),
                "trend_r2": round(r_squared, 3),
                "price_change_rate": round(price_change_rate, 4),
                "period_start": history[0].get("Date"),
                "period_end": latest.get("Date"),
                "years_checked": CONSECUTIVE_GROWTH_YEARS,
            })
            if verbose:
                print(f"[HIT] {code}  PER={per:.1f}  price={price}  "
                      f"上昇率={price_change_rate:.1%}  R2={r_squared:.2f}")

        except Exception as e:
            if verbose:
                print(f"[ERROR] {code}: {e}")

        if verbose and (i + 1) % 50 == 0:
            print(f"...{i + 1}/{len(codes)} 銘柄処理済み")

    candidates.sort(key=lambda h: h["price_change_rate"], reverse=True)
    return candidates[:TOP_N]


def save_results(hits, path=None):
    if not path:
        out_dir = PROJECT_ROOT / "output"
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f"screening_result_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    fieldnames = [
        "code", "price", "eps", "per", "mktcap_million_yen",
        "trend_slope", "trend_r2", "price_change_rate",
        "period_start", "period_end", "years_checked",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(hits)
    return path


if __name__ == "__main__":
    if not API_KEY:
        raise SystemExit(".env に JQUANTS_API_KEY を設定してください")

    equities = get_listed_equities(market_filters=TARGET_MARKETS)
    codes = [e["Code"] for e in equities]

    print(f"対象銘柄数: {len(codes)}（{'/'.join(TARGET_MARKETS)}）")
    print(f"リクエスト間隔: {REQUEST_INTERVAL_SEC:.1f}秒（{REQUESTS_PER_MINUTE}req/分想定）")
    print(f"条件: 時価総額{SMALL_CAP_MAX_MKTCAP:,}百万円以下 / "
          f"増収営業増益{CONSECUTIVE_GROWTH_YEARS}期連続 / PER{MAX_PER}倍以下 / "
          f"株価トレンド右肩上がり(R2>={TREND_MIN_R2})")
    results = screen(codes)

    print(f"\n条件合致銘柄数（上位{TOP_N}件抽出後）: {len(results)}")
    out_path = save_results(results)
    print(f"結果を保存しました: {out_path}")
