"""Exports immutable model snapshots. Runs Blender separately from live editing."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile
from archforge_domain.model import digest, validate, DomainError
from archforge_domain.geometry import plan_svg


def find_blender(explicit=None):
    if explicit and Path(explicit).is_file():return str(Path(explicit).resolve())
    found=shutil.which('blender')
    if found:return found
    if os.name=='nt':
        paths=sorted(Path('C:/Program Files/Blender Foundation').glob('Blender */blender.exe'),reverse=True)
        if paths:return str(paths[0])
    raise DomainError('BLENDER_NOT_FOUND','Specify the Blender executable with --blender')


def export_project(model,output,format='blend',blender=None):
    validate(model)
    path=Path(output).resolve();path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():raise DomainError('OUTPUT_EXISTS','Choose a new output file; existing files are preserved')
    if format=='json':path.write_text(json.dumps(model,indent=2),encoding='utf-8')
    elif format=='svg':path.write_text(plan_svg(model),encoding='utf-8')
    elif format=='glb':
        from archforge_domain.glb import encode_glb
        path.write_bytes(encode_glb(model))
    elif format=='archforge':
        with zipfile.ZipFile(path,'x',zipfile.ZIP_DEFLATED) as z:
            z.writestr('manifest.json',json.dumps({'format':'archforge.project','schema_version':'1.0.0','project_id':model['project_id'],'revision':model['revision'],'model_hash':digest(model),'contents':'semantic snapshot; procedural geometry regenerates; unmanaged objects not included'}))
            z.writestr('model.json',json.dumps(model,indent=2));z.writestr('plan.svg',plan_svg(model))
    elif format in ('blend','png'):
        with tempfile.TemporaryDirectory(prefix='archforge-export-') as tmp:
            source=Path(tmp)/'model.json';source.write_text(json.dumps(model))
            script=Path(__file__).with_name('blender_worker.py')
            result=subprocess.run([find_blender(blender),'--background','--factory-startup','--disable-autoexec','--python',str(script),'--',str(source),str(path),format],capture_output=True,text=True,timeout=240,
                                  creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode or not path.is_file():raise DomainError('EXPORT_FAILED',(result.stderr or result.stdout)[-1800:])
    else:raise DomainError('UNSUPPORTED_FORMAT',format)
    return {'path':str(path),'format':format,'revision':model['revision'],'bytes':path.stat().st_size}
