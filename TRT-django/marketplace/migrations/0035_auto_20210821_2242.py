# Generated by Django 3.1.8 on 2021-08-22 05:42

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0034_auto_20210821_1806'),
    ]

    operations = [
        migrations.AddField(
            model_name='albumimage',
            name='img',
            field=models.ImageField(null=True, upload_to='images/'),
        ),
        migrations.AddField(
            model_name='itemrequest',
            name='img',
            field=models.ImageField(null=True, upload_to='images/'),
        ),
    ]