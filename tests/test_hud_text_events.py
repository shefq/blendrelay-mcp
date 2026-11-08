from types import SimpleNamespace
import unittest
from unittest.mock import patch

try:
    from blendrelay_blender import viewport_hud as hud
except ImportError:
    hud = None


@unittest.skipIf(hud is None, 'Blender Python API is not available')
class HudTextEvents(unittest.TestCase):
    def setUp(self):
        self.saved = dict(hud.HUD_STATE)
        hud.HUD_STATE.update(editor=hud.Editor(), typing=False, text_drag=None, text_region=None)
        self.context = SimpleNamespace(scene=SimpleNamespace(blendrelay_codex_prompt='abcdef'),
                                       window_manager=SimpleNamespace(clipboard=''))
        self.bounds = {'prompt': (0, 0, 100, 70), 'prompt_clear': (85, 48, 14, 16),
                       'prompt_editor': dict(x=0, top=50, width=40, visible=3,
                                             bottom=5, size=13, line_height=16, region=1)}
        self.measure = patch.object(hud, '_prompt_measure', side_effect=lambda text, size: len(text))
        self.measure.start()

    def tearDown(self):
        self.measure.stop()
        hud.HUD_STATE.clear()
        hud.HUD_STATE.update(self.saved)

    def event(self, kind, x=2, y=55, value='PRESS', ctrl=False, shift=False, text='', region=1):
        event = SimpleNamespace(type=kind, value=value, ctrl=ctrl, shift=shift,
                                alt=False, oskey=False, unicode=text, ascii=text)
        return hud._handle_prompt_event(self.context, event, self.bounds, x, y, region)

    def focus(self):
        self.event('LEFTMOUSE')
        self.event('LEFTMOUSE', value='RELEASE')

    def test_click_arrow_and_type_edits_at_clicked_position(self):
        self.focus()
        self.event('RIGHT_ARROW')
        self.event('X', text='X')
        self.assertEqual(self.context.scene.blendrelay_codex_prompt, 'abcXdef')

    def test_drag_select_copy_cut_paste_and_undo(self):
        self.event('LEFTMOUSE', x=1)
        self.event('MOUSEMOVE', x=4, value='NOTHING')
        self.event('LEFTMOUSE', value='RELEASE')
        self.event('C', ctrl=True)
        self.assertEqual(self.context.window_manager.clipboard, 'bcd')
        self.event('X', ctrl=True)
        self.assertEqual(self.context.scene.blendrelay_codex_prompt, 'aef')
        self.context.window_manager.clipboard = '  hi\r\nthere '
        self.event('V', ctrl=True)
        self.assertEqual(self.context.scene.blendrelay_codex_prompt, 'a  hi\nthere ef')
        self.event('Z', ctrl=True)
        self.assertEqual(self.context.scene.blendrelay_codex_prompt, 'aef')

    def test_enter_inserts_newline_ctrl_enter_submits(self):
        self.focus()
        self.event('RET')
        self.assertEqual(self.context.scene.blendrelay_codex_prompt, 'ab\ncdef')
        with patch.object(hud, '_submit_agent_prompt') as submit:
            self.event('RET', ctrl=True)
            submit.assert_called_once_with(self.context)

    def test_click_outside_releases_focus_and_passes_to_hud(self):
        self.focus()
        self.assertIsNone(self.event('LEFTMOUSE', x=110))
        self.assertFalse(hud.HUD_STATE['typing'])

    def test_clear_is_undoable_and_keeps_editor_focus(self):
        self.focus()
        self.event('LEFTMOUSE', x=90)
        self.assertEqual(self.context.scene.blendrelay_codex_prompt, '')
        self.event('Z', ctrl=True)
        self.assertEqual(self.context.scene.blendrelay_codex_prompt, 'abcdef')

    def test_wheel_scrolls_without_changing_text(self):
        text = '\n'.join(str(i) for i in range(20))
        self.context.scene.blendrelay_codex_prompt = text
        self.event('WHEELUPMOUSE')
        self.assertEqual(hud.HUD_STATE['editor'].scroll, 14)
        self.assertEqual(self.context.scene.blendrelay_codex_prompt, text)

    def test_keyboard_in_other_viewport_does_not_edit_focused_prompt(self):
        self.focus()
        self.assertIsNone(self.event('X', text='X', region=2))
        self.assertEqual(self.context.scene.blendrelay_codex_prompt, 'abcdef')


if __name__ == '__main__':
    unittest.main()


@unittest.skipIf(hud is None, 'Blender Python API is not available')
class HudHeightLayout(unittest.TestCase):
    def test_height_changes_without_scaling_text_or_width(self):
        for adaptive in (True, False):
            small = hud._hud_layout_metrics(1600, 900, 780, 1, adaptive, 90)
            large = hud._hud_layout_metrics(1600, 900, 780, 1, adaptive, 320)
            self.assertGreater(large['height'], small['height'])
            self.assertEqual(large['scale'], small['scale'])
            self.assertEqual(large['width'], small['width'])

    def test_height_fits_short_viewport(self):
        metrics = hud._hud_layout_metrics(900, 200, 780, 1, True, 900)
        self.assertLessEqual(metrics['height'], 176)

    def test_minimum_height_keeps_a_prompt_line(self):
        metrics = hud._hud_layout_metrics(1600, 900, 780, 1, False, 0)
        self.assertEqual(metrics['height'], 90)
