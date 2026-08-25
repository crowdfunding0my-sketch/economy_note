"""
「経済指標トピック」を表として貼り付けられるよう、画像(PNG)として生成する。

note.comはMarkdownの表を貼り付けても表として表示されない仕様（プレーンテキストになる）
のため、Pillowで表そのものを画像として描画し、note投稿時にその画像を本文中に挿入する運用にする。

前提ファイル: output/fred_indicators_*.json（最新日付のもの、無ければ何も生成しない）

使い方:
  python src/formatters/indicators_table_image.py
"""

import json
import glob
from datetime import datetime, timezone
from pathlib import Path

import textwrap

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "output"

FONT_DIR = Path(r"C:\Windows\Fonts")
FONT_BOLD = FONT_DIR / "YuGothB.ttc"
FONT_MEDIUM = FONT_DIR / "YuGothM.ttc"

COLOR_BG = (255, 255, 255)
COLOR_HEADER_BG = (26, 33, 58)
COLOR_HEADER_TEXT = (255, 255, 255)
COLOR_ROW_ALT = (243, 245, 250)
COLOR_TEXT = (30, 34, 44)
COLOR_UP = (200, 30, 30)     # 赤（プラス、日本の相場報道の慣習）
COLOR_DOWN = (30, 90, 200)   # 青（マイナス）
COLOR_BORDER = (220, 223, 230)
COLOR_CAPTION = (120, 126, 145)

FRED_LABELS = {
    "CPIAUCSL": "CPI（総合）",
    "CPILFESL": "コアCPI",
    "UNRATE": "失業率",
    "PAYEMS": "非農業部門雇用者数",
    "GACDFSA066MSFRBPHI": "フィラデルフィア連銀指数*",
    "GACDISA066MSFRBNY": "NY連銀 Empire State指数*",
}
POINT_DIFF_SERIES = {"UNRATE", "GACDFSA066MSFRBPHI", "GACDISA066MSFRBNY"}


def _font(path, size):
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        return ImageFont.load_default()


def _latest_json(pattern):
    files = sorted(glob.glob(str(OUTPUT_DIR / pattern)))
    if not files:
        return None
    with open(files[-1], encoding="utf-8") as f:
        return json.load(f)


def _fmt_change(value, is_point_diff, unit="pt"):
    if value is None:
        return "—", COLOR_TEXT
    text = f"{value:+.1f}{unit}" if is_point_diff else f"{value*100:+.1f}%"
    color = COLOR_UP if value >= 0 else COLOR_DOWN
    return text, color


def build_rows(fred):
    rows = []
    for sid, label in FRED_LABELS.items():
        s = (fred.get(sid) or {}).get("summary")
        if not s:
            continue
        is_pt = sid in POINT_DIFF_SERIES
        mom_val = s["mom_diff"] if is_pt else s.get("mom_change_rate")
        yoy_val = s["yoy_diff"] if is_pt else s.get("yoy_change_rate")
        mom_text, mom_color = _fmt_change(mom_val, is_pt)
        yoy_text, yoy_color = _fmt_change(yoy_val, is_pt)
        rows.append({
            "label": f"{label}（{s['latest_date']}）",
            "value": f"{s['latest_value']:,.2f}",
            "mom_text": mom_text, "mom_color": mom_color,
            "yoy_text": yoy_text, "yoy_color": yoy_color,
        })
    return rows


def build_table_image(out_path=None):
    fred = _latest_json("fred_indicators_*.json")
    if not fred:
        return None
    rows = build_rows(fred)
    if not rows:
        return None

    col_widths = [740, 220, 220, 220]
    header = ["指標", "最新値", "前月比", "前年同月比"]
    row_h = 70
    header_h = 80
    pad = 30

    caption = ("出典：FRED（セントルイス連銀）。*正式なPMIは無料取得不可のため地区連銀の製造業"
               "サーベイを代替掲載（0が拡大/縮小の境目）。指標発表当日はBLSの速報値を優先。")
    font_caption = _font(FONT_MEDIUM, 20)
    caption_wrap_width = 60  # 半角換算のおおよその折り返し文字数
    caption_lines = textwrap.wrap(caption, width=caption_wrap_width)
    caption_line_h = 30
    caption_h = caption_line_h * len(caption_lines) + 20

    width = sum(col_widths) + pad * 2
    height = header_h + row_h * len(rows) + caption_h + pad * 2

    img = Image.new("RGB", (width, height), COLOR_BG)
    draw = ImageDraw.Draw(img)

    font_header = _font(FONT_BOLD, 30)
    font_cell = _font(FONT_MEDIUM, 28)
    font_caption = _font(FONT_MEDIUM, 20)

    y = pad
    x = pad
    draw.rectangle([x, y, x + sum(col_widths), y + header_h], fill=COLOR_HEADER_BG)
    cx = x
    for w, text in zip(col_widths, header):
        draw.text((cx + 20, y + header_h / 2), text, font=font_header, fill=COLOR_HEADER_TEXT, anchor="lm")
        cx += w
    y += header_h

    for i, row in enumerate(rows):
        if i % 2 == 1:
            draw.rectangle([x, y, x + sum(col_widths), y + row_h], fill=COLOR_ROW_ALT)
        cx = x
        draw.text((cx + 20, y + row_h / 2), row["label"], font=font_cell, fill=COLOR_TEXT, anchor="lm")
        cx += col_widths[0]
        draw.text((cx + 20, y + row_h / 2), row["value"], font=font_cell, fill=COLOR_TEXT, anchor="lm")
        cx += col_widths[1]
        draw.text((cx + 20, y + row_h / 2), row["mom_text"], font=font_cell, fill=row["mom_color"], anchor="lm")
        cx += col_widths[2]
        draw.text((cx + 20, y + row_h / 2), row["yoy_text"], font=font_cell, fill=row["yoy_color"], anchor="lm")
        y += row_h

    draw.rectangle([x, pad, x + sum(col_widths), y], outline=COLOR_BORDER, width=2)
    for i in range(len(rows) + 1):
        yy = pad + header_h + row_h * i
        draw.line([(x, yy), (x + sum(col_widths), yy)], fill=COLOR_BORDER, width=1)
    cx = x
    for w in col_widths[:-1]:
        cx += w
        draw.line([(cx, pad), (cx, y)], fill=COLOR_BORDER, width=1)

    cy = y + 20
    for line in caption_lines:
        draw.text((x, cy), line, font=font_caption, fill=COLOR_CAPTION)
        cy += caption_line_h

    if not out_path:
        out_dir = OUTPUT_DIR / "tables"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"indicators_table_{datetime.now(timezone.utc).strftime('%Y%m%d')}.png"
    img.save(out_path)
    return out_path


def run(verbose=True):
    out_path = build_table_image()
    if verbose:
        if out_path:
            print(f"経済指標テーブル画像を生成しました: {out_path}")
        else:
            print("FREDデータが無いため、経済指標テーブル画像は生成しませんでした")
    return out_path


if __name__ == "__main__":
    run()
