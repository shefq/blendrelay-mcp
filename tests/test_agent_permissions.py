import json
from pathlib import Path
import tempfile
import unittest

from archforge_blender.agent_permissions import (
    ARCHFORGE_MCP_RULE, agent_failure, antigravity_mcp_is_allowed, codex_automatic_review_args,
    ensure_antigravity_mcp_permission,
)


class AgentPermissionTests(unittest.TestCase):
    def test_codex_automatic_review_has_no_conflicting_sandbox_flag(self):
        args=codex_automatic_review_args()
        self.assertIn('--approve-for-me',args)
        self.assertNotIn('--sandbox',args)

    def test_adds_only_archforge_rule_and_preserves_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json'
            path.write_text(json.dumps({'verbosity':'low','permissions':{'allow':['command(git)']}}))
            changed,_=ensure_antigravity_mcp_permission(path)
            result=json.loads(path.read_text())
            self.assertTrue(changed)
            self.assertEqual(result['verbosity'],'low')
            self.assertEqual(result['permissions']['allow'],['command(git)',ARCHFORGE_MCP_RULE])
            self.assertTrue(antigravity_mcp_is_allowed(path))
            self.assertFalse(ensure_antigravity_mcp_permission(path)[0])

    def test_conflicting_higher_priority_rule_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json'
            path.write_text(json.dumps({'permissions':{'ask':['mcp(*)']}}))
            with self.assertRaises(RuntimeError):ensure_antigravity_mcp_permission(path)

    def test_zero_exit_permission_denial_is_failure(self):
        text='a tool required the "mcp" permission that headless mode cannot prompt for, so it was auto-denied'
        self.assertIn('denied',agent_failure(0,log_text=text).lower())
        self.assertIsNone(agent_failure(0,log_text='completed normally'))

    def test_expected_edit_requires_a_completed_blender_edit(self):
        self.assertIn('without completing', agent_failure(0, expected_edit=True, successful_edits=0))
        self.assertIsNone(agent_failure(0, expected_edit=True, successful_edits=1))

    def test_blender_job_error_overrides_zero_exit(self):
        failure=agent_failure(0,blender_errors=[{'action':'execute','error':'Traceback\nTypeError: bad argument'}])
        self.assertIn('TypeError: bad argument',failure)


if __name__=='__main__':unittest.main()
