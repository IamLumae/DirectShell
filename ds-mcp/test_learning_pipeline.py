"""Synthetic learning evidence only; never reads real action logs."""
import unittest
import tip_miner


class LearningEvidence(unittest.TestCase):
    def test_feedback_belongs_to_previous_action_not_current_action(self):
        entries = [
            dict(ts=1, tool='ds_click', app='fixture', prev_ok='unknown'),
            dict(ts=2, tool='ds_text', app='fixture', prev_ok='no'),
            dict(ts=3, tool='ds_update_view', app='fixture', prev_ok='yes'),
        ]
        chains = tip_miner.extract_failure_chains(entries)
        self.assertEqual(len(chains), 1)
        self.assertEqual(chains[0]['failed_tool'], 'ds_click')
        self.assertEqual(chains[0]['recovery_tool'], 'ds_text')

    def test_unknown_feedback_and_context_switch_are_not_recovery_proof(self):
        entries = [
            dict(ts=1, tool='ds_click', app='a', prev_ok='unknown'),
            dict(ts=2, tool='ds_text', app='a', prev_ok='no'),
            dict(ts=3, tool='ds_click', app='b', prev_ok='unknown'),
            dict(ts=4, tool='ds_update_view', app='b', prev_ok='yes'),
        ]
        self.assertEqual(tip_miner.extract_failure_chains(entries), [])


if __name__ == '__main__': unittest.main()
