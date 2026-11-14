from unittest.mock import patch
from celery.exceptions import Retry
from concurrent.futures import ThreadPoolExecutor
from django.test import TransactionTestCase, TestCase, Client
from django.db import connections
from .models import Person, Operation, Fight, FightEvent
from . import tasks
from .remote import RemoteError


class PipelineTests(TransactionTestCase):
    def operation(self, key):
        return Operation.objects.create(key=key, stage='test', label=key)

    def people(self):
        a = Person.objects.create(external_id=0, name='Blue Fighter', age=25, rank='Private')
        b = Person.objects.create(external_id=1, name='Red Fighter', age=27, rank='General')
        return a, b

    def store(self, fight_id=0, steps=None, blue=None, red=None):
        if blue is None:
            blue, red = self.people()
        op = self.operation('store:' + str(fight_id))
        data = {'fight_id': fight_id, 'blue_soldier_id': blue.external_id, 'red_soldier_id': red.external_id,
                'steps': steps or [{'attacker': blue.external_id, 'defender': red.external_id, 'damage': 8}, {'attacker': red.external_id, 'defender': blue.external_id, 'damage': 6}]}
        tasks.save_fight.apply(args=(op.pk, data), throw=True)
        return Fight.objects.get(external_id=fight_id)

    @patch('army.tasks.read_instruction.apply_async')
    @patch('army.tasks.fetch_json')
    def test_import_is_idempotent_and_preserves_external_id_zero(self, fetch, queue):
        fetch.return_value = {'status': 200, 'data': [{'id': 0, 'name': 'Citizen', 'age': 20}, {'id': 99, 'name': 'Hellstorm Fireburn', 'age': 24, 'key': 'draft-info'}]}
        op = self.operation('import')
        tasks.import_people.apply(args=(op.pk,), throw=True)
        tasks.import_people.apply(args=(op.pk,), throw=True)
        self.assertEqual(Person.objects.count(), 2)
        self.assertTrue(Person.objects.filter(external_id=0).exists())
        self.assertEqual(queue.call_count, 1)

    @patch('army.tasks.download_draft_batch.apply_async')
    @patch('army.tasks.save_draft_batch.apply_async')
    @patch('army.tasks.fetch_json')
    def test_draft_download_queues_save_and_next_independently(self, fetch, save, next_batch):
        fetch.return_value = {'status': 200, 'data': [], 'next_batch': 1}
        op = self.operation('draft')
        tasks.download_draft_batch.apply(args=(op.pk, 0), throw=True)
        self.assertEqual(save.call_count, 1)
        self.assertEqual(next_batch.call_count, 1)
        self.assertEqual(Operation.objects.get(pk=op.pk).payload['next_batch'], 1)

    @patch('army.tasks.discover')
    def test_draft_updates_rank_and_next_key_without_duplicate_people(self, discover):
        p = Person.objects.create(external_id=99, name='Hellstorm Fireburn', age=24)
        download = self.operation('download')
        download.payload = {'data': [{'id': 99, 'rank': 'General', 'health': 100, 'key': 'fight-info'}]}
        download.save()
        op = self.operation('save')
        tasks.save_draft_batch.apply(args=(op.pk, download.pk), throw=True)
        p.refresh_from_db()
        self.assertEqual(p.rank, 'General')
        self.assertEqual(p.key, 'fight-info')
        self.assertIsNotNone(p.drafted_at)
        self.assertEqual(Person.objects.count(), 1)
        discover.assert_called_once_with('fight-info')

    @patch('army.tasks.discover')
    @patch('army.tasks.fetch_json')
    def test_transient_timeout_retries_and_completes(self, fetch, discover):
        fetch.side_effect = [RemoteError('timed out'), {'status': 200, 'data': []}]
        op = self.operation('retry-import')
        result = tasks.import_people.apply(args=(op.pk,), throw=False)
        self.assertTrue(result.successful())
        op.refresh_from_db()
        self.assertEqual(op.attempts, 2)
        self.assertEqual(op.status, 'done')

    def test_fight_import_preserves_every_event_and_is_idempotent(self):
        fight = self.store()
        self.assertEqual(fight.events.count(), 2)
        op = self.operation('duplicate-save')
        data = {'fight_id': 0, 'blue_soldier_id': 0, 'red_soldier_id': 1, 'steps': []}
        tasks.save_fight.apply(args=(op.pk, data), throw=True)
        self.assertEqual(Fight.objects.count(), 1)
        self.assertEqual(FightEvent.objects.count(), 2)

    def test_invalid_event_rolls_back_fight_import(self):
        a, b = self.people()
        op = self.operation('invalid-save')
        data = {'fight_id': 0, 'blue_soldier_id': 0, 'red_soldier_id': 1, 'steps': [{'attacker': 77, 'defender': 1, 'damage': 10}]}
        with self.assertRaises(ValueError):
            tasks.save_fight.apply(args=(op.pk, data), throw=True)
        self.assertEqual(Fight.objects.count(), 0)
        self.assertEqual(FightEvent.objects.count(), 0)

    def test_replay_records_health_and_cannot_apply_damage_twice(self):
        fight = self.store()
        op = self.operation('replay')
        tasks.simulate_fight.apply(args=(op.pk, fight.pk), throw=True)
        fight.refresh_from_db()
        self.assertEqual((fight.blue_health, fight.red_health), (94, 92))
        self.assertEqual(list(fight.events.values_list('actual_damage', flat=True)), [8, 6])
        self.assertIsNone(fight.winner)
        tasks.simulate_fight.apply(args=(op.pk, fight.pk), throw=True)
        self.assertEqual(Person.objects.get(external_id=1).health, 92)

    def test_health_is_clamped_but_original_attack_is_preserved(self):
        fight = self.store(steps=[{'attacker': 0, 'defender': 1, 'damage': 120}])
        op = self.operation('lethal-replay')
        tasks.simulate_fight.apply(args=(op.pk, fight.pk), throw=True)
        fight.refresh_from_db()
        event = fight.events.get()
        self.assertEqual(event.damage, 120)
        self.assertEqual(event.actual_damage, 100)
        self.assertEqual(event.defender_health_after, 0)
        self.assertEqual(fight.winner_id, fight.blue_id)

    def test_shared_defender_takes_damage_from_both_fights(self):
        a, b = self.people()
        c = Person.objects.create(external_id=2, name='Other Blue', age=30, rank='General')
        f1 = self.store(1, [{'attacker': 0, 'defender': 1, 'damage': 17}], a, b)
        f2 = self.store(2, [{'attacker': 2, 'defender': 1, 'damage': 23}], c, b)
        op1, op2 = self.operation('parallel-1'), self.operation('parallel-2')
        def replay(args):
            try:
                return tasks.simulate_fight.apply(args=args, throw=True).get(disable_sync_subtasks=False)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(replay, [(op1.pk, f1.pk), (op2.pk, f2.pk)]))
        b.refresh_from_db()
        self.assertEqual(b.health, 60)
        self.assertEqual(FightEvent.objects.filter(applied_at__isnull=False).count(), 2)

    def test_simulation_waits_for_pending_imports(self):
        Operation.objects.create(key='pending', stage='fight_download', label='pending')
        with self.assertRaises(ValueError):
            tasks.start_simulation()


