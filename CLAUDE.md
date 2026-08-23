# 相場note自動投稿ツール

## リポジトリ

GitHub: https://github.com/crowdfunding0my-sketch/economy_note.git

## 技術スタック

- **言語**: Python（データ取得・加工が中心のバッチ処理のため。pandas等の資産を活用）
- **秘密情報管理**: `.env`ファイル（`.gitignore`で除外）。`.env.example`にキー名のみ記載
- **実行**: ローカルで`python src/main.py`を手動実行→動作確認後、Windowsタスクスケジューラ or GitHub Actions(cron)で自動化を検討
- **ディレクトリ構成**:
  ```
  相場note/
  ├── .env / .env.example
  ├── .gitignore
  ├── requirements.txt
  ├── src/
  │   ├── fetchers/      # ソースごとの取得ロジック
  │   ├── formatters/    # note記事フォーマット生成
  │   └── main.py
  └── output/            # 生成した下書き・CSV等（gitignore対象）
  ```
- **note自動投稿について**: note.comは公式の一般公開APIを提供していない。下書きMarkdown自動生成までは可能だが、実際の自動投稿には非公式API利用 or ブラウザ自動化（Playwright等）が必要になり、規約リスクもある。自動投稿までやるか下書き生成に留めるかは要検討。

## 概要

日本と海外の株式相場に関する情報を毎日自動で取得し、note投稿用のフォーマット（記事の下書き）を自動生成するツールを作成する。

最終的なゴールは以下の4本柱で構成される日次レポートの自動生成。

1. **日本株**：J-Quants APIから取得した個別企業のスクリーニング情報
2. **海外株・ニュース**：Woodstock（注文執行）＋Alpha Vantage（決算・ファンダメンタルズ・ニュース）を軸とした相場動向
3. **経済指標**：米国のコア指数・CPI・雇用統計など、マクロ経済指標の推移（FRED / BLS / Trading Economicsを使い分け）
4. **一次情報の原則**：ニュース解説記事（WSJ等）より先に、FRB・BLS等の一次発表そのものを参照する

---

## 1. 日本株データ（J-Quants API V2 / Freeプラン、無料エリア用）

### 現状

J-Quants APIはすでに契約・接続済み。**2025年12月のV2リリースに伴い、エンドポイント・認証方式・フィールド名がV1から大きく変更されている**（V1は2026/6/1に終了済み）。
- 認証: `x-api-key`ヘッダーでAPIキーを送信（トークン認証は廃止）
- エンドポイント例: `/v1/prices/daily_quotes` → `/v2/equities/bars/daily`、`/v1/listed/info` → `/v2/equities/master`、`/v1/fins/statements` → `/v2/fins/summary`
- レスポンスのフィールド名が短縮化（例: `Close`→`C`、`NetSales`→`Sales`、`EarningsPerShare`→`EPS`、`TypeOfCurrentPeriod`→`CurPerType`、`DisclosedDate`→`DiscDate`）

### 実現したいスクリーニング

- 各社の**売上高**・**営業利益**を取得
- **PERが15近辺**の銘柄をピックアップ

### Freeプランでの実現可否メモ

- 財務情報は `/v2/fins/summary` から取得可能（売上高`Sales`・営業利益`OP`・経常利益`OdP`・当期純利益`NP`・EPS`EPS`など）。**実データで検証済み**。
- PERはJ-Quants側が直接値として提供しているわけではないため、`株価 ÷ EPS` で自前計算する（株価は `/v2/equities/bars/daily` から取得、終値フィールドは`C`）。**実データで検証済み**。
- 銘柄一覧・市場区分は `/v2/equities/master` から取得可能。市場区分名は`MarketCodeName`ではなく**`MktNm`**（会社名は`CoName`、時価総額区分は`ScaleCat`、例:"TOPIX Small 1"）。**実データで確認し、コードも修正済み**。
- `/v2/equities/bars/daily`のレスポンスには**`MktCap`（時価総額）が直接含まれる**ため、小型株の絞り込みは`ScaleCat`の文字列一致より`MktCap`の数値閾値で行う方が確実。
- **Freeプランの制約（公式仕様で確認済み）**：
  - **レート制限：5リクエスト/分**（Light=60, Standard=120, Premium=500）。全銘柄走査には向かない。
  - **データ取得可能期間：直近12週間〜過去2年12週間分のみ**。つまり「最新株価」は実際には最大12週間（約3ヶ月）前のものになる。当日リアルタイム性は無い。
