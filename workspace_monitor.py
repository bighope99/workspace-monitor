#!/usr/bin/env python3
"""
Workspace Monitor - Claude Code セッション監視ツール

複数の Claude Code セッションで何をやっているか一目で把握するための監視プログラム。
今日更新されたセッションを監視し、各セッションの最初の3つのプロンプトを表示する。
"""

import json
import logging
import os
import tempfile
import shutil
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import time
from dotenv import load_dotenv

# .env.local を読み込み（環境変数を設定セクションより前に読む必要がある）
_env_file = Path(__file__).parent / ".env.local"
if _env_file.exists():
    load_dotenv(_env_file)

# ============================================================
# 設定
# ============================================================
CLAUDE_DIR = Path(os.environ.get('WORKSPACE_MONITOR_CLAUDE_DIR', str(Path.home() / ".claude")))
PROJECTS_DIR = CLAUDE_DIR / "projects"
HISTORY_FILE = CLAUDE_DIR / "history.jsonl"
OUTPUT_FILE = Path(os.environ.get('WORKSPACE_MONITOR_OUTPUT', r"C:\Users\nakamura taiki\Desktop\システム開発部\オブスメモ\10_Projects\JS\active_chat.md"))
MAX_PROMPTS_PER_SESSION = 3
MAX_SESSIONS_PER_PROJECT = 3
POLL_INTERVAL = int(os.environ.get('WORKSPACE_MONITOR_INTERVAL', '180'))
MAX_PROMPT_CHARS = int(os.environ.get('WORKSPACE_MONITOR_MAX_CHARS', '300'))

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================
# データクラス
# ============================================================
@dataclass
class SessionInfo:
    """セッション情報を保持"""
    session_id: str
    project_path: str
    last_updated: datetime
    prompts: List[str] = field(default_factory=list)


# ============================================================
# キャッシュ
# ============================================================
class SessionCache:
    """セッションプロンプトのキャッシュ"""

    def __init__(self):
        self._cache: Dict[str, Tuple[float, List[str]]] = {}

    def get_prompts(self, session_file: Path, max_prompts: int) -> List[str]:
        """キャッシュからプロンプトを取得、なければファイルから読み込み"""
        session_id = session_file.stem

        try:
            current_mtime = session_file.stat().st_mtime
        except OSError:
            return []

        if session_id in self._cache:
            cached_mtime, cached_prompts = self._cache[session_id]
            if cached_mtime == current_mtime:
                return cached_prompts

        prompts = extract_prompts_from_session(session_file, max_prompts)
        self._cache[session_id] = (current_mtime, prompts)
        return prompts

    def clear(self):
        """キャッシュをクリア"""
        self._cache.clear()


# グローバルキャッシュインスタンス
_session_cache = SessionCache()


# ============================================================
# JSONLパース関数
# ============================================================
def parse_user_message(line: str) -> Optional[str]:
    """
    JSONLの1行をパースし、ユーザープロンプトを抽出

    条件:
    - type == "user"
    - isMeta != True
    - message.role == "user"
    - message.content が文字列
    - システムタグを含まない
    """
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None

    # ユーザーメッセージかチェック
    if data.get("type") != "user":
        return None

    # メタデータ（/clear等のコマンド）をスキップ
    if data.get("isMeta", False):
        return None

    # message フィールドを取得
    message = data.get("message")
    if not message or not isinstance(message, dict):
        return None

    # role が user かチェック
    if message.get("role") != "user":
        return None

    # content を取得
    content = message.get("content")

    # 文字列でない場合（ツール結果など）はスキップ
    if not isinstance(content, str):
        return None

    # 空の内容はスキップ
    content = content.strip()
    if not content:
        return None

    # システムタグを含むプロンプトをスキップ
    skip_tags = ['<command-name>', '<local-command-stdout>', '<system-reminder>', '<task-notification>', '<task-id>', '<output-file>']
    for tag in skip_tags:
        if tag in content:
            return None

    # 閉じられていないタグをチェック（マークダウン崩れ防止）
    import re
    open_tags = re.findall(r'<([a-zA-Z][a-zA-Z0-9-]*)(?:\s[^>]*)?>(?!</)', content)
    close_tags = re.findall(r'</([a-zA-Z][a-zA-Z0-9-]*)>', content)
    if len(open_tags) != len(close_tags):
        return None

    # 改行を空白に置換して1行にする
    content = ' '.join(content.split())

    # 文字数制限
    if len(content) > MAX_PROMPT_CHARS:
        content = content[:MAX_PROMPT_CHARS] + '...'

    return content


def extract_prompts_from_session(session_file: Path, max_prompts: int = 3) -> List[str]:
    """
    セッションファイルから最初のN個のユーザープロンプトを抽出
    """
    prompts = []

    try:
        with session_file.open('r', encoding='utf-8') as f:
            for line in f:
                if len(prompts) >= max_prompts:
                    break

                prompt = parse_user_message(line.strip())
                if prompt:
                    prompts.append(prompt)
    except (OSError, IOError) as e:
        logger.warning(f"Failed to read {session_file}: {e}")

    return prompts


# ============================================================
# セッション取得関数
# ============================================================
def project_path_to_dir_name(project_path: str) -> str:
    """プロジェクトパスをディレクトリ名に変換"""
    # C:\Users\nakamura taiki\Documents\_support_item
    # -> C--Users-nakamura-taiki-Documents--support-item
    # 注: Claude CLI は ':' と '\\' と ' ' と '_' をすべてハイフンに置換する
    normalized = project_path.replace(':', '-').replace('\\', '-').replace('/', '-').replace(' ', '-').replace('_', '-')
    return normalized


