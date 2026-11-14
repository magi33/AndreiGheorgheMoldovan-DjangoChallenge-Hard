from django.contrib import admin
from .models import Person, Operation, Instruction


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ('external_id', 'name', 'age', 'rank', 'health', 'drafted_at')
    list_filter = ('rank',)
    search_fields = ('name',)


admin.site.register(Operation)
admin.site.register(Instruction)
