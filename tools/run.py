"""Run either entry point directly from a checkout without installing packages."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
# Propagate source discovery when the gateway launches its companion runtime.
import os
os.environ['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'src')+os.pathsep+os.environ.get('PYTHONPATH','')
mode=sys.argv.pop(1) if len(sys.argv)>1 else 'runtime'
if mode=='mcp':
    from archforge_mcp.server import main
else:
    from archforge_runtime.cli import main
main()
