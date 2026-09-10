import json
import struct
import unittest
from archforge_domain.model import house
from archforge_domain.glb import encode_glb


class GlbTests(unittest.TestCase):
    def test_buffers_axes_and_entity_metadata(self):
        model=house();data=encode_glb(model)
        magic,version,size=struct.unpack_from('<4sII',data)
        self.assertEqual((magic,version,size),(b'glTF',2,len(data)))
        length,kind=struct.unpack_from('<I4s',data,12);self.assertEqual(kind,b'JSON')
        document=json.loads(data[20:20+length]);binary_length,kind=struct.unpack_from('<I4s',data,20+length)
        self.assertEqual(kind,b'BIN\0');self.assertEqual(binary_length,document['buffers'][0]['byteLength'])
        body=data[28+length:]
        for view in document['bufferViews']:
            self.assertEqual(view['byteOffset']%4,0)
            self.assertLessEqual(view['byteOffset']+view['byteLength'],len(body))
        for mesh in document['meshes']:
            primitive=mesh['primitives'][0];position=document['accessors'][primitive['attributes']['POSITION']]
            index=document['accessors'][primitive['indices']];view=document['bufferViews'][index['bufferView']]
            values=struct.unpack_from('<'+'I'*index['count'],body,view['byteOffset'])
            self.assertLess(max(values),position['count'])
            self.assertEqual(index['count']%3,0)
        # Metres retained; Blender Y becomes negative glTF Z, height becomes Y.
        mins=[document['accessors'][m['primitives'][0]['attributes']['POSITION']]['min'] for m in document['meshes']]
        maxs=[document['accessors'][m['primitives'][0]['attributes']['POSITION']]['max'] for m in document['meshes']]
        self.assertLess(min(v[2] for v in mins),-10)
        self.assertGreater(max(v[1] for v in maxs),3)
        self.assertTrue(all(n['extras']['archforge_entity_id'] for n in document['nodes']))