- 上記を踏まえ、「売上高・営業利益の取得」および「PER≒15の銘柄抽出」は**Freeプランでも実装可能**。ただし①対象銘柄を絞らないと毎日の自動実行が非現実的、②株価・財務データは最大3ヶ月遅延、という前提で運用する。

### 今後の実装イメージ

1. `/v2/equities/master` で上場銘柄一覧を取得
2. `/v2/fins/summary` で各社の売上高・営業利益・EPSを取得
3. `/v2/equities/bars/daily` で株価を取得（Freeプランでは3ヶ月遅延データである点を記事内に明記）
4. `株価 ÷ EPS` でPERを算出し、PER 15前後（例：13〜17などレンジで閾値を要調整）の銘柄を抽出
5. 抽出結果をnote記事フォーマットに整形

実装: [src/fetchers/jquants_screener.py](src/fetchers/jquants_screener.py)（V2フィールド名に修正済み・Freeプランのレート制限に合わせた待機時間を自動計算）

---

## 1-2. 有料エリア（プレミアムnote）：日本の小型株スクリーニング

### 方針（決定済み）

- 有料エリアのコンテンツとして、**小型株に特化した投資銘柄情報**を掲載する。
- 対象市場：**グロース市場・スタンダード市場**（プライムは大型株中心のため対象外）。
- スクリーニング条件：PER 15倍以下 かつ **売上高・営業利益**が直近3期連続増加（増益判定は営業利益(`OP`)基準。EPS/PER自体は当期純利益ベースのAPI値`EPS`をそのまま使用）。

### 要検討事項（未決定）

- **J-Quantsのプラン**：現状Freeプランのまま運用するか、Light以上（60req/分〜）にアップグレードするかは未決定。
  - Freeのままの場合：対象をグロース＋スタンダードに絞ってもAPI呼び出し数が多く、日次自動実行の所要時間・株価の3ヶ月遅延という制約が残る。
  - 有料エリアの記事として売る以上、データ鮮度が信頼性に直結するため、実運用開始前に要判断。
- 時価総額区分（`ScaleCat`等）でさらに絞り込む場合は、正式なフィールド名・値をJ-Quantsダッシュボード/スキーマで要確認。

---

## 1-3. 有料エリア（プレミアムnote）：米国「成長モメンタム小・中型株」1銘柄/日ローテーション

### 経緯・方針転換

当初は日本株と同様に「割安小型株（PER15倍以下・増収増益）」を米国株にも適用しようとしたが、
Alpha Vantageは日本株のニュース・ファンダメンタルズカバレッジが薄く、**有料エリアの対象を米国株の
1銘柄/日ローテーションに変更**した（無料エリアの日本株PERスクリーニングはJ-Quantsのまま継続）。

さらに「過去2年で株価が大きく上がった銘柄」を起点にすると、その多くは①黒字化していない
高成長株（PER計算不能）か、②すでに時価総額が数十億〜数千億ドルに育った大型株（PER・時価総額とも
小型株の基準に合わない）のどちらかに偏り、「割安・小型・連続増益」の組み合わせでは**該当銘柄0件**
という結果になった。そのため方針を「**割安小型株」ではなく「成長モメンタムのある小・中型株**」に
転換し、利益性・PERは問わず「売上高が前期より増加していること」のみを必須条件とした。

### データソースの役割分担

- **Alpaca（Woodstockの裏側API。直接キー取得済み・Paper Tradingキーで可）**：株価データ・銘柄一覧は
  ここから直接（`data.alpaca.markets`）取得できるため、**完全自動化・レート制限の心配なし**。
  ただしファンダメンタルズ（決算）データはAlpacaの公開APIには存在しない。
- **Woodstock（`get_fundamentals`等。このClaude環境経由でしか呼べない）**：決算情報（売上高・営業利益・
  EPS・発行済株式数など）はここからのみ取得可能。自動実行スクリプトから直接は呼べないため、
  **銘柄選定（Stage C）は都度Claudeを呼び出して行うか、`/schedule`等で定期実行する半自動運用になる**。

