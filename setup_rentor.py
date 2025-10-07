#!/usr/bin/env python
"""
Setup script for Rentor import command.
Run this script to create the required operator and data source.
"""

import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'buses.settings')
sys.path.insert(0, os.path.dirname(__file__))
django.setup()

from busstops.models import Operator, DataSource

def setup_rentor():
    print("Setting up Rentor import...")

    # Create THRA operator
    operator, created = Operator.objects.get_or_create(
        noc='THRA',
        defaults={
            'name': 'THRA (Train Simulator)',
            'vehicle_mode': 'train'
        }
    )
    print(f"✓ Operator THRA: {'created' if created else 'already exists'}")

    # Create Rentor DataSource
    datasource, created = DataSource.objects.get_or_create(
        name='rentor',
        defaults={
            'url': 'https://www.rentor.nl/api/radar/GetRadarData'
        }
    )
    print(f"✓ DataSource 'rentor': {'created' if created else 'already exists'}")

    print("\nSetup complete! You can now run:")
    print("python manage.py import_rentor rentor")

if __name__ == '__main__':
    setup_rentor()