# ChemOmni 開発計画

- 状態: 設計確定（2026-11-04、設計インタビュー Q1〜Q47 の結論に基づく）
- 対象: MVP（M0〜M4）。Phase 2 と対象外は §2 に明記
- 読み方: §1〜§3 で全体像、§4〜§9 が実装の契約、§10 が進め方、§12 が決定ログ

---

## 1. ビジョンと位置づけ

**ChemOmni** は、ケミストが慣れ親しんだ ChemDraw ライクな構造エディタ（Ketcher）を主役に、LLM チャットを副操縦士として一体化した化合物構造ワークスペースである。

- **エディタが主、チャットが補助。** AI の出力（生成した構造、編集結果）は「新しいタブ」としてエディタ側に返る。チャットは文字で説明し、構造はタブと構造カードで見せる
- **ローカル優先・OSS（MIT）・プロバイダ非依存。** localhost で単一ユーザーが動かす。LLM は OpenAI で始め、OpenAI 互換サーバー（Ollama / vLLM / LM Studio）やその他プロバイダへ差し替えられる
- **軽く・速く。** 初回ロード以外は体感を損なわない。§9 の性能予算を設計上の制約として扱う

### 先行事例との違い

| 既存 | 形 | ChemOmni との違い |
|---|---|---|
| live-ketcher-mcp, MolTalk, ChemCrow | MCP サーバー / エージェントライブラリ。チャットが主でエディタは道具 | ChemOmni はエディタが主。タブが AI の出力面 |
| Claude Science の Ketcher コネクタ（EPAM ブログ） | ホスト型ワークベンチの一機能 | ローカル・OSS・プロバイダ非依存 |
| ChemDraw / Marvin / ChemDoodle | 商用。AI 機能はエディタ外か無し | OSS エディタ + AI を同一画面で |

「ChemDraw 級のエディタ + チャット + タブ」を一体化したものは 2026-11 時点で存在しない。

---

## 2. スコープ

### MVP（M0〜M4）

- Ketcher エディタ + ユーザー管理のタブ（新規・閉じる・名前変更・複製・インポート・エクスポート・分子ごとの分割）
- LLM チャット（SSE ストリーミング、ツール呼び出しの可視化）
- AI 機能（優先順）: 物性計算 → 構造の説明・Q&A → 自然言語での構造編集 → 化合物生成（アナログ提案）→ 名前→構造
- AI の結果は既定で新規タブ。指示があればアクティブタブを置換（Undo 可）
- SQLite への自動保存と起動時復元
- ライブ物性バー（編集に追従）
- Docker（単一コンテナ）での配布

### Phase 2（MVP 後の候補）

MCP クライアント（外部ツールの接続）/ 原子インデックス指定の編集ツール / アルゴリズム生成（R 基列挙、バイオアイソスター置換ルール、BRICS、mmpdb）/ 類似・部分構造検索（PubChem / ChEMBL）/ 反応式タブ / i18n / ダークテーマ / タブ並べ替え・結合 / 会話一覧・履歴要約 / クライアント側描画（RDKit.js / OpenChemLib）/ Ketcher 複数インスタンスの LRU 管理 / Anthropic・Gemini アダプタ

### 対象外

生成モデル（REINVENT 等）/ 合成経路提案 / pKa・logD / マルチユーザー・認証 / 公開ホスティング

---

## 3. アーキテクチャ

```mermaid
flowchart LR
  subgraph Browser["ブラウザ (React 19 + Vite)"]
    TB[TabBar]
    KE[KetcherEditor<br/>共有1インスタンス]
    PB[PropertyBar]
    CP[ChatPanel]
    ST[(Zustand store)]
    SSE[SSE client]
    TB --> ST
    KE --> ST
    PB --> ST
    CP --> SSE
    SSE --> ST
  end
  subgraph Backend["FastAPI (Python 3.12)"]
    API[REST: workspace / tabs / conversations / chem]
    CHAT[POST /api/chat → SSE]
    LOOP[Agent loop]
    REG[Tool registry]
    CHEM[chem: RDKit<br/>props / depict / edit / generate / names]
    LLM[LLMProvider<br/>OpenAI Chat Completions / Fake]
    DB[(SQLite)]
    CHAT --> LOOP --> REG --> CHEM
    LOOP --> LLM
    API --> DB
    REG --> DB
  end
  ST <--> API
  SSE <--> CHAT
  LLM --> OAI[(OpenAI API /<br/>OpenAI 互換サーバー)]
  CHEM -.任意.-> OPSIN[OPSIN (Java)]
  CHEM -.fallback.-> PUBCHEM[PubChem PUG REST]
```

