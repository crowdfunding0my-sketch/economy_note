"""
相場note 日次自動実行のエントリポイント。

【自動化できる範囲・できない範囲】
このスクリプトが担当するのは「完全自動化できる部分」のみ:
  1. FRED（経済指標の基本データ）
  2. BLS（指標発表日判定込みの速報値）
  3. FRB公式発表（一次情報。RSSフィードから新着を検知）
  4. 米国株ローテーション（Alpacaの直接APIで取得済みの候補15銘柄から本日の1銘柄を選び、
     Alpha Vantageで最新ニュースを取得）
  5. 上記を踏まえたnote記事下書きの組み立て
  6. サムネイル画像の生成（記事の内容が確定した後、本日の注目株の分野キーワードでPixabay検索）

以下は自動化の対象外（別途手動 or 別の仕組みが必要）:
  - 日本株の全銘柄スクリーニング（J-Quants Freeプランでは全銘柄走査に十数時間かかるため、
    本スクリプトには含めない。別途 src/fetchers/jquants_screener.py を単独実行する運用）
  - 米国株候補15銘柄の再選定（Stage C）：Woodstockの`get_fundamentals`はこのClaude環境
    経由でしか呼べないため、本スクリプトからは実行できない。週1〜2週に1回、Claude側で
    手動 or `/schedule`により再実行し、output/us_premium_rotation_candidates.json を
    更新する運用（詳細はCLAUDE.md「1-3.」参照）
  - noteへの実際の投稿：note.comに公式APIが無いため、生成された下書き（output/article_draft_*.md）
    を人がコピー＆ペーストして投稿する

使い方:
  python src/main.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src" / "fetchers"))
sys.path.insert(0, str(PROJECT_ROOT / "src" / "formatters"))

import fred_indicators
import bls_indicators
import fed_press_releases
import us_stock_rotation
import article_builder
import thumbnail_generator


def main():
    print("=== 1/6 FRED経済指標を取得 ===")
    fred_indicators.run()

    print("\n=== 2/6 BLS経済指標を取得（発表日判定込み） ===")
    bls_indicators.run()

    print("\n=== 3/6 FRB公式発表を取得（新着判定込み） ===")
    fed_press_releases.run()

    print("\n=== 4/6 本日の米国株ピックを選定・ニュース取得 ===")
    us_stock_rotation.run()

    print("\n=== 5/6 note記事下書きを組み立て ===")
    article = article_builder.build_article()
    out_dir = PROJECT_ROOT / "output"
    out_dir.mkdir(exist_ok=True)
    from datetime import datetime, timezone
    out_path = out_dir / f"article_draft_{datetime.now(timezone.utc).strftime('%Y%m%d')}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(article)
    print(f"記事下書きを保存しました: {out_path}")

    print("\n=== 6/6 サムネイル画像を生成 ===")
    thumbnail_generator.run()


if __name__ == "__main__":
    main()
