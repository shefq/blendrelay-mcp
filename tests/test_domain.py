import copy
import unittest
from archforge_domain.model import house,from_rectangles,entities,validate,DomainError,parse_brief,digest
from archforge_domain.operations import propose,prompt_operations
from archforge_domain.geometry import build_specs


class DomainTests(unittest.TestCase):
    def setUp(self):self.model=house()

    def test_shared_wall(self):
        m=from_rectangles([{'name':'A','x':0,'y':0,'width':5,'depth':8},{'name':'B','x':5,'y':0,'width':5,'depth':8}],furnish=False)
        shared=[w for w in m['walls'] if len(w['space_ids'])==2]
        self.assertEqual(len(shared),1)
        edge=shared[0]['edge_id']
        self.assertEqual({s['direction'] for r in m['spaces'] for s in r['boundary'] if s['edge_id']==edge},{'forward','reverse'})

    def test_fractional_template_edges_are_not_duplicated(self):
        from archforge_domain.model import wall_points
        segments=[tuple(sorted(tuple(p) for p in wall_points(self.model,w))) for w in self.model['walls']]
        self.assertEqual(len(segments),len(set(segments)))
        self.assertEqual(len(self.model['doors']),8)

    def test_resize_anchor_and_immutability(self):
        m=self.model;before=digest(m);o=m['openings'][0];centre=o['offset_m']+o['width_m']/2
        candidate,affected,_=propose(m,[{'kind':'opening.resize','target_id':o['id'],'parameters':{'width_m':o['width_m']+.1}}])
        after=entities(candidate)[o['id']]
        self.assertAlmostEqual(after['offset_m']+after['width_m']/2,centre)
        self.assertEqual(digest(m),before);self.assertIn(o['host_wall_id'],affected)

    def test_invalid_opening_rejected(self):
        o=self.model['openings'][0]
        with self.assertRaises(DomainError):propose(self.model,[{'kind':'opening.resize','target_id':o['id'],'parameters':{'width_m':100}}])

    def test_protected_host_blocks_indirect_change(self):
        o=self.model['openings'][0];self.model['protected_ids']=[o['host_wall_id']]
        with self.assertRaises(DomainError) as c:propose(self.model,[{'kind':'opening.resize','target_id':o['id'],'parameters':{'width_m':o['width_m']-.1}}])
        self.assertEqual(c.exception.code,'PROTECTED_ENTITY')

    def test_protected_roof_blocks_wall_height_dependency(self):
        self.model['protected_ids']=['roof']
        with self.assertRaises(DomainError) as c:propose(self.model,[{'kind':'wall.set_height','target_id':self.model['walls'][0]['id'],'parameters':{'height_m':3.2}}])
        self.assertEqual(c.exception.code,'PROTECTED_ENTITY')

    def test_nonfinite_and_unknown_fields(self):
        with self.assertRaises(DomainError):house(width=float('nan'))
        with self.assertRaises(DomainError):propose(self.model,[{'kind':'wall.set_height','target_id':self.model['walls'][0]['id'],'parameters':{'height_m':3,'execute':'bad'}}])

    def test_generators_deterministic_and_unaffected_hashes(self):
        first=build_specs(self.model);self.assertEqual(first,build_specs(self.model))
        window=self.model['windows'][0]
        c,_,_=propose(self.model,[{'kind':'material.assign','target_id':window['id'],'parameters':{'role':'frame','material_id':'mat-black'}}])
        old={(s['entity_id'],s['role']):s['hash'] for s in first};new={(s['entity_id'],s['role']):s['hash'] for s in build_specs(c)}
        for key in old:
            if key[0]!=window['id']:self.assertEqual(old[key],new[key])

    def test_native_prompt(self):
        w=self.model['windows'][0]
        ops=prompt_operations(self.model,'Make this window 20 cm wider and use a black frame',[w['id']])
        self.assertEqual(len(ops),2);propose(self.model,ops)

    def test_text_layout_has_explicit_assumptions(self):
        m=parse_brief('a house');self.assertTrue(m['assumptions']);self.assertEqual(len(m['spaces']),7)

    def test_mesh_faces_use_valid_indices(self):
        for s in build_specs(self.model):
            self.assertTrue(all(0<=i<len(s['vertices']) for f in s['faces'] for i in f))
            self.assertTrue(all(len(set(f))==len(f) for f in s['faces']))

    def test_assets_remain_inside_room(self):
        a=self.model['assets'][0]
        with self.assertRaises(DomainError):propose(self.model,[{'kind':'asset.move','target_id':a['id'],'parameters':{'position_m':[99,99,0]}}])

    def test_rectangles_cannot_overlap(self):
        with self.assertRaises(DomainError):from_rectangles([{'name':'A','x':0,'y':0,'width':5,'depth':5},{'name':'B','x':2,'y':2,'width':5,'depth':5}])
