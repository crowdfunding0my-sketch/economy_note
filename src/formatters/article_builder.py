"""
note投稿記事の下書きを組み立てるビルダー。

無料エリアでは「読む価値のある情報」をきちんと出しつつ、有料エリアの直前で
「本日の米国株ピック」の際立った数字（時価総額・株価上昇率・売上成長率）だけを見せて
銘柄名を伏せ、続きへの興味を引く構成にしている。

note.comには「ここから有料」を示すMarkdown記法は無い（エディタ上でボタン操作するため）。
本スクリプトは出力の中に `<!-- PAYWALL -->` という目印コメントを入れるので、
note投稿時にその直前で「続きを読むには」ボタンを配置する。

前提ファイル（無ければその項目は本文からスキップされる）:
- output/fred_indicators_*.json（最新日付のもの）
- output/bls_indicators_*.json（最新日付のもの）
- output/us_premium_rotation_candidates.json + output/rotation_state.json

使い方:
  python src/formatters/article_builder.py
"""

import csv
import json
import glob
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "output"

JP_PICKS_SHOWN = 3  # 日本株ピックアップで本文に載せる件数

# 投資初心者向けの用語解説。外部リンクではなく記事内で完結させる方針（note.comは
# サイドバー的なレイアウトを作れないため）。各セクションで使う用語をあらかじめ紐付けておき、
# 「用語解説」セクションとして自動生成する。
GLOSSARY = {
    "CPI": "消費者物価指数。モノやサービスの値段が全体としてどれくらい上がった/下がったかを示す、物価の「体温計」のような指標。",
    "コアCPI": "CPIから、値動きの大きい食品・エネルギーを除いた指標。物価の基調的な動きを見るのに使われる。",
    "失業率": "働く意思のある人のうち、実際に仕事に就けていない人の割合。",
    "非農業部門雇用者数": "農業以外の産業で新たに増えた雇用者数。米国の景気を測る代表的な指標の一つ。",
    "PMI": "購買担当者景気指数。企業の仕入れ担当者への調査をもとにした景況感の指数（50が拡大/縮小の境目）。"
           "正式なPMIは無料で取得できないため、本記事では代わりに地区連銀の製造業サーベイ（0が境目）を掲載している。",
    "FOMC": "米連邦公開市場委員会。FRB（米国の中央銀行にあたる組織）の中で、政策金利などの金融政策を決める会合。",
    "FRB": "米連邦準備制度理事会。日本で言う日本銀行にあたる、米国の中央銀行。",
    "PER": "株価収益率。株価が1株あたり利益の何倍まで買われているかを示す指標。数字が低いほど「割安」とされる。",
    "時価総額": "株価 × 発行済み株式数。その会社が株式市場でどれくらいの規模と評価されているかを表す。",
    "EPS": "1株あたり利益。会社の利益を発行済み株式数で割ったもの。プラスなら黒字、マイナスなら赤字。",
    "R²（決定係数）": "株価の推移が、どれだけ一貫して同じ方向に動いているかを0〜1の数値で表したもの。"
                     "1に近いほど値動きにブレが少なく、一貫した傾向と言える。",
    "ドルインデックス": "ドルが主要な複数通貨に対して全体としてどれくらい強い/弱いかを示す指数。",
    "レバレッジド・マネー": "ヘッジファンドなど、借入等を活用して積極的に売買する投資家層を指すCFTC（米商品先物取引委員会）の分類。",
    "ネットポジション": "買い建玉（買いの契約数）から売り建玉（売りの契約数）を差し引いた数。プラスなら買い越し、マイナスなら売り越し。",
}

# 無料エリア／有料エリアそれぞれで登場する用語（記事の構成が概ね固定のため静的に対応付けている）
FREE_AREA_TERMS = ["CPI", "コアCPI", "失業率", "非農業部門雇用者数", "PMI", "FOMC", "FRB", "PER"]
PAID_AREA_TERMS = ["時価総額", "EPS", "R²（決定係数）", "ドルインデックス", "レバレッジド・マネー", "ネットポジション"]


