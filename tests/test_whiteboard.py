from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from pilferedparrot.config import DEFAULTS
from pilferedparrot.model import Conversation
from pilferedparrot.whiteboard import Whiteboard, MAX_OUTPUT, whiteboard_discovery
from pilferedparrot.qwen_tools import QwenToolbox
from pilferedparrot.dispatch import _codex_command, _claude_command, capture_dispatch, RunResult
from pilferedparrot.adapters import GeminiAdapter
from pilferedparrot.antigravity import AntigravityAdapter


class WhiteboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config = deepcopy(DEFAULTS)
        self.config['web']['chat_store'] = str(self.root / 'chats.json')
        self.board = Whiteboard(self.config)

    def test_concurrent_posts_survive_and_reads_are_bounded(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(lambda i: self.board.post(str(i) + 'x' * 1990, 'model')['id'], range(50)))
        self.assertEqual(len(set(ids)), 50)
        self.assertEqual(len(list(self.board.directory.glob('*.txt'))), 50)
        result = self.board.read()
        self.assertLessEqual(len(result['messages']), 20)
        self.assertLessEqual(sum(len(m['text']) for m in result['messages']), MAX_OUTPUT)
        self.assertNotIn('text', result)  # no second copy consuming model context
        self.assertEqual(self.board.read(since='9999')['messages'], [])
        self.assertEqual(len(self.board.read(limit=1)['messages']), 1)

    def test_native_notes_and_unsafe_files(self):
        self.board.post('valid note', 'test')
        (self.board.directory / 'native.txt').write_text('Author: native\n---\nA native worker note', encoding='utf-8')
        (self.board.directory / 'huge.txt').write_bytes(b'x' * 50_000)
        outside = self.root / 'private.txt'
        outside.write_text('private secret')
        try:
            (self.board.directory / 'link.txt').symlink_to(outside)
        except OSError:
            pass
        notes = self.board.read()['messages']
        self.assertEqual({m['text'] for m in notes}, {'valid note', 'A native worker note'})
        with self.assertRaises(ValueError): self.board.post('x' * 2001, 'test')
        with self.assertRaises(ValueError): self.board.post(None, 'test')

    def test_tool_access_includes_read_only_chat(self):
        tool_config = {'_whiteboard_directory': str(self.board.directory), 'read_only': True}
        toolbox = QwenToolbox(self.root, tool_config)
        self.assertIn('messages', toolbox.execute('whiteboard_read', {}))
        with self.assertRaises(PermissionError):
            toolbox.execute('whiteboard_post', {'text': 'no', 'author': 'Chat'})
        tool_config['read_only'] = False
        self.assertIn('yes', toolbox.execute('whiteboard_post', {'text': 'yes', 'author': 'worker'}))
        self.assertEqual(self.board.read()['messages'][0]['text'], 'yes')

    @patch('pilferedparrot.dispatch.provider_command', side_effect=lambda cfg, provider: provider)
    def test_all_native_work_providers_get_only_the_board_directory(self, _command):
        conversation = Conversation()
        commands = [(_codex_command(conversation, self.config, self.root), '--add-dir'),
                    (_claude_command(conversation, self.config), '--add-dir'),
                    (GeminiAdapter('gemini', self.config)._command(conversation), '--include-directories'),
                    (AntigravityAdapter('antigravity', self.config)._command(conversation), '--add-dir')]
        for command, flag in commands:
            self.assertEqual(command[command.index(flag) + 1], str(self.board.directory))
        self.config['codex']['sandbox'] = 'read-only'
        self.config['codex']['additional_write_dirs'] = []
        self.assertNotIn('--add-dir', _codex_command(conversation, self.config, self.root))
        self.config['claude']['permission_mode'] = 'plan'
        self.assertNotIn('--add-dir', _claude_command(conversation, self.config))

    def test_discovery_once_and_reset_and_compatible_tools(self):
        conversation = Conversation(provider='qwen')
        self.assertIn('whiteboard_post', whiteboard_discovery(conversation, self.config))
        self.assertEqual(whiteboard_discovery(conversation, self.config), '')
        conversation.reset('qwen')
        self.config['qwen']['read_only'] = True
        self.assertNotIn('whiteboard_post', whiteboard_discovery(conversation, self.config))
        conversation.reset('codex')
        adapter = MagicMock()
        adapter.run.return_value = RunResult('ok', 0)
        with patch('pilferedparrot.adapters.adapter_for', return_value=adapter):
            capture_dispatch('codex', 'task', self.root, conversation, self.config)
            capture_dispatch('codex', 'next', self.root, conversation, self.config)
        self.assertIn('Shared model whiteboard', adapter.run.call_args_list[0].args[0])
        self.assertEqual(adapter.run.call_args_list[1].args[0], 'next')

    def test_web_discovery_survives_reload_and_model_change(self):
        import io, json
        from pilferedparrot.web import PilferedParrotApp
        from tests import test_reasoning
        config = test_reasoning.ReasoningTests.config(self, self.root)
        app = PilferedParrotApp(config, self.root)
        work = app.create_chat({'provider': 'qwen', 'model': 'first'})
        sent = []
        def respond(_config, provider, request, **kwargs):
            sent.append(json.loads(request.data))
            return io.StringIO(json.dumps({'choices': [{'message': {'role': 'assistant', 'content': 'done'}}]}))
        with patch('pilferedparrot.web.ensure_qwen'), patch('pilferedparrot.qwen.open_compatible_url', side_effect=respond):
            for model in ('first', 'first', 'second'):
                app.send_message(work['id'], {'content': 'task', 'model': model})
                test_reasoning.ReasoningTests.wait_for_work(self, app, work['id'])
                app = PilferedParrotApp(config, self.root)
        latest_users = [next(m['content'] for m in reversed(r['messages']) if m['role'] == 'user') for r in sent]
        self.assertIn('Shared model whiteboard', latest_users[0])
        self.assertEqual(latest_users[1], 'task')
        self.assertIn('Shared model whiteboard', latest_users[2])
