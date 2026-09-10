"""Launch isolated runtime and GUI; never connects to the user's scene."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from archforge_runtime.protocol import Client

project=Path(__file__).resolve().parents[1]
root=project/'.test-output'/('general-live-'+uuid.uuid4().hex);root.mkdir(parents=True)
env=dict(os.environ,PYTHONPATH=str(project/'src'),ARCHFORGE_TEST_ROOT=str(root))
startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
runtime=blender=None
with (root/'runtime.log').open('w') as log,(root/'blender.log').open('w') as blog:
    try:
        runtime=subprocess.Popen([sys.executable,'-m','archforge_runtime.cli','--data-dir',str(root),'serve'],env=env,stdout=log,stderr=log,startupinfo=startup)
        blender=subprocess.Popen([r'C:\Program Files\Blender Foundation\Blender 4.5\blender.exe','--factory-startup','--python',str(project/'tests/blender_general_live.py')],env=env,stdout=blog,stderr=blog,startupinfo=startup)
        client=Client(root,timeout=3)
        until=time.monotonic()+45
        while True:
            try:
                if client.call('blender.sessions'):break
            except (OSError,Exception):pass
            if time.monotonic()>until:raise RuntimeError('Blender connection timed out')
            time.sleep(.25)
        def run(action,arguments=None):
            op=uuid.uuid4().hex
            job=client.call('blender.submit',dict(operation_id=op,action=action,arguments=arguments or {}))
            deadline=time.monotonic()+35
            while job['status'] in ('queued','running'):
                if time.monotonic()>deadline:raise RuntimeError('Job timeout: '+action)
                time.sleep(.15);job=client.call('blender.job',{'operation_id':op})
            assert job['status']=='complete',job
            return job['result']
        scene=run('inspect');assert scene['total']==3
        edit=run('execute',dict(code="bpy.ops.mesh.primitive_monkey_add(location=(0,0,4))\nbpy.context.object.name='Live sculpture'\nresult={'name':bpy.context.object.name}",label='Live sculpture',save_checkpoint=True))
        assert edit['result']['name']=='Live sculpture'
        assert len(run('versions'))==2
        run('restore',{'version_id':edit['before']['version_id']})
        assert run('inspect')['total']==3
        shot=run('screenshot');assert shot['mime_type']=='image/png'
        receipt=dict(ok=True,checks=['live socket','arbitrary code','before/after versions','restore reconnect','viewport screenshot'],root=str(root))
        (root/'result.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt))
    finally:
        if blender:blender.terminate();blender.wait(timeout=10)
        if runtime:runtime.terminate();runtime.wait(timeout=10)
