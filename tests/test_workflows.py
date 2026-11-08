"""Her workflow dosyası geçerli YAML olmalı ve beklenen işi tanımlamalı.

Regresyon (2026-11-08): `aggregator-probe.yml` içine eklenen bir adım, `run: |`
bloğunun içine girintisiz yazıldı. GitHub o dosyayı **hiç çalıştırmadı**: iş
listede "workflow file issue" olarak kaydedildi, kullanıcı "test bozuk, run bile
çıkmıyor" dedi. Birim testleri bunu görmedi çünkü YAML hiç okunmuyordu.

Bu test, dosya bozulduğunda **commit aşamasında** kırmızı yakar.
"""
import sys
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / '.github' / 'workflows'


def load(path):
    try:
        import yaml
    except ImportError:                     # CI'da kurulur; yerelde yoksa atla
        raise unittest.SkipTest('pyyaml kurulu değil')
    return yaml.safe_load(path.read_text(encoding='utf-8'))


class WorkflowYamlTests(unittest.TestCase):
    def setUp(self):
        if not WORKFLOWS.is_dir():
            self.skipTest('workflow dizini yok')

    def test_every_workflow_is_valid_yaml_and_has_a_name(self):
        files = sorted(WORKFLOWS.glob('*.yml')) + sorted(WORKFLOWS.glob('*.yaml'))
        self.assertTrue(files, 'en az bir workflow olmalı')
        for path in files:
            with self.subTest(workflow=path.name):
                data = load(path)
                self.assertIsInstance(data, dict, f'{path.name}: kök nesne değil')
                self.assertIn('name', data, f'{path.name}: "name" yok')
                # YAML'da "on" anahtarı True'ya dönüşebilir; ikisini de kabul et.
                trigger = data.get('on', data.get(True))
                self.assertIsNotNone(trigger, f'{path.name}: tetikleyici yok')
                self.assertIn('jobs', data, f'{path.name}: "jobs" yok')
                self.assertIsInstance(data['jobs'], dict)
                self.assertTrue(data['jobs'], f'{path.name}: iş tanımsız')

    def test_every_job_has_a_runner_and_steps_or_uses(self):
        for path in sorted(WORKFLOWS.glob('*.*ml')):
            data = load(path)
            for name, job in (data.get('jobs') or {}).items():
                with self.subTest(workflow=path.name, job=name):
                    if 'uses' in job:
                        continue
                    self.assertIn('runs-on', job, f'{path.name}/{name}: runs-on yok')
                    steps = job.get('steps')
                    self.assertIsInstance(steps, list, f'{path.name}/{name}: adım yok')
                    for step in steps:
                        self.assertTrue(
                            isinstance(step, dict) and ('run' in step or 'uses' in step),
                            f'{path.name}/{name}: adımda run/uses yok')

    def test_run_blocks_are_non_empty_strings(self):
        """`run: |` bozulduğunda genelde komut dizesi None/kısa kalır."""
        for path in sorted(WORKFLOWS.glob('*.*ml')):
            data = load(path)
            for name, job in (data.get('jobs') or {}).items():
                for step in job.get('steps') or []:
                    if 'run' not in step:
                        continue
                    with self.subTest(workflow=path.name, job=name,
                                      step=step.get('name', '?')):
                        self.assertIsInstance(step['run'], str)
                        self.assertTrue(step['run'].strip(), 'boş run bloğu')

    def test_probe_workflow_is_manual_only_and_secret_free(self):
        """Teşhis işleri elle tetiklenir: cron yok, secret yok."""
        for name in ('aggregator-probe.yml', 'store-probe.yml'):
            data = load(WORKFLOWS / name)
            trigger = data.get('on', data.get(True))
            self.assertEqual(list(trigger), ['workflow_dispatch'], name)
            text = (WORKFLOWS / name).read_text(encoding='utf-8')
            self.assertNotIn('secrets.', text, f'{name}: secret kullanmamalı')

    def test_test_workflow_runs_the_suite(self):
        data = load(WORKFLOWS / 'tests.yml')
        runs = [step.get('run', '') for step in data['jobs']['test']['steps']]
        self.assertTrue(any('unittest discover' in run for run in runs))


if __name__ == '__main__':
    unittest.main()
