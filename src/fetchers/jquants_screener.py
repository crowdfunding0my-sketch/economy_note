"""
J-Quants API V2 銘柄スクリーニングスクリプト（無料エリア「小型株ピックアップ」向け）

条件（すべてAND）:
  1. 時価総額が SMALL_CAP_MAX_MKTCAP 以下（小型株）
  2. 直近期の売上高が前期より増加
  3. PERが MAX_PER 以下（黒字であること含む）
  4. 過去2年程度の株価が右肩上がり（回帰直線の傾き>0）かつ一貫性がある（決定係数R²が閾値以上）
上記を満たす銘柄を、2年間の株価上昇率が高い順に並べて全件CSVに保存する
（旧: 上位15銘柄のみ保存だったが、母数が大きくなったため2026-09-08にTOP_N絞り込みを撤廃。
article_builder.py側で日替わりローテーション表示する）。

【2026-09-06/07 方針転換】当初は「PER15倍以下」（割安）と「売上高・営業利益が3期連続増加」
という、より厳しい条件だったが、米国株の有料エリア（1-3.参照）と同じく、割安×小型×連続増益×
上昇トレンドを同時に満たす銘柄はほぼ存在しないことが確認できた（2169銘柄中0件）。
そこでまずPER・黒字条件を撤廃したが、それでも0件だったため、増収の判定も「3期連続」から
「直近1期のみ」に緩和し、米国株の有料エリアと完全に同じ基準（直近1期の売上高成長のみ、
営業利益・複数期連続は問わない）に統一した。

【2026-09-08 PER条件の復活】上記の緩和後（PER不問）で全銘柄走査したところ580件ヒットし、
そのうち396件がPER15倍以下だった（十分な母数）。そこで「割安さ」の参考情報だったPER条件を
判定条件として復活させた（小型株＋直近期売上高成長＋PER15倍以下＋株価トレンド上昇）。
これにより一時的な緩和策（PER不問の成長モメンタム方式）から、より本来の「割安成長株」
スクリーニングに戻している。

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

【進捗の可視化・チェックポイント再開（2026-09-01追加）】
全銘柄走査に14.5時間程度かかる想定だったが、実機では24時間経っても完走しないことがあり、
かつ完走するまで結果を一切保存しない作りだったため、「本当に進んでいるのか」を外から確認できず、
途中で止めると最初からやり直しになる問題があった。対応として:
- `output/jquants_screening_progress.json`に処理件数・直近コード・推定残り時間を随時書き出す
  （`cat`等でいつでも進捗確認できる）。
- `output/jquants_screening_checkpoint.json`に処理済みコードと途中経過のヒットを随時保存し、
  中断後の再実行時はそこから再開する（銘柄一覧の再取得はしない＝APIリクエストを無駄にしない）。
  正常完走した場合はチェックポイントを削除し、次回は新規スキャンとして開始する。
"""

import os
import sys
import json
import time
import csv
import traceback
import requests
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from trend_utils import linear_trend

load_dotenv()

# Windowsのコンソール既定コードページ(cp932)だと銘柄名等の一部文字でprint()が落ちることがあるため、
# UTF-8に強制する（fed_press_releases.pyと同じ対策）。タスクスケジューラ経由の非対話実行では
# stdoutがreconfigure()に対応しない環境になりうるため、失敗してもスクリプト全体を落とさないよう
# try/exceptで囲む（2026-09-06追加：この行が原因かは未確認だが、原因不明のままクラッシュする
# 不具合が実機で発生したため、疑わしい箇所は防御的にしておく）。
try:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ERROR_LOG_PATH = PROJECT_ROOT / "output" / "jquants_screening_error.log"


