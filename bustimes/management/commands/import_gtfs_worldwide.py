import logging
from pathlib import Path
from itertools import pairwise
from zipfile import BadZipFile

import gtfs_kit
import pandas as pd
from shapely.errors import EmptyPartError
from shapely import ops as so
from django.conf import settings
from django.contrib.gis.geos import GEOSGeometry
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import Count, Exists, OuterRef, Q
from django.db.models.functions import Now
from django.utils.dateparse import parse_duration

from busstops.models import AdminArea, DataSource, Operator, Region, Service, StopPoint

from ...download_utils import download_if_modified
from ...utils import log_time_taken
from ...models import Route, Trip, RouteLink
from ...gtfs_utils import get_calendars, MODES

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Import GTFS timetables from worldwide feeds (based on Ireland NTA imports).

    This command is configurable via DataSource.settings and supports:
    - Custom agency/operator ID prefixes (like IE "ie-" prefix)
    - Configurable stop ID prefixes
    - Different operator matching strategies
    - Optional route link processing
    - Custom processing rules per feed

    Usage: python manage.py import_gtfs_worldwide <source_name> [--force]

    DataSource.settings["gtfs"] should contain:
    {
        "agency_prefix": "ie-",           // Optional prefix for agency IDs (like IE)
        "stop_prefix": "custom-",         // Optional prefix for stop IDs
        "operator_noc": "CUSTOMNOC",      // Optional override for operator NOC
        "default_operator": "Translink",  // Default operator name for routes without agency_id
        "operator_matching": "noc",       // "noc", "name", or "url"
        "region_handling": "auto",        // "auto", "skip", or custom
        "skip_route_links": false,        // Skip expensive route link processing
        "custom_processing": {            // Feed-specific options
            "handle_agencies": true,
            "handle_shapes": true
        }
    }
    """

    def add_arguments(self, parser):
        parser.add_argument("source_name", type=str)
        parser.add_argument(
            "--force",
            action="store_true",
            help="Import data even if the GTFS feeds haven't changed",
        )

    def handle(self, source_name, **options):
        self.source_name = source_name
        self.force = options["force"]

        # Get the DataSource
        try:
            self.source = DataSource.objects.get(name=source_name)
        except DataSource.DoesNotExist:
            logger.error(f"DataSource '{source_name}' not found")
            return

        # Load GTFS-specific settings
        self.gtfs_settings = self.source.settings.get("gtfs", {}) if self.source.settings else {}

        # Set configuration defaults (IE-style)
        self.agency_prefix = self.gtfs_settings.get("agency_prefix", "")
        self.stop_prefix = self.gtfs_settings.get("stop_prefix", "")
        # Auto-generate stop prefix from source name if not explicitly set
        if not self.stop_prefix:
            self.stop_prefix = f"{self.source_name.lower()}-"
        self.operator_noc = self.gtfs_settings.get("operator_noc", "")
        self.default_operator = self.gtfs_settings.get("default_operator", "")
        self.operator_matching = self.gtfs_settings.get("operator_matching", "noc")
        self.region_handling = self.gtfs_settings.get("region_handling", "auto")
        self.skip_route_links = self.gtfs_settings.get("skip_route_links", False)
        self.custom_processing = self.gtfs_settings.get("custom_processing", {})

        # Download and process the GTFS feed
        path = settings.DATA_DIR / Path(self.source.url).name

        modified, last_modified = download_if_modified(path, self.source)
        if modified or last_modified != self.source.datetime or self.force:
            logger.info(f"{self.source} {last_modified}")
            if last_modified:
                self.source.datetime = last_modified
            try:
                with log_time_taken(logger):
                    self.handle_zipfile(path)
            except (OSError, BadZipFile) as e:
                logger.exception(e)

    def handle_operator(self, line):
        """Handle operator/agency creation with configurable prefix (IE-style)"""
        # Handle feeds that don't have agency_id (like Translink Queensland)
        agency_id = getattr(line, 'agency_id', None)
        if agency_id is None or pd.isna(agency_id):
            # Use configured operator NOC, or fall back to agency name
            if self.operator_noc:
                agency_id = self.operator_noc
            else:
                # Use agency name as fallback, or source name
                agency_name = getattr(line, 'agency_name', '')
                if agency_name:
                    agency_id = agency_name.split()[0][:10]  # First word, max 10 chars
                else:
                    agency_id = self.source.name.split()[0][:10]

        # Apply agency prefix if configured (like IE "ie-" prefix)
        if self.agency_prefix:
            agency_id = f"{self.agency_prefix}{agency_id}"

        name = getattr(line, 'agency_name', '')

        # Match operators based on configured strategy
        operator_query = Q()
        if self.operator_matching == "noc":
            operator_query = Q(noc=agency_id)
        elif self.operator_matching == "name":
            operator_query = Q(name__iexact=name)
        elif self.operator_matching == "url" and getattr(line, 'agency_url', None):
            operator_query = Q(url=getattr(line, 'agency_url'))

        operator = Operator.objects.filter(operator_query).first()

        if not operator:
            operator = Operator(
                name=name,
                noc=agency_id,
                url=getattr(line, 'agency_url', None)
            )
            operator.save()
        elif operator.url != getattr(line, 'agency_url', None):
            operator.url = getattr(line, 'agency_url', None)
            operator.save(update_fields=["url"])

        return operator

    def do_stops(self, feed: gtfs_kit.feed.Feed) -> dict[str, StopPoint]:
        """Process stops with bulk operations and admin area assignment (IE-style)"""
        stops = {}
        admin_areas = {}

        print(f"{self.source_name}: Processing {len(feed.stops)} stops")
        for i, (_, line) in enumerate(feed.stops.iterrows()):
            if i % 100 == 0:
                print(f"{self.source_name}: Processed {i}/{len(feed.stops)} stops")

            stop_id = line.stop_id

            # Apply stop prefix if configured
            if self.stop_prefix:
                stop_id = f"{self.stop_prefix}{stop_id}"

            stop = StopPoint(
                atco_code=stop_id,
                common_name=line.stop_name,
                latlong=GEOSGeometry(f"POINT({line.stop_lon} {line.stop_lat})"),
                locality_centre=False,
                active=True,
                source=self.source,
            )

            if ", stop" in stop.common_name and stop.common_name.count(", ") == 1:
                stop.common_name, stop.indicator = stop.common_name.split(", ")
            stop.common_name = stop.common_name[:48]
            stops[stop_id] = stop

        print(f"{self.source_name}: Finished processing {len(stops)} stops")

        existing_stops = StopPoint.objects.only(
            "atco_code", "common_name", "latlong", "source_id"
        ).in_bulk(stops)

        stops_to_create = [
            stop for stop in stops.values() if stop.atco_code not in existing_stops
        ]
        stops_to_update = [
            stop
            for stop in stops.values()
            if stop.atco_code in existing_stops
            and existing_stops[stop.atco_code].source_id in (self.source.id, None)
            and (
                existing_stops[stop.atco_code].latlong != stop.latlong
                or existing_stops[stop.atco_code].common_name != stop.common_name
            )
        ]

        print(f"{self.source_name}: Bulk updating {len(stops_to_update)} existing stops")
        StopPoint.objects.bulk_update(
            stops_to_update, ["common_name", "latlong", "indicator", "source"]
        )

        # Handle admin area assignment based on region_handling setting
        if self.region_handling != "skip":
            print(f"{self.source_name}: Assigning admin areas to {len(stops_to_create)} new stops")
            for stop in stops_to_create:
                admin_area_id = stop.atco_code[:3]
                # Only assign admin area if the ID is numeric (for UK-style ATCO codes)
                if admin_area_id.isdigit():
                    if admin_area_id not in admin_areas:
                        admin_areas[admin_area_id] = AdminArea.objects.filter(
                            id=admin_area_id
                        ).exists()
                    if admin_areas[admin_area_id]:
                        stop.admin_area_id = admin_area_id
                # If region_handling is set to a specific region, try to assign an admin area from that region
                elif self.region_handling and self.region_handling != "auto":
                    from busstops.models import Region
                    try:
                        region = Region.objects.get(id=self.region_handling)
                        # Find the first admin area in this region
                        admin_area = region.adminarea_set.first()
                        if admin_area:
                            stop.admin_area_id = admin_area.id
                    except Region.DoesNotExist:
                        pass

        print(f"{self.source_name}: Bulk creating {len(stops_to_create)} new stops")
        try:
            StopPoint.objects.bulk_create(stops_to_create, batch_size=1000)
            print(f"{self.source_name}: Stops bulk create successful")
        except Exception as e:
            print(f"{self.source_name}: Error in bulk create stops: {e}")
            raise
        print(f"{self.source_name}: Stops processing completed")
        return StopPoint.objects.only("atco_code", "latlong").in_bulk(stops)

    def handle_route(self, line):
        """Handle route creation with service matching (IE-style)"""
        line_name = line.route_short_name if type(line.route_short_name) is str else ""
        description = line.route_long_name if type(line.route_long_name) is str else ""

        if not line_name and " " not in description:
            line_name = description
            if len(line_name) < 5:
                description = ""

        # Handle feeds that don't have agency_id in routes (like Translink Queensland)
        agency_id = getattr(line, 'agency_id', None)
        if agency_id is not None and pd.isna(agency_id):
            agency_id = None

        # Apply agency prefix to operator lookup
        if self.agency_prefix and agency_id:
            agency_id = f"{self.agency_prefix}{agency_id}"

        # If no agency_id specified, try to find a suitable operator
        if not agency_id:
            if len(self.operators) == 1:
                # Use the single agency
                agency_id = list(self.operators.keys())[0]
            elif self.default_operator:
                # Use configured default operator
                agency_id = self.default_operator

        operator = self.operators.get(agency_id) if agency_id else None
        services = Service.objects.filter(operator=operator)

        q = Exists(
            Route.objects.filter(code=line.route_id, service=OuterRef("id"))
        ) | Q(service_code=line.route_id)

        if line_name and line_name not in ("rail", "InterCity"):
            q |= Q(line_name__iexact=line_name)
        elif description:
            q |= Q(description=description)

        service = services.filter(q).order_by("id").first()
        if not service:
            service = Service(source=self.source)

        service.service_code = line.route_id
        service.line_name = line_name
        service.description = description
        if line.route_type in MODES:
            service.mode = MODES[line.route_type]
        else:
            logger.warning("unknown route type %s", line)
        service.current = True
        service.source = self.source
        service.save()

        if operator:
            if service.id in self.services:
                service.operator.add(operator)
            else:
                service.operator.set([operator])
        self.services[service.id] = service

        route, created = Route.objects.update_or_create(
            {
                "line_name": service.line_name,
                "description": service.description,
                "service": service,
            },
            source=self.source,
            code=line.route_id,
        )
        if not created:
            route.trip_set.all().delete()
        self.routes[line.route_id] = route
        self.route_operators[line.route_id] = operator

    def handle_zipfile(self, path):
        """Main processing function with optimized trip handling (IE-style)"""
        print(f"{self.source_name}: Loading GTFS feed from {path}")
        feed = gtfs_kit.read_feed(path, dist_units="km")
        print(f"{self.source_name}: Feed loaded, {len(feed.trips)} trips, {len(feed.stop_times)} stop times")
        calendar_count = len(feed.calendar) if feed.calendar is not None else 0
        calendar_dates_count = len(feed.calendar_dates) if feed.calendar_dates is not None else 0
        print(f"{self.source_name}: Feed contains {len(feed.agency)} agencies, {len(feed.routes)} routes, {len(feed.stops)} stops, {calendar_count} calendar entries, {calendar_dates_count} calendar dates")

        self.operators = {}
        self.routes = {}
        self.route_operators = {}
        self.services = {}

        # Handle agencies if enabled (default true)
        print(f"{self.source_name}: Processing agencies")
        if self.custom_processing.get("handle_agencies", True) and feed.agency is not None:
            for agency in feed.agency.itertuples():
                agency_id = getattr(agency, 'agency_id', None)
                if agency_id is None:
                    agency_id = getattr(agency, 'agency_name', None)
                self.operators[agency_id] = self.handle_operator(agency)
        print(f"{self.source_name}: Agencies processed, {len(self.operators)} operators")

        # Handle routes
        print(f"{self.source_name}: Processing {len(feed.routes)} routes")
        for i, route in enumerate(feed.routes.itertuples()):
            if i % 10 == 0:
                print(f"{self.source_name}: Processed {i}/{len(feed.routes)} routes")
            print(f"{self.source_name}: Processing route {i+1}: {route.route_id} - {getattr(route, 'route_short_name', 'N/A')}")
            try:
                self.handle_route(route)
                print(f"{self.source_name}: Route {route.route_id} processed successfully")
            except Exception as e:
                print(f"{self.source_name}: Error processing route {route.route_id}: {e}")
                raise
        print(f"{self.source_name}: Routes processed, {len(self.routes)} routes created")

        # Handle shapes if enabled (default true)
        if self.custom_processing.get("handle_shapes", True):
            try:
                for route in gtfs_kit.routes.get_routes(feed, as_gdf=True).itertuples():
                    self.routes[route.route_id].service.geometry = route.geometry.wkt
                    if route.geometry:
                        self.routes[route.route_id].service.save(update_fields=["geometry"])
            except (AttributeError, EmptyPartError, ValueError):
                pass

        stops = self.do_stops(feed)
        calendars = get_calendars(feed, source=self.source)

        # Pre-process stop_times by trip_id for efficient lookup (optimization)
        print(f"{self.source_name}: Pre-processing {len(feed.stop_times)} stop times by trip")
        stop_times_by_trip = {}
        for line in feed.stop_times.itertuples():
            trip_id = line.trip_id
            if trip_id not in stop_times_by_trip:
                stop_times_by_trip[trip_id] = []
            stop_times_by_trip[trip_id].append(line)

        print(f"{self.source_name}: Grouped stop times into {len(stop_times_by_trip)} trip groups")

        created_trip_ids = set()
        trip_id_to_pk = {}
        valid_trips = []
        trips_with_start = 0
        trips_without_start = 0

        print(f"{self.source_name}: Processing {len(feed.trips)} trips with optimized stop time calculation")
        for i, line in enumerate(feed.trips.itertuples()):
            if i % 100 == 0:
                print(f"{self.source_name}: Processed {i}/{len(feed.trips)} trips")

            route = self.routes[line.route_id]
            trip_id = line.trip_id

            # Create trip object
            block_id = getattr(line, "block_id", None)
            if block_id is not None and not pd.isna(block_id) and str(block_id).strip() not in ("", "N/A", "n/a"):
                block = str(block_id).strip()
            else:
                block = ""

            trip = Trip(
                route=route,
                calendar=calendars[line.service_id],
                inbound=getattr(line, "direction_id", 0) == 1,
                headsign=getattr(line, "trip_headsign", ""),
                ticket_machine_code=trip_id,
                block=block,
                vehicle_journey_code=getattr(line, "trip_short_name", ""),
                operator=self.route_operators[line.route_id],
            )

            # Calculate start/end times from pre-grouped stop_times
            if trip_id in stop_times_by_trip:
                stop_times = sorted(stop_times_by_trip[trip_id], key=lambda x: x.stop_sequence)
                if stop_times:
                    # First stop: departure time
                    trip.start = stop_times[0].departure_time
                    # Last stop: arrival time
                    last_stop = stop_times[-1]
                    trip.end = last_stop.arrival_time
                    # Set destination from last stop
                    stop_id = last_stop.stop_id
                    if self.stop_prefix:
                        stop_id = f"{self.stop_prefix}{stop_id}"
                     trip.destination = stops.get(stop_id)

            if trip.start is None:
                logger.warning(f"trip {trip_id} has no stop times")
                trips_without_start += 1
            else:
                valid_trips.append(trip)
                created_trip_ids.add(trip_id)
                trips_with_start += 1
                if len(valid_trips) >= 1000:
                    Trip.objects.bulk_create(valid_trips)
                    for t in valid_trips:
                        trip_id_to_pk[t.ticket_machine_code] = t.pk
                    valid_trips = []

        if valid_trips:
            Trip.objects.bulk_create(valid_trips)
            for t in valid_trips:
                trip_id_to_pk[t.ticket_machine_code] = t.pk

        print(f"{self.source_name}: Finished processing trips, {trips_with_start} with start times, {trips_without_start} without")

        # Handle stop times with COPY for performance
        print(f"{self.source_name}: Processing {len(feed.stop_times)} stop times")
        logger.info(f"{self.source_name}: Processing {len(feed.stop_times)} stop times")

        stop_times_processed = 0
        stop_times_skipped = 0

        with (
            connection.cursor() as cursor,
            cursor.copy(
                "COPY bustimes_stoptime (stop_id, arrival, departure, sequence, trip_id, timing_status, pick_up, set_down, stop_code) FROM STDIN"
            ) as copy,
        ):
            for i, line in enumerate(feed.stop_times.itertuples()):
                if i % 1000 == 0:
                    print(f"{self.source_name}: Processed {i}/{len(feed.stop_times)} stop times")
                timing_status = "PTP" if getattr(line, "timepoint", 1) == 1 else "OTH"

                pick_up = True  # Default to True (regularly scheduled pickup)
                pickup_type = getattr(line, "pickup_type", None)
                if pickup_type is not None:
                    match pickup_type:
                        case 0:  # Regularly scheduled pickup
                            pick_up = True
                        case 1:  # "No pickup available"
                            pick_up = False
                        case _:  # Other values, keep default
                            pass

                set_down = True  # Default to True (regularly scheduled drop off)
                drop_off_type = getattr(line, "drop_off_type", None)
                if drop_off_type is not None:
                    match drop_off_type:
                        case 0:  # Regularly scheduled drop off
                            set_down = True
                        case 1:  # "No drop off available"
                            set_down = False
                        case _:  # Other values, keep default
                            pass

                departure = int(parse_duration(line.departure_time).total_seconds())
                arrival = None
                if line.arrival_time != departure:
                    arrival = int(parse_duration(line.arrival_time).total_seconds())

                # Apply stop prefix to stop_id lookup
                stop_id = line.stop_id
                if self.stop_prefix:
                    stop_id = f"{self.stop_prefix}{stop_id}"

                # Check if trip exists
                if line.trip_id in created_trip_ids:
                    copy.write_row(
                        (
                            stop_id,
                            arrival,
                            departure,
                            line.stop_sequence,
                            trip_id_to_pk[line.trip_id],
                            timing_status,
                            pick_up,
                            set_down,
                            "",
                        )
                    )
                    stop_times_processed += 1
                else:
                    stop_times_skipped += 1
                    if stop_times_skipped < 5:  # Log first few
                        logger.warning(f"{self.source_name}: Skipping stop time for missing trip {line.trip_id}")

        logger.info(f"{self.source_name}: Processed {stop_times_processed} stop times, skipped {stop_times_skipped}")
        print(f"{self.source_name}: Completed processing stop times: {stop_times_processed} processed, {stop_times_skipped} skipped")

        services = Service.objects.filter(id__in=self.services.keys())

        for service in services:
            service.do_stop_usages()

            if self.region_handling != "skip":
                region = (
                    Region.objects.filter(adminarea__stoppoint__service=service)
                    .annotate(Count("adminarea__stoppoint__service"))
                    .order_by("-adminarea__stoppoint__service__count")
                    .first()
                )
                if region and region != service.region:
                    service.save(update_fields=["region"])

            service.update_search_vector()

        services.update(modified_at=Now())
        self.source.save(update_fields=["datetime"])

        # Handle operators' regions
        if self.region_handling != "skip":
            for operator in self.operators.values():
                operator.region = (
                    Region.objects.filter(adminarea__stoppoint__service__operator=operator)
                    .annotate(Count("adminarea__stoppoint__service__operator"))
                    .order_by("-adminarea__stoppoint__service__operator__count")
                    .first()
                )
                if operator.region_id:
                    operator.save(update_fields=["region"])

        # Clean up old routes
        old_routes = self.source.route_set.exclude(
            id__in=(route.id for route in self.routes.values())
        )
        logger.info(old_routes.update(service=None))

        current_services = self.source.service_set.filter(current=True)
        logger.info(
            current_services.exclude(route__in=self.routes.values()).update(
                current=False
            )
        )
        old_routes.update(service=None)

        # Handle route links unless skipped
        if not self.skip_route_links:
            do_route_links(feed, self.source, self.routes, stops, self.stop_prefix)


def do_route_links(
    feed: gtfs_kit.feed.Feed, source: DataSource, routes: dict, stops: dict, stop_prefix: str = ""
):
    """Handle route geometry links between stops"""
    try:
        trips = feed.get_trips(as_gdf=True).drop_duplicates("shape_id")
    except ValueError:
        return

    existing_route_links = {
        (rl.service_id, rl.from_stop_id, rl.to_stop_id): rl
        for rl in RouteLink.objects.filter(service__source=source)
    }
    route_links = {}

    for trip in trips.itertuples():
        if trip.geometry is None:
            continue

        service = routes[trip.route_id].service_id

        start_dist = None

        for a, b in pairwise(
            feed.stop_times[feed.stop_times.trip_id == trip.trip_id].itertuples()
        ):
            # Apply stop prefix to stop IDs for route link lookup
            from_stop_id = a.stop_id
            to_stop_id = b.stop_id
            if stop_prefix:
                from_stop_id = f"{stop_prefix}{from_stop_id}"
                to_stop_id = f"{stop_prefix}{to_stop_id}"

            key = (service, from_stop_id, to_stop_id)

            if key in route_links:
                start_dist = None
                continue

            # Find the substring of trip.geometry between the stops a and b
            if not start_dist:
                stop_a = stops[from_stop_id]
                point_a = so.Point(stop_a.latlong.coords)
                start_dist = trip.geometry.project(point_a)
            stop_b = stops[to_stop_id]
            point_b = so.Point(stop_b.latlong.coords)
            end_dist = trip.geometry.project(point_b)

            geom = so.substring(trip.geometry, start_dist, end_dist)
            if type(geom) is so.LineString:
                if key in existing_route_links:
                    rl = existing_route_links[key]
                else:
                    rl = RouteLink(
                        service_id=key[0],
                        from_stop_id=key[1],
                        to_stop_id=key[2],
                    )
                rl.geometry = geom.wkt
                route_links[key] = rl

            start_dist = end_dist

    RouteLink.objects.bulk_update(
        [rl for rl in route_links.values() if rl.id], fields=["geometry"]
    )
    RouteLink.objects.bulk_create([rl for rl in route_links.values() if not rl.id])