def get_today_sessions() -> Dict[str, List[SessionInfo]]:
    """
    history.jsonl から今日更新されたセッション一覧を取得
    プロジェクトごとに最大3セッション（新しい順）を返す
    """
    # セッションID -> セッション情報（最新のタイムスタンプを保持）
    all_sessions: Dict[str, SessionInfo] = {}
    today = date.today()

    if not HISTORY_FILE.exists():
        logger.warning(f"History file not found: {HISTORY_FILE}")
        return {}

    try:
        with HISTORY_FILE.open('r', encoding='utf-8') as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                except json.JSONDecodeError:
                    continue

                # 必須フィールドチェック
                timestamp_ms = data.get("timestamp")
                project = data.get("project")
                session_id = data.get("sessionId")

                if not all([timestamp_ms, project, session_id]):
                    continue

                # タイムスタンプを datetime に変換
                try:
                    dt = datetime.fromtimestamp(timestamp_ms / 1000)
                except (ValueError, TypeError):
                    continue

                # 今日のセッションかチェック
                if dt.date() != today:
                    continue

                # セッションIDでユニーク化（最新のタイムスタンプを保持）
                if session_id not in all_sessions or dt > all_sessions[session_id].last_updated:
                    all_sessions[session_id] = SessionInfo(
                        session_id=session_id,
                        project_path=project,
                        last_updated=dt,
                        prompts=[]
                    )
    except (OSError, IOError) as e:
        logger.error(f"Failed to read history file: {e}")

    # プロジェクトごとにグループ化
    project_sessions: Dict[str, List[SessionInfo]] = {}
    for session in all_sessions.values():
        project = session.project_path
        if project not in project_sessions:
            project_sessions[project] = []
        project_sessions[project].append(session)

    # 各プロジェクトのセッションを新しい順にソートし、最大3件に制限
    for project in project_sessions:
        project_sessions[project].sort(key=lambda s: s.last_updated, reverse=True)
        project_sessions[project] = project_sessions[project][:MAX_SESSIONS_PER_PROJECT]

    return project_sessions


def load_session_prompts(sessions: Dict[str, List[SessionInfo]]) -> Dict[str, List[SessionInfo]]:
    """
    各セッションのプロンプトを読み込む
    """
    for project_path, session_list in sessions.items():
        for session in session_list:
            # プロジェクトディレクトリ名を生成
            dir_name = project_path_to_dir_name(session.project_path)

            # セッションファイルを探す
            session_file = PROJECTS_DIR / dir_name / f"{session.session_id}.jsonl"

            if session_file.exists():
                session.prompts = _session_cache.get_prompts(session_file, MAX_PROMPTS_PER_SESSION)
            else:
                # ディレクトリ名のバリエーションを試す
                for project_dir in PROJECTS_DIR.iterdir():
                    if project_dir.is_dir():
                        alt_session_file = project_dir / f"{session.session_id}.jsonl"
                        if alt_session_file.exists():
                            session.prompts = _session_cache.get_prompts(alt_session_file, MAX_PROMPTS_PER_SESSION)
                            break

    return sessions


# ============================================================
# 出力関数
# ============================================================
def format_markdown(sessions: Dict[str, List[SessionInfo]]) -> str:
    """
    セッション情報をMarkdown形式にフォーマット
    """
    lines = [
        "# 🔄 作業状況",
        "",
        f"*最終更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
        "",
    ]

    if not sessions:
        lines.append("アクティブなセッションはありません。")
        return "\n".join(lines)

    # プロジェクトパス名でソート（アルファベット順、大文字小文字無視）
    sorted_projects = sorted(
        sessions.items(),
        key=lambda item: item[0].lower()
    )

    for project_path, session_list in sorted_projects:
        lines.append("---")
        lines.append("")
        lines.append(f"## {project_path}")

        for session in session_list:
            lines.append(f"**最終更新**: {session.last_updated.strftime('%H:%M')}")
            lines.append("**プロンプト履歴**:")

            if session.prompts:
                for i, prompt in enumerate(session.prompts, 1):
                    lines.append(f"{i}. {prompt}")
            else:
                lines.append("1. （なし）")

            lines.append("")

    return "\n".join(lines)


def write_output(content: str):
    """
    Markdown内容をファイルに書き込み（atomic write）
    """
    # 出力ディレクトリが存在することを確認
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    # 一時ファイルに書き込んでからリネーム（atomic）
    fd, temp_path = tempfile.mkstemp(suffix='.md', dir=OUTPUT_FILE.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
        shutil.move(temp_path, OUTPUT_FILE)
        logger.info(f"Updated: {OUTPUT_FILE}")
    except Exception as e:
        logger.error(f"Failed to write output: {e}")
        # 一時ファイルをクリーンアップ
        try:
            os.unlink(temp_path)
        except OSError:
            pass


# ============================================================
# メイン更新関数
# ============================================================
def update_workspace_status():
    """
    作業状況を更新するメイン関数
    """
    logger.info("Updating workspace status...")

    # 今日のセッションを取得
    sessions = get_today_sessions()
    logger.info(f"Found {len(sessions)} session(s) for today")

    # 各セッションのプロンプトを読み込む
    sessions = load_session_prompts(sessions)

    # Markdownを生成して出力
    content = format_markdown(sessions)
    write_output(content)


# ============================================================
# メインループ
# ============================================================
def main():
    """エントリーポイント"""
    logger.info("Starting Workspace Monitor...")
    logger.info(f"Polling interval: {POLL_INTERVAL} seconds")
    logger.info(f"Output: {OUTPUT_FILE}")

    if not CLAUDE_DIR.exists():
        logger.error(f"Claude directory not found: {CLAUDE_DIR}")
        return 1

    try:
        while True:
            update_workspace_status()
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        logger.info("Stopped.")

    return 0


if __name__ == "__main__":
    exit(main())
