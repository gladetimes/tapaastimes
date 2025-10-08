import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'buses.settings')
django.setup()

from busstops.models import DataSource

ds_name = DataSource.objects.filter(name='151')
ds_id = DataSource.objects.filter(id=151)

print('DataSource name=151 exists:', ds_name.exists())
print('DataSource id=151 exists:', ds_id.exists())

if ds_name.exists():
    ds = ds_name.first()
    print('Name 151 details: name=', ds.name, 'url=', ds.url)

if ds_id.exists():
    ds = ds_id.first()
    print('ID 151 details: name=', ds.name, 'url=', ds.url)