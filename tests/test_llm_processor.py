"""Offline regression tests for the Responses API streaming support."""

import copy
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from processors import llm_processor


RESULT = {
    'groups': [{'name': '音乐', 'items': [{'tweet_url': 'https://x.com/test/status/1', 'summary': '音乐（新歌发布）：这首歌适合观察情绪与传播机制。'}]}],
    'top_actions': [],
}
TEXT = json.dumps(RESULT, ensure_ascii=False)


def sse(event, event_name=None, newline='\n', final_blank=True):
    payload = event if isinstance(event, str) else json.dumps(event, ensure_ascii=False)
    parts = []
    if event_name:
        parts.append('event: ' + event_name)
    parts.extend('data: ' + line for line in payload.split('\n'))
    result = newline.join(parts) + newline
    if final_blank:
        result += newline
    return result.encode('utf-8')


def completed(text=TEXT, **overrides):
    response = {'status': 'completed', 'output_text': text}
    response.update(overrides)
    return {'type': 'response.completed', 'response': response}


class Response(io.BytesIO):
    def __init__(self, body, content_type='application/json'):
        super().__init__(body)
        self.headers = {'Content-Type': content_type}


class GuardedResponse(Response):
    """Any attempt to read beyond the completed event is a test failure."""

    def __iter__(self):
        return self

    def __next__(self):
        line = self.readline()
        if not line:
            raise AssertionError('reader waited for more data after response.completed')
        return line

    def read(self, *args):
        raise AssertionError('stream must be read incrementally')


