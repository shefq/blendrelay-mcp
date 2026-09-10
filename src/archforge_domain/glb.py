"""Small glTF 2.0 writer for ArchForge's untextured mesh recipes.

Uses metre units and the glTF right-handed Y-up frame. No Blender installation
is needed for geometry export. Format: registry.khronos.org/glTF/specs/2.0/.
"""
import json
import math
import struct
from .geometry import build_specs


def encode_glb(model):
    document={'asset':{'version':'2.0','generator':'ArchForge MCP 0.1.0'},'scene':0,
              'scenes':[{'nodes':[]}],'nodes':[],'meshes':[],'materials':[],
              'buffers':[],'bufferViews':[],'accessors':[],
              'extras':{'archforge_project_id':model['project_id'],'archforge_revision':model['revision']}}
    binary=bytearray();material_ids={}
    def accessor(values,components=3,integer=False,bounds=False):
        fmt='I' if integer else 'f';packed=struct.pack('<'+fmt*len(values),*values)
        offset=len(binary);binary.extend(packed)
        view=len(document['bufferViews'])
        document['bufferViews'].append({'buffer':0,'byteOffset':offset,'byteLength':len(packed),'target':34963 if integer else 34962})
        item={'bufferView':view,'componentType':5125 if integer else 5126,'count':len(values)//components,'type':'SCALAR' if components==1 else 'VEC3'}
        if bounds:
            rounded=struct.unpack('<'+fmt*len(values),packed)
            item['min']=[min(rounded[i::components]) for i in range(components)]
            item['max']=[max(rounded[i::components]) for i in range(components)]
        result=len(document['accessors']);document['accessors'].append(item);return result
    for spec in build_specs(model):
        material=spec['material'];mid=material['id']
        if mid not in material_ids:
            rgba=material.get('rgba',[.7,.7,.7,1])
            material_ids[mid]=len(document['materials'])
            item={'name':material.get('label',mid),'pbrMetallicRoughness':{'baseColorFactor':rgba,'metallicFactor':0,'roughnessFactor':.45}}
            if rgba[3]<1:item['alphaMode']='BLEND'
            document['materials'].append(item)
        positions=[];normals=[];indices=[]
        for face in spec['faces']:
            points=[[spec['vertices'][i][0],spec['vertices'][i][2],-spec['vertices'][i][1]] for i in face]
            a=[points[1][i]-points[0][i] for i in range(3)];b=[points[2][i]-points[0][i] for i in range(3)]
            normal=[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]
            length=math.sqrt(sum(v*v for v in normal))
            if length<1e-12:continue
            normal=[v/length for v in normal];base=len(positions)//3
            for point in points:positions.extend(point);normals.extend(normal)
            for i in range(1,len(points)-1):indices.extend((base,base+i,base+i+1))
        primitive={'attributes':{'POSITION':accessor(positions,bounds=True),'NORMAL':accessor(normals)},
                   'indices':accessor(indices,1,True),'material':material_ids[mid],'mode':4}
        index=len(document['meshes']);document['meshes'].append({'name':spec['label'],'primitives':[primitive]})
        document['nodes'].append({'mesh':index,'name':spec['label']+' '+spec['role'],
                                  'extras':{'archforge_entity_id':spec['entity_id'],'archforge_part_role':spec['role']}})
        document['scenes'][0]['nodes'].append(index)
    document['buffers']=[{'byteLength':len(binary)}]
    encoded=json.dumps(document,separators=(',',':'),allow_nan=False).encode()
    encoded+=b' '*((-len(encoded))%4);binary.extend(b'\0'*((-len(binary))%4))
    length=12+8+len(encoded)+8+len(binary)
    return struct.pack('<4sII',b'glTF',2,length)+struct.pack('<I4s',len(encoded),b'JSON')+encoded+struct.pack('<I4s',len(binary),b'BIN\0')+binary