def _log_fatal_error():
    """タスクスケジューラ経由の実行はstdoutがどこにも残らないため、致命的なエラーが起きた場合は
    トレースバックをファイルに書き残す（2026-09-06追加：原因不明のクラッシュが実機で発生し、
    調査しようにも手がかりが全く残っていなかったため）。"""
    ERROR_LOG_PATH.parent.mkdir(exist_ok=True)
    with open(ERROR_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"\n=== {datetime.now().isoformat(timespec='seconds')} ===\n")
        f.write(traceback.format_exc())

BASE_URL = "https://api.jquants.com"
API_KEY = os.environ.get("JQUANTS_API_KEY", "")

HEADERS = {"x-api-key": API_KEY}

# スクリーニング条件
# CONSECUTIVE_GROWTH_YEARS（旧: 3期連続増収増益）は2026-09-07に撤廃。米国株の有料エリアと
# 同じ基準（直近1期の売上高成長のみ、営業利益・複数期連続は問わない）に統一した（has_revenue_growth参照）。
# MAX_PER は2026-09-06にいったん撤廃したが、580件ヒットのうち396件がPER15倍以下で
# 十分な母数を確保できることを確認できたため、2026-09-08に「割安さの参考情報」ではなく
# 判定条件として復活させた（小型株＋直近期売上高成長＋株価トレンド上昇＋PER15倍以下）。
SMALL_CAP_MAX_MKTCAP = 50_000  # 時価総額の上限（百万円単位。J-QuantsのMktCapと同じ単位＝500億円）
TREND_MIN_R2 = 0.3  # 株価トレンドの「一貫性」とみなす決定係数R²の下限（要調整の目安値）
MIN_PRICE_POINTS = 200  # トレンド判定に必要な最低営業日数（目安：約10ヶ月分）
MAX_PER = 15.0  # PERの上限（黒字かつ割安とみなす基準）
# TOP_N（旧: 上位15銘柄のみCSV保存）は2026-09-08に撤廃。PER条件込みで数百件規模の
# 母集団を確保できるようになったため、条件に合致した銘柄は全件CSVに保存し、
# article_builder.py側で日替わりローテーション表示する運用に変更した。

# Freeプラン=5req/分が前提。上位プランに変更したら環境変数で上書きする。
REQUESTS_PER_MINUTE = int(os.environ.get("JQUANTS_REQUESTS_PER_MINUTE", "5"))
REQUEST_INTERVAL_SEC = 60.0 / REQUESTS_PER_MINUTE

# 小型株の比率が高い市場区分（プライムは大型株中心のため対象外）
TARGET_MARKETS = ["グロース", "スタンダード"]

OUTPUT_DIR = PROJECT_ROOT / "output"
CHECKPOINT_PATH = OUTPUT_DIR / "jquants_screening_checkpoint.json"
PROGRESS_PATH = OUTPUT_DIR / "jquants_screening_progress.json"
PROGRESS_UPDATE_EVERY = 5  # この件数ごとに進捗ファイルを更新（頻繁すぎるディスクI/Oを避ける）


# ページネーションの安全上限（2026-09-06追加）。実機で、特定銘柄の処理中に
# pagination_keyが終了せず（同じキーが返り続ける、または際限なく新しいキーが返り続ける）
# 事実上の無限ループに陥り、14.5時間の全銘柄走査が最初の数銘柄から一歩も進まない不具合が
# 複数回発生した。原因はAPI側の挙動まで特定できていないが、原因究明よりも「1銘柄が
# どんな事情でも異常に長時間かからないようにする」ことを優先し、ページ数に上限を設けた。
MAX_PAGINATION_PAGES = 30
REQUEST_TIMEOUT_SEC = 30  # ネットワーク側がハングした場合に無限に待たないためのタイムアウト


def _get(url, params=None):
    resp = requests.get(url, headers=HEADERS, params=params, timeout=REQUEST_TIMEOUT_SEC)
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