class StreamRegressionTests(unittest.TestCase):
    def setUp(self):
        self.processor = llm_processor.LLMProcessor(
            api_key='offline-test-key',
            base_url='https://offline.invalid/v1',
            model='offline-model',
            enabled=True,
            required=True,
        )

    def call(self, body, content_type='application/json', payload=None):
        response = Response(body, content_type)
        with patch.object(llm_processor.request, 'urlopen', return_value=response) as opener:
            result = self.processor._responses_json(payload or {'groups': []})
            req = opener.call_args.args[0]
            timeout = opener.call_args.kwargs['timeout']
        return result, req, timeout

    def test_request_stream_accept_model_url_and_payload(self):
        body = json.dumps({'output_text': TEXT}, ensure_ascii=False).encode('utf-8')
        payload = {'groups': [{'name': '中文', 'items': []}]}
        result, req, timeout = self.call(body, payload=payload)
        self.assertEqual(result, RESULT)
        data = json.loads(req.data)
        self.assertIs(data['stream'], True)
        self.assertEqual(data['model'], 'offline-model')
        self.assertEqual(data['input'][0]['role'], 'user')
        content = data['input'][0]['content']
        self.assertEqual(len(content), 1)
        self.assertEqual(content[0]['type'], 'input_text')
        self.assertEqual(json.loads(content[0]['text']), payload)
        self.assertIn('text/event-stream', req.get_header('Accept'))
        self.assertIn('application/json', req.get_header('Accept'))
        self.assertEqual(timeout, llm_processor.LLM_REQUEST_TIMEOUT)
        self.assertEqual(req.full_url, 'https://offline.invalid/v1/responses')
        self.assertEqual(req.get_method(), 'POST')

    def test_chinese_deltas_then_completed_are_not_duplicated(self):
        events = b''.join(sse({'type': 'response.output_text.delta', 'delta': TEXT[i:i + 9]}) for i in range(0, len(TEXT), 9))
        result, _, _ = self.call(events + sse(completed()), 'text/event-stream')
        self.assertEqual(result, RESULT)

    def test_completed_without_deltas(self):
        result, _, _ = self.call(sse(completed()))
        self.assertEqual(result, RESULT)

    def test_completed_output_multiple_text_parts_excludes_reasoning_tools(self):
        split = len(TEXT) // 2
        output = [
            {'type': 'reasoning', 'content': [{'type': 'output_text', 'text': 'not JSON'}]},
            {'type': 'function_call', 'content': [{'text': 'also not JSON'}]},
            {'type': 'message', 'content': [{'type': 'refusal', 'text': 'ignore'}, {'type': 'output_text', 'text': TEXT[:split]}]},
            {'type': 'message', 'content': [{'type': 'output_text', 'text': TEXT[split:]}]},
        ]
        result, _, _ = self.call(sse(completed(output_text=None, output=output)))
        self.assertEqual(result, RESULT)

    def test_out_of_order_output_indexes_and_done_text_replace_deltas(self):
        split = len(TEXT) // 2
        events = [
            {'type': 'response.output_text.delta', 'output_index': 2, 'delta': TEXT[split:]},
            {'type': 'response.output_text.delta', 'output_index': 1, 'delta': 'partial text to replace'},
            {'type': 'response.output_text.done', 'output_index': 1, 'text': TEXT[:split]},
            {'type': 'response.completed', 'response': {'status': 'completed'}},
        ]
        result, _, _ = self.call(b''.join(sse(event) for event in events))
        self.assertEqual(result, RESULT)

    def test_crlf_comments_ids_retry_and_multiline_data(self):
        event = json.dumps(completed(), ensure_ascii=False, indent=2)
        body = b'\xef\xbb\xbf\r\n: heartbeat\r\nid: 7\r\nretry: 1000\r\n\r\n'
        body += sse(event, 'response.completed', newline='\r\n')
        result, _, _ = self.call(body)
        self.assertEqual(result, RESULT)

    def test_sse_event_name_is_used_when_json_type_is_absent(self):
        event = completed()
        del event['type']
        result, _, _ = self.call(sse(event, 'response.completed'))
        self.assertEqual(result, RESULT)

    def test_completed_last_event_without_trailing_blank_line(self):
        result, _, _ = self.call(sse(completed(), final_blank=False))
        self.assertEqual(result, RESULT)

    def test_completed_does_not_wait_for_or_parse_more_events(self):
        with patch.object(llm_processor.request, 'urlopen', return_value=GuardedResponse(sse(completed()))):
            result = self.processor._responses_json({'groups': []})
        self.assertEqual(result, RESULT)

    def test_completed_without_final_text_uses_previously_collected_delta(self):
        for output in (
            [{'type': 'reasoning', 'summary': []}],
            [{'type': 'message', 'content': []}],
        ):
            with self.subTest(output=output):
                body = sse({'type': 'response.output_text.delta', 'delta': TEXT})
                body += sse(completed(output_text=None, output=output))
                result, _, _ = self.call(body)
                self.assertEqual(result, RESULT)

    def test_failure_events_fail_even_after_valid_json_delta(self):
        for event in [
            {'type': 'response.failed', 'response': {'error': {'message': 'provider failed'}}},
            {'type': 'response.incomplete', 'response': {'incomplete_details': {'reason': 'max_output_tokens'}}},
            {'type': 'error', 'message': 'gateway error'},
            {'type': 'response.cancelled'},
        ]:
            with self.subTest(event=event['type']):
                body = sse({'type': 'response.output_text.delta', 'delta': TEXT}) + sse(event)
                with self.assertRaisesRegex(RuntimeError, 'stream failed'):
                    self.call(body)

    def test_eof_without_completed_rejects_parseable_delta(self):
        with self.assertRaisesRegex(RuntimeError, 'before response.completed'):
            self.call(sse({'type': 'response.output_text.delta', 'delta': TEXT}))

    def test_done_marker_without_completed_rejects_parseable_delta(self):
        with self.assertRaisesRegex(RuntimeError, 'before response.completed'):
            self.call(sse({'type': 'response.output_text.delta', 'delta': TEXT}) + sse('[DONE]'))

    def test_non_object_event_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'JSON object'):
            self.call(sse('[1, 2]'))

    def test_empty_completed_response_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'without output text'):
            self.call(sse({'type': 'response.completed', 'response': {'status': 'completed'}}))

    def test_json_response_works_with_mislabeled_sse_content_type(self):
        response = {'status': 'completed', 'output': [{'content': [{'text': TEXT}]}]}
        result, _, _ = self.call(b'\xef\xbb\xbf\n' + json.dumps(response, ensure_ascii=False).encode('utf-8'), 'text/event-stream')
        self.assertEqual(result, RESULT)

    def test_json_failed_or_incomplete_status_rejected(self):
        for status in ('failed', 'incomplete', 'in_progress'):
            with self.subTest(status=status):
                with self.assertRaisesRegex(RuntimeError, 'did not complete'):
                    self.call(json.dumps({'status': status, 'output_text': TEXT}).encode('utf-8'))

    def test_completed_event_with_failed_status_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'did not complete'):
            self.call(sse(completed(status='failed', error={'message': 'bad'})))

    def test_http_error_still_reports_provider_detail(self):
        error = HTTPError('https://offline.invalid/v1/responses', 400, 'Bad Request', {}, io.BytesIO(b'{"error":{"message":"Stream must be set to true"}}'))
        try:
            with patch.object(llm_processor.request, 'urlopen', side_effect=error):
                with self.assertRaisesRegex(RuntimeError, 'Stream must be set to true'):
                    self.processor._responses_json({'groups': []})
        finally:
            error.close()

    def test_required_true_propagates_stream_failure(self):
        body = sse({'type': 'error', 'message': 'offline failure'})
        with patch.object(llm_processor.request, 'urlopen', return_value=Response(body)):
            with self.assertRaisesRegex(RuntimeError, 'LLM enrichment is required but failed'):
                self.processor.enrich({'groups': []})

    def test_required_false_preserves_fallback_and_marks_unused(self):
        processor = llm_processor.LLMProcessor(
            api_key='offline-test-key',
            base_url='https://offline.invalid/v1',
            model='offline-model',
            enabled=True,
            required=False,
        )
        data = {'groups': [{'name': '音乐', 'items': [{'tweet_url': 'url', 'summary': '原有摘要'}]}], 'top_actions': [{'title': '原有动作'}]}
        expected = copy.deepcopy(data)
        expected['llm_used'] = False
        body = sse({'type': 'response.incomplete', 'response': {'incomplete_details': {'reason': 'max_output_tokens'}}})
        with patch.object(llm_processor.request, 'urlopen', return_value=Response(body)):
            with self.assertLogs(llm_processor.logger, level='WARNING'):
                result = processor.enrich(data)
        self.assertIs(result, data)
        self.assertEqual(result, expected)

    def test_success_enrich_merges_summary_and_marks_used(self):
        data = {'groups': [{'name': '音乐', 'items': [{'tweet_url': 'https://x.com/test/status/1', 'summary': '旧摘要'}]}]}
        with patch.object(llm_processor.request, 'urlopen', return_value=Response(sse(completed()))):
            result = self.processor.enrich(data)
        self.assertTrue(result['llm_used'])
        self.assertEqual(result['groups'][0]['items'][0]['summary'], RESULT['groups'][0]['items'][0]['summary'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
