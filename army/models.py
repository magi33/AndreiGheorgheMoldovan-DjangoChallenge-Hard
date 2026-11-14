from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator


class Person(models.Model):
    external_id = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=160)
    age = models.PositiveIntegerField()
    key = models.CharField(max_length=200, blank=True)
    rank = models.CharField(max_length=80, blank=True)
    team = models.CharField(max_length=4, blank=True, choices=[('Blue', 'Blue'), ('Red', 'Red')])
    health = models.PositiveSmallIntegerField(default=100, validators=[MinValueValidator(0), MaxValueValidator(100)])
    payload = models.JSONField(default=dict)
    drafted_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['external_id']

    def __str__(self):
        return self.name


class Operation(models.Model):
    key = models.CharField(max_length=220, unique=True)
    stage = models.CharField(max_length=50)
    label = models.CharField(max_length=200)
    status = models.CharField(max_length=16, default='queued')
    task_id = models.CharField(max_length=80, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    payload = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']


class Instruction(models.Model):
    key = models.CharField(max_length=200, unique=True)
    message = models.TextField()
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class Fight(models.Model):
    external_id = models.PositiveIntegerField(unique=True)
    blue = models.ForeignKey(Person, on_delete=models.PROTECT, related_name='blue_fights')
    red = models.ForeignKey(Person, on_delete=models.PROTECT, related_name='red_fights')
    status = models.CharField(max_length=16, default='ready')
    blue_health = models.PositiveIntegerField(null=True, blank=True)
    red_health = models.PositiveIntegerField(null=True, blank=True)
    winner = models.ForeignKey(Person, on_delete=models.PROTECT, null=True, blank=True, related_name='won_fights')
    replayed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['external_id']


class FightEvent(models.Model):
    fight = models.ForeignKey(Fight, on_delete=models.CASCADE, related_name='events')
    sequence = models.PositiveIntegerField()
    attacker = models.ForeignKey(Person, on_delete=models.PROTECT, related_name='attacks')
    defender = models.ForeignKey(Person, on_delete=models.PROTECT, related_name='defences')
    damage = models.PositiveIntegerField()
    actual_damage = models.PositiveIntegerField(null=True, blank=True)
    defender_health_before = models.PositiveIntegerField(null=True, blank=True)
    defender_health_after = models.PositiveIntegerField(null=True, blank=True)
    blue_health = models.PositiveIntegerField(null=True, blank=True)
    red_health = models.PositiveIntegerField(null=True, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['sequence']
        constraints = [models.UniqueConstraint(fields=['fight', 'sequence'], name='unique_fight_step')]
