import unittest
from unittest.mock import Mock, patch
from fusionedges import server

class ProgressTests(unittest.TestCase):
    def test_worker_exposes_assignment_before_completion(self):
        job=server.Job('adaptive')
        def run(params,log,stage,run_id):
            stage('port','running')
            self.assertEqual(job.public()['routing'][-1]['route'],'rules')
            stage('assignment','update','{"route":"agent","reasons":["Framework API mismatch"]}')
            self.assertEqual(job.public()['routing'][-1]['reasons'],['Framework API mismatch'])
            return {}
        with patch.object(server,'run',side_effect=run):server._worker(job,{'mode':'adaptive'})
        self.assertEqual(job.status,'done')
    def test_canceled_response_does_not_attempt_error_response(self):
        for where,error in [('end_headers',BrokenPipeError()),('write',ConnectionResetError())]:
            handler=object.__new__(server.Handler)
            handler.send_response=Mock();handler.send_header=Mock();handler.end_headers=Mock();handler.wfile=Mock()
            target=handler.end_headers if where=='end_headers' else handler.wfile.write
            target.side_effect=error
            handler._send(b'body','text/plain')
            self.assertTrue(handler.close_connection)
            handler.send_response.assert_called_once_with(200)
