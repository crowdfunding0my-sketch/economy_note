"""
BLS（米労働省統計局）APIを使った指標発表日の速報値取得。

役割: 指標発表当日に一次情報として速報値を吸い上げる（FREDは二次集約元で反映に
ラグがあるため、発表当日はBLSを直接参照する）。平常時の日次収集は fred_indicators.py を使う。

【重要】BLS API v2は登録キーをクエリパラメータのGETで渡すと機能しないことがあり、
本実装はPOST（JSON body）でリクエストする（実機で確認済み）。

取得する系列（FREDの系列と対応）:
- CUSR0000SA0：CPI 全品目・季節調整済み（FREDのCPIAUCSLに対応）
- LNS14000000：失業率・季節調整済み（FREDのUNRATEに対応）
- CES0000000001：非農業部門雇用者数・季節調整済み（FREDのPAYEMSに対応）

【要確認事項】
BLSには「予想値（コンセンサス予想）」は無く実績値のみ。予想値との比較を行う場合は
Trading Economics等の別ソースが必要（TRADINGECONOMICS_API_KEY未設定のため未実装）。

【指標発表日の判定について】
BLS公式サイトの発表スケジュールページ・iCalフィード（bls.gov/schedule/news_release/）は
Akamaiのボット対策で自動アクセスが拒否される（実機で403を確認済み）。BLSの利用規約上も
自動取得プログラムの利用は禁止されているため、スクレイピングでの回避は行わない。

代わりに、正式なBLS Data APIを使い「前回実行時から最新期間(latest_period)が更新されたか」を
比較する方式で発表日を検知する（output/bls_release_state.json に前回値を保存）。
これなら発表日カレンダーを別途保持・更新する必要がなく、規約にも抵触しない。

使い方:
  1. .env に BLS_API_KEY をセット
  2. python src/fetchers/bls_indicators.py
"""

import os
import json
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

BLS_API_KEY = os.environ.get("BLS_API_KEY", "")
BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RELEASE_STATE_PATH = PROJECT_ROOT / "output" / "bls_release_state.json"

SERIES = [
    ("CUSR0000SA0", "CPI（総合・季節調整済み）"),
    ("LNS14000000", "失業率（季節調整済み）"),
    ("CES0000000001", "非農業部門雇用者数（季節調整済み）"),
]


def fetch_series(series_ids, start_year=None, end_year=None):
    """複数系列をまとめて取得（BLS APIは1リクエストで最大50系列まで対応）"""
    payload = {
        "seriesid": series_ids,
        "registrationkey": BLS_API_KEY,
    }
    if start_year and end_year:
        payload["startyear"] = str(start_year)
        payload["endyear"] = str(end_year)

    resp = requests.post(
        BLS_URL,
        data=json.dumps(payload),
        headers={"Content-type": "application/json"},
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS API エラー: {body.get('message')}")
    return body["Results"]["series"]


def summarize(series):
    """各系列の最新値・前期値・前期比を計算（footnoteでデータ欠測の期間はスキップ）"""
    data_points = [d for d in series["data"] if d.get("value") not in (None, "-")]
    if len(data_points) < 2:
        return None
    latest, prev = data_points[0], data_points[1]
    latest_val = float(latest["value"])
    prev_val = float(prev["value"])
    return {
        "latest_period": f"{latest['year']}-{latest['periodName']}",
        "latest_value": latest_val,
        "prev_period": f"{prev['year']}-{prev['periodName']}",
        "prev_value": prev_val,
        "change_rate": round((latest_val - prev_val) / prev_val, 5),
        "forecast_value": None,  # Trading Economics等の予想値ソース未実装
    }


def fetch_all():
    series_ids = [s[0] for s in SERIES]
    labels = dict(SERIES)
    fetched = fetch_series(series_ids)
    result = {}
    for series in fetched:
        sid = series["seriesID"]
        result[sid] = {"label": labels.get(sid, sid), "summary": summarize(series)}
    return result


def load_release_state():
    if RELEASE_STATE_PATH.exists():
        with open(RELEASE_STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_release_state(state):
    with open(RELEASE_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def detect_releases(result):
    """
    前回実行時に記録したlatest_periodと比較し、新しい期間のデータが増えていれば
    「本日（このスクリプト実行時点で）新規発表があった」とみなす。
    戻り値: {series_id: bool}（Trueなら新規発表あり）。実行後は状態を更新して保存する。
    """
    state = load_release_state()
    releases = {}
    for sid, data in result.items():
        summary = data.get("summary")
        if not summary:
            continue
        latest_period = summary["latest_period"]
        previously_seen = state.get(sid)
        releases[sid] = previously_seen is not None and previously_seen != latest_period
        state[sid] = latest_period
    save_release_state(state)
    return releases


def save_result(result, path=None):
    if not path:
        out_dir = PROJECT_ROOT / "output"
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f"bls_indicators_{datetime.now().strftime('%Y%m%d')}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return path


def run(verbose=True):
    if not BLS_API_KEY:
        raise SystemExit(".env に BLS_API_KEY を設定してください")

    had_prior_state = RELEASE_STATE_PATH.exists()
    result = fetch_all()
    releases = detect_releases(result)
    for sid in result:
        result[sid]["is_new_release"] = releases.get(sid, False)  # article_builder等が参照できるよう保存結果に含める
    if verbose:
        for sid, data in result.items():
            flag = "【本日発表】" if releases.get(sid) else ""
            print(f"{flag}[{sid}] {data['label']}: {data['summary']}")

    out_path = save_result(result)
    if verbose:
        print(f"\n結果を保存しました: {out_path}")
        if not had_prior_state:
            print("※今回が初回実行のため、比較対象がなく「本日発表」判定は出ていません（次回実行以降、正しく機能します）")
        elif not any(releases.values()):
            print("新規発表なし（前回実行時と同じ期間のデータ）")
    return result, releases, out_path


if __name__ == "__main__":
    run()
