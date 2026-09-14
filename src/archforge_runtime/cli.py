import argparse
import json
import os
import secrets
import socketserver
import subprocess
import sys
from pathlib import Path
from archforge_domain.model import DomainError
from .protocol import data_dir, frame, receive, Client
from .service import Service


def secure_private(path):
    """Restrict runtime secrets to the current Windows account where possible."""
    path=Path(path);os.chmod(path,0o600)
    if os.name!='nt':return
    user=os.environ.get('USERNAME')
    domain=os.environ.get('USERDOMAIN')
    identity=(domain+'\\'+user) if domain and user else user
    if not identity:return
    result=subprocess.run(['icacls',str(path),'/inheritance:r','/grant:r',identity+':(F)'],
                          capture_output=True,text=True,timeout=10,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise OSError('Could not protect runtime credentials with Windows ACLs')


def serve(root,source_roots=()):
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    lockfile=open(root/'runtime.lock','a+b')
    try:
        if os.name=='nt':
            import msvcrt
            lockfile.seek(0);lockfile.write(b'0');lockfile.flush();lockfile.seek(0)
            msvcrt.locking(lockfile.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lockfile,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        lockfile.close();raise SystemExit('An ArchForge runtime already owns this data directory')
    service=Service(root,source_roots);token=secrets.token_urlsafe(32)
    if not service.audit_path.exists():service.audit_path.touch()
    secure_private(service.audit_path)
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(35)
            while True:
                request={}
                try:
                    request=receive(self.request)
                    if not isinstance(request,dict):raise DomainError('INVALID_MESSAGE','Expected an envelope')
                    if not secrets.compare_digest(str(request.get('token','')),token):raise DomainError('UNAUTHORIZED','Invalid session token')
                    if request.get('protocol_version')!=1:raise DomainError('PROTOCOL_VERSION','Expected bridge protocol 1')
                    result={'request_id':request.get('request_id'),'result':service.dispatch(request.get('method',''),request.get('params',{}))}
                    encoded=frame(result)
                except (EOFError,ConnectionError,TimeoutError):return
                except DomainError as e:encoded=frame({'request_id':request.get('request_id') if isinstance(request,dict) else None,'error':e.as_dict()})
                except Exception as e:
                    print(f'ArchForge request error: {type(e).__name__}: {e}',file=sys.stderr)
                    encoded=frame({'request_id':request.get('request_id'),'error':{'code':'INTERNAL_ERROR','message':'Operation failed; see runtime log','details':{},'entity_ids':[]}})
                try:self.request.sendall(encoded)
                except OSError:return
    class Server(socketserver.ThreadingTCPServer):
        daemon_threads=True
        allow_reuse_address=False
    server=Server(('127.0.0.1',0),Handler)
    connection={'product':'ArchForge MCP','protocol_version':1,'port':server.server_address[1],'token':token,'pid':os.getpid()}
    temp=root/'connection.tmp';temp.write_text(json.dumps(connection));secure_private(temp);temp.replace(root/'connection.json')
    print(f'ArchForge MCP runtime ready on loopback port {server.server_address[1]}',file=sys.stderr,flush=True)
    try:server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:pass
    finally:
        server.server_close();service.store.close()
        try:(root/'connection.json').unlink()
        except OSError:pass
        lockfile.close()


def main():
    parser=argparse.ArgumentParser(description='ArchForge MCP local architectural runtime')
    parser.add_argument('--data-dir',default=str(data_dir()))
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('serve');p.add_argument('--allow-source-root',action='append',default=[])
    sub.add_parser('status')
    p=sub.add_parser('create');p.add_argument('--prompt',default='12 x 10 m house with 3 bedrooms');p.add_argument('--output')
    p=sub.add_parser('export');p.add_argument('project_id');p.add_argument('output');p.add_argument('--format',choices=['json','svg','archforge','blend','glb','png'],default='blend');p.add_argument('--blender')
    args=parser.parse_args()
    if args.command=='serve':serve(args.data_dir,args.allow_source_root);return
    client=Client(args.data_dir)
    if args.command=='status':result=client.call('capabilities')
    elif args.command=='create':
        result=client.call('project.create',{'prompt':args.prompt})
        if args.output:Path(args.output).write_text(json.dumps(client.call('project.get',{'project_id':result['project_id']}),indent=2))
    else:
        from .export import export_project
        model=client.call('project.get',{'project_id':args.project_id})
        result=export_project(model,args.output,args.format,args.blender)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
