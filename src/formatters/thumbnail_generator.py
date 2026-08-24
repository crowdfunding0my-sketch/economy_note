"""
note投稿用サムネイル画像を生成する。

方針：その日の有料エリア注目株の「分野」を表す無料ストック写真（Pixabay）を背景にし、
株価上昇率・売上成長率のバッジを重ねる。銘柄名・ティッカーは出さない（記事本文の
「チラ見せ」戦略と一貫性を保つため）。record_builder.build_article() を実行した後、
main.pyの最終ステップとして呼び出す想定（記事の内容が確定してから画像を作るため）。

画像取得元: https://pixabay.com/ （PIXABAY_API_KEYが必要）
Pixabayで写真が見つからない場合（キー未設定・分野キーワード無し・該当銘柄無し等）は、
自動生成のグラデーション＋スパークライン画像にフォールバックする。

配色は日本の相場報道の慣習（陽線=赤=上昇、陰線=青=下落）に合わせている。

使い方:
  python src/formatters/thumbnail_generator.py
"""

import os
import sys
import json
import glob
import random
from io import BytesIO
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont, ImageFilter

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src" / "fetchers"))
from alpaca_price_trend import fetch_bars  # noqa: E402  (フォールバック時のスパークライン用)

load_dotenv()

PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY", "")
PIXABAY_URL = "https://pixabay.com/api/"

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"

OUTPUT_DIR = PROJECT_ROOT / "output"

WIDTH, HEIGHT = 1920, 1006  # note.com推奨1280x670の高品質版(1.5倍、同縦横比1.91:1)

COLOR_BG_TOP = (13, 20, 38)
COLOR_BG_BOTTOM = (26, 33, 58)
COLOR_UP = (229, 62, 62)      # 赤（上昇。日本の相場報道の慣習）
COLOR_DOWN = (59, 130, 246)   # 青（下落）
COLOR_TEXT = (245, 246, 250)
COLOR_SUBTEXT = (200, 205, 220)

FONT_DIR = Path(r"C:\Windows\Fonts")
FONT_BOLD = FONT_DIR / "YuGothB.ttc"
FONT_MEDIUM = FONT_DIR / "YuGothM.ttc"


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


def get_todays_featured_stock():
    """本日の有料エリア注目株の統計情報を返す（symbol/image_keywordは画像検索にのみ使い、描画はしない）"""
    state = _latest_json("rotation_state.json")
    candidates_data = _latest_json("us_premium_rotation_candidates.json")
    if not state or not candidates_data:
        return None
    symbol = state.get("last_picked_symbol")
    if not symbol:
        return None
    for c in candidates_data["candidates"]:
        if c["symbol"] == symbol:
            return c
    return None


def fetch_pixabay_photo(keyword):
    """
    Pixabayで分野キーワードに合う横長写真を取得し、複数件の中からランダムに1枚選んでPIL Imageで返す
    （見つからなければNone）。毎回同じ写真・ロゴ風の文字が写り込んだ写真に固定されないようにするため。
    """
    if not PIXABAY_API_KEY or not keyword:
        return None
    resp = requests.get(PIXABAY_URL, params={
        "key": PIXABAY_API_KEY,
        "q": keyword,
        "image_type": "photo",
        "orientation": "horizontal",
        "safesearch": "true",
        "per_page": 20,
    }, timeout=20)
    resp.raise_for_status()
    hits = resp.json().get("hits", [])
    if not hits:
        return None
    chosen = random.choice(hits)
    img_resp = requests.get(chosen["largeImageURL"], timeout=20)
    img_resp.raise_for_status()
    return Image.open(BytesIO(img_resp.content)).convert("RGB")


def fit_and_crop(img, target_w, target_h):
    """アスペクト比を保って拡大し、中央でtarget_w x target_hに切り抜く"""
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = int(src_w * scale), int(src_h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


def darken_for_text(img, opacity=175):
    """写真の上に黒の半透明レイヤーを重ね、テキストを読みやすくする"""
    overlay = Image.new("RGBA", img.size, (10, 12, 20, opacity))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def fetch_stock_price_history(symbol, lookback_days=760):
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=lookback_days)
    bars = fetch_bars([symbol], start, end).get(symbol, [])
    bars = sorted(bars, key=lambda b: b["t"])
    return [b["c"] for b in bars]


def fetch_sp500_recent(limit=20):
    if not FRED_API_KEY:
        return []
    resp = requests.get(FRED_URL, params={
        "series_id": "SP500", "api_key": FRED_API_KEY, "file_type": "json",
        "sort_order": "desc", "limit": limit,
    })
    resp.raise_for_status()
    obs = resp.json().get("observations", [])
    values = [float(o["value"]) for o in obs if o.get("value") not in (None, ".")]
    return list(reversed(values))


def draw_gradient_background(draw):
    for y in range(HEIGHT):
        t = y / HEIGHT
        color = tuple(int(COLOR_BG_TOP[i] + (COLOR_BG_BOTTOM[i] - COLOR_BG_TOP[i]) * t) for i in range(3))
        draw.line([(0, y), (WIDTH, y)], fill=color)