### 責務の分担

| 層 | 持つもの | 持たないもの |
|---|---|---|
| ブラウザ | Ketcher の表示と操作、タブ UI、チャット UI、作業状態のスナップショット作成、SSE イベントの適用 | 化学計算、LLM 呼び出し |
| バックエンド | 正本データ（SQLite）、化学計算（RDKit）、LLM ループ、ツール実行、タブのレコード作成 | KET の解釈（Molfile / SMILES は Ketcher が出したものを受け取る） |

### チャット 1 ターンの流れ

1. ユーザーが送信。フロントは **スナップショット**（アクティブタブの KET / Molfile V3000 / SMILES / 選択原子 / `revision`、全タブの要約、`@タブ名` で明示したタブ）を本文と一緒に `POST /api/chat` へ送る
2. バックエンドはユーザーメッセージを保存し、システムプロンプト + 会話履歴 + スナップショットから LLM メッセージを組み立ててループを回す
3. テキストは `text.delta` で流す。ツール呼び出しはサーバー内で実行し、`tool.start` / `tool.result` を流す。タブを作るツールは DB にレコードを作ってから `tab.create` を流す
4. フロントはイベントを順に適用する（文字の追記、構造カードの描画、タブの追加・置換、ハイライト）
5. `message.done` で終了。ユーザー編集との衝突（`revision` 不一致）は `tab.replace_rejected` で新規タブにフォールバック

---

## 4. データモデル（SQLModel / SQLite）

```text
workspace     id, user_id, name, created_at, updated_at
tab           id, workspace_id, name, kind('molecules'), source('user'|'ai'),
              origin_message_id?, content_format('ket'|'mol'), content(text),
              smiles_cache(json), revision(int), position(int),
              is_deleted(bool), deleted_at?, created_at, updated_at
conversation  id, workspace_id, title, created_at, updated_at
message       id, conversation_id, role('user'|'assistant'|'tool'), content(json blocks),
              model?, usage(json)?, stop_reason?, created_at
tool_call     id, message_id, call_id, name, arguments(json), result(json),
              ok(bool), duration_ms, created_at
artifact      id, message_id, type('structure'|'structure_set'|'diff'|'table'),
              payload(json), tab_id?, created_at
```

- `user_id` は MVP では固定値 `"local"`。マルチユーザー化のときに意味を持たせる
- `content_format`: AI が作った直後は `mol`（RDKit が座標付きで出す Molfile V3000）、Ketcher が一度でも保存したら `ket` が正本
- `smiles_cache`: タブ一覧の要約やサムネイルに使う派生値。保存時に更新
- `revision`: 楽観ロック。`PATCH /api/tabs/{id}` は `revision` 一致時のみ更新して +1
- 削除はソフト削除。「最近閉じたタブ」から復元できる
- `message.content` はブロック配列（`{"type":"text"}` / `{"type":"artifact_ref","artifact_id":...}` / `{"type":"tool_use"}` / `{"type":"tool_result"}`）
- SQLite は WAL モード。MVP では単一ワークスペース・単一会話を既定で作る

---

## 5. API と SSE イベント

### REST（JSON）

| メソッド | パス | 用途 |
|---|---|---|
| GET | `/api/workspace` | 既定ワークスペース（タブ一覧 + 現在の会話）を返す |
| POST | `/api/tabs` | 新規タブ（空 or 初期内容） |
| GET | `/api/tabs/{id}` | タブ本体 |
| PATCH | `/api/tabs/{id}` | 名前・内容の更新。`revision` 必須（不一致は 409） |
| DELETE | `/api/tabs/{id}` | ソフト削除 |
| POST | `/api/tabs/{id}/restore` | 復元 |
| POST | `/api/tabs/{id}/duplicate` | 複製 |
| POST | `/api/tabs/{id}/split` | 分子ごとに新規タブへ分割 |
| POST | `/api/tabs/import` | ファイル（.mol / .sdf / .ket / .smi）または SMILES テキストから新規タブ |
| GET | `/api/tabs/{id}/export?format=ket\|mol\|sdf\|smi` | テキスト形式のエクスポート（画像は Ketcher 側で生成） |
| POST | `/api/conversations` | 新しいチャット |
| GET | `/api/conversations/{id}/messages` | 履歴（アーティファクト込み） |
| POST | `/api/chat` | チャット送信。レスポンスは `text/event-stream` |
| POST | `/api/chem/properties` | ライブ物性バー用（SMILES 配列 → 物性） |
| POST | `/api/chem/depict` | SVG 描画（canonical SMILES キーで LRU キャッシュ） |
| POST | `/api/chem/validate` | SMILES 検証 |
| GET | `/api/health`, `/api/config` | 死活、モデル名などの公開設定 |