def _paginated_get(url, code, verbose=True):
    """
    codeをキーにページネーションしながら全件取得する共通処理。
    MAX_PAGINATION_PAGESに達しても終わらない場合は、そこで打ち切って
    それまでに取得できた分だけを返す（1銘柄のせいで全体が止まるのを防ぐ）。
    """
    params = {"code": code}
    results = []
    pagination_key = None
    for page in range(MAX_PAGINATION_PAGES):
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
    else:
        if verbose:
            print(f"[WARN] {code}: {url} が{MAX_PAGINATION_PAGES}ページ経っても終了しなかったため打ち切りました")
    return results


def get_financial_summary(code):
    """指定銘柄の財務情報サマリー(四半期・通期含む)を取得し、開示日昇順で返す"""
    results = _paginated_get(f"{BASE_URL}/v2/fins/summary", code)
    results.sort(key=lambda d: d.get("DiscDate", ""))
    return results


def get_price_history(code):
    """
    株価の日次履歴を取得（Freeプランでは直近12週間〜過去2年12週間分のみ、
    すなわち実質「過去2年弱の値動き」が取れる。この制約が今回の
    「過去2年の株価推移で判定する」という設計とちょうど噛み合う）。
    日付昇順で返す。
    """
    results = _paginated_get(f"{BASE_URL}/v2/equities/bars/daily", code)
    results.sort(key=lambda d: d.get("Date", ""))
    return results


def annual_records(financials):
    """
    通期(FY)決算のレコードだけを抽出。判定に使うのは売上高(Sales)のみのため、
    OPの有無は絞り込み条件にしない（2026-09-07、営業利益の連続増加要件を撤廃したのに合わせて変更。
    以前はOPが無いレコードを丸ごと除外していたが、Salesさえあれば判定に使えるため不要な除外だった）。
    """
    return [
        r for r in financials
        if r.get("CurPerType") == "FY"
        and r.get("Sales") not in (None, "")
    ]


def has_revenue_growth(fy_records):
    """
    直近期の売上高が前期より増加しているか判定（2026-09-07変更）。
    従来は「3期連続で売上高・営業利益がともに増加」という厳しい条件だったが、
    PER・黒字条件を撤廃した後も全2,169銘柄でヒット0件だったため、米国株の
    有料エリア（1-3参照）と完全に同じ基準に統一した：直近1期の売上高成長のみを見る
    （営業利益の連続増加・複数期の連続性は問わない）。
    """
    if len(fy_records) < 2:
        return False
    prev, curr = fy_records[-2], fy_records[-1]
    try:
        return float(curr["Sales"]) > float(prev["Sales"])
    except (TypeError, ValueError):
        return False


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


def load_checkpoint():
    if CHECKPOINT_PATH.exists():
        with open(CHECKPOINT_PATH, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_checkpoint(codes, next_index, candidates, started_at):
    OUTPUT_DIR.mkdir(exist_ok=True)
    with open(CHECKPOINT_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "codes": codes,
            "next_index": next_index,
            "candidates": candidates,
            "started_at": started_at,
        }, f, ensure_ascii=False)


def clear_checkpoint():
    if CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()


def save_progress(processed, total, hits_so_far, last_code, started_at, session_processed=None):
    """
    session_processed: 今回の実行セッションで実際に処理した件数（再開時はcheckpointの
    続きから数える）。ペース計算に絶対値のprocessedを使うと、中断・再開直後は
    「elapsed(このセッションの経過時間)÷processed(全体の累計処理数)」という
    不整合な割り算になり、残り時間が実態よりずっと短く表示される不具合があった
    （2026-09-06発見・修正）。session_processedが渡されなければ従来通りprocessedを使う。
    """
    elapsed = time.monotonic() - started_at
    remaining = total - processed
    pace_base = session_processed if session_processed else processed
    eta_sec = remaining * (elapsed / pace_base) if pace_base else None
    OUTPUT_DIR.mkdir(exist_ok=True)
    with open(PROGRESS_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "processed": processed,
            "total": total,
            "hits_so_far": hits_so_far,
            "last_code": last_code,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "elapsed_minutes": round(elapsed / 60, 1),
            "estimated_remaining_minutes": round(eta_sec / 60, 1) if eta_sec is not None else None,
        }, f, ensure_ascii=False, indent=2)


