from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from pilferedparrot.config import DEFAULTS
from pilferedparrot.model import Conversation
from pilferedparrot.whiteboard import Whiteboard, MAX_OUTPUT, MAX_SERIALIZED_OUTPUT, whiteboard_discovery
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
        (self.board.directory / 'native-crlf.txt').write_bytes(
            b'Author: native\r\nMetadata: {"kind":"finding","project":"Windows"}\r\n---\r\nCRLF note')
        (self.board.directory / 'huge.txt').write_bytes(b'x' * 50_000)
        outside = self.root / 'private.txt'
        outside.write_text('private secret')
        try:
            (self.board.directory / 'link.txt').symlink_to(outside)
        except OSError:
            pass
        notes = self.board.read()['messages']
        self.assertEqual({m['text'] for m in notes}, {'valid note', 'A native worker note', 'CRLF note'})
        self.assertEqual(self.board.read(project='Windows')['messages'][0]['kind'], 'finding')
        with self.assertRaises(ValueError): self.board.post('x' * 2001, 'test')
        with self.assertRaises(ValueError): self.board.post(None, 'test')

    def test_searches_old_notes_and_round_trips_structured_metadata(self):
        older = self.board.post(
            'The database answer is SQLite.', 'researcher', workspace='docs', kind='finding',
            title='Storage choice', project='Parrot', topics=['Storage', 'Search'],
            evidence='benchmark 17', applies_to='v2', basis='independent',
        )
        for index in range(25):
            self.board.post(f'newer unrelated {index}', 'model', project='other')
        result = self.board.read(query='BENCHMARK 17', project='parrot', topic='storage')
        self.assertEqual([message['id'] for message in result['messages']], [older['id']])
        note = result['messages'][0]
        self.assertEqual(note['kind'], 'finding')
        self.assertEqual(note['title'], 'Storage choice')
        self.assertEqual(note['topics'], ['Storage', 'Search'])
        self.assertEqual(note['workspace'], 'docs')
        self.assertEqual(note['basis'], 'independent')
        legacy = self.board.directory / 'legacy.txt'
        legacy.write_text('Author: native\n---\nlegacy searchable text', encoding='utf-8')
        self.assertEqual(self.board.read(query='legacy searchable')['messages'][0]['kind'], 'note')

    def test_pagination_returns_whole_notes_in_chronological_order(self):
        posted = [self.board.post(f'note {index}', 'model')['id'] for index in range(5)]
        first = self.board.read(limit=2)
        self.assertEqual([note['id'] for note in first['messages']], posted[-2:])
        self.assertTrue(first['has_more'])
        self.assertEqual(first['next_before'], posted[-2])
        second = self.board.read(limit=2, before=first['next_before'])
        self.assertEqual([note['id'] for note in second['messages']], posted[-4:-2])
        third = self.board.read(limit=2, before=second['next_before'])
        self.assertEqual([note['id'] for note in third['messages']], posted[:1])
        self.assertFalse(third['has_more'])
        self.assertIsNone(third['next_before'])

    def test_pagination_keeps_post_order_when_clock_repeats(self):
        instant = datetime(2026, 9, 24, 0, 41, 26, 825711, tzinfo=timezone.utc)

        class RepeatingClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return instant

        with patch('pilferedparrot.whiteboard.datetime', RepeatingClock):
            posted = [self.board.post(f'note {index}', 'model')['id'] for index in range(5)]
        self.assertEqual([note['id'] for note in self.board.read()['messages']], posted)
        self.assertEqual([note['id'] for note in self.board.read(limit=2)['messages']], posted[-2:])
        (self.board.directory / ('99991231T235959.999999Z-' + 'f' * 32 + '.txt')).write_text('future')
        with patch('pilferedparrot.whiteboard.datetime', RepeatingClock):
            next_post = self.board.post('next note', 'model')
        self.assertTrue(next_post['id'].startswith('20260924T004126.825716Z-'))

    def test_replies_updates_expiry_and_validation(self):
        request = self.board.post(
            'please investigate', 'owner', kind='request', expires_at='2000-01-01T00:00:00Z',
        )
        reply = self.board.post('I will investigate', 'worker', reply_to=request['id'])
        self.board.post('claimed', 'worker', kind='update', reply_to=request['id'], status='claimed')
        expired = {note['id']: note for note in self.board.read(thread=request['id'])['messages']}
        self.assertEqual(expired[request['id']]['effective_status'], 'expired')
        self.assertTrue(expired[request['id']]['expired'])
        self.assertEqual(expired[request['id']]['reply_count'], 2)
        self.board.post('finished', 'worker', kind='update', reply_to=request['id'], status='resolved')
        resolved = {note['id']: note for note in self.board.read(status='resolved')['messages']}
        self.assertEqual(resolved[request['id']]['effective_status'], 'resolved')
        with self.assertRaises(ValueError):
            self.board.post('bad', 'worker', reply_to='../private')
        with self.assertRaises(ValueError):
            self.board.post('bad', 'worker', kind='update', reply_to=request['id'])
        with self.assertRaises(ValueError):
            self.board.post('bad', 'worker', topics=['x'] * 9)

    def test_invalid_native_metadata_is_inert_and_output_is_serialized_bounded(self):
        target = self.board.post('keep open', 'owner', kind='request')
        (self.board.directory / 'bad.txt').write_text(
            'Author: native\nMetadata: {"kind":"update","reply_to":"' + target['id'] +
            '","status":"resolved","title":42}\n---\nmalformed update', encoding='utf-8')
        notes = {note['id']: note for note in self.board.read()['messages']}
        self.assertEqual(notes[target['id']]['effective_status'], 'open')
        for index in range(20):
            self.board.post('x' * 2000, 'model', evidence='e' * 1000, applies_to='a' * 500,
                            topics=['t' * 40] * 8, title='h' * 160, project='p' * 200)
        result = self.board.read()
        self.assertLessEqual(sum(len(note['text']) for note in result['messages']), MAX_OUTPUT)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), MAX_SERIALIZED_OUTPUT)
        self.assertTrue(result['has_more'])

    def test_unicode_metadata_round_trips_within_native_file_limit(self):
        posted = self.board.post(
            '😀' * 2000, 'author', title='😀' * 160, project='😀' * 200,
            topics=['😀' * 40] * 8, evidence='😀' * 1000, applies_to='😀' * 500,
        )
        path = self.board.directory / (posted['id'] + '.txt')
        self.assertGreater(path.stat().st_size, 12_000)
        found = {note['id']: note for note in self.board.read()['messages']}
        self.assertEqual(found[posted['id']]['text'], '😀' * 2000)

    def test_since_normalizes_timezones_and_plain_separator_stays_plain(self):
        native = self.board.directory
        native.mkdir(parents=True, exist_ok=True)
        (native / 'dated.txt').write_text(
            'Author: native\nCreated: 2020-01-01T00:00:00+00:00\n---\ndated', encoding='utf-8')
        self.assertEqual(self.board.read(since='2019-12-31T18:00:00-05:00')['count'], 1)
        self.assertEqual(self.board.read(since='2020-01-01T01:00:00+01:00')['count'], 0)
        (native / 'plain.txt').write_bytes(b'ordinary text\r\n---\r\nnot a header')
        plain = next(note for note in self.board.read(query='ordinary text')['messages'] if note['id'] == 'plain')
        self.assertEqual(plain['text'], 'ordinary text\n---\nnot a header')

    def test_malformed_native_json_timestamps_and_surrogates_are_inert(self):
        valid = self.board.post('adjacent valid note', 'author', kind='finding', title='safe')
        native = self.board.directory
        nested = '{"x":' + '[' * 1500 + '0' + ']' * 1500 + '}'
        (native / 'nested.txt').write_text(
            'Author: native\nMetadata: ' + nested + '\n---\nvery nested metadata', encoding='utf-8')
        (native / 'overflow.txt').write_text(
            'Author: native\nCreated: 0001-01-01T00:00:00+14:00\n'
            'Metadata: {"expires_at":"0001-01-01T00:00:00+14:00"}\n---\noverflow timestamp',
            encoding='utf-8')
        (native / 'surrogate.txt').write_text(
            r'Author: native\nMetadata: {"title":"\ud800"}\n---\nescaped surrogate'.replace(r'\n', '\n'),
            encoding='utf-8')
        notes = {note['id']: note for note in self.board.read()['messages']}
        self.assertEqual(notes[valid['id']]['title'], 'safe')
        self.assertEqual(notes['surrogate']['title'], '')
        self.assertEqual(notes['nested']['kind'], 'note')
        with self.assertRaises(ValueError):
            self.board.post('bad\ud800', 'author')
        with self.assertRaises(ValueError):
            self.board.post('body', 'author', title='bad\ud800')

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
        discovery = whiteboard_discovery(conversation, self.config)
        self.assertIn('whiteboard_post', discovery)
        self.assertNotIn('Pass this pointer to delegated workers', discovery)
        self.assertIn('share board access only when their task requires it', discovery)
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
        self.assertNotIn('Pass this pointer to delegated workers', adapter.run.call_args_list[0].args[0])
        self.assertIn('Give workers relevant excerpts', adapter.run.call_args_list[0].args[0])
        followup = adapter.run.call_args_list[1].args[0]
        self.assertTrue(followup.startswith('next'))
        self.assertNotIn('Shared model whiteboard', followup)
        self.assertIn('Native whiteboard posting', followup)

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
        self.assertNotIn('Shared model whiteboard', latest_users[1])
        self.assertIn('Shared model whiteboard', latest_users[2])
        for prompt in latest_users:
            self.assertTrue(prompt.startswith('task\n\n'))
            self.assertEqual(prompt.count('[Incomplete work handoff]'), 1)
        stored_users = [m['content'] for m in app.store.get(work['id'])['messages']
                        if m['role'] == 'user']
        self.assertEqual(stored_users, ['task', 'task', 'task'])