### `POST /api/chat` のリクエスト

```json
{
  "conversation_id": "conv_…",
  "content": "このタブの物性を教えて",
  "snapshot": {
    "active_tab": {
      "id": "tab_…", "revision": 6,
      "ket": "…", "molfile": "…V3000…", "smiles": ["CC(=O)Oc1ccccc1C(=O)O"],
      "selection": { "atoms": [3, 4, 5], "bonds": [2, 3] }
    },
    "tabs": [ { "id": "tab_…", "name": "Aspirin", "smiles": ["…"], "n_molecules": 1 } ],
    "mentioned_tab_ids": []
  }
}
```

### SSE イベント（`event:` 名と `data:` の JSON）

| event | data | フロントの処理 |
|---|---|---|
| `message.start` | `{message_id, conversation_id}` | アシスタントの吹き出しを作る |
| `text.delta` | `{delta}` | 文字を追記 |
| `tool.start` | `{call_id, name, arguments}` | 折りたたみチップ「物性を計算中…」 |
| `tool.result` | `{call_id, ok, summary, duration_ms}` | チップを完了表示に |
| `artifact` | `{artifact: {id, type, payload, tab_id?}}` | 構造カード / グリッド / before-after を描画 |
| `tab.create` | `{tab, activate}` | ストアにタブを追加、`activate` なら切替 |
| `tab.replace` | `{tab_id, content_format, content, revision}` | Ketcher に `setMolecule`、トースト「元に戻す」 |
| `tab.replace_rejected` | `{tab_id, reason, fallback_tab_id}` | 通知「編集中だったので新規タブに出しました」 |
| `highlight` | `{tab_id, atoms, bonds}` | Ketcher の選択で強調 |
| `message.done` | `{message_id, usage, stop_reason}` | ストリーム終了（`end_turn` / `max_tools` / `error`） |
| `error` | `{code, message}` | エラー表示 |

実装: サーバーは `sse_starlette` の `EventSourceResponse`。クライアントは `fetch` + `ReadableStream` で `event:` / `data:` 行を自前パースする（`EventSource` は POST を扱えないため）。

---

## 6. ツール一覧（LLM に見せる道具）

すべて `@tool` デコレータで登録し、Pydantic モデルから JSON Schema を自動生成する。ツール数は少なく保ち、各ツールの出力に「次に何ができるか」を短く含める。

| # | ツール | 入力 | 出力 | 副作用 |
|---|---|---|---|---|
| 1 | `compute_properties` | `smiles: list[str]`（≤ 50） | 行ごとに §7 の物性セット | なし |
| 2 | `validate_structures` | `smiles: list[str]` | `{input, ok, canonical_smiles, errors[], warnings[]}` | なし |
| 3 | `edit_structure` | `parent_smiles, new_smiles, description, target: "new_tab" \| "replace_active"` | `{ok, before:{smiles,svg}, after:{smiles,svg,props}, tab_id, errors[]}` | 新規タブ作成、または `revision` 一致時に置換 |
| 4 | `propose_structures` | `parent_smiles, candidates: [{smiles, rationale}], title` | `{accepted:[{smiles, rationale, props, similarity, svg}], rejected:[{smiles, reason}], tab_id}` | グリッド配置した 1 タブを作成 |
| 5 | `open_in_new_tab` | `smiles: list[str], title` | `{tab_id, molecules:[{smiles, svg}]}` | 新規タブ作成 |
| 6 | `name_to_structure` | `name` | `{smiles, source: "opsin" \| "pubchem", svg}` | なし（表示やタブ化は LLM が 5 を呼ぶ） |
| 7 | `similarity` / `substructure_search` | `smiles_a, smiles_b` / `smiles[], smarts` | `{tanimoto}` / `[{smiles, match, atoms[]}]` | なし |