def build_glossary_section(term_keys, heading="## 📘 用語解説"):
    lines = [heading, ""]
    for term in term_keys:
        explanation = GLOSSARY.get(term)
        if explanation:
            lines.append(f"- **{term}**：{explanation}")
    lines.append("")
    return "\n".join(lines)


def _latest_csv_rows(pattern):
    """最新の日本株スクリーニング結果CSV（週次実行でキャッシュされたもの）を読み込む"""
    files = sorted(glob.glob(str(OUTPUT_DIR / pattern)))
    if not files:
        return None, None
    latest_path = files[-1]
    with open(latest_path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return rows, latest_path


def _latest_json(pattern):
    files = sorted(glob.glob(str(OUTPUT_DIR / pattern)))
    if not files:
        return None
    with open(files[-1], encoding="utf-8") as f:
        return json.load(f)


def _pct(x, digits=1):
    return f"{x * 100:+.{digits}f}%"


def _todays_stock(candidates_data, state):
    """
    本日の米国株ピックを取得する。

    us_stock_rotation.py が既に実行済みなら state["last_picked_symbol"] に
    「本日選ばれた銘柄」が入っているので、それを優先して使う（symbol一致で検索）。
    next_index は「次回（明日）用」のインデックスなので、本日分の判定には使わない
    （article_builderをus_stock_rotationの後に実行する運用だと、next_indexは既に
    1つ進んでしまっているため、本日の銘柄とズレてしまう）。

    us_stock_rotation.py が未実行（state に last_picked_symbol が無い）場合のみ、
    next_index をそのまま「これから選ばれる予定の銘柄」の目安として使う。
    """
    candidates = candidates_data["candidates"]
    last_symbol = state.get("last_picked_symbol")
    if last_symbol:
        for c in candidates:
            if c["symbol"] == last_symbol:
                return c
    index = state.get("next_index", 0) % len(candidates)
    return candidates[index]


def _todays_rotation_draft():
    """us_stock_rotation.py が本日分に保存した下書き（ニュース・決算情報入り）があれば読み込む"""
    today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    path = OUTPUT_DIR / "drafts" / f"us_pick_{today_str}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def build_market_summary(fred):
    if not fred:
        return "（本日の海外市場データは未取得です）"
    lines = ["## 本日の海外市場サマリー", ""]
    index_labels = {"SP500": "S&P500", "NASDAQCOM": "NASDAQ総合指数", "DJIA": "NYダウ平均"}
    for sid, label in index_labels.items():
        s = fred.get(sid, {}).get("summary")
        if not s:
            continue
        lines.append(f"- **{label}**：{s['latest_value']:,.2f}（前営業日比 {_pct(s['change_rate'])}）")
    lines.append("")
    return "\n".join(lines)


def build_indicators_section(fred, bls):
    lines = ["## 経済指標トピック", ""]

    # BLSで「本日発表」フラグが立っている指標があれば、その旨を先頭で強調する（一次情報の原則）
    bls_labels = {
        "CUSR0000SA0": "CPI",
        "LNS14000000": "失業率",
        "CES0000000001": "雇用統計",
    }
    if bls:
        new_releases = [bls_labels.get(sid, sid) for sid, d in bls.items() if d.get("is_new_release")]
        if new_releases:
            lines.append(f"**【本日発表】{'・'.join(new_releases)}の最新値が発表されました。"
                          f"以下はBLS（米労働省統計局）の速報値を反映しています。**")
            lines.append("")

    fred_labels = {
        "CPIAUCSL": "CPI（総合）",
        "CPILFESL": "コアCPI（食品・エネルギー除く）",
        "UNRATE": "失業率",
        "PAYEMS": "非農業部門雇用者数",
        "GACDFSA066MSFRBPHI": "フィラデルフィア連銀製造業指数（PMI代替）",
        "GACDISA066MSFRBNY": "NY連銀 Empire State製造業指数（PMI代替）",
    }
    # 失業率・地区連銀の景況指数は「率の変化率(%)」だと誤解を招く/0除算になりうるため、pt差で表示する
    point_diff_series = {"UNRATE", "GACDFSA066MSFRBPHI", "GACDISA066MSFRBNY"}
    if fred:
        lines.append("| 指標 | 最新値 | 前月比 | 前年同月比 |")
        lines.append("|---|---|---|---|")
        for sid, label in fred_labels.items():
            s = fred.get(sid, {}).get("summary")
            if not s:
                continue
            if sid in point_diff_series:
                mom = f"{s['mom_diff']:+.1f}pt"
                yoy = f"{s['yoy_diff']:+.1f}pt" if s.get("yoy_diff") is not None else "—"
            else:
                mom = _pct(s["mom_change_rate"])
                yoy = _pct(s["yoy_change_rate"]) if s.get("yoy_change_rate") is not None else "—"
            lines.append(f"| {label}（{s['latest_date']}） | {s['latest_value']:,.3f} | {mom} | {yoy} |")
        lines.append("")
    lines.append("> 数値は特に断りがない限りFRED（セントルイス連銀）を情報源としています。"
                  "指標発表当日はBLS（米労働省統計局）一次発表の速報値を優先して反映します。"
                  "正式なISM/S&P Global PMIは無料での取得元が無いため、地区連銀の製造業サーベイを"
                  "PMI発表前の先行指標として代わりに掲載しています（0が拡大/縮小の境目）。")
    lines.append("")
    return "\n".join(lines)


def build_fed_section(fed):
    """FRB公式発表（一次情報）のセクション。新着があれば強調し、無ければ直近の発表を参考情報として載せる"""
    lines = ["## FRB最新発表", ""]
    if not fed or not fed.get("items"):
        lines.append("（FRB発表データは未取得です）")
        lines.append("")
        return "\n".join(lines)

    new_items = fed.get("new_items") or []
    if new_items:
        lines.append("**前回チェック以降、FRBから新たに発表がありました：**")
        lines.append("")
        for item in new_items:
            lines.append(f"- [{item['title']}]({item['link']})（{item['pub_date']}）")
    else:
        lines.append("本日時点で新規発表はありません。直近の発表：")
        lines.append("")
        for item in fed["items"][:3]:
            lines.append(f"- [{item['title']}]({item['link']})（{item['pub_date']}）")
    lines.append("")
    lines.append("> 出典：FRB公式サイト（federalreserve.gov）のプレスリリースRSSフィードより。"
                  "解説記事より先に一次発表そのものを参照する方針です。")
    lines.append("")
    return "\n".join(lines)


def build_jp_pick_section():
    """
    日本株ピックアップ（無料エリア）。J-Quantsの全銘柄スクリーニングはFreeプランだと
    約14.5時間かかるため、main.pyの日次実行には含めず、週次で別途実行してCSVに
    キャッシュする運用（src/fetchers/jquants_screener.py を参照）。
    本関数はそのキャッシュ済みCSVのうち最新のものを読み込むだけ。
    """
    rows, path = _latest_csv_rows("screening_result_*.csv")
    lines = ["## 本日の日本株ピックアップ（無料）", ""]
    if not rows:
        lines.append("（週次スクリーニング未実行のため、まだ候補がありません。"
                      "`python src/fetchers/jquants_screener.py` の実行後に反映されます）")
        lines.append("")
        return "\n".join(lines)

    scan_date = Path(path).stem.replace("screening_result_", "")
    lines.append(f"（{scan_date} 実行のスクリーニング結果より。PER15倍以下・増収営業増益・"
                  f"小型株・株価トレンド上昇を満たした銘柄）")
    lines.append("")
    top_rows = sorted(rows, key=lambda r: float(r["price_change_rate"]), reverse=True)[:JP_PICKS_SHOWN]
    for r in top_rows:
        lines.append(
            f"- **{r['code']}**：PER {float(r['per']):.1f}倍／株価{float(r['price']):,.0f}円／"
            f"過去の株価上昇率 {float(r['price_change_rate'])*100:+.1f}%"
        )
    lines.append("")
    return "\n".join(lines)


def build_us_pick_teaser(candidates_data, state):
    if not candidates_data or not state:
        return "## 本日の米国株ピック（有料エリア）\n\n（候補データ未準備）\n"

    stock = _todays_stock(candidates_data, state)

    mktcap_b = stock["market_cap_usd"] / 1_000_000_000
    growth_pct = stock["revenue_growth_rate"] * 100
    price_change_pct = stock["price_change_rate_2y"] * 100

    lines = [
        "## 本日の米国株ピック（有料エリア）",
        "",
        f"取引可能な米国株1,056銘柄を「過去2年の株価トレンド」「売上成長」「時価総額150億ドル以下」で"
        f"スクリーニングし、最終的に生き残ったのは**わずか15銘柄**でした。本日はその中から1銘柄を深掘りします。",
        "",
        "**この銘柄の際立った数字：**",
        "",
        f"- 時価総額：約{mktcap_b:.1f}億ドル（大型株の陰に隠れがちなサイズ感）",
        f"- 売上高成長率：前期比 {growth_pct:+.0f}%",
        f"- 株価上昇率（過去2年）：{price_change_pct:+.0f}%",
        "",
        "銘柄名・直近決算の詳細・最新ニュース・今後の展望は、この続き（有料エリア）でお読みいただけます。",
        "15銘柄を1日1銘柄ずつ深掘りしていくので、2週間かけて全銘柄をチェックできます。",
        "",
    ]
    return "\n".join(lines)


def build_us_pick_full(candidates_data, state):
    """
    有料エリア本文（銘柄名を含むフル情報）。
    us_stock_rotation.py が本日分の下書き（決算＋ニュース入り）を既に生成していれば、
    その内容をそのまま使う（ニュースAPIを二重に呼ばないため）。
    未実行の場合は候補データのみから簡易版を組み立てる。
    """
    draft = _todays_rotation_draft()
    if draft:
        return draft

    stock = _todays_stock(candidates_data, state)
    lines = [
        f"### {stock['symbol']}（{stock.get('company_hint', '')}）",
        "",
        f"- 売上高：{stock['revenue_curr']:,.0f}ドル（前期 {stock['revenue_prev']:,.0f}ドル、"
        f"{stock['revenue_growth_rate']*100:+.1f}%）",
        f"- EPS（直近期）：{stock['eps_curr']}",
        f"- 時価総額：約{stock['market_cap_usd']/1_000_000_000:.2f}億ドル",
        f"- 株価上昇率（過去2年）：{stock['price_change_rate_2y']*100:+.1f}%（トレンドの一貫性 R²={stock['trend_r2']}）",
        "",
        "> 本銘柄は「割安小型株」ではなく「売上成長・株価モメンタム」を基準に選定しています。"
        "現時点で黒字化していない場合があります。",
        "",
        "（※ `src/formatters/us_stock_rotation.py` 未実行のため、ニュース・展望は未反映です）",
        "",
    ]
    return "\n".join(lines)


def build_fx_section(fx):
    """
    有料エリア：ドルインデックス・ドル円・クロス円動向＋CFTC投機筋ポジション。
    一般的なFXレートに加えて「他ではなかなか得られない情報」として、
    CFTCのTraders in Financial Futuresレポートから円先物のレバレッジド・マネー
    （ヘッジファンド等）のネットポジションを載せる（プロのFXトレーダー向けの視点）。
    """
    lines = ["## ドルインデックス・ドル円・クロス円動向（有料エリア）", ""]
    if not fx:
        lines.append("（為替データ未取得です）")
        lines.append("")
        return "\n".join(lines)

    rates = fx.get("fx_rates", {})
    cross = fx.get("cross_yen", {})

    dxy = (rates.get("DTWEXBGS") or {}).get("summary")
    usdjpy = (rates.get("DEXJPUS") or {}).get("summary")
    if dxy:
        lines.append(f"- **ドルインデックス**（FRED広義ドル指数）：{dxy['latest_value']:.2f}（前日比 {_pct(dxy['change_rate'])}）")
    if usdjpy:
        lines.append(f"- **ドル円**：{usdjpy['latest_value']:.2f}円（前日比 {_pct(usdjpy['change_rate'])}）")
    for pair, label in [("EURJPY", "ユーロ円"), ("GBPJPY", "ポンド円")]:
        c = cross.get(pair)
        if c:
            lines.append(f"- **{label}**（算出値）：{c['latest_value']:.2f}円（前日比 {_pct(c['change_rate'])}）")
    lines.append("")
    lines.append("> ドルインデックスはICE公表の一般的なDXY（6通貨）とは構成通貨が異なる代替指標です。"
                 "為替データはFRED発表の都合上、数日〜1週間程度のラグがあります。")
    lines.append("")

    positioning = fx.get("jpy_futures_positioning")
    if positioning:
        lines.append("### 【差がつく情報】投機筋の円先物ポジション（CFTC）")
        lines.append("")
        lines.append("個人向けの相場記事ではほとんど扱われませんが、CFTC（米商品先物取引委員会）が"
                     "毎週発表する建玉報告から、ヘッジファンド等の「レバレッジド・マネー」が"
                     "円先物をどちらに賭けているかが分かります。")
        lines.append("")
        net = positioning["net_position"]
        change = positioning["net_position_change"]
        change_word = "積み増し" if (net < 0) == (change < 0) else "巻き戻し"
        lines.append(
            f"- レバレッジド・マネーの円先物ネットポジション：**{net:+,}枚（{positioning['direction']}）**"
            f"（{positioning['report_date']}時点、前週比 {change:+,}枚の{change_word}）"
        )
        lines.append(f"- 買い建玉 {positioning['lev_money_long']:,}枚 / 売り建玉 {positioning['lev_money_short']:,}枚")
        lines.append("")
        lines.append(f"> 出典：CFTC Traders in Financial Futures（TFF）レポート。"
                     f"{positioning['prev_report_date']}→{positioning['report_date']}の変化。")
        lines.append("")
    return "\n".join(lines)


def build_article():
    fred = _latest_json("fred_indicators_*.json")
    bls = _latest_json("bls_indicators_*.json")
    fed = _latest_json("fed_press_releases_*.json")
    fx = _latest_json("fx_indicators_*.json")
    candidates_data = _latest_json("us_premium_rotation_candidates.json")
    state = _latest_json("rotation_state.json")

    today = datetime.now(timezone.utc).strftime("%Y/%m/%d")

    parts = [
        f"# 【{today}】市場サマリーと注目銘柄ピックアップ",
        "",
        "本日の海外市場の値動きと主要経済指標、そして独自スクリーニングによる注目銘柄をお届けします。",
        "",
        "---",
        "",
        build_market_summary(fred),
        build_indicators_section(fred, bls),
        build_fed_section(fed),
        build_jp_pick_section(),
        build_glossary_section(FREE_AREA_TERMS),
        "---",
        "",
        build_us_pick_teaser(candidates_data, state),
        "",
        "<!-- PAYWALL -->",
        "",
        "## ここから有料エリア",
        "",
        build_us_pick_full(candidates_data, state) if candidates_data and state else "",
        build_fx_section(fx),
        build_glossary_section(PAID_AREA_TERMS, heading="## 📘 用語解説（有料エリアの用語）"),
    ]
    return "\n".join(parts)


if __name__ == "__main__":
    article = build_article()
    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / f"article_draft_{datetime.now(timezone.utc).strftime('%Y%m%d')}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(article)
    print(f"下書きを保存しました: {out_path}")