def _evaluate_code(code):
    """
    1銘柄分の判定処理。条件を満たさなければNoneを返す（screen()側のループでは
    continueを使わず、必ずreturnで抜けるようにしている。これは2026-09-06に見つかった
    重大な不具合の修正のため：以前はこの判定を screen() のforループ本体に直接書いており、
    条件不一致による`continue`が、その下にあるチェックポイント保存処理まで丸ごと
    読み飛ばしてしまっていた。増収増益・PER・トレンドの条件は非常に厳しく、
    ほぼ全銘柄が`continue`する（＝ヒットする銘柄の方が稀）ため、結果として
    チェックポイントが実質一度も保存されない状態になっていた。全銘柄走査が
    14.5時間×複数回、一度も完走せず、かつ再開もできないまま無駄になっていた
    原因はこれだった。判定ロジックを別関数に分離し、returnで抜けても呼び出し元の
    ループ（チェックポイント保存を含む）には影響しない構造にした。
    """
    financials = get_financial_summary(code)
    fy = annual_records(financials)

    if not has_revenue_growth(fy):
        return None
    revenue_growth_rate = round(float(fy[-1]["Sales"]) / float(fy[-2]["Sales"]) - 1, 4)

    eps = latest_eps(fy)

    history = get_price_history(code)
    if len(history) < MIN_PRICE_POINTS:
        return None

    latest = history[-1]
    price = latest.get("C")
    mktcap = latest.get("MktCap")
    if not price or not mktcap:
        return None

    if mktcap > SMALL_CAP_MAX_MKTCAP:
        return None

    per = round(price / eps, 2) if eps else None
    # 2026-09-08: PER条件を復活。赤字（eps<=0によりper=None）・PER15倍超は除外する
    if per is None or per <= 0 or per > MAX_PER:
        return None

    closes = [h["C"] for h in history if h.get("C") is not None]
    slope, r_squared = linear_trend(closes)
    if slope <= 0 or r_squared < TREND_MIN_R2:
        return None

    first_price = closes[0]
    price_change_rate = (price - first_price) / first_price

    return {
        "code": code,
        "price": price,
        "eps": round(eps, 2) if eps is not None else None,
        "per": per,
        "mktcap_million_yen": mktcap,
        "trend_slope": round(slope, 4),
        "trend_r2": round(r_squared, 3),
        "price_change_rate": round(price_change_rate, 4),
        "period_start": history[0].get("Date"),
        "period_end": latest.get("Date"),
        "revenue_growth_rate": revenue_growth_rate,
    }