- 不正な SMILES はツールがエラー文（RDKit のメッセージを要約）を返し、LLM が最大 2 回まで自己修正する
- SVG はツールの戻り値に含めず、バックエンドがアーティファクト生成時に付与する（LLM のコンテキストを汚さない）
- 3 の座標整合: `rdFMCS.FindMCS([parent, new])` で共通部分を取り、`GenerateDepictionMatching2DStructure` で親の座標に合わせる。失敗時は `Compute2DCoords`
- 4 のグリッド: 分子ごとに 2D 座標を生成して正規化し、列 × 行のオフセットをかけて `CombineMols` → 1 つの Molfile V3000。Ketcher は座標をそのまま表示する
- 4 の受け入れ条件: parse / sanitize 成功、canonical SMILES で重複除去、親と同一は除外。フィルタ（Lipinski 等）は除外せず表に出す

---

## 7. 化学計算と描画

### 標準物性セット（`compute_properties` とライブ物性バー）

MW、精密質量、分子式、重原子数、cLogP（Crippen）、TPSA、HBD / HBA、回転結合、環数 / 芳香環数、Fsp3、形式電荷、立体中心（定義 / 未定義）、Lipinski 違反数、Veber、QED、SA score（`rdkit.Contrib.SA_Score`）、PAINS / Brenk アラート（`FilterCatalog`）、InChIKey、canonical SMILES。すべて RDKit 2026.09 同梱で追加依存なし。ライブ物性バーは MW / cLogP / TPSA / HBD / HBA / 回転結合 / Lipinski の 7 項目のみ表示する。

### 描画

- チャット内の構造カードとタブのサムネイルは RDKit `MolDraw2DSVG`。`depict(smiles) -> svg` の 1 関数に閉じ、canonical SMILES をキーに LRU キャッシュする
- 画像エクスポート（PNG / SVG）は Ketcher 内蔵の `generateImage()` を使う（見た目がケミスト好みのため）。Ketcher の「画像として保存」ボタンも残す
- M1 で実物を見て、描画をエディタと揃えたければ `epam.indigo` の Python レンダラーに差し替える（決定 Q47）

### 名前→構造

`py2opsin`（Java が必要なので任意依存、`extras = ["opsin"]`）→ 失敗時は PubChem PUG REST（約 5 req/s の制限を守る）。

---

## 8. LLM 層

### プロバイダ抽象

```python
class LLMEvent: TextDelta(delta) | ToolCall(id, name, arguments_json) | Done(usage, stop_reason)

class LLMProvider(Protocol):
    async def stream(self, messages: list[Message], tools: list[ToolSpec],
                     *, model: str, reasoning_effort: str | None) -> AsyncIterator[LLMEvent]: ...
```

- `OpenAIChatProvider`: Chat Completions の `stream=True`。tool_call のチャンクは `index` ごとに蓄積し、`finish_reason` で確定する。`base_url` を変えるだけで OpenAI 互換サーバーが使える
- `FakeProvider`: YAML / JSON の台本からイベントを再生する。テストと E2E はこれで API を使わない
- Anthropic / Gemini は後からアダプタを足す（LiteLLM への置換も可能な粒度）

### エージェントループ

- 1 ターンのツール呼び出し上限 8 回。超えたら `stop_reason = "max_tools"` で「ここまでやった」と報告
- タイムアウト: LLM 60 秒、ツール 30 秒
- ツール呼び出しの入出力は `tool_call` テーブルに保存する（デバッグと記事の素材）
- コンテキストの組み立て（トークン予算は設定、既定 24k）:
  1. システムプロンプト（ペルソナと規則）
  2. `<workspace>`: タブ一覧（id・名前・SMILES 要約）
  3. `<active_tab>`: SMILES 一覧 + Molfile V3000。原子数 200 超または分子数 20 超なら SMILES のみ
  4. `<selection>`: 選択原子・結合のインデックス（Molfile の番号と一致させる）
  5. 直近の会話（予算内で古いものから落とす）

### システムプロンプトの要点

- メディシナルケミストの相棒。構造は必ずツールで検証してから提示し、SMILES を地の文に裸で書かない（アーティファクト経由）
- ユーザーの言語に追従する
- 単位・慣習を明記する（MW は g/mol、cLogP は Crippen）
- 編集・生成の結果は既定で新規タブ。「置き換えて」と言われたときだけ `target = "replace_active"`
- 範囲外（合成経路など）は「現時点では対応していない」と答える

### モデル設定（`.env`）

