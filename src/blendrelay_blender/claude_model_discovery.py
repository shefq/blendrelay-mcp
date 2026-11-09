# SPDX-License-Identifier: GPL-3.0-or-later
"""Read Claude Code's model catalog without sending a user prompt."""
import json
import subprocess
import tempfile


class ModelDiscoveryError(RuntimeError):
    pass


def _models_from_response(output, request_id):
    for line in output.splitlines():
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if not isinstance(message, dict) or message.get('type') != 'control_response':
            continue
        response = message.get('response')
        if not isinstance(response, dict) or response.get('request_id') != request_id:
            continue
        if response.get('subtype') != 'success':
            raise ModelDiscoveryError('Claude refused model discovery; check Claude sign-in and settings')
        payload = response.get('response')
        entries = payload.get('models') if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            raise ModelDiscoveryError('This Claude CLI did not return a model catalog; update Claude Code')
        models, seen = [], set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            value = entry.get('value')
            if not isinstance(value, str) or not value.strip():
                continue
            value = value.strip()
            if value in seen or value in ('__DEFAULT__', '__CUSTOM__'):
                continue
            seen.add(value)
            name = 'Default' if value == 'default' else entry.get('displayName')
            description = entry.get('description')
            models.append((value, name if isinstance(name, str) and name else value,
                           description if isinstance(description, str) and description else value))
        if not models:
            raise ModelDiscoveryError('Claude returned no selectable models; check Claude sign-in and settings')
        return models
    raise ModelDiscoveryError('Claude did not return model initialization data; update Claude Code or check sign-in')


def discover_models(executable, timeout=20):
    """Use the same initialize response as Agent SDK supportedModels().

    No user message is sent. Isolate discovery from project instructions, hooks,
    tools, and MCP servers, while retaining Claude's user/managed model settings.
    The control-protocol adapter is deliberately bounded and falls back in the UI
    when an older or incompatible CLI cannot supply its model catalog.
    """
    request_id = 'blendrelay-model-discovery'
    request = {'type': 'control_request', 'request_id': request_id,
               'request': {'subtype': 'initialize', 'hooks': {}}}
    command = [str(executable), '--print', '--input-format', 'stream-json',
               '--output-format', 'stream-json', '--verbose', '--no-session-persistence',
               '--strict-mcp-config', '--mcp-config', json.dumps({'mcpServers': {}}),
               '--settings', json.dumps({'disableAllHooks': True}), '--tools', '']
    try:
        # Blender may quit while the CLI still runs; a leftover empty folder is harmless.
        with tempfile.TemporaryDirectory(prefix='blendrelay-models-', ignore_cleanup_errors=True) as directory:
            completed = subprocess.run(
                command, input=json.dumps(request) + '\n', capture_output=True,
                text=True, encoding='utf-8', errors='replace', timeout=timeout,
                cwd=directory, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
    except subprocess.TimeoutExpired as error:
        raise ModelDiscoveryError('Claude model discovery timed out; retry or use CLI default / Custom') from error
    except OSError as error:
        raise ModelDiscoveryError('Could not launch Claude; check the configured CLI path') from error
    if completed.returncode:
        # CLI output can include account/configuration information. Keep it out
        # of the Blender UI and avoid treating arbitrary stdout as model IDs.
        raise ModelDiscoveryError(f'Claude model discovery exited with code {completed.returncode}; '
                                  'check Claude sign-in, CLI version and settings')
    return _models_from_response(completed.stdout, request_id)