### 銘柄選定パイプライン（3段階）

1. **Stage A**：Woodstockの`get_tradable_symbols`で取引可能銘柄一覧を取得（1056銘柄、ETF/レバレッジ商品を含む）
   → [output/us_tradable_symbols_raw.json](output/us_tradable_symbols_raw.json)
2. **Stage B**（完全自動・Alpaca直接API）：全銘柄の過去2年の日足を取得し、単回帰トレンド
   （傾き>0かつ決定係数R²≥0.3）でフィルタし、株価上昇率が高い順に上位60銘柄に絞る
   → [src/fetchers/alpaca_price_trend.py](src/fetchers/alpaca_price_trend.py)
   → [output/us_trend_candidates.json](output/us_trend_candidates.json)
3. **Stage C**（Claude経由・Woodstock `get_fundamentals`）：Stage Bの60銘柄について決算情報を取得し、
   ①売上高が前期比で増加、②時価総額が150億ドル以下、の2条件で絞り込み、株価上昇率順に並べる
   → [output/us_premium_rotation_candidates.json](output/us_premium_rotation_candidates.json)（**15銘柄確定・2026-08-24時点**）

最終候補15銘柄：UAMY, AAOI, DAVE, PL, BFLY, ONDS, AMPX, AGX, APLD, AEVA, VICR, VIAV, TTMI, RCAT, HUT
（多くは現時点で赤字だが、売上成長と株価モメンタムを基準に選定。**この点は記事内で必ず明示する**）

### 記事化の方針（決定済み）

- 1日1銘柄ずつ、上記15銘柄を順番にローテーションして深掘り記事にする（15日で1周）。
- 記事内容：**直近決算・最近のニュース・今後の展望**の3本立て。
  - 直近決算：Woodstockの`get_fundamentals`で取得済みのデータを流用（Stage Cのキャッシュから、追加API不要）
  - 最近のニュース：Alpha Vantageのニュース機能（米国株のためカバレッジ良好）
  - 今後の展望：決算内の予想値フィールド（`FSales`/`FOP`/`FNP`/`FEPS`等）＋ニュースをもとに生成
- ローテーションの周回位置は状態ファイル（例：`output/rotation_state.json`）で管理し、`main.py`が
  日々1銘柄ずつ順番に取り出す想定。

### 要検討事項（未決定）

- Stage C（Woodstock決算チェック）の再実行頻度：目安は週1〜2週に1回。頻度・実行方法（都度Claude呼び出し
  か`/schedule`によるcloud定期実行か）は未確定。
- 15銘柄を一周した後の扱い（同じ15銘柄で再周回するか、次回Stage C実行時に総入れ替えするか）は未確定。

---

## 2. 海外株データ・ニュース（方針決定）

### 役割分担

- **Woodstock（接続済み）**：注文執行・保有ポジション・株価/指標データ寄り。売買や口座管理が中心で、決算の深掘りや値動きの背景分析には不向き。
- **Alpha Vantage（接続済み）**：株価に加え、**決算・SEC提出書類・企業ニュース**を取得可能。note記事の一次情報として使いやすく、Woodstockの補完役として採用。

### ニュースソースの方針

- **Alpha Vantageのニュース機能**を基本のニュース取得元とする。
- 重大な経済指標（CPI等）については、WSJなどの解説記事に頼らず、**FRB・BLSの発表文そのもの**を直接参照する。
  - 理由：解説記事は一次発表の後追いになるため、速報性・正確性の観点で一次情報を優先する（「一次情報の原則」）。
- **Reuters/APについては直接利用を見送り確定**（2026-08-24）。Reuters Connect・AP News APIはいずれも
  企業向け営業窓口経由の契約が必要で料金非公開のエンタープライズ向けサービスであり、個人運営の
  本ツールで使える自己登録型の無料/安価プランは存在しないため。一次情報はFRB公式発表、
  ニュース全般はAlpha Vantageの集約ニュースでカバーする方針に統一した。

### 実装済み（2026-08-24）