```dotenv
OPENAI_API_KEY=…
OPENAI_BASE_URL=            # 空なら公式。Ollama なら http://localhost:11434/v1
CHEMOMNI_MODEL=gpt-6.1-sol
CHEMOMNI_REASONING_EFFORT=low
CHEMOMNI_PROVIDER=openai    # openai | fake
CHEMOMNI_DB=./data/chemomni.sqlite
CHEMOMNI_CONTEXT_BUDGET=24000
```

暫定既定は `gpt-6.1-sol` + 低 effort。M1 完了時に `gpt-6.1-terra` と同じ 10 問程度で速度と質を比べて決める。

---

## 9. フロントエンド構成と Ketcher 統合

### 技術

Vite + React 19 + TypeScript、Tailwind + shadcn/ui、Zustand、react-markdown + remark-gfm、react-resizable-panels、lucide-react。UI は英語、ライトテーマのみ。

### 画面

左: タブバー → Ketcher エディタ → ライブ物性バー。右: チャット履歴 → 入力欄。左右は 60 / 40 の可変分割。

### ディレクトリ

```text
frontend/src/
  App.tsx
  components/   TabBar, EditorHost, PropertyBar, ChatPanel, MessageList, Composer,
                StructureCard, StructureGrid, DiffCard, ToolCallChip, Toast
  ketcher/      KetcherEditor.tsx（React.lazy）, bridge.ts（get/set/subscribe の薄い層）
  store/        workspace.ts（tabs, activeTabId, selection, loading）, chat.ts
  lib/          api.ts, sse.ts（パーサ）, types.ts（SSE / API の型）
e2e/            Playwright
```

### Ketcher（3.18.0 に固定）の使い方

- `ketcher-react` の `<Editor>` に `staticResourcesUrl`、`structServiceProvider`（`ketcher-standalone` の `StandaloneStructServiceProvider`、`binaryWasm` エントリ）、`onInit(ketcher)`、`disableMacromoleculesEditor` を渡す。`import 'ketcher-react/dist/index.css'` が必須
- `window.ketcher` は使わず、`onInit` で受け取ったインスタンスを `bridge.ts` のモジュール変数に保持する
- 読み書き: `getKet()` / `getMolfile('v3000')` / `getSmiles()` / `setMolecule(str)` / `addFragment(str)` / `generateImage()`
- 購読: `ketcher.editor.subscribe('change', …)` → 500 ms デバウンスで `getKet()` → `PATCH /api/tabs/{id}`。同時に `getSmiles()` → 300 ms デバウンスで `/api/chem/properties`（物性バー）。`'selectionChange'` → ストアの `selection`
- **タブ切替（共有 1 インスタンス）**: `loading = true` → 現タブを `getKet()` で保存 → `setMolecule(次の KET)` → `editor.clearHistory()` → `loading = false`。`loading` 中は `change` を無視する（`setMolecule` 自体が履歴に積まれ `change` を発火するため）
- **AI の置換**: `tab.replace` を受けたら同じ手順で `setMolecule`。`clearHistory()` は呼ばない（Undo で戻せるようにする）。トーストに「元に戻す」
- 既知の調整: Vite では `@mui` / `@emotion` を手動チャンクに分ける、`global = window` のシム、`.wasm` を静的配信する。EPAM の `example/vite.config.js` を参照
- 制約: 履歴は 32 段、macro モードでは `getSmiles` / `getMolfile` が例外（無効化済み）

### ロードと配信

- Ketcher 一式は `React.lazy` で分割し、アプリ本体を先に描く（スケルトン表示）
- 本番ビルドで `.wasm` と JS を事前圧縮（brotli / gzip）し、ハッシュ付きファイル名 + `Cache-Control: immutable` で配信。FastAPI 側は `Accept-Encoding` を見て事前圧縮ファイルを返す小さな静的ハンドラを持つ（11.8 MB の `.wasm` をオンザフライ圧縮しない）
- 開発時は Vite dev server（5173）が `/api` を FastAPI（8000）へ proxy

---

## 10. 性能予算（目標値。MVP では CI ゲートにしない）

| 項目 | 目標 |
|---|---|
| 初回ロード（キャッシュなし、ローカル） | 転送 ≤ 4.5 MB gz、エディタ操作可能まで ≤ 4 秒 |
| 2 回目以降のロード | ≤ 1 秒 |
| タブ切替 | ≤ 150 ms |
| ライブ物性バー更新 | 入力停止から ≤ 300 ms（デバウンス込み） |
| チャット送信 → 最初のトークン表示 | LLM 待ち以外のオーバーヘッド ≤ 100 ms |
| 物性計算（10 分子） | ≤ 50 ms |
| バックエンド起動 | ≤ 2 秒 |

