from django.contrib.gis.geos import Point
from django.db.models import Q

from busstops.models import Service

from ...models import VehicleJourney, VehicleLocation
from ..import_live_vehicles import ImportLiveVehiclesCommand


class Command(ImportLiveVehiclesCommand):
    @staticmethod
    def add_arguments(parser):
        super(Command, Command).add_arguments(parser)
        parser.add_argument("source_name", type=str)

    def handle(self, source_name, **options):
        self.source_name = source_name
        super().handle(**options)

    def do_source(self):
        super().do_source()
        # Set operator to THRA
        self.operator_id = "THRA"
        return self

    def get_items(self):
        # Make POST request to Rentor API with JSON payload
        response = self.session.post(
            "https://www.rentor.nl/api/radar/GetRadarData",
            json={"request": "data"},  # API requires non-empty JSON
            timeout=20
        )
        response.raise_for_status()
        return response.json()

    def get_vehicle_identity(self, item):
        # Extract vehicle identifier from API response
        return item.get("Name") or item.get("UniqueName")

    @staticmethod
    def get_item_identity(item):
        # Return coordinates as unique identifier for the item
        if "Points" in item:
            return item["Points"]
        return item.get("UniqueName", "")

    def get_journey_identity(self, item):
        # Extract journey identifier from API response
        return (
            item.get("Loco", ""),
            "forward",  # Default direction
            item.get("Simulator", ""),
        )

    def get_vehicle(self, item):
        code = self.get_vehicle_identity(item)

        defaults = {
            "source": self.source,
            "operator_id": self.operator_id,
            "code": code
        }

        # Add additional vehicle fields if available in API
        if "Loco" in item:
            defaults["name"] = item["Loco"]  # Store locomotive type as name
        if "Speed" in item:
            defaults["notes"] = f"Speed: {item['Speed']}"  # Store speed as notes

        condition = Q(operator_id=self.operator_id) | Q(source=self.source)
        vehicles = self.vehicles.filter(condition)

        vehicle = vehicles.filter(code__iexact=code).first()
        if vehicle:
            return vehicle, False

        return vehicles.get_or_create(defaults, code=code)

    def get_journey(self, item, vehicle):
        journey = VehicleJourney(
            route_name=item.get("Loco", "Unknown"),  # Use locomotive type as route
            direction="forward",  # Default direction
            destination=item.get("Simulator", "Unknown"),  # Use simulator as destination
        )

        # For train data, we may not have matching services
        # Just return the journey as-is
        return journey

    def create_vehicle_location(self, item):
        # Parse coordinates from "longitude,latitude" format
        if "Points" in item:
            coords = item["Points"].split(",")
            if len(coords) == 2:
                longitude, latitude = map(float, coords)
                return VehicleLocation(
                    latlong=Point(longitude, latitude),
                    heading=item.get("heading"),
                )
        return None