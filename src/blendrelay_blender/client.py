# SPDX-License-Identifier: GPL-3.0-or-later
"""Nonblocking main-thread client. No persistent threads in Blender."""
import json
import os
import errno
import select
import socket
import struct
import time
from pathlib import Path

MAX_FRAME = 1024 * 1024


def default_root():
    if os.environ.get('BLENDRELAY_DATA_DIR'):
        return str(Path(os.environ['BLENDRELAY_DATA_DIR']).resolve())
    base = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local/share'))
    return str((base / 'BlendRelayMCP').resolve())


class Connection:
    def __init__(self, root):
        self.root = Path(root)
        self.sock = None
        self.connecting = False
        self.buffer = bytearray()
        self.out = bytearray()
        self.pending = {}
        self.sequence = 0

    def close(self):
        if self.sock:
            self.sock.close()
        self.sock = None
        self.connecting = False
        self.buffer.clear()
        self.out.clear()
        self.pending.clear()

    def request(self, method, params, callback):
        if self.sock is None:
            try:
                config = json.loads((self.root / 'connection.json').read_text())
                self.token = config['token']
                port = int(config['port'])
            except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                raise ConnectionError('BlendRelay runtime is unavailable. Start it, then click Connect / Refresh.')
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setblocking(False)
            status = self.sock.connect_ex(('127.0.0.1', port))
            if status not in (0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, 10035, 10036):
                self.close()
                raise ConnectionError('BlendRelay runtime rejected the connection. Click Connect / Refresh after it is ready.')
            self.connecting = status != 0
        self.sequence += 1
        rid = str(self.sequence)
        data = json.dumps({'protocol_version': 1, 'request_id': rid, 'token': self.token, 'method': method, 'params': params}, ensure_ascii=True).encode()
        if len(data) > MAX_FRAME:
            raise RuntimeError('Request exceeds 1 MiB')
        self.out.extend(struct.pack('!I', len(data)) + data)
        self.pending[rid] = (callback, time.monotonic())

    def tick(self):
        if not self.sock:
            return
        if any(time.monotonic() - start > 35 for _, start in self.pending.values()):
            raise TimeoutError('Runtime response timed out; reconnect to inspect the outcome')
        if self.connecting:
            _, writable, failed = select.select([], [self.sock], [self.sock], 0)
            if failed or writable:
                error = self.sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if error:
                    raise ConnectionError('BlendRelay runtime is unavailable. Click Connect / Refresh after it is ready.')
                self.connecting = False
            else:
                return
        if self.out:
            try:
                sent = self.sock.send(self.out)
                del self.out[:sent]
            except BlockingIOError:
                pass
            except OSError as e:
                raise ConnectionError('Lost connection to the BlendRelay runtime.') from e
        # Bounded IO per timer tick.
        for _ in range(4):
            try:
                part = self.sock.recv(65536)
            except BlockingIOError:
                break
            if not part:
                raise ConnectionError('Runtime disconnected')
            self.buffer.extend(part)
            if len(self.buffer) > MAX_FRAME + 4:
                raise RuntimeError('Runtime response exceeds control-message budget')
            if len(part) < 65536:
                break
        while len(self.buffer) >= 4:
            size = struct.unpack('!I', self.buffer[:4])[0]
            if not 0 < size <= MAX_FRAME:
                raise RuntimeError('Invalid runtime frame')
            if len(self.buffer) < size + 4:
                break
            data = json.loads(bytes(self.buffer[4:size + 4]))
            del self.buffer[:size + 4]
            pending = self.pending.pop(data.get('request_id'), None)
            if pending:
                pending[0](data)