手段: 遅延ロード、事前圧縮 + immutable キャッシュ、SVG / 物性の LRU キャッシュ、楽観的なタブ作成（サーバー応答前に UI に出す）、SQLite WAL、uvicorn + uvloop、依存の最小化、Ketcher は共有 1 インスタンス。M4 で Playwright により実測し表にする。

---

## 11. 開発プロセス

### リポジトリ

- `https://github.com/aurio9923/chemomni`、MIT、monorepo
- M0 までは `main` に直接、以降は feature ブランチ + PR。CI（lint + テスト）が緑になってからマージ
- Claude 側の push 権限は claude.ai の GitHub 連携で付与する（未了）。付与できない間は Windows 側フォルダ経由で受け渡し、WSL2 から push する
- 作業は WSL2 のファイルシステム上（`/mnt/c` は避ける）

```text
chemomni/
  README.md  LICENSE  docker-compose.yml  Dockerfile
  docs/PLAN.md
  .claude/skills/        取り込んだスキルと chemomni-dev
  .github/workflows/ci.yml
  backend/
    pyproject.toml       uv 管理。Python 3.12
    chemomni/
      main.py  config.py  db.py  models.py  sse.py
      api/     workspace.py tabs.py conversations.py chat.py chem.py
      chem/    props.py depict.py edit.py generate.py names.py layout.py
      llm/     provider.py openai_provider.py fake_provider.py loop.py context.py prompts.py
      tools/   registry.py chem_tools.py tab_tools.py
    tests/
  frontend/              pnpm。Node 22 LTS
```

### テスト

- バックエンド: pytest。化学ツールは決定的なゴールデンテスト、ループは `FakeProvider` の台本で tool call を再生、API は `httpx`
- フロント: Vitest（ストア、SSE パーサ）
- E2E: Playwright。`CHEMOMNI_PROVIDER=fake` で起動した本物のスタックに対し、MVP ではスモーク 2〜3 本（タブ作成と復元 / チャットで物性表 / 編集で新規タブ）
- CI: GitHub Actions（backend: ruff + pytest、frontend: tsc + vitest、e2e はスモークのみ）

### スキル

取り込み前に `skill-inspector` で検査し、結果に警告があれば提示して判断を仰ぐ。置き場所はリポジトリ内 `.claude/skills/`。

| スキル | 出所 | 用途 |
|---|---|---|
| `rdkit` | K-Dense `scientific-agent-skills` | RDKit の作法、落とし穴、スクリプト |
| `cheminformatics` | AWS Labs `hcls-agent-skills` | 非推奨 API の回避、sanitize の作法 |
| `webapp-testing` | Anthropic `skills` | Playwright の手順 |
| `modern-python` | Trail of Bits `skills` | uv / ruff / pytest の規約 |
| `llm-streaming` | `orchestkit`（SKILL.md のみ写す） | FastAPI + SSE + OpenAI ストリーミングの型 |
| `frontend-design` | Anthropic（既存） | UI の方向付け |
| `chemomni-dev` | 自作 | Ketcher API メモ、ツールレジストリの書き方、プロジェクト規約。開発しながら育てる |

OpenAI SDK の作法（function calling / streaming）は良いスキルが存在しないため、`chemomni-dev` に書き溜める。Ketcher / KET のスキルも存在しない。

### マイルストーン

