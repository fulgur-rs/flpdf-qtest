# qtest による flpdf 受け入れテスト基盤 — 設計書

- 日付: 2026-05-13
- 対象リポジトリ: `fulgur-rs/flpdf-qtest` (本リポジトリ、これから作成)
- 関連リポジトリ: `fulgur-rs/flpdf` (被テスト)
- 関連ドキュメント: flpdf 側 `docs/qpdf-compat.md`, `docs/qpdf-compat-decisions.md`

## 1. 全体像とゴール

qpdf 本家の qtest スイートを丸ごとベンダリングした本リポジトリを作り、flpdf-cli を「PATH 上に `qpdf` として置いた状態」で `qtest-driver` に走らせ、合格させたい `.test` を allowlist で宣言・成長させていく受け入れテスト基盤を構築する。

### 既存テストとの役割分担

| レイヤ | 場所 | 目的 |
|---|---|---|
| 単体・統合 | `flpdf` 内 `cargo test` | 各 crate 内部ロジックの正しさ |
| 書き手バイト互換 | `flpdf` 内 `compat_baseline_*` / `compat_matrix_*` | flpdf 出力 PDF を qpdf golden と比較 (出力視点) |
| **受け入れ (qpdf 視点 E2E)** | **本リポジトリ (flpdf-qtest)** | **qpdf ユーザが触る CLI シナリオを flpdf が代行できるか (入力 + CLI 視点)** |
| 意図的差分の宣言 | flpdf 側 `docs/qpdf-compat-decisions.md` + 本リポ allowlist | divergence の文書化 |

### サイズ感

qpdf 11.9.0 時点で 107 個の `.test` ファイル、約 1700 個の fixtures、見積 1500+ runtests。CLI 互換度の低い flpdf 現状から allowlist 0 件で開始し、flpdf 側 `flpdf-9hc` エピック進捗とリンクして増やす。

## 2. リポジトリレイアウト

```
flpdf-qtest/
├── README.md                   # セットアップ、shim 経由実行、allowlist 追加の手順
├── LICENSE.md / NOTICE.md      # qtest=Artistic 2.0 / qpdf fixtures=Apache 2.0
├── vendor/                     # 上流 qpdf からのコピー (改変禁止ゾーン)
│   ├── qtest/                  # qtest-driver, module/, QTC/ (Perl 一式)
│   └── qpdf-qtest/             # .test と fixtures (qpdf/qtest/* 由来)
│       ├── arg-parsing.test
│       ├── deterministic-id.test
│       ├── ...
│       └── qpdf/               # 入力 PDF と期待出力ファイル群
├── shim/
│   ├── qpdf                    # PATH 先頭に置く実体 (sh ラッパ or symlink)
│   ├── zlib-flate              # qtest 一部で使われる場合に備える
│   └── compare-for-test        # 同上
├── allowlist.txt               # PASS 必須エントリ (test:subtest 形式)
├── normalize/
│   └── stderr-rules.sed        # "flpdf:" → "qpdf:" などの出力正規化
├── scripts/
│   ├── run.sh                  # flpdf-cli ビルド → PATH 設定 → qtest-driver → 集計
│   ├── vendor-sync.sh          # qpdf 上流の特定 tag から vendor/ を再同期
│   └── verify-allowlist.py     # 出力解析 → allowlist 不整合があれば exit 1
├── Cargo.toml (optional)       # shim を Rust 製にする場合のみ
└── .github/workflows/
    └── ci.yml                  # push / PR / weekly / workflow_dispatch
```

### 設計判断

- `vendor/` は上流からの**機械的コピー**に限定。flpdf 都合のパッチは入れない。発散箇所は `normalize/` か allowlist 側で吸収する (再同期コストを下げる)。
- `shim/qpdf` の **ファイル名が `qpdf` であること**が肝。`.test` 内 `COMMAND => "qpdf ..."` がここを叩く。
- `vendor-sync.sh` は qpdf の **特定 release tag** を固定参照。upstream HEAD 追跡はせず、tag を bump するときだけ allowlist の妥当性を再検証する運用。

## 3. shim と CLI 互換戦略

### shim/qpdf

最小実装:

```bash
#!/usr/bin/env bash
exec "${FLPDF_CLI_BIN:-flpdf-cli}" "$@"
```

stderr 正規化が必要な場合の派生:

```bash
exec "${FLPDF_CLI_BIN}" "$@" 2> >(sed -f "$NORMALIZE_RULES" 1>&2)
```

### flpdf-cli 側の変更 (段階的)

現状 flpdf-cli は clap サブコマンド型 (`flpdf check input.pdf`) で qpdf 互換 (`qpdf --check input.pdf`) ではない。allowlist を伸ばすには CLI のフラット化が必要。

