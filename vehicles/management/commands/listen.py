from django.db import connection
import time
import requests
from django.core.management.base import BaseCommand
from django.conf import settings


def get_content(slug):
    content = slug

    # vehicle = Vehicle.objects.get(slug=slug)
    # if vehicle.latest_journey and vehicle.latest_journey.route_name:
    #    content += f" on route {vehicle.latest_journey.route_name}"
    #    if vehicle.latest_journey.destination:
    #        content += " to {vehicle.latest_journey.destination}"

    return f"[{content}](https://gladetimes.midlandbus.uk/vehicles/{slug})"


class Command(BaseCommand):
    def handle(self, *args, **options):
        assert settings.NEW_VEHICLE_WEBHOOK_URL, "NEW_VEHICLE_WEBHOOK_URL is not set"

        session = requests.Session()

        with connection.cursor() as cursor:
            cursor.execute("""CREATE OR REPLACE FUNCTION notify_new_vehicle()
                           RETURNS trigger AS $$
                           BEGIN
                           PERFORM pg_notify('new_vehicle', NEW.slug || '|' || COALESCE(NEW.operator_id, ''));
                           RETURN NEW;
                           END;
                           $$ LANGUAGE plpgsql;""")
            cursor.execute("""CREATE OR REPLACE TRIGGER notify_new_vehicle
                           AFTER INSERT ON vehicles_vehicle
                           FOR EACH ROW
                           EXECUTE PROCEDURE notify_new_vehicle();""")

            cursor.execute("LISTEN new_vehicle")
            gen = cursor.connection.notifies()
            for notify in gen:
                print(notify)

                payload_parts = notify.payload.split("|")
                slug = payload_parts[0]
                operator_id = payload_parts[1] if len(payload_parts) > 1 else ""

                content = get_content(slug)
                if operator_id == "NCTR":
                    content += " <@1238439672708075520>"

                response = session.post(
                    settings.NEW_VEHICLE_WEBHOOK_URL,
                    json={
                        "username": "bot",
                        "content": content,
                    },
                    timeout=10,
                )

                print(response, response.headers, response.text)

                time.sleep(5)
