"""Framed local RPC. No Blender dependency; no Python object deserialization."""
import json
import os
import socket
import struct
from pathlib import Path
from blendrelay_runtime.errors import BlendRelayError, DomainError, canonical

MAX_FRAME = 1024 * 1024


def data_dir():
    if os.environ.get('BLENDRELAY_DATA_DIR'):
        return Path(os.environ['BLENDRELAY_DATA_DIR']).resolve()
    base = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local/share'))
    return (base / 'BlendRelayMCP').resolve()


def frame(message):
    data = canonical(message).encode('utf-8')
    if len(data) > MAX_FRAME:
        raise BlendRelayError('RESOURCE_LIMIT', 'Control message exceeds 1 MiB')
    return struct.pack('!I', len(data)) + data


def receive(sock):
    def exact(count):
        result = bytearray()
        while len(result) < count:
            part = sock.recv(count - len(result))
            if not part:
                raise EOFError('Connection closed')
            result.extend(part)
        return bytes(result)
    size = struct.unpack('!I', exact(4))[0]
    if not 0 < size <= MAX_FRAME:
        raise BlendRelayError('RESOURCE_LIMIT', 'Invalid frame size')
    return json.loads(exact(size))


class Client:
    def __init__(self, root=None, timeout=30):
        self.root = Path(root or data_dir())
        self.timeout = timeout

    def call(self, method, params=None):
        try:
            config = json.loads((self.root / 'connection.json').read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            raise BlendRelayError('RUNTIME_OFFLINE', 'Start blendrelay-mcp runtime first')
        with socket.create_connection(('127.0.0.1', config['port']), timeout=self.timeout) as sock:
            sock.sendall(frame({'protocol_version': 1, 'token': config['token'], 'request_id': 'sync', 'method': method, 'params': params or {}}))
            result = receive(sock)
        if 'error' in result:
            err = result['error']
            raise BlendRelayError(err['code'], err['message'], err.get('entity_ids', []), **err.get('details', {}))
        return result['result']