1. **互換モード切替**: `FLPDF_QPDF_COMPAT=1` env または `--qpdf-compat` フラグで qpdf 風フラットフラグを受け付けるパーサに分岐。shim はこの env を立てて呼び出す。
2. **段階的拡張**: 最初は `arg-parsing` / `deterministic-id` 系の最小サブセットだけ受理し、allowlist 追加に合わせて受け付けるフラグを増やす。フラグ追加は対応する beads タスクで管理。

### 出力正規化の必要箇所

- エラーメッセージプリフィックス `flpdf: ` → `qpdf: ` (`normalize/stderr-rules.sed`)
- バージョン文字列 (`--version`): qpdf 風文字列を吐く専用パス
- `--check` の出力フォーマット: qpdf の文言を模倣 (flpdf-cli 本体側)
- ファイル出力 (PDF バイト列): 正規化不可。allowlist 追加時に **byte-identical で揃える** か、qtest 側で diff 緩和フラグを利用するかをテスト単位で判断

### YAGNI で外すもの

- C API テスト群 (`c-api*.test`): flpdf に C API が無いため恒久 skip。allowlist 不可。
- fuzz / 大規模パフォーマンス系 (`large-files.test` 等): 恒久 skip 候補。

## 4. allowlist のセマンティクス

### フォーマット (`allowlist.txt`)

```
# 1 行 1 エントリ。# 始まりはコメント。
# 形式: <test-stem>[:<subtest-name>]
# subtest 省略時はその .test 内の全 runtest を要求

deterministic-id
arg-parsing:required argument
arg-parsing:required argument with choices
basic-parsing:empty file detection

# 一時的に外す場合は理由コメント
# linearize:hint stream layout   # blocked on flpdf-9hc-21
```

### 判定マトリクス (`scripts/verify-allowlist.py`)

qtest-driver の出力をパースし、各 runtest の PASS/FAIL を allowlist と突き合わせる:

| 状況 | CI 結果 |
|---|---|
| allowlist に載っているエントリが PASS | ✅ 期待通り |
| allowlist に載っているエントリが FAIL | ❌ **CI レッド (regression)** |
| allowlist に載っていないエントリが FAIL | ℹ️ informational (緑のまま) |
| allowlist に載っていないエントリが PASS | ⚠️ **WARN: allowlist 追加候補** をサマリに出力 |
| allowlist に載っているがそもそも実行されなかった | ❌ **CI レッド (typo / 削除検出)** |

最後 2 つが効く: 「想定外に通った」テストは追加を促し、「allowlist に名前があるのに走らなかった」エントリは vendor 同期で名前が変わった等の事故を検出する。

### サマリ成果物

- `qtest-summary.md` を CI artifacts に出力: 「allowlisted PASS x/y, non-allowlisted PASS z/w, regression: 0」
- ローカルでも `scripts/run.sh` 末尾で同じサマリを stdout へ
- 数値の長期トレンドは追わない (golden-matrix で別途追っているため、ここは「壊れていない」ことのみ示す)

### 実装上のメモ

- qtest-driver は `--filter` で .test 単位の絞り込みが可能。allowlist に出てくる .test stem のみを実行すれば CI 時間を節約 (非 allowlist は `--full` 指定時のみ走らせるオプションを持たせる)。
- subtest 名の表記揺れ (同名 subtest が複数定義されているケースあり) は、ファイル内通番を付けて区別する。verify スクリプトで明示エラー化。

## 5. CI 統合: flpdf-qtest 自走モデル

クロスリポジトリ連携は行わない。本リポジトリの CI だけで完結する。

### トリガ

```yaml
on:
  push:         { branches: [main] }
  pull_request: { branches: [main] }
  workflow_dispatch:
    inputs:
      flpdf_ref:
        description: "flpdf branch / tag / SHA (default: main)"
        required: false
        default: main
  schedule:
    - cron: "0 18 * * 1"  # 毎週月曜 18:00 UTC = flpdf main HEAD で回帰検出
```

### ジョブ構成

```yaml
jobs:
  qtest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4                              # flpdf-qtest
      - uses: actions/checkout@v4
        with:
          repository: fulgur-rs/flpdf
          ref: ${{ inputs.flpdf_ref || 'main' }}
          path: flpdf
      - uses: dtolnay/rust-toolchain@stable
      - run: cargo build --release -p flpdf-cli
        working-directory: flpdf
      - run: FLPDF_CLI_BIN=$PWD/flpdf/target/release/flpdf-cli ./scripts/run.sh
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: qtest-results
          path: |
            qtest.log
            qtest-summary.md
```

### 結果の見え方

