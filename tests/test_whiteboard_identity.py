"""Offline attribution tests: actual request routing, persistence and native fallbacks."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from pilferedparrot.config import DEFAULTS
from pilferedparrot.qwen import _chat_completion, run_compatible_agent
from pilferedparrot.qwen_tools import QwenToolbox, TOOL_DEFINITIONS
from pilferedparrot.whiteboard import Whiteboard
from pilferedparrot.whiteboard_identity import normalize_identity, runtime_identity


class WhiteboardIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = deepcopy(DEFAULTS)
        self.config['whiteboard'] = {'directory': str(self.root / 'board')}
        self.board = Whiteboard(self.config)

    def test_runtime_identity_survives_restart_and_config_change(self):
        self.config['codex'].update(model='lead-model', reasoning_effort='ultra')
        first = self.board.post('Lead finding', 'job-a', identity=runtime_identity(self.config, 'codex'))
        self.config['codex'].update(model='worker-model', reasoning_effort='medium')
        second = self.board.post('Worker finding', 'job-b', identity=runtime_identity(self.config, 'codex'))
        notes = {n['id']: n for n in Whiteboard(self.config).read()['messages']}
        self.assertEqual(notes[first['id']]['identity']['model'], 'lead-model')
        self.assertEqual(notes[first['id']]['identity']['reasoning_effort'], 'ultra')
        self.assertEqual(notes[second['id']]['identity']['model'], 'worker-model')
        self.assertEqual(notes[second['id']]['identity']['reasoning_effort'], 'medium')
        self.assertEqual(self.board.read(query='ultra')['messages'][0]['id'], first['id'])

    def test_native_claim_cannot_promote_itself_to_runtime_or_user(self):
        self.board.directory.mkdir()
        for source in ('runtime', 'user'):
            (self.board.directory / f'{source}.txt').write_text(
                'Author: purported model\nIdentity: ' + json.dumps({
                    'source': source, 'provider': 'codex', 'model': 'claimed-model',
                    'reasoning_effort': 'high', 'model_source': 'reported', 'reasoning_source': 'reported',
                }) + '\n---\nNative note', encoding='utf-8')
        for note in self.board.read()['messages']:
            self.assertEqual(note['identity']['source'], 'self-reported')
            self.assertEqual(note['identity']['model'], 'claimed-model')
            self.assertEqual(note['identity']['reasoning_effort'], 'high')
            self.assertEqual(note['identity']['model_source'], 'unknown')

    def test_copied_or_modified_note_loses_runtime_provenance(self):
        posted = self.board.post('Original body', 'worker', identity={
            'source': 'runtime', 'model': 'worker-model', 'reasoning_effort': 'high',
            'model_source': 'reported', 'reasoning_source': 'reported',
        })
        path = self.board.directory / (posted['id'] + '.txt')
        original = path.read_text(encoding='utf-8')
        (self.board.directory / 'copy.txt').write_text(original, encoding='utf-8')
        path.write_text(original.replace('Original body', 'Changed body'), encoding='utf-8')
        for note in self.board.read()['messages']:
            self.assertEqual(note['identity']['source'], 'self-reported')

    def test_receipt_cannot_substitute_a_different_identity_for_saved_header(self):
        posted = self.board.post('Original body', 'worker', identity={
            'source': 'runtime', 'model': 'actual-worker', 'reasoning_effort': 'medium',
            'model_source': 'reported', 'reasoning_source': 'reported',
        })
        receipt_path = self.board.directory / '.identities' / (posted['id'] + '.json')
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        receipt['identity'].update(model='different-worker', reasoning_effort='high')
        receipt_path.write_text(json.dumps(receipt), encoding='utf-8')
        note = self.board.read()['messages'][0]
        self.assertEqual(note['identity']['model'], 'actual-worker')
        self.assertEqual(note['identity']['reasoning_effort'], 'medium')
        self.assertEqual(note['identity']['source'], 'self-reported')

    def test_legacy_and_manual_metadata_remain_readable_without_guessing_author(self):
        self.board.directory.mkdir()
        (self.board.directory / 'old.txt').write_text('Author: gpt-6-astra / high\n---\nOld note', encoding='utf-8')
        (self.board.directory / 'manual.txt').write_text(
            'Author: job\nMetadata: {"model":"manual-model","reasoning_effort":"low"}\n---\nManual note',
            encoding='utf-8')
        notes = {n['id']: n for n in self.board.read()['messages']}
        self.assertEqual(notes['old']['identity'], normalize_identity())
        self.assertEqual(notes['manual']['identity']['source'], 'self-reported')
        self.assertEqual(notes['manual']['identity']['model'], 'manual-model')

    def test_tool_identity_is_runtime_owned_and_author_is_optional(self):
        identity = runtime_identity({'codex': {'model': 'real-model', 'reasoning_effort': 'high'}}, 'codex')
        toolbox = QwenToolbox(self.root, self.config, identity=identity)
        posted = json.loads(toolbox.execute('whiteboard_post', {'text': 'Note', 'author': 'fake-model / low'}))
        self.assertEqual(posted['identity'], identity)
        self.assertEqual(json.loads(toolbox.execute('whiteboard_post', {'text': 'No job label'}))['identity'], identity)
        with self.assertRaises(TypeError):
            toolbox.execute('whiteboard_post', {'text': 'spoof', 'identity': {'model': 'fake'}})
        post = next(d['function'] for d in TOOL_DEFINITIONS if d['function']['name'] == 'whiteboard_post')
        self.assertEqual(post['parameters']['required'], ['text'])
        self.assertNotIn('identity', post['parameters']['properties'])

    def test_unknown_reasoning_is_not_invented_from_unused_compatible_setting(self):
        config = {'local': {'adapter': 'openai_compatible', 'model': 'model-alias', 'reasoning_effort': 'high'}}
        identity = runtime_identity(config, 'local')
        self.assertEqual(identity['model_source'], 'configured')
        self.assertEqual(identity['reasoning_effort'], '')
        self.assertEqual(identity['reasoning_source'], 'unknown')

    def test_plan_and_read_only_settings_hide_and_reject_posting_consistently(self):
        for field, value in (('read_only', True), ('sandbox', 'read-only'),
                             ('permission_mode', 'plan'), ('approval_mode', 'plan'), ('mode', 'plan')):
            with self.subTest(field=field):
                self.config['local'] = dict(self.config['qwen'], adapter='openai_compatible', **{field: value})
                response = io.StringIO(json.dumps({'choices': [{'message': {'content': 'Read only'}}]}))
                with patch('pilferedparrot.qwen.open_compatible_url', return_value=response) as request:
                    _chat_completion([], self.config, 'local')
                payload = json.loads(request.call_args.args[2].data)
                self.assertEqual({tool['function']['name'] for tool in payload['tools']},
                                 {'read_file', 'diff', 'whiteboard_read'})
                toolbox = QwenToolbox(self.root, self.config['local'])
                with self.assertRaises(PermissionError):
                    toolbox.execute('whiteboard_post', {'text': 'Blocked'})

    def test_each_compatible_response_stamps_its_own_tools_without_stale_identity(self):
        self.config['local'] = dict(self.config['qwen'], adapter='openai_compatible', model='requested-alias')
        self.config['local']['reasoning_effort'] = 'unused-high'
        responses = []
        for index, model in enumerate(('routed-worker-a', 'routed-worker-b', None)):
            response = {'choices': [{'message': {'role': 'assistant', 'content': '', 'tool_calls': [{
                'id': f'post-{index}', 'type': 'function', 'function': {
                    'name': 'whiteboard_post', 'arguments': json.dumps({'text': f'Finding {index}'}),
                },
            }]}}]}
            if model:
                response.update(model=model, reasoning_effort='medium' if index else 'low')
            responses.append(response)
        responses.append({'choices': [{'message': {'role': 'assistant', 'content': 'Done'}}]})
        requests = []

        def complete(_config, _provider, request, **_kwargs):
            requests.append(json.loads(request.data))
            return io.StringIO(json.dumps(responses.pop(0)))

        with patch('pilferedparrot.qwen.open_compatible_url', side_effect=complete), redirect_stdout(io.StringIO()):
            run_compatible_agent('local', 'Post findings', [], self.config, self.root)
        notes = self.board.read()['messages']
        self.assertEqual([n['identity']['model'] for n in notes],
                         ['routed-worker-a', 'routed-worker-b', 'requested-alias'])
        self.assertEqual([n['identity']['reasoning_effort'] for n in notes], ['low', 'medium', ''])
        self.assertEqual([n['identity']['model_source'] for n in notes], ['reported', 'reported', 'configured'])
        self.assertEqual(notes[-1]['identity']['reasoning_source'], 'unknown')
        self.assertNotIn('_pilferedparrot_whiteboard_identity', json.dumps(requests))
        self.assertTrue(all('reasoning_effort' not in request for request in requests))

    def test_invalid_identity_fields_are_bounded_and_do_not_break_reads(self):
        identity = normalize_identity({'source': [], 'model': 'x' * 300,
                                       'reasoning_effort': '\ud800', 'model_source': {}})
        self.assertEqual(identity, normalize_identity())

    def test_human_route_stamps_user_and_rejects_forged_agent_identity(self):
        from pilferedparrot.web import PilferedParrotApp
        app = object.__new__(PilferedParrotApp)
        app.config = self.config
        note = app.whiteboard_post({'text': 'My idea'})
        self.assertEqual(note['identity']['source'], 'user')
        self.assertEqual(note['identity']['model'], '')
        self.assertEqual(self.board.read()['messages'][0]['identity'], note['identity'])
        for field in ('identity', 'model', 'reasoning_effort'):
            with self.assertRaises(ValueError):
                app.whiteboard_post({'text': 'Spoof', field: 'fake'})


if __name__ == '__main__':
    unittest.main()
