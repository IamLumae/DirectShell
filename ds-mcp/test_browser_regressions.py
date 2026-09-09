"""Offline protocol regressions; does not start DS, a browser or user applications."""
import json
import unittest
from unittest.mock import patch
import server


class Socket:
    def __init__(self): self.messages=[]
    def send(self, value): self.messages.append(json.loads(value))
    def recv(self): return json.dumps({'id':self.messages[-1]['id'], 'result':{}})
    def close(self): pass


class BrowserContract(unittest.TestCase):
    def test_missing_observed_element_never_falls_back_to_old_coordinates(self):
        ws=Socket()
        with patch.object(server,'_cdp_eval',return_value={'result':{'result':{'value':'not_found'}}}):
            self.assertIsNone(server._cdp_find_coords_for_tool(ws,{'dsid':'gone','selector':'#replacement','x':50,'y':70}))

    def test_click_rejects_cdp_error_instead_of_claiming_clicked(self):
        ws=Socket()
        ws.recv=lambda: json.dumps({'id':ws.messages[-1]['id'], 'error':{'message':'Input rejected'}})
        with self.assertRaisesRegex(RuntimeError, 'Input rejected'):
            server._cdp_dispatch_click(ws, 10, 10)

    def test_click_ignores_notifications_until_matching_ack(self):
        ws=Socket()
        responses=iter([json.dumps({'method':'Page.event'}),json.dumps({'id':2,'result':{}}),json.dumps({'id':3,'result':{}})])
        ws.recv=lambda:next(responses)
        server._cdp_dispatch_click(ws,10,10)
        self.assertEqual(list(responses),[])

    def test_enter_has_text_for_browser_implicit_submit(self):
        ws=Socket()
        with patch.object(server,'_cdp_ws',return_value=ws):
            server._cdp_key('enter')
        down=ws.messages[0]['params']
        self.assertEqual(down['text'], '\r')
        self.assertEqual(down['unmodifiedText'], '\r')
        self.assertNotIn('text', ws.messages[-1]['params'])

    def test_keys_trim_aliases_and_reject_unknown_instead_of_false_success(self):
        ws=Socket()
        with patch.object(server,'_cdp_ws',return_value=ws):
            server._cdp_key(' control + a ')
            self.assertEqual(ws.messages[0]['params']['modifiers'],2)
            with self.assertRaises(ValueError): server._cdp_key('invented-key')


if __name__=='__main__': unittest.main()