- 本リポジトリの Actions タブに push / weekly / manual 各 run の緑赤が出る
- 失敗時は GitHub の通知 / メールで watcher に届く
- flpdf 側 PR には qtest 結果は出ない。PR 中に確認したい場合は手動で `workflow_dispatch` を `flpdf_ref=<PR SHA>` で叩く運用
- README に「flpdf 側で大きな CLI 変更をしたら手動 dispatch を推奨」と明記

### トレードオフ

- ✅ PAT / GitHub App 一切不要、認証まわりゼロ
- ✅ flpdf 側 CI 時間に影響しない
- ✅ vendor / allowlist 変更は flpdf-qtest 内 PR で完結
- ⚠️ flpdf PR で「この変更が qtest を壊すか」を即時には見られない → main マージ後に検出する後追いモデル。当面は週次 + 手動キックで十分という判断
- ⚠️ 将来 allowlist が大きく成長して PR-time フィードバックが欲しくなったら、flpdf 側 trigger workflow を**後付け追加**する。今は YAGNI

### ローカル / 手動運用

```bash
# 任意の flpdf SHA で qtest を回したいとき
gh workflow run ci.yml -R fulgur-rs/flpdf-qtest -f flpdf_ref=<SHA>
gh run watch -R fulgur-rs/flpdf-qtest

# ローカル実行
cargo build --release -p flpdf-cli   # flpdf 側で
cd ../flpdf-qtest
FLPDF_CLI_BIN=../flpdf/target/release/flpdf-cli ./scripts/run.sh
```

## 6. ブートストラップ手順とマイルストーン

### Phase 0: リポジトリ立ち上げ (1 PR)

1. `fulgur-rs/flpdf-qtest` リポジトリ新規作成
2. `vendor-sync.sh` を書く: 引数で qpdf tag (例: `v11.9.0`) を取り、tar からダウンロード → `vendor/qtest/` と `vendor/qpdf-qtest/` に必要部分のみ展開
3. 上記スクリプトで `v11.9.0` (現在 PATH に入っている qpdf と同バージョン) を vendor 化、コミット
4. `LICENSE.md` / `NOTICE.md` 整備: qtest=Artistic 2.0、vendored fixtures=Apache 2.0、上流著作権表示を保存

### Phase 1: 空 allowlist で qtest が起動する状態 (1 PR)

1. `shim/qpdf` (sh ラッパ) を書く
2. `scripts/run.sh`: flpdf-cli ビルド → PATH 注入 → qtest-driver 起動 → ログ採取
3. `scripts/verify-allowlist.py`: qtest-driver 出力をパースし allowlist と突き合わせ
4. `allowlist.txt` は空 + コメントのみ。`run.sh` は「allowlist 由来の regression 0、informational fail 大量」で **exit 0** になることを確認
5. CI workflow を追加し、空 allowlist で緑が出ることを確認

### Phase 2: 最初の 1 テスト合格 (1 PR)

候補: `deterministic-id.test` の冒頭サブテストひとつ。理由:

- flpdf-cli はすでに `--static-id` 相当を持つ
- 出力は固定文字列 + 決定論的 ID なので shim 経由でも揃いやすい
- ここが通れば「shim + 互換 CLI + allowlist 判定」のループが回ることが実証できる

このフェーズで `--qpdf-compat` モードか相当の CLI 互換層を flpdf-cli 側に追加 (flpdf 本体側の beads タスクとして切り出す)。

### Phase 3: エピック連動で allowlist 拡張

`flpdf-9hc` 配下の各サブエピック (Filters, Encryption, Linearize, ObjStm ...) が完了するたびに、対応する .test を allowlist に追加する PR を 1 本書く。

- 対応する beads issue ID をコミットメッセージ / PR 本文に書く
- `flpdf-qtest` 側で WARN として上がっていた「想定外 PASS」エントリをそのまま回収するだけになるのが理想 (実装が先、allowlist が後追い)

### README に書くこと

- 何をするリポジトリか / flpdf 本体との関係
- vendor の出所と再同期手順
- allowlist の追加方法とポリシー (「実装で通せるようになってから足す」)
- ローカル実行手順、CI トリガ手段
- 既知の永続 skip 群 (c-api*, large-files など) とその理由

## ライセンス整理

| 構成要素 | 出所 | ライセンス | 取り扱い |
|---|---|---|---|
| `vendor/qtest/` | qpdf 上流 `qtest/` | Artistic 2.0 | 原文ライセンス保存、改変禁止 |
| `vendor/qpdf-qtest/` | qpdf 上流 `qpdf/qtest/` | Apache 2.0 | 原文ライセンス保存、改変禁止 |
| `shim/`, `scripts/`, `allowlist.txt`, `normalize/` | 本リポジトリ独自 | MIT OR Apache-2.0 (flpdf と同条件) | 通常 PR |

`NOTICE.md` で上流著作権表示と本リポジトリのライセンスを明示する。
