"""
output/配下に溜まり続ける記事下書き・サムネイル・テーブル画像・米国株ピック下書きを、
直近RETENTION_DAYS日分だけ残してoutput/old/に移動する。

【削除ではなく移動にしている理由】
note.comへの実際の投稿は今のところ手動（コピー＆ペースト）で行っており、システム側は
「どの記事がすでに投稿済みか」を把握できない。もし記事生成が投稿作業より先行した場合
（実際、Task Schedulerが数日起動しなかったケースがあった）、削除だと未投稿の記事を
誤って失ってしまう可能性がある。移動であれば見た目はすっきりする一方、必要なら
output/old/から手動で取り出せる。

【曜日を限定せず毎回チェックする理由】
「週の始めに」という運用イメージだったが、特定の曜日（例：月曜）にしか整理しない実装だと、
その曜日の自動実行自体が抜けた場合（このプロジェクトで実際に複数回発生）に整理も
止まってしまう。ファイル名の日付を見て「7日より古いか」を毎回チェックするだけの
シンプルな処理にすることで、実行タイミングに依存せず、結果的に常に直近1週間分だけが
output/直下に残る状態を維持できる。

使い方:
  python src/formatters/archive_old_files.py
"""

import re
import shutil
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "output"
OLD_DIR = OUTPUT_DIR / "old"

RETENTION_DAYS = 7

# (OUTPUT_DIR基準のglobパターン, ファイル名から日付を取り出す正規表現, output/old/配下のサブフォルダ名)
TARGETS = [
    ("article_draft_*.md", r"article_draft_(\d{8})\.md", None),
    ("thumbnails/*.png", r"thumbnail_(\d{8})\.png", "thumbnails"),
    ("tables/*.png", r"indicators_table_(\d{8})\.png", "tables"),
    ("drafts/us_pick_*.md", r"us_pick_(\d{8})\.md", "drafts"),
]


def archive_old_files(today=None, verbose=True):
    today = today or date.today()
    cutoff = today - timedelta(days=RETENTION_DAYS)
    moved = 0
    for pattern, date_re, subdir in TARGETS:
        for path in OUTPUT_DIR.glob(pattern):
            m = re.search(date_re, path.name)
            if not m:
                continue
            try:
                file_date = date(int(m.group(1)[:4]), int(m.group(1)[4:6]), int(m.group(1)[6:8]))
            except ValueError:
                continue
            if file_date >= cutoff:
                continue

            dest_dir = (OLD_DIR / subdir) if subdir else OLD_DIR
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / path.name
            shutil.move(str(path), str(dest))
            moved += 1
            if verbose:
                print(f"  移動: {path.relative_to(OUTPUT_DIR)} → old/{dest.relative_to(OLD_DIR)}")

    if verbose:
        print(f"古いファイルの整理: {moved}件をoutput/old/へ移動しました"
              f"（{RETENTION_DAYS}日より古いもの、{cutoff}より前）")
    return moved


def run(verbose=True):
    archive_old_files(verbose=verbose)


if __name__ == "__main__":
    run()
