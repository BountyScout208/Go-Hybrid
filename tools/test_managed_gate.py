"""Regression tests for lossless source/report pairing in the managed gate."""
import contextlib
import importlib.util
import io
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'managed_gate', pathlib.Path(__file__).with_name('managed_gate.py'))
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def block(reason):
    return {'reason': reason, 'category': 'other'}


class ManagedGateTests(unittest.TestCase):
    def audit(self, source, blocks, returncode=0):
        with tempfile.TemporaryDirectory() as directory:
            prog = pathlib.Path(directory) / 'test.hy'
            prog.write_text(source, encoding='utf-8')
            report = {'summary': {'managed': {'blocks': blocks}}}
            result = subprocess.CompletedProcess([], returncode, json.dumps(report), '')
            with patch.object(gate.subprocess, 'run', return_value=result):
                return gate.audit('hyb', str(prog))


    def test_separate_lines_keep_existing_baseline_keys(self):
        found = self.audit('managed("a", "other") {}\nmanaged("b", "other") {}',
                           [block('a'), block('b')])
        self.assertEqual([key.rsplit(':', 1)[1] for key in found], ['1', '2'])
        self.assertEqual(list(found.values()), [block('a'), block('b')])

    def test_empty_file_has_no_blocks(self):
        self.assertEqual(self.audit('fn main() {}', []), {})

    def test_removed_block_keeps_remaining_location(self):
        found = self.audit('\nmanaged("b", "other") {}', [block('b')])
        self.assertEqual([key.rsplit(':', 1)[1] for key in found], ['2'])

    def assert_mapping_error(self, source, blocks):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as error:
            self.audit(source, blocks)
        self.assertEqual(error.exception.code, 2)
        self.assertIn('GATE ERROR', output.getvalue())

    def test_two_blocks_on_one_line_cannot_create_ambiguous_baseline(self):
        self.assert_mapping_error('managed("a", "other") {} managed("c", "other") {}',
                                  [block('a'), block('c')])

    def test_inserted_middle_block_cannot_be_overwritten_at_question_mark_key(self):
        self.assert_mapping_error(
            'managed("a", "other") {} managed("NEW", "other") {} managed("c", "other") {}',
            [block('a'), block('NEW'), block('c')])

    def test_unlocated_reported_block_fails_closed(self):
        self.assert_mapping_error('fn main() {}', [block('missing')])

    def test_extra_source_candidate_is_not_paired_with_wrong_report(self):
        self.assert_mapping_error('// mentions managed("not code", "other")\nmanaged("a", "other") {}',
                                  [block('a')])

    def test_invalid_category_still_fails_closed(self):
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as error:
            self.audit('managed("a", "invalid") {}',
                       [{'reason': 'a', 'category': 'invalid'}])
        self.assertEqual(error.exception.code, 2)

    def test_checker_failure_fails_the_gate_even_with_parseable_output(self):
        # Issue #53: a rejected/malformed program must not read as "zero
        # blocks". Even a parseable JSON report cannot back a PASS (or a
        # rebaseline) when hyb check itself exited nonzero.
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as error:
            self.audit('fn main( {}', [], returncode=1)
        self.assertEqual(error.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
