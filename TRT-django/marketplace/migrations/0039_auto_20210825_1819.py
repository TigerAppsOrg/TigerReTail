# Generated by Django 3.1.8 on 2021-08-26 01:19

import datetime
import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0038_auto_20210822_2313'),
    ]

    operations = [
        migrations.AddField(
            model_name='account',
            name='emailActivity',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='account',
            name='emailUnreadNotification',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='account',
            name='remindSetEmailSettings',
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name='item',
            name='deadline',
            field=models.DateField(help_text='Latest allowed is 2022-08-26', validators=[django.core.validators.MinValueValidator(datetime.date(2021, 8, 26)), django.core.validators.MaxValueValidator(datetime.date(2022, 8, 26))]),
        ),
        migrations.AlterField(
            model_name='itemrequest',
            name='deadline',
            field=models.DateField(help_text='Latest allowed is 2022-08-26', validators=[django.core.validators.MinValueValidator(datetime.date(2021, 8, 26)), django.core.validators.MaxValueValidator(datetime.date(2022, 8, 26))]),
        ),
    ]
