import uuid
from celery import Task, shared_task
from kombu.exceptions import OperationalError as BrokerError
from django.db import OperationalError, transaction, close_old_connections
from django.db.models import F, Value
from django.db.models.functions import Greatest
from django.utils import timezone
from .models import Person, Operation, Instruction, Fight, FightEvent
from .remote import fetch_json, RemoteError


class RecordedTask(Task):
    autoretry_for = (RemoteError, OperationalError, BrokerError)
    retry_kwargs = {'max_retries': 5}
    retry_backoff = 2
    retry_backoff_max = 30
    retry_jitter = True

    def on_retry(self, exc, task_id, args, kwargs, einfo):
        close_old_connections()
        Operation.objects.filter(pk=args[0]).update(status='retry', error=str(exc)[:2000], updated_at=timezone.now())

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        close_old_connections()
        Operation.objects.filter(pk=args[0]).update(status='failed', error=str(exc)[:2000], updated_at=timezone.now())


def enqueue(stage, key, label, task, *args, force=False):
    with transaction.atomic():
        operation, created = Operation.objects.get_or_create(key=key, defaults={'stage': stage, 'label': label})
        if not created and not force and operation.status not in ('failed',):
            return operation
        task_id = str(uuid.uuid4())
        operation.status = 'queued'
        operation.task_id = task_id
        operation.error = ''
        operation.save(update_fields=['status', 'task_id', 'error', 'updated_at'])
        transaction.on_commit(lambda: task.apply_async(args=(operation.pk, *args), task_id=task_id))
    return operation


def begin(operation_id):
    close_old_connections()
    Operation.objects.filter(pk=operation_id).update(status='running', attempts=F('attempts') + 1, error='', updated_at=timezone.now())


def finish(operation_id, payload):
    Operation.objects.filter(pk=operation_id).update(status='done', payload=payload, error='', updated_at=timezone.now())


def discover(key):
    if key:
        return enqueue('instructions', 'instruction:' + key, 'Instructions: ' + key, read_instruction, key)


def start_import(force=False):
    return enqueue('people', 'people', 'Register all people', import_people, force=force)


def start_drafting(force=False):
    if not Person.objects.exists():
        raise ValueError('Import the people first.')
    return enqueue('draft_download', 'draft-download:0', 'Download draft batch 0', download_draft_batch, 0, force=force)


@shared_task(bind=True, base=RecordedTask)
def import_people(self, operation_id):
    begin(operation_id)
    result = fetch_json('/people')
    records = result['data']
    if not isinstance(records, list):
        raise ValueError('The people response must contain a list.')
    with transaction.atomic():
        for record in records:
            Person.objects.update_or_create(external_id=record['id'], defaults={
                'name': record['name'], 'age': record['age'], 'key': record.get('key', ''), 'payload': record,
            })
        finish(operation_id, result)
    hellstorm = Person.objects.filter(name='Hellstorm Fireburn').first()
    if hellstorm:
        discover(hellstorm.key)
    return {'registered': len(records)}


@shared_task(bind=True, base=RecordedTask)
def read_instruction(self, operation_id, key):
    begin(operation_id)
    result = fetch_json('/' + key.lstrip('/'))
    content = result.get('data', result)
    message = content.get('message', '') if isinstance(content, dict) else str(content)
    Instruction.objects.update_or_create(key=key, defaults={'message': message, 'payload': result})
    finish(operation_id, result)
    return result


@shared_task(bind=True, base=RecordedTask)
def download_draft_batch(self, operation_id, cursor):
    begin(operation_id)
    result = fetch_json('/draft/' + str(cursor))
    if not isinstance(result.get('data'), list):
        raise ValueError('Draft data is not a list.')
    finish(operation_id, result)
    enqueue('draft_save', 'draft-save:' + str(cursor), 'Save draft batch ' + str(cursor), save_draft_batch, operation_id)
    next_cursor = result.get('next_batch')
    if next_cursor is not None:
        if not isinstance(next_cursor, (int, str)) or isinstance(next_cursor, bool):
            raise ValueError('Invalid next batch cursor.')
        enqueue('draft_download', 'draft-download:' + str(next_cursor), 'Download draft batch ' + str(next_cursor), download_draft_batch, next_cursor)
    return {'batch': cursor, 'people': len(result['data']), 'next_batch': next_cursor}


@shared_task(bind=True, base=RecordedTask)
def save_draft_batch(self, operation_id, download_id):
    begin(operation_id)
    result = Operation.objects.get(pk=download_id).payload
    keys = []
    with transaction.atomic():
        for record in result['data']:
            person = Person.objects.get(external_id=record['id'])
            person.rank = record['rank']
            person.health = record.get('health', person.health)
            person.drafted_at = timezone.now()
            person.payload = {**person.payload, **record}
            if record.get('key'):
                person.key = record['key']
                if person.name == 'Hellstorm Fireburn':
                    keys.append(person.key)
            person.save()
        finish(operation_id, {'saved': len(result['data']), 'download_id': download_id})
    for key in keys:
        discover(key)
    return {'saved': len(result['data'])}


def start_fights(force=False):
    if not Person.objects.exists() or Person.objects.filter(rank='').exists():
        raise ValueError('Finish drafting all registered people first.')
    return enqueue('fight_download', 'fight-download:0', 'Download fight batch 0', download_fight_batch, 0, force=force)