class ViewTests(TestCase):
    def test_empty_pages_render(self):
        for path in ['/', '/people', '/simulate', '/api/status']:
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_actions_require_post_and_csrf(self):
        self.assertEqual(self.client.get('/actions/import').status_code, 405)
        self.assertEqual(Client(enforce_csrf_checks=True).post('/actions/import').status_code, 403)

    def test_person_and_fight_history_render(self):
        a = Person.objects.create(external_id=0, name='Blue', age=25)
        b = Person.objects.create(external_id=1, name='Red', age=30)
        f = Fight.objects.create(external_id=0, blue=a, red=b)
        FightEvent.objects.create(fight=f, sequence=1, attacker=a, defender=b, damage=5)
        self.assertContains(self.client.get('/people/' + str(a.pk)), 'Blue')
        self.assertContains(self.client.get('/fight/' + str(f.pk)), 'Red')

    @patch('army.remote.urllib.request.urlopen')
    def test_instructions_accept_ok_status(self, open_url):
        import io
        open_url.return_value.__enter__.return_value = io.BytesIO(b'{"data":{"message":"next"},"status":"ok"}')
        from .remote import fetch_json
        self.assertEqual(fetch_json('/fight-info')['data']['message'], 'next')

    def test_remote_url_stays_on_challenge_host(self):
        from .remote import fetch_json
        for path in ['https://other.invalid/', '//other.invalid/', '/../secrets']:
            with self.assertRaises(ValueError):
                fetch_json(path)
