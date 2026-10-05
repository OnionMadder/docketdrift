from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('opinions', '0046_judge_retirement_date'),
    ]

    operations = [
        migrations.AddField(
            model_name='judge',
            name='appointment_date_precision',
            field=models.CharField(choices=[('day', 'Day'), ('month', 'Month'), ('year', 'Year')], default='day', help_text="How much of appointment_date the source states. 'year' renders as '2019', 'month' as 'December 2017'.", max_length=5),
        ),
        migrations.AddField(
            model_name='judge',
            name='appointment_date_event',
            field=models.CharField(choices=[('appointed', 'Appointed'), ('seated', 'Seated')], default='appointed', help_text='Whether appointment_date is the appointment or the day the judge took the seat.', max_length=9),
        ),
    ]
