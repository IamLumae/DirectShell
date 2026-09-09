import types
import unittest
from unittest.mock import Mock,patch
from attia_entry import await_browser_document


class BrowserReadiness(unittest.TestCase):
    def test_listening_port_does_not_mean_ready_and_reads_do_not_navigate(self):
        ds=types.SimpleNamespace(_cdp_tabs=Mock(return_value=[{'id':'own','type':'page','url':'http://fixture/'}]),
            _cdp_ws=Mock(return_value=Mock()),_cdp_eval=Mock(side_effect=[
                {'result':{'result':{'value':{'state':'complete','url':'about:blank'}}}},
                {'result':{'result':{'value':{'state':'loading','url':'http://fixture/'}}}},
                {'result':{'result':{'value':{'state':'interactive','url':'http://fixture/'}}}},
            ]))
        with patch('attia_entry.time.sleep'):
            await_browser_document(ds,Mock(poll=Mock(return_value=None)),'http://fixture/')
        self.assertEqual(ds._cdp_active_tab_id,'own')
        self.assertEqual(ds._cdp_eval.call_count,3)
        self.assertEqual(ds._cdp_ws.return_value.close.call_count,3)
    def test_dead_browser_is_not_ready(self):
        with self.assertRaisesRegex(Exception,'exited'):
            await_browser_document(types.SimpleNamespace(),Mock(poll=Mock(return_value=1)),'http://fixture/')


if __name__=='__main__':unittest.main()
