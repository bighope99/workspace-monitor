# Workspace Monitor

Claude Code セッション監視ツール。複数の Claude Code セッションで何をやっているか一目で把握できます。

## 機能

- 今日更新されたセッションを自動検出
- プロジェクトごとに最大3セッションを表示
- 各セッションの最初の3つのプロンプトを表示
- 3分間隔でMarkdownファイルを自動更新

## インストール

```bash
pip install -r requirements.txt
```

## 使い方

```bash
python workspace_monitor.py
```

Ctrl+C で停止。

## 環境変数

`.env.local` ファイルまたは環境変数で設定可能:

| 変数名 | 説明 | デフォルト |
|--------|------|-----------|
| `WORKSPACE_MONITOR_INTERVAL` | 更新間隔（秒） | `180` |
| `WORKSPACE_MONITOR_OUTPUT` | 出力ファイルパス | (コード内のパス) |
| `WORKSPACE_MONITOR_CLAUDE_DIR` | .claude ディレクトリ | `~/.claude` |
| `WORKSPACE_MONITOR_MAX_CHARS` | プロンプト最大文字数 | `300` |

## 設定ファイル

`.env.sample` をコピーして `.env.local` を作成:

```bash
cp .env.sample .env.local
```

`.env.local` を編集して設定を変更。

## ライセンス

MIT
