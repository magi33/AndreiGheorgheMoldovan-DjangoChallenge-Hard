from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Sum, F, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from .models import Person, Operation, Instruction, Fight, FightEvent
from . import tasks


def metrics():
    return {
        'people': Person.objects.count(),
        'drafted': Person.objects.exclude(rank='').count(),
        'fights': Fight.objects.count(),
        'events': FightEvent.objects.count(),
        'replayed': Fight.objects.filter(status='done').count(),
        'pending': Operation.objects.filter(status__in=['queued', 'running', 'retry']).count(),
        'failed': Operation.objects.filter(status='failed').count(),
    }


def home(request):
    return render(request, 'army/home.html', {'stats': metrics(), 'operations': Operation.objects.all()[:30], 'instructions': Instruction.objects.all(), 'hellstorm': Person.objects.filter(name='Hellstorm Fireburn').first()})


def people(request):
    queryset = Person.objects.all()
    search = request.GET.get('q', '').strip()
    rank = request.GET.get('rank', '')
    if search:
        queryset = queryset.filter(name__icontains=search)
    if rank:
        queryset = queryset.filter(rank=rank)
    return render(request, 'army/people.html', {'page': Paginator(queryset, 24).get_page(request.GET.get('page')), 'search': search, 'selected_rank': rank, 'ranks': Person.objects.exclude(rank='').values_list('rank', flat=True).distinct().order_by('rank'), 'stats': metrics()})


def person_detail(request, pk):
    person = get_object_or_404(Person, pk=pk)
    fights = Fight.objects.filter(Q(blue=person) | Q(red=person)).select_related('blue', 'red', 'winner')
    return render(request, 'army/person.html', {'person': person, 'fights': fights, 'stats': metrics()})


def team_stats():
    result = []
    for team, field in [('Blue', 'blue_id'), ('Red', 'red_id')]:
        caused = FightEvent.objects.filter(attacker_id=F('fight__' + field)).aggregate(damage=Sum('damage'), actual=Sum('actual_damage'))
        taken = FightEvent.objects.filter(defender_id=F('fight__' + field)).aggregate(damage=Sum('damage'), actual=Sum('actual_damage'))
        result.append({'team': team, 'fights': Fight.objects.count(), 'damage_caused': caused['damage'] or 0, 'damage_taken': taken['damage'] or 0, 'actual_caused': caused['actual'] or 0, 'actual_taken': taken['actual'] or 0})
    return result


def simulate(request):
    if request.method == 'POST':
        try:
            count = tasks.start_simulation()
            messages.success(request, f'{count} fights queued for independent background replay.')
        except (ValueError, OSError) as exc:
            messages.error(request, str(exc))
        return redirect('simulate')
    queryset = Fight.objects.select_related('blue', 'red', 'winner').annotate(event_count=Count('events')).order_by('external_id')
    return render(request, 'army/simulate.html', {'page': Paginator(queryset, 24).get_page(request.GET.get('page')), 'stats': metrics(), 'teams': team_stats(), 'imports_pending': Operation.objects.filter(stage__in=['fight_download', 'fight_save'], status__in=['queued', 'running', 'retry']).exists()})


def fight_detail(request, pk):
    fight = get_object_or_404(Fight.objects.select_related('blue', 'red', 'winner'), pk=pk)
    if request.method == 'POST':
        tasks.enqueue('fight_simulate', 'simulate:' + str(fight.external_id), 'Replay fight ' + str(fight.external_id), tasks.simulate_fight, fight.pk)
        messages.success(request, 'Replay queued. The worker will process this fight.')
        return redirect('fight_detail', pk=pk)
    return render(request, 'army/fight.html', {'fight': fight, 'events': fight.events.select_related('attacker', 'defender'), 'stats': metrics()})


@require_POST
def action(request, name):
    try:
        if name == 'import':
            tasks.start_import()
        elif name == 'draft':
            tasks.start_drafting()
        elif name == 'fights':
            tasks.start_fights()
        else:
            messages.error(request, 'Unknown action.')
            return redirect('home')
        messages.success(request, 'Operation queued. Keep the Celery worker terminal running.')
    except (ValueError, OSError) as exc:
        messages.error(request, str(exc))
    return redirect('home')


def status(request):
    return JsonResponse({'stats': metrics(), 'operations': list(Operation.objects.values('key', 'stage', 'status', 'attempts', 'error')[:50])})