def start_simulation():
    if Operation.objects.filter(stage__in=['fight_download', 'fight_save'], status__in=['queued', 'running', 'retry']).exists():
        raise ValueError('Wait until all fights and events have been imported.')
    count = Fight.objects.exclude(status='done').count()
    if count:
        existing = Operation.objects.filter(key='simulate-all').first()
        enqueue('simulation_setup', 'simulate-all', 'Queue independent fight replays', queue_replays, force=bool(existing and existing.status == 'done'))
    return count


@shared_task(bind=True, base=RecordedTask)
def queue_replays(self, operation_id):
    begin(operation_id)
    ids = list(Fight.objects.exclude(status='done').values_list('pk', 'external_id'))
    for pk, external_id in ids:
        enqueue('fight_simulate', 'simulate:' + str(external_id), 'Replay fight ' + str(external_id), simulate_fight, pk)
    finish(operation_id, {'queued': len(ids)})
    return {'queued': len(ids)}


@shared_task(bind=True, base=RecordedTask)
def download_fight_batch(self, operation_id, cursor):
    begin(operation_id)
    result = fetch_json('/fight/' + str(cursor))
    if not isinstance(result.get('data'), list):
        raise ValueError('Fight data is not a list.')
    finish(operation_id, result)
    for fight in result['data']:
        enqueue('fight_save', 'fight-save:' + str(fight['fight_id']), 'Store fight ' + str(fight['fight_id']), save_fight, fight)
    next_cursor = result.get('next_batch', result.get('next'))
    if next_cursor is not None:
        if not isinstance(next_cursor, (int, str)) or isinstance(next_cursor, bool):
            raise ValueError('Invalid next fight cursor.')
        enqueue('fight_download', 'fight-download:' + str(next_cursor), 'Download fight batch ' + str(next_cursor), download_fight_batch, next_cursor)
    else:
        discover('simulate-fight-info')
    return {'batch': cursor, 'fights': len(result['data']), 'next_batch': next_cursor}


@shared_task(bind=True, base=RecordedTask)
def save_fight(self, operation_id, data):
    begin(operation_id)
    blue = Person.objects.get(external_id=data['blue_soldier_id'])
    red = Person.objects.get(external_id=data['red_soldier_id'])
    if blue.pk == red.pk:
        raise ValueError('A fighter cannot fight themselves.')
    participants = {blue.external_id: blue, red.external_id: red}
    with transaction.atomic():
        fight, created = Fight.objects.get_or_create(external_id=data['fight_id'], defaults={'blue': blue, 'red': red})
        if created:
            Person.objects.filter(pk=blue.pk).update(team='Blue')
            Person.objects.filter(pk=red.pk).update(team='Red')
            events = []
            for sequence, step in enumerate(data['steps'], start=1):
                if step['attacker'] not in participants or step['defender'] not in participants or step['attacker'] == step['defender']:
                    raise ValueError('Attack references invalid participants.')
                if type(step['damage']) is not int or step['damage'] < 0:
                    raise ValueError('Damage must be a nonnegative integer.')
                events.append(FightEvent(fight=fight, sequence=sequence, attacker=participants[step['attacker']], defender=participants[step['defender']], damage=step['damage']))
            FightEvent.objects.bulk_create(events)
        finish(operation_id, {'fight': fight.external_id, 'events': fight.events.count()})
    return {'fight': fight.external_id, 'events': fight.events.count()}


@shared_task(bind=True, base=RecordedTask)
def simulate_fight(self, operation_id, fight_id):
    begin(operation_id)
    with transaction.atomic():
        claimed = Fight.objects.filter(pk=fight_id, status='ready').update(status='running')
        if not claimed:
            fight = Fight.objects.get(pk=fight_id)
            finish(operation_id, {'fight': fight.external_id, 'already_processed': True})
            return {'already_processed': True}
        fight = Fight.objects.select_related('blue', 'red').get(pk=fight_id)
        events = list(fight.events.all())
        for event in events:
            before = Person.objects.get(pk=event.defender_id).health
            Person.objects.filter(pk=event.defender_id).update(health=Greatest(Value(0), F('health') - event.damage))
            after = Person.objects.get(pk=event.defender_id).health
            health = dict(Person.objects.filter(pk__in=[fight.blue_id, fight.red_id]).values_list('pk', 'health'))
            event.defender_health_before = before
            event.defender_health_after = after
            event.actual_damage = before - after
            event.blue_health = health[fight.blue_id]
            event.red_health = health[fight.red_id]
            event.applied_at = timezone.now()
        FightEvent.objects.bulk_update(events, ['defender_health_before', 'defender_health_after', 'actual_damage', 'blue_health', 'red_health', 'applied_at'])
        health = dict(Person.objects.filter(pk__in=[fight.blue_id, fight.red_id]).values_list('pk', 'health'))
        fight.blue_health = health[fight.blue_id]
        fight.red_health = health[fight.red_id]
        fight.status = 'done'
        fight.replayed_at = timezone.now()
        if fight.red_health == 0 and fight.blue_health > 0:
            fight.winner_id = fight.blue_id
        elif fight.blue_health == 0 and fight.red_health > 0:
            fight.winner_id = fight.red_id
        fight.save()
        finish(operation_id, {'fight': fight.external_id, 'events': len(events), 'blue_health': fight.blue_health, 'red_health': fight.red_health})
    return {'fight': fight.external_id, 'events': len(events)}