- [src/fetchers/fed_press_releases.py](src/fetchers/fed_press_releases.py)：FRB公式サイトの
  金融政策プレスリリースRSSフィード（`federalreserve.gov/feeds/press_monetary.xml`）から
  FOMC声明・議事要旨等を取得。**動作確認済み**（200 OK、BLSと違いボット対策による拒否は無い）。
  標準ライブラリの`xml.etree.ElementTree`でパースでき、追加ライブラリ不要。
  - 前回実行時からの新着を`output/fed_press_release_state.json`で検知し、
    `article_builder.py`の「FRB最新発表」セクションで新着があれば強調表示する。

---

## 3. 経済指標データ（方針決定：FRED / BLS / Trading Economicsの使い分け）

日々提供したい指標：

- アメリカのコア指数（S&P500, NASDAQ, ダウ平均などの終値・騰落率）
- CPI（消費者物価指数）の推移
- 雇用統計など、その他マクロ指標

### データソースの役割分担（優先順位付き）

| ソース | 役割 | 参照タイミング |
|---|---|---|
| **FRED**（セントルイス連銀） | 基本情報の吸い上げ元。CPI（[CPIAUCSL](https://fred.stlouisfed.org/series/CPIAUCSL)等）をはじめ、食品・衣料・住居・燃料・交通運賃など幅広い品目の消費者物価データを無料で取得（CSV/JSON）。過去数値との比較（前年同月比・前回比）テーブルのベースデータとして使用。 | 日次の基本情報収集（常時） |
| **BLS**（米労働省統計局） | CPI・失業率・賃金など労働市場・物価データの一次情報源。FREDはBLSを含む84万系列以上を集約した二次集約元であるのに対し、BLSは自らのデータを直接扱う元データ。速報性・系列の細かさで優位。 | 指標の**速報が出た当日**に情報を吸い上げ、実績値と予想・前回値を比較する結果テーブルを作成 |
| **Trading Economics** | CPI、コアインフレ率、GDPデフレーター、マネーサプライ、貿易収支など各国の経済指標を横断的に取得可能。米国以外も含む国際比較向き。 | 上記の結果が出た後、または**世界規模で影響の大きいニュース**が出たときの補完的な参照先 |

### 運用フロー（想定）

1. **平常時**：FREDから基本データを吸い上げ、過去（1年前・前回）との比較テーブルを日次で更新
2. **指標発表日**：BLSから速報値を取得し、予想値・前回値との比較結果テーブルを作成
3. **重大ニュース時**：Trading Economicsで国際的な影響・他国比較を補完的に確認

### 要確認事項

- 「予想値（コンセンサス予想）」はFRED・BLSには基本的に存在しない（両者とも実績データが中心）。予想値との比較テーブルを作る場合、**予想値の取得元をTrading Economics等の別ソースにするか要検討**。現状`TRADINGECONOMICS_API_KEY`は未設定・未実装。
- FRED・BLSは`.env`設定・疎通確認済み。Trading Economics APIの登録・料金体系（無料枠の有無）は未確認。

### 実装済み（2026-08-24）

- [src/fetchers/fred_indicators.py](src/fetchers/fred_indicators.py)：SP500・NASDAQCOM・DJIA（日次、前営業日比）、
  CPIAUCSL・CPILFESL・UNRATE・PAYEMS（月次、前月比・前年同月比）を取得。**動作確認済み**。
  - 前年同月比は「12個前のレコード」ではなく**日付そのもの（年-1・同月）で照合**する実装にしている。
    政府機関閉鎖等で欠測月があると配列位置がずれるため（実際にBLS側で2025年10月のCPIが
    "Data unavailable due to the 2025 lapse in appropriations" として欠測していることを確認した）。
- [src/fetchers/bls_indicators.py](src/fetchers/bls_indicators.py)：CUSR0000SA0（CPI）・LNS14000000（失業率）・
  CES0000000001（雇用者数）を取得。**動作確認済み、FRED側の値と完全一致することを確認**。
  - **注意**：BLS API v2はクエリパラメータのGETでは`registrationkey`が認識されないことがあり、
    本実装は**POST（JSON body）**でリクエストしている（実機で再現・修正済み）。
  - `forecast_value`フィールドは常に`None`（Trading Economics等の予想値ソースが未実装のため）。

---

## 4. note投稿フォーマット（方針決定・実装済み）

### 構成方針

有料エリアへの誘導を意識し、**無料エリアで信頼できる情報をきちんと出しつつ、有料エリア直前で
「本日の米国株ピック」の際立った数字だけをチラ見せして銘柄名を伏せる**構成にした。

**無料エリア**
1. 本日の海外市場サマリー（S&P500・NASDAQ・ダウ平均、FRED）
2. 経済指標トピック（CPI・コアCPI・失業率・雇用統計の前月比/前年同月比テーブル、FRED基本値＋BLS発表日は速報優先）
3. 本日の日本株ピックアップ（J-Quantsスクリーニング結果。**未実装**：全銘柄フルスキャン未実施のためプレースホルダー）

**有料エリアへの「チラ見せ」**（無料エリア末尾）
- 「対象1,056銘柄→最終15銘柄」という絞り込みの厳しさを提示
- 銘柄名は伏せ、**時価総額・売上成長率・株価上昇率**という際立った数字だけ先出し
- 「15銘柄を2週間かけて1日1つ深掘り」という継続購読の理由を明示

**有料エリア本体**
- 本日の米国株ピック：銘柄名＋直近決算＋最新ニュース＋展望（フル情報）
- ドルインデックス・ドル円・クロス円動向（**未実装**：FREDのBroad Dollar Index(DTWEXBGS)とAlpha VantageのFXデータを使用予定）

### 実装上の注意

- note.comには「ここから有料」を示すMarkdown記法が無い（エディタ上でボタン操作するため）。
  出力には`<!-- PAYWALL -->`という目印コメントを入れており、note投稿時にその直前で
  「続きを読むには」ボタンを配置する運用とする。
- 失業率のような「率」の系列は、変化率(%)で表示すると誤解を招く（例: 4.2%→4.1%が「-2.4%」に
  見えてしまう）ため、pt差（ポイント差）で表示するようにしている。

実装: [src/formatters/article_builder.py](src/formatters/article_builder.py)（`output/fred_indicators_*.json`・
`output/bls_indicators_*.json`・`output/us_premium_rotation_candidates.json`を読み込んで下書きMarkdownを組み立てる。
動作確認済み・[サンプル出力例あり](output/article_draft_20260823.md)）

---

## 6. 自動実行の仕組み（方針決定・実装済み）

### 構成

米国株と同様、**完全自動化できる部分**と**Claude経由でしか実行できない部分**を分離した。

| 実行対象 | 頻度 | 方法 | 所要時間 |
|---|---|---|---|
| `src/main.py`（FRED・BLS・米国株ローテーション・記事下書き組み立て） | 毎日 7:00 | Windowsタスクスケジューラ | 数十秒 |
| `src/fetchers/jquants_screener.py`（日本株の全銘柄スクリーニング） | 毎週土曜 8:00 | Windowsタスクスケジューラ | 約14.5時間（Freeプランのレート制限のため） |
| Stage C（米国株候補15銘柄の再選定、Woodstock `get_fundamentals`） | 週1〜2週に1回目安 | **このClaude環境で都度実行**（自動化不可） | 未定 |
| noteへの実際の投稿 | 都度 | **手動**（下書きをコピー＆ペースト、有料エリアの区切りを設定） | - |

### 実装したタスク（2026-08-24, タスクスケジューラに登録済み）

- `SoubaNote_DailyMain`：毎日7:00、`python src/main.py` を実行
- `SoubaNote_WeeklyJPScan`：毎週土曜8:00、`python src/fetchers/jquants_screener.py` を実行
  （土曜の朝に開始することで、約14.5時間かかっても同日中に完了し、日曜・月曜の記事には
  最新のキャッシュが反映される）

`article_builder.py`の日本株ピックアップは、この週次キャッシュ（`output/screening_result_*.csv`の
最新ファイル）を読み込む方式にした（`main.py`には日本株の全銘柄走査を含めない）。

### 未確定事項

- Stage C（米国株の決算チェック）の再実行は自動化されていない。手動でこのClaude環境を呼び出すか、
  `/schedule`スキルでクラウド定期実行を組むかは未確定。
- タスクスケジューラのタスクは「ユーザーログオン時のみ実行」がデフォルト設定。PCの電源が切れている
  時間帯は実行されない点に注意（現状はこれで運用、必要になれば「ログオンしていなくても実行」への
  変更を検討）。

---

## 5. 今後のタスク

### 日本株（J-Quants）
- [x] J-Quants Freeプランの実際のレート制限・取得可能期間を公式ドキュメントで再確認（5req/分、直近12週間〜2年12週間のみ取得可）
- [x] PER抽出ロジックの実装（`株価 ÷ EPS`）→ [src/fetchers/jquants_screener.py](src/fetchers/jquants_screener.py)
- [ ] レンジ閾値（PER15固定 or 13〜17等の幅）の調整
- [ ] J-QuantsプランをFreeのまま進めるか、Light以上にアップグレードするか決定（有料エリアのデータ鮮度に直結）
- [x] `.env`にAPIキーを設定し、疎通確認済み（グロース+スタンダードで2176銘柄取得、財務・株価データとも正常取得を確認。最新株価の日付は実行時点から約3ヶ月前で、想定通りFreeプランの遅延あり）
- [ ] `ScaleCat`の文字列一致ではなく`MktCap`（時価総額、`/v2/equities/bars/daily`に含まれる）の数値閾値で小型株を絞り込むロジックへの変更を検討

### 海外株・ニュース
- [x] Alpacaの直接APIキー（Paper Trading）を取得・.env設定・疎通確認済み（取引用・マーケットデータ用の両エンドポイントで200 OK）
- [x] 有料エリアを「米国成長モメンタム小・中型株 1銘柄/日ローテーション」として設計・15銘柄確定（詳細は「1-3.」参照）
- [x] Alpha VantageのNEWS_SENTIMENTで米国株ニュース取得を確認済み（`limit`パラメータが効かない場合があるためクライアント側で件数を切り詰める実装済み）
- [x] [src/fetchers/alpaca_price_trend.py](src/fetchers/alpaca_price_trend.py)（Stage B：株価トレンド一次スクリーニング、完全自動）と
      [src/formatters/us_stock_rotation.py](src/formatters/us_stock_rotation.py)（日次ローテーション記事下書き生成）を実装・動作確認済み
- [ ] Stage C（Woodstockでの決算チェック・候補15銘柄の確定）の再実行フローを決定（都度Claude呼び出し／`/schedule`定期実行）
- [ ] Reuters/AP系一次情報、FRB・BLS発表文の具体的な取得方法を確定（API経由か、公式サイトの発表ページ参照か）

### 経済指標
- [x] FRED APIキー取得・.env設定・疎通確認済み、CPI系列（CPIAUCSL, CPILFESL）+ コア指数（SP500, NASDAQCOM, DJIA）+ 雇用統計（UNRATE, PAYEMS）を選定
- [x] 前年同月比・前月比の比較ロジック設計・実装（欠測月を考慮し日付照合方式に修正済み）→ [src/fetchers/fred_indicators.py](src/fetchers/fred_indicators.py)
- [x] BLS APIキー登録・.env設定・疎通確認済み（POST方式が必要と判明）→ [src/fetchers/bls_indicators.py](src/fetchers/bls_indicators.py)
- [x] BLSの「発表日判定」ロジックを実装・動作確認済み。BLS公式の発表スケジュールページ・iCalは
      Akamaiのボット対策で自動アクセスが拒否される（403、実機で確認済み。利用規約上も自動取得は禁止のため
      スクレイピングでの回避はしない）ため、**BLS Data APIで「前回実行時からlatest_periodが更新されたか」を
      比較する方式**に変更した。`output/bls_release_state.json`に前回値を保存し、新しい期間のデータが
      増えていれば「本日発表」として扱う → [src/fetchers/bls_indicators.py](src/fetchers/bls_indicators.py)
- [ ] 「予想値（コンセンサス）」は**当面見送り**（Trading Economics APIキーが有料のため取得しない方針に確定）
- [ ] Trading Economics APIの利用は見送り（上記の通り予想値取得を見送ったため対応不要）

### 全体
- [x] note投稿用テンプレートの設計・実装・ユーザー承認済み（詳細は「4. note投稿フォーマット」参照）
      → [src/formatters/article_builder.py](src/formatters/article_builder.py)
- [x] 自動実行の仕組みを実装済み（詳細は「6. 自動実行の仕組み」参照）
