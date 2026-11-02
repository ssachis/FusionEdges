import unittest
from unittest.mock import patch
from fusionedges import llm, agents, browser


class Signatures(unittest.TestCase):
    def test_provider_metadata_roundtrip(self):
        calls = [{"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": '{ "path": "x" }'}, "extra_content": {"google": {"thought_signature": "opaque"}}}]
        response = {"choices": [{"message": {"tool_calls": calls, "extra_content": {"google": {"thought_signature": "text-opaque"}}}}]}
        with patch.object(llm, '_post', return_value=response) as post:
            msg, _ = llm._openai(llm.SPECS['gemini'], 'model', 'test', 'system', [], [], 100)
            llm._openai(llm.SPECS['gemini'], 'model', 'test', 'system', [msg], [], 100)
            wire = post.call_args.args[2]['messages'][1]
            self.assertEqual(wire['tool_calls'], calls)
            self.assertEqual(wire['extra_content'], response['choices'][0]['message']['extra_content'])
            llm._openai(llm.SPECS['openai'], 'model', 'test', 'system', [msg], [], 100)
            self.assertNotIn('extra_content', post.call_args.args[2]['messages'][1]['tool_calls'][0])

    def test_loop_keeps_metadata(self):
        class Session:
            label = 'test'
            def chat(self, system, messages, tools, max_tokens):
                if len(messages) == 1:
                    return {'role': 'assistant', 'text': '', 'tool_calls': [{'id': 'a', 'name': 'read', 'args': {}}], 'wire_tool_calls': ['signature']}
                self.history = messages
                return {'role': 'assistant', 'text': 'done'}
        class Workspace:
            def run(self, *args): return 'ok'
        session = Session()
        agents.run_loop(session, '', '', Workspace(), [], 3, lambda *_: None)
        self.assertEqual(session.history[1]['wire_tool_calls'], ['signature'])

    def test_footer_not_subject_to_navbar_height(self):
        result = {'component': 'footer', 'viewports': {'desktop': {'text': 100, 'found': True, 'height': 220, 'links': []}}}
        self.assertEqual(browser.problems(result), [])