def screen(codes, start_index=0, candidates=None, overall_started_at=None, verbose=True):
    """
    スクリーニングを実行し、条件（小型株・増収増益・PER15以下・株価が右肩上がり）を
    すべて満たす銘柄を、2年間の株価上昇率が高い順に並べて返す（TOP_N絞り込みは呼び出し側で行う）。

    途中中断からの再開に対応するため、start_index（前回処理済みの続きから開始する位置）と
    candidates（前回までに見つかった全ヒット、TOP_N絞り込み前）を受け取れるようにしている。
    overall_started_at はチェックポイントに記録する「初回開始時刻」（再開しても変わらない、
    人が読むための記録用）。ETA計算にはこの関数呼び出し時点からの実測ペース（time.monotonic）を使う
    （中断・再開をまたぐと計測が途切れるため、直近のペースの方が実態に近いと判断）。
    処理中は一定件数ごとにチェックポイント・進捗ファイルを更新する。
    """
    candidates = list(candidates) if candidates else []
    overall_started_at = overall_started_at or datetime.now().isoformat(timespec="seconds")
    session_started_at = time.monotonic()
    total = len(codes)

    for i in range(start_index, total):
        code = codes[i]
        if verbose and i % PROGRESS_UPDATE_EVERY == 0:
            # 銘柄ごとの処理に想定外に時間がかかった場合、どの銘柄で発生したか特定できるよう、
            # 開始時点でも(完了を待たず)出力する(2026-09-06追加、print後は即flushする)
            print(f"  -> 処理開始: {code} ({i + 1}/{total})", flush=True)
        try:
            hit = _evaluate_code(code)
            if hit:
                candidates.append(hit)
                if verbose:
                    print(f"[HIT] {code}  PER={hit['per']:.1f}  price={hit['price']}  "
                          f"上昇率={hit['price_change_rate']:.1%}  R2={hit['trend_r2']:.2f}")
        except Exception as e:
            if verbose:
                print(f"[ERROR] {code}: {e}")

        # 上のtry/exceptでcontinueを一切使っていないため、この後の処理は
        # 銘柄の判定結果に関わらず必ず実行される（2026-09-06の不具合修正の要点）
        processed = i + 1
        if processed % PROGRESS_UPDATE_EVERY == 0 or processed == total:
            save_checkpoint(codes, processed, candidates, overall_started_at)
            save_progress(processed, total, len(candidates), code, session_started_at,
                          session_processed=processed - start_index)
            if verbose:
                print(f"...{processed}/{total} 銘柄処理済み（ヒット{len(candidates)}件）", flush=True)

    candidates.sort(key=lambda h: h["price_change_rate"], reverse=True)
    return candidates


def save_results(hits, path=None):
    if not path:
        out_dir = PROJECT_ROOT / "output"
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f"screening_result_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    fieldnames = [
        "code", "price", "eps", "per", "mktcap_million_yen",
        "trend_slope", "trend_r2", "price_change_rate",
        "period_start", "period_end", "revenue_growth_rate",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(hits)
    return path


def _main():
    if not API_KEY:
        raise SystemExit(".env に JQUANTS_API_KEY を設定してください")

    checkpoint = load_checkpoint()
    if checkpoint:
        codes = checkpoint["codes"]
        start_index = checkpoint["next_index"]
        candidates_so_far = checkpoint["candidates"]
        overall_started_at = checkpoint["started_at"]
        print(f"前回の中断分を検知: {start_index}/{len(codes)} 銘柄まで処理済み"
              f"（開始: {overall_started_at}）。続きから再開します。")
    else:
        equities = get_listed_equities(market_filters=TARGET_MARKETS)
        codes = [e["Code"] for e in equities]
        start_index = 0
        candidates_so_far = []
        overall_started_at = None
        print(f"対象銘柄数: {len(codes)}（{'/'.join(TARGET_MARKETS)}）")

    print(f"リクエスト間隔: {REQUEST_INTERVAL_SEC:.1f}秒（{REQUESTS_PER_MINUTE}req/分想定）")
    print(f"条件: 時価総額{SMALL_CAP_MAX_MKTCAP:,}百万円以下 / "
          f"直近期の売上高成長 / PER{MAX_PER}倍以下（黒字） / "
          f"株価トレンド右肩上がり(R2>={TREND_MIN_R2}) ※営業利益・複数期連続は不問（成長モメンタム方式）")
    print(f"進捗は{PROGRESS_PATH}で随時確認できます。")

    all_hits = screen(codes, start_index=start_index, candidates=candidates_so_far,
                       overall_started_at=overall_started_at)
    results = all_hits

    print(f"\n条件合致銘柄数（全件保存）: {len(results)}")
    out_path = save_results(results)
    print(f"結果を保存しました: {out_path}")

    clear_checkpoint()
    if PROGRESS_PATH.exists():
        PROGRESS_PATH.unlink()
    print("完走したためチェックポイント・進捗ファイルを削除しました。")


if __name__ == "__main__":
    try:
        _main()
    except Exception:
        _log_fatal_error()
        raise