| M | 目標 | 含む | 完了条件 | 記事 |
|---|---|---|---|---|
| M0 骨組み | 動く土台 | monorepo と CI、Ketcher 表示（遅延ロード、Vite 設定の確定）、タブ CRUD と切替、SQLite 自動保存・復元、左右分割とチャット枠 | リロード後にタブと構造が復元される。初回ロードの実測値を記録 | — |
| M1 チャット + 物性 | 会話が成立する | SSE、`LLMProvider`（OpenAI + Fake）、ツールレジストリとループ、`compute_properties` / `validate_structures` / `similarity` / `substructure_search`、構造カード、ツールチップ、ライブ物性バー、pytest / Vitest / E2E 1 本、モデル比較と描画エンジンの判断 | 「このタブの物性を教えて」で表が出る。「懸念点は？」で説明が返る | 第 1 回「Ketcher + LLM チャットを繋ぐ」 |
| M2 編集 | 言葉で構造を変える | `edit_structure`（SMILES 提案 + 座標整合）、新規タブ / 置換と `revision` 検査、Undo トースト、選択範囲コンテキスト、`highlight` | 「3 位にメチル」で新規タブ。「置き換えて」で置換して Undo で戻る | 第 2 回「自然言語で構造を編集する」 |
| M3 生成 | AI に提案させる | `propose_structures` → グリッドタブ + 物性 / 類似度表、分子ごとのタブ分割、`open_in_new_tab`、`name_to_structure` | 「アナログを 5 個」でグリッドタブと表が出る | 第 3 回「AI にアナログを提案させる」 |
| M4 仕上げ | 配れる形に | import / export（Ketcher の画像出力含む）、単一コンテナ + 事前圧縮配信、README、E2E 3 本、性能実測表 | `docker compose up` で動く。性能予算の実測表がある | 第 4 回「配布と性能」 |

M0 の前に: GitHub 権限の付与、スキルの検査と取り込み。

---

## 12. 決定ログ

| ID | 項目 | 決定 |
|---|---|---|
| Q1 | 目的 | ブログ題材 + 自分用 MVP。OSS 品質で作る |
| Q2 | 利用形態 | localhost 単一ユーザー、認証なし。`user_id` 列は持つ |
| Q3 | アーキテクチャ | React/TS + FastAPI。Pure Python UI は不採用 |
| Q4 | エディタ | Ketcher standalone（Apache-2.0）。商用品は除外 |
| Q5 | LLM | MVP は OpenAI。他プロバイダ・ローカル LLM へ移行可能に |
| Q6 | AI ツール | 内部関数 + レジストリ。MCP は Phase 2 |
| Q7 | AI 機能の順 | 物性 → 説明 → 編集 → 生成 → 名前→構造。検索・反応は後 |
| Q8 | タブ | 1 タブ = Ketcher ドキュメント（KET 正本）。生成結果は 1 タブ + 分割 |
| Q9 | AI 編集 | 既定は新規タブ、指示があれば置換（Undo 保証） |
| Q10 | UI 言語 | 英語。i18n は Phase 2 |
| Q11 | スキル | 個人作も検査の上で可。不足は自作 |
| Q12 | リポジトリ | `chemomni`、monorepo、MIT、uv + pnpm |
| Q13 | 環境 | WSL2 で開発、Docker で配布 |
| Q14 | 計画書 | `docs/PLAN.md` |
| Q15 | LLM 抽象 | 自前 `LLMProvider` + OpenAI Chat Completions（`base_url` 可変） |
| Q16 | ブラウザ側操作 | スナップショット入力 + SSE アクション出力。WebSocket なし |
| Q17 | チャット単位 | ワークスペースに 1 本、`@タブ名` で添付 |
| Q18 | コンテキスト | アクティブタブ + 選択 + タブ一覧 + 直近会話。選択は MVP から |
| Q19 | 永続化 | SQLite（SQLModel）、自動保存、起動時復元 |
| Q20 | Ketcher インスタンス | 共有 1 つ。切替で `clearHistory()` |
| Q21 / Q44 | Ketcher エントリ | `binaryWasm`（描画込み）。画像出力は Ketcher 内蔵を使う |
| Q22 | 編集方式 | SMILES 全体提案 + MCS 座標整合。原子指定ツールは Phase 1.5 |
| Q23 | 生成方式 | LLM 提案 → RDKit 検証 → グリッドタブ。アルゴリズム系は Phase 2 |
| Q24 / Q47 | 描画 | チャット・サムネイルは RDKit SVG（1 関数に隔離）。Indigo は後で差し替え可 |
| Q25 | 物性セット | QED / SA score / PAINS・Brenk まで全部 MVP |
| Q26 | タブ操作 | ソフト削除、復元、複製、import / export、分割。並べ替えは Phase 2 |
| Q27 | フロント | Vite + React 19 + Tailwind + shadcn/ui + Zustand |
| Q28 | バックエンド | Python 3.12 + FastAPI + SQLModel + openai + rdkit。KET は解釈しない |
| Q29 | 作業フロー | GitHub をハブ。WSL2 側に clone |
| Q30 | スキル確定 | rdkit / cheminformatics / webapp-testing / modern-python / llm-streaming + 自作 chemomni-dev |
| Q31 | テスト | pytest + Vitest + Playwright スモーク、GitHub Actions |
| Q32 | マイルストーン | M0〜M4、各段が記事 1 本 |
| Q33 | GitHub 運用 | M0 まで main 直接、以降 PR |
| Q34 | モデル | 暫定 `gpt-6.1-sol`。テストは Fake |
| Q35 | ループ方針 | ツール 8 回 / ターン、自己修正 2 回、LLM 60 秒 / ツール 30 秒、監査ログ |
| Q36 | ツール一覧 | §6 の 7 種 |
| Q37 | システムプロンプト | メディシナルケミストの相棒、検証必須、言語追従 |
| Q38 | ワークスペース | 単一。会話テーブルあり、「新しいチャット」のみ |
| Q39 | 衝突 | `revision` 一致時のみ置換、不一致は新規タブ |
| Q40 | UI 詳細 | ライブ物性バー、ツール折りたたみ、ライトテーマ、60/40 |
| Q41 | 配布 | 単一コンテナ |
| Q42 | 計画書構成 | 本ファイルの構成 + Project に決定サマリ |
| Q43 | 軽さ vs らしさ | Ketcher を維持。OCL は Phase 2 の描画用途 |
| Q45 | モデル比較 | M1 で `sol` と `terra` を比較 |
| Q46 | 性能予算 | §10 |

