# to remove the Cloudinary image fields

from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0035_auto_20210821_2242'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='item',
            name='image',
        ),
        migrations.RemoveField(
            model_name='itemrequest',
            name='image',
        ),
        migrations.RemoveField(
            model_name='albumimage',
            name='image',
        ),
    ]