import unittest

from sambinha.strategies.batch import snapshot


class SnapshotTests(unittest.TestCase):
    def test_mutations_after_the_snapshot_do_not_leak(self):
        control = {'files': [{'path': 'a'}]}
        state = {'active': {'k': control}, 'excluded': {'x': control}, 'origins': [{'host': 'h'}],
                 'retired': [{'key': 'r', 'control': 'r.json', 'files': ['a']}], 'last_full_scan': 1}
        copy = snapshot(state)
        self.assertEqual(copy, state)
        copy['active']['new'] = control
        copy['excluded'].pop('x')
        copy['retired'][0].pop('control')
        copy['retired'].append({'key': 'q'})
        copy['origins'][0]['host'] = 'other'
        copy['last_full_scan'] = 2
        self.assertEqual(state, {'active': {'k': control}, 'excluded': {'x': control}, 'origins': [{'host': 'h'}],
                                 'retired': [{'key': 'r', 'control': 'r.json', 'files': ['a']}],
                                 'last_full_scan': 1})
        state['retired'][0]['files'] = ['z']  # and the other way around
        self.assertEqual(copy['retired'][0]['files'], ['a'])
