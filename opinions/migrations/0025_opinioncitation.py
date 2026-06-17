import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('opinions', '0024_opinion_reporter_cite'),
    ]

    operations = [
        migrations.CreateModel(
            name='OpinionCitation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cited_reference', models.CharField(db_index=True, help_text="Reporter cite as referenced, e.g. '2026 N.H. 7'. Always set.", max_length=64)),
                ('treatment', models.CharField(choices=[('CITED', 'Cited'), ('FOLLOWED', 'Followed'), ('DISTINGUISHED', 'Distinguished'), ('OVERRULED', 'Overruled'), ('CRITICIZED', 'Criticized'), ('EXPLAINED', 'Explained')], db_index=True, default='CITED', help_text='How the citing opinion treats the cited case (Phase 14b classifier; default CITED).', max_length=16)),
                ('context', models.TextField(blank=True, default='', help_text='Text window around the citation -- for treatment cues + display.')),
                ('text_offset', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('cited_opinion', models.ForeignKey(blank=True, help_text='Resolved target opinion in our corpus; null for external authority.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='citations_received', to='opinions.opinion')),
                ('citing_opinion', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='citations_made', to='opinions.opinion')),
            ],
            options={
                'ordering': ['citing_opinion', 'text_offset'],
            },
        ),
    ]