def draw_sparkline(draw, values, accent_color, box):
    if len(values) < 2:
        return
    x0, y0, x1, y1 = box
    vmin, vmax = min(values), max(values)
    vrange = vmax - vmin or 1
    n = len(values)
    points = []
    for i, v in enumerate(values):
        x = x0 + (x1 - x0) * i / (n - 1)
        y = y1 - (y1 - y0) * (v - vmin) / vrange
        points.append((x, y))
    draw.line(points, fill=accent_color, width=6, joint="curve")
    lx, ly = points[-1]
    r = 11
    draw.ellipse([lx - r, ly - r, lx + r, ly + r], fill=accent_color)


def draw_badge(draw, text, font, x, y, fill, text_fill=(255, 255, 255)):
    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad_x, pad_y = 32, 18
    draw.rounded_rectangle([x, y, x + w + pad_x * 2, y + h + pad_y * 2], radius=16, fill=fill)
    draw.text((x + pad_x, y + pad_y - bbox[1]), text, font=font, fill=text_fill)
    return y + h + pad_y * 2


def build_text_layer(featured, sp500_summary):
    """写真の上に重ねる文字情報を描いたRGBA画像を返す"""
    layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    draw.text((90, 75), "相場note", font=_font(FONT_BOLD, 60), fill=COLOR_TEXT)
    today = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    draw.text((90, 190), today, font=_font(FONT_BOLD, 110), fill=COLOR_TEXT)

    if featured:
        price_pct = featured["price_change_rate_2y"] * 100
        growth_pct = featured["revenue_growth_rate"] * 100
        accent = COLOR_UP if price_pct >= 0 else COLOR_DOWN

        draw.text((90, 400), "本日の有料エリア注目株", font=_font(FONT_MEDIUM, 40), fill=COLOR_SUBTEXT)
        bottom_y = draw_badge(draw, f"株価 2年で {price_pct:+.0f}%", _font(FONT_BOLD, 68), 90, 455, fill=accent)
        draw_badge(draw, f"売上高 前期比 {growth_pct:+.0f}%", _font(FONT_BOLD, 44), 90, bottom_y + 20,
                   fill=(45, 55, 85), text_fill=COLOR_TEXT)

    if sp500_summary is not None:
        sp500_change = sp500_summary["change_rate"]
        arrow = "▲" if sp500_change >= 0 else "▼"
        sp_text = f"S&P500 {arrow} {sp500_change*100:+.2f}%"
        sp_font = _font(FONT_MEDIUM, 34)
        bbox = draw.textbbox((0, 0), sp_text, font=sp_font)
        tw = bbox[2] - bbox[0]
        draw.text((WIDTH - tw - 90, 90), sp_text, font=sp_font,
                  fill=COLOR_UP if sp500_change >= 0 else COLOR_DOWN)

    tagline = "日本株 × 米国株 × 経済指標"
    tagline_font = _font(FONT_MEDIUM, 36)
    bbox = draw.textbbox((0, 0), tagline, font=tagline_font)
    tw = bbox[2] - bbox[0]
    draw.text((WIDTH - tw - 90, 150), tagline, font=tagline_font, fill=COLOR_SUBTEXT)

    return layer


def build_thumbnail(out_path=None):
    fred = _latest_json("fred_indicators_*.json")
    sp500_summary = (fred or {}).get("SP500", {}).get("summary")
    featured = get_todays_featured_stock()

    photo = None
    if featured and featured.get("image_keyword"):
        try:
            photo = fetch_pixabay_photo(featured["image_keyword"])
        except Exception as e:
            print(f"Pixabay取得に失敗（フォールバックします）: {e}")

    if photo:
        base = fit_and_crop(photo, WIDTH, HEIGHT)
        base = darken_for_text(base)
        base = base.filter(ImageFilter.GaussianBlur(1))  # 軽くぼかして文字を読みやすく
    else:
        # フォールバック：写真が用意できない場合はグラデーション＋スパークライン
        base = Image.new("RGB", (WIDTH, HEIGHT), COLOR_BG_TOP)
        draw_bg = ImageDraw.Draw(base)
        draw_gradient_background(draw_bg)
        if featured:
            accent = COLOR_UP if featured["price_change_rate_2y"] >= 0 else COLOR_DOWN
            try:
                history = fetch_stock_price_history(featured["symbol"])
            except Exception:
                history = []
            if history:
                draw_sparkline(draw_bg, history, accent, box=(90, 780, WIDTH - 90, 940))
        else:
            sp500_history = fetch_sp500_recent()
            change_rate = sp500_summary["change_rate"] if sp500_summary else 0.0
            accent = COLOR_UP if change_rate >= 0 else COLOR_DOWN
            if sp500_history:
                draw_sparkline(draw_bg, sp500_history, accent, box=(90, 620, WIDTH - 90, 840))

    text_layer = build_text_layer(featured, sp500_summary)
    final_img = Image.alpha_composite(base.convert("RGBA"), text_layer).convert("RGB")

    if not out_path:
        out_dir = OUTPUT_DIR / "thumbnails"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"thumbnail_{datetime.now(timezone.utc).strftime('%Y%m%d')}.png"
    final_img.save(out_path)
    return out_path


def run(verbose=True):
    out_path = build_thumbnail()
    if verbose:
        print(f"サムネイルを生成しました: {out_path}")
    return out_path


if __name__ == "__main__":
    run()
