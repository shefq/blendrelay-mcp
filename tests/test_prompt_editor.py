import unittest
from blendrelay_blender.prompt_editor import Editor, wrap_lines


class PromptEditorTests(unittest.TestCase):
    def editor(self, text, width=100, visible=3):
        value = Editor()
        value.sync(text, 'scene')
        value.layout(width, len, visible)
        return value

    def test_click_position_inserts_in_middle_instead_of_appending(self):
        editor = self.editor('red dragon')
        editor.place(editor.hit(0, 4, len))
        editor.replace('small ')
        self.assertEqual(editor.text, 'red small dragon')

    def test_backward_and_forward_delete_have_distinct_behavior(self):
        editor = self.editor('abcd')
        editor.place(2)
        editor.delete()
        self.assertEqual(editor.text, 'abd')
        editor.delete(backward=True)
        self.assertEqual(editor.text, 'ad')

    def test_wrapping_preserves_spaces_tabs_blank_lines_and_long_words(self):
        text = '  a\tb  c\n\nabcdefghij\n'
        lines = wrap_lines(text, 4, len)
        covered = ''.join(text[line.start:line.end] for line in lines)
        self.assertEqual(covered, text.replace('\n', ''))
        self.assertEqual(lines[-1].start, len(text))
        self.assertTrue(all(line.end - line.start <= 4 for line in lines))

    def test_shift_selection_replacement_and_selection_collapse(self):
        editor = self.editor('abcdef')
        editor.place(1)
        editor.move('RIGHT', extend=True)
        editor.move('RIGHT', extend=True)
        self.assertEqual(editor.selected_text, 'bc')
        editor.move('LEFT')
        self.assertEqual(editor.cursor, 1)
        editor.place(3, extend=True)
        editor.replace('!')
        self.assertEqual(editor.text, 'a!def')

    def test_ctrl_word_navigation_and_deletion(self):
        editor = self.editor('one two three')
        editor.move('LEFT', word=True)
        self.assertEqual(editor.cursor, 8)
        editor.delete(backward=True, word=True)
        self.assertEqual(editor.text, 'one three')

    def test_vertical_navigation_preserves_column_through_short_lines(self):
        editor = self.editor('abcdef\nx\nabcdef')
        editor.place(5)
        editor.move('DOWN')
        self.assertEqual(editor.cursor, 8)
        editor.move('DOWN')
        self.assertEqual(editor.cursor, 14)
        editor.move('UP')
        editor.move('UP')
        self.assertEqual(editor.cursor, 5)

    def test_home_end_on_soft_wrap_does_not_jump_to_next_row(self):
        editor = self.editor('abcdefgh', width=4)
        editor.place(1)
        editor.move('END')
        self.assertEqual(editor.cursor, 4)
        self.assertEqual(editor.row(), 0)
        editor.move('HOME')
        self.assertEqual(editor.cursor, 0)
        editor.move('END', word=True)
        self.assertEqual(editor.cursor, 8)

    def test_page_navigation_and_scroll_follow_caret(self):
        editor = self.editor('\n'.join(str(i) for i in range(12)))
        self.assertEqual(editor.scroll, 9)
        editor.scroll_by(-100)
        editor.layout(100, len, 3)
        self.assertEqual(editor.scroll, 0, 'draw must not undo wheel scrolling')
        editor.move('HOME', word=True)
        editor.move('PAGEDOWN')
        editor.layout(100, len, 3)
        self.assertEqual(editor.row(), 3)
        self.assertEqual(editor.scroll, 1)

    def test_undo_redo_and_new_edit_invalidates_redo(self):
        editor = self.editor('abc')
        editor.replace('de', kind='typing')
        editor.replace('f', kind='typing')
        editor.undo()
        self.assertEqual(editor.text, 'abc')
        editor.undo(redo=True)
        self.assertEqual(editor.text, 'abcdef')
        editor.undo()
        editor.replace('!')
        editor.undo(redo=True)
        self.assertEqual(editor.text, 'abc!')

    def test_paste_preserves_whitespace_and_normalizes_newlines(self):
        editor = self.editor('old')
        editor.select_all()
        editor.replace('  hello\r\nworld \r\n')
        self.assertEqual(editor.text, '  hello\nworld \n')
        editor.undo()
        self.assertEqual(editor.text, 'old')

    def test_unicode_can_be_inserted_at_cursor(self):
        editor = self.editor('ab')
        editor.place(1)
        editor.replace('é🐉')
        self.assertEqual(editor.text, 'aé🐉b')
        self.assertEqual(editor.cursor, 3)

    def test_double_click_selects_word(self):
        editor = self.editor('small red dragon')
        editor.select_word(7)
        self.assertEqual(editor.selected_text, 'red')

    def test_external_changes_and_different_scenes_reset_history(self):
        editor = self.editor('first')
        editor.replace('!')
        editor.sync('different', 'other-scene')
        editor.undo()
        self.assertEqual(editor.text, 'different')
        self.assertEqual(editor.selection, (9, 9))

    def test_narrow_layout_makes_progress_and_scroll_is_clamped(self):
        editor = self.editor('longword', width=.1, visible=2)
        self.assertEqual(len(editor.lines), 8)
        editor.scroll_by(100)
        self.assertEqual(editor.scroll, 6)
        editor.layout(100, len, 3)
        self.assertEqual(editor.scroll, 0)


if __name__ == '__main__':
    unittest.main()
