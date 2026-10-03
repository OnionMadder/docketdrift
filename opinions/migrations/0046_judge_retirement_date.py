from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('opinions', '0045_statutecitation_is_boilerplate'),
    ]

    operations = [
        migrations.AddField(
            model_name='judge',
            name='retirement_date',
            field=models.DateField(blank=True, help_text='When this judge left the bench, from a cited source. Leave empty if unknown -- do not infer it from the last vote.', null=True),
        ),
    ]
