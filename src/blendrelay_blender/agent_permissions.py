"""Narrow, reversible permissions needed by headless agent providers."""
import json
from pathlib import Path
import shutil
import sys


BLENDRELAY_MCP_RULE = 'mcp(blendrelay/*)'


def codex_automatic_review_args():
    """Codex selects workspace-write itself; explicit --sandbox is incompatible."""
    return ['--approve-for-me']


def antigravity_settings_path():
    return Path.home() / '.gemini' / 'antigravity-cli' / 'settings.json'


def antigravity_mcp_config_path():
    return Path.home() / '.gemini' / 'config' / 'mcp_config.json'


def default_blendrelay_server():
    executable = shutil.which('blendrelay-mcp')
    if executable:
        return {'command': executable, 'args': ['mcp']}
    uvx = shutil.which('uvx')
    if uvx:
        return {'command': uvx, 'args': ['blendrelay-mcp', 'mcp']}
    # This fallback is mainly useful in development installs where the package is
    # available to the interpreter that loaded the add-on.
    return {'command': sys.executable, 'args': ['-m', 'blendrelay_mcp.cli', 'mcp']}


def write_claude_mcp_config(path):
    """Write an isolated Claude Code MCP config for one BlendRelay run."""
    path = Path(path)
    config = {'mcpServers': {'blendrelay': default_blendrelay_server()}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
    return path


def claude_mcp_permission_args():
    """Approve only BlendRelay's MCP tools in unattended Claude Code runs."""
    return ['--allowedTools', 'mcp__blendrelay__*']


def claude_command(executable, mcp_config, resume_id='', model='', add_dirs=()):
    """Build the documented non-interactive Claude Code invocation."""
    return [
        str(executable),
        '--print',
        '--output-format', 'stream-json',
        '--verbose',
        '--include-partial-messages',
        '--mcp-config', str(mcp_config),
        '--strict-mcp-config',
        *claude_mcp_permission_args(),
        *(part for directory in add_dirs for part in ('--add-dir', str(directory))),
        *(['--resume', resume_id] if resume_id else []),
        *(['--model', model] if model else []),
    ]


def ensure_antigravity_mcp_server(path=None):
    """Register BlendRelay in Antigravity while preserving unrelated servers."""
    path = Path(path) if path else antigravity_mcp_config_path()
    if path.exists():
        try:
            config = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            raise RuntimeError(f'Could not read Antigravity MCP configuration: {error}') from error
        if not isinstance(config, dict):
            raise RuntimeError('Antigravity mcp_config.json must contain a JSON object')
    else:
        config = {}
    servers = config.setdefault('mcpServers', {})
    if not isinstance(servers, dict):
        raise RuntimeError('Antigravity mcpServers setting must be a JSON object')
    if isinstance(servers.get('blendrelay'), dict):
        return False, path
    servers['blendrelay'] = default_blendrelay_server()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)
    return True, path


def antigravity_mcp_is_allowed(path=None):
    path = Path(path) if path else antigravity_settings_path()
    if not path.exists():
        return False
    try:
        settings = json.loads(path.read_text(encoding='utf-8'))
        permissions = settings.get('permissions', {})
        allowed = permissions.get('allow', [])
        blocked = permissions.get('deny', []) + permissions.get('ask', [])
        has_rule = BLENDRELAY_MCP_RULE in allowed
        is_blocked = BLENDRELAY_MCP_RULE in blocked or 'mcp(*)' in blocked
        return has_rule and not is_blocked
    except (OSError, ValueError, TypeError):
        return False


def ensure_antigravity_mcp_permission(path=None):
    """Allow only the BlendRelay MCP server after explicit add-on opt-in."""
    path = Path(path) if path else antigravity_settings_path()
    if path.exists():
        try:
            settings = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            raise RuntimeError(f'Could not read Antigravity settings: {error}') from error
        if not isinstance(settings, dict):
            raise RuntimeError('Antigravity settings.json must contain a JSON object')
    else:
        settings = {}
    permissions = settings.setdefault('permissions', {})
    if not isinstance(permissions, dict):
        raise RuntimeError('Antigravity permissions setting must be a JSON object')
    for higher_priority in ('deny', 'ask'):
        rules = permissions.get(higher_priority, [])
        if not isinstance(rules, list):
            raise RuntimeError(f'Antigravity permissions.{higher_priority} must be a list')
        if 'mcp(*)' in rules or BLENDRELAY_MCP_RULE in rules:
            raise RuntimeError(
                f'Antigravity permissions.{higher_priority} overrides the BlendRelay allow rule. '
                f'Remove the conflicting MCP rule in {path}.'
            )
    allowed = permissions.setdefault('allow', [])
    if not isinstance(allowed, list):
        raise RuntimeError('Antigravity permissions.allow must be a list')
    if BLENDRELAY_MCP_RULE in allowed:
        return False, path
    allowed.append(BLENDRELAY_MCP_RULE)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(settings, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)
    return True, path


def agent_failure(exit_code, message='', log_text='', blender_errors=(), expected_edit=False, successful_edits=0):
    """Return a failure reason when a CLI incorrectly exits zero."""
    if blender_errors:
        latest = blender_errors[-1]
        action = latest.get('action', 'operation') if isinstance(latest, dict) else 'operation'
        detail = latest.get('error', '') if isinstance(latest, dict) else str(latest)
        detail = str(detail).strip().splitlines()[-1] if str(detail).strip() else 'unknown Blender error'
        return f'Blender {action} failed: {detail[:180]}'
    combined = f'{message}\n{log_text}'.lower()
    if any(marker in combined for marker in (
        'required the "mcp" permission',
        'headless mode cannot prompt',
        'so it was auto-denied',
    )):
        return 'Antigravity denied the BlendRelay MCP permission in headless mode.'
    if ('"type":"result"' in combined or '"type": "result"' in combined) and any(
        marker in combined for marker in ('"subtype":"error_', '"subtype": "error_')
    ):
        return 'Claude Code reported an unsuccessful result; see the run log for details.'
    if exit_code:
        return f'Agent exited with code {exit_code}.'
    if expected_edit and not successful_edits:
        return 'Agent finished without completing a Blender edit.'
    return None
