import tempfile
import unittest
from pathlib import Path
from archforge_runtime.service import Service


class ImagePlanTests(unittest.TestCase):
    def test_calibrated_pixel_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            image=Path(tmp)/'plan.png';image.write_bytes(b'\x89PNG\r\n\x1a\n')
            s=Service(Path(tmp)/'data',[tmp])
            try:
                source=s.rpc_source_register(str(image))
                result=s.rpc_project_from_plan(source['source_id'],[{'name':'Left','x':0,'y':0,'width':500,'depth':800},{'name':'Right','x':500,'y':0,'width':500,'depth':800}],100,1.0,furnish=False)
                model=s.rpc_project_get(result['project_id'])
                self.assertEqual(max(v['xy_m'][0] for v in model['vertices']),10)
                self.assertEqual(max(v['xy_m'][1] for v in model['vertices']),8)
                self.assertEqual(len([w for w in model['walls'] if not w['exterior']]),1)
            finally:s.store.close()
