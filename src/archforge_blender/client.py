# SPDX-License-Identifier: GPL-3.0-or-later
"""Nonblocking main-thread client. No persistent threads in Blender."""
import json
import os
import socket
import struct
import time
from pathlib import Path

MAX_FRAME=1024*1024


def default_root():return str(Path(os.environ.get('ARCHFORGE_DATA_DIR',Path(os.environ.get('LOCALAPPDATA',Path.home()/'.local/share'))/'ArchForgeMCP')))


class Connection:
    def __init__(self,root):
        self.root=Path(root);self.sock=None;self.buffer=bytearray();self.out=bytearray();self.pending={};self.sequence=0

    def close(self):
        if self.sock:self.sock.close()
        self.sock=None;self.buffer.clear();self.out.clear();self.pending.clear()

    def request(self,method,params,callback):
        if self.sock is None:
            config=json.loads((self.root/'connection.json').read_text());self.token=config['token']
            self.sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM);self.sock.setblocking(False);self.sock.connect_ex(('127.0.0.1',config['port']))
        self.sequence+=1;rid=str(self.sequence)
        data=json.dumps({'protocol_version':1,'request_id':rid,'token':self.token,'method':method,'params':params},ensure_ascii=True).encode()
        if len(data)>MAX_FRAME:raise RuntimeError('Request exceeds 1 MiB')
        self.out.extend(struct.pack('!I',len(data))+data);self.pending[rid]=(callback,time.monotonic())

    def tick(self):
        if not self.sock:return
        if any(time.monotonic()-start>35 for _,start in self.pending.values()):raise TimeoutError('Runtime response timed out; reconnect to inspect the outcome')
        if self.out:
            try:
                sent=self.sock.send(self.out);del self.out[:sent]
            except BlockingIOError:pass
        # Bounded IO per timer tick.
        for _ in range(4):
            try:part=self.sock.recv(65536)
            except BlockingIOError:break
            if not part:raise ConnectionError('Runtime disconnected')
            self.buffer.extend(part)
            if len(self.buffer)>MAX_FRAME+4:raise RuntimeError('Runtime response exceeds control-message budget')
            if len(part)<65536:break
        while len(self.buffer)>=4:
            size=struct.unpack('!I',self.buffer[:4])[0]
            if not 0<size<=MAX_FRAME:raise RuntimeError('Invalid runtime frame')
            if len(self.buffer)<size+4:break
            data=json.loads(bytes(self.buffer[4:size+4]));del self.buffer[:size+4]
            pending=self.pending.pop(data.get('request_id'),None)
            if pending:pending[0](data)
