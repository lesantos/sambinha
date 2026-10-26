import unittest
from unittest.mock import Mock

from sambinha.errors import JobError, job_errors


class JobErrorTests(unittest.TestCase):
    def test_error_preserves_job_and_cause_without_duplicate_prefix(self):
        cause = PermissionError('sem acesso')
        with self.assertRaises(JobError) as raised, job_errors('first'), job_errors('first'):
            raise cause
        self.assertEqual(raised.exception.job, 'first')
        self.assertEqual(str(raised.exception), 'Job first: sem acesso')
        self.assertIs(raised.exception.__cause__, cause)

    def test_error_callback_can_continue_but_does_not_swallow_interrupt(self):
        callback = Mock()
        with job_errors('first', callback):
            raise ValueError('inválido')
        self.assertEqual(str(callback.call_args.args[0]), 'Job first: inválido')
        with self.assertRaises(KeyboardInterrupt), job_errors('first', callback):
            raise KeyboardInterrupt
        callback.assert_called_once()