---

## 13. 未決事項と実装上の既定値

### 未決（ユーザー側の作業、または計測後に決める）

- GitHub `aurio9923/chemomni` への Claude の push 権限（claude.ai Settings → Connectors → GitHub）
- スキルの `skill-inspector` 検査結果の確認
- `gpt-6.1-sol` vs `gpt-6.1-terra`（M1 で計測）
- 描画エンジン RDKit vs Indigo（M1 で実物を見て判断）
- `ketcher-standalone` の `binaryWasm` サブパスの正確な import 経路（M0 で確認）

### 実装上の既定値（私が選んだもの。変更は一言で可）

- AI が作ったタブは自動でアクティブにする（複数作ったときは最後のもの）。設定で無効化可
- ライブ物性バーの計算はバックエンドで行う（クライアント側 WASM は持たない）
- 会話履歴のトークン予算は 24k
- 自動保存のデバウンスは 500 ms、物性バーは 300 ms
- 生成の既定件数は 5、上限 20
- タブ名は AI が付ける（`propose_structures` の `title`）。空なら「Untitled n」

### リスクと対策

| リスク | 対策 |
|---|---|
| Ketcher の Vite 統合でつまずく | M0 の最初に潰す。EPAM の example 設定を参照。はまった知見は `chemomni-dev` に残す |
| Ketcher の月次リリースで API が変わる | 3.18.0 に固定。更新は意図的に行う |
| LLM が出す SMILES の品質 | ツールで検証し自己修正。物性表と before/after で人が確認できる形にする |
| 初回ロードが重い | 遅延ロード、事前圧縮、immutable キャッシュ。予算を実測で追う |
| OPSIN の Java 依存 | 任意依存。無ければ PubChem にフォールバック |
| Windows と WSL2 の混在 | リポジトリは WSL2 側。Windows フォルダは受け渡しにだけ使う |

---

## 14. 参考資料

- Ketcher: https://github.com/epam/ketcher （releases、`example/vite.config.js`、`packages/ketcher-core/src/application/editor/editor.types.ts`）
- Ketcher 3.6.0 release notes（複数インスタンス対応）: https://lifescience.opensource.epam.com/ketcher/release-notes/ketcher-3.6.0.html
- RDKit: https://github.com/rdkit/rdkit/releases 、PyPI `rdkit`
- OPSIN: https://github.com/dan2097/opsin 、`py2opsin`
- PubChem PUG REST: https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest
- OpenAI models: https://developers.openai.com/docs/models
- 先行事例: live-ketcher-mcp（https://github.com/MALeitch/live-ketcher-mcp）、MolTalk（https://github.com/DomFico/moltalk）、ChemCrow（https://github.com/ur-whitelab/chemcrow-public）、EPAM ブログ（https://solutionshub.epam.com/blog/post/epam-ketcher-claude-science）
- スキル: https://github.com/anthropics/skills 、https://github.com/K-Dense-AI/scientific-agent-skills 、https://github.com/awslabs/hcls-agent-skills 、https://github.com/trailofbits/skills 、https://github.com/yonatangross/orchestkit
