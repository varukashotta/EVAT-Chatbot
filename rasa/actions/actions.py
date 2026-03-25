from rasa_sdk import Action, Tracker
from rasa_sdk.events import SlotSet, FollowupAction
from rasa_sdk.executor import CollectingDispatcher
from typing import Any, Text, Dict, List, Optional, Tuple
from urllib.parse import quote_plus
import logging

from actions.data_service import data_service
from actions.constants import ConversationContexts, MainMenuOptions, PreferenceTypes, ActionTypes, Messages

# Import real-time integration
try:
    from .real_time_integration import real_time_manager
    REAL_TIME_INTEGRATION_AVAILABLE = True
except ImportError:
    REAL_TIME_INTEGRATION_AVAILABLE = False
    real_time_manager = None

logger = logging.getLogger(__name__)


def format_station_list(stations: List[Dict[str, Any]], limit: int = 5, show_indices: bool = True) -> str:
    lines: List[str] = []
    for i, station in enumerate(stations[:limit], 1):
        name = station.get('name', 'Unknown')
        distance = station.get('distance_km')
        power = station.get('power')
        cost = station.get('cost')

        prefix = f"{i}. " if show_indices else ""
        if distance is not None:
            lines.append(f"{prefix}**{name}** - {distance}km away")
        else:
            lines.append(f"{prefix}**{name}**")

        extras: List[str] = []
        if power:
            extras.append(f"⚡ {power}")
        if cost:
            extras.append(f"💰 {cost}")
        if extras:
            lines.append(f"   {' | '.join(extras)}")
    return "\n".join(lines)


def _map_station_for_ui(station: Dict[str, Any]) -> Dict[str, Any]:
    """Convert internal station dict to frontend card schema.

    Required keys:
    - station_id: string
    - name: string
    - address: string
    - distance_km: number (float)
    - cost: string
    - power: number or None
    - availability: one of {"yes","no","busy","available"}
    """
    # Station ID: prefer provided IDs; fallback to lat/lon or name
    sid = station.get("station_id") or station.get("id") or station.get("ID")
    if not sid:
        lat = station.get("latitude")
        lon = station.get("longitude")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            sid = f"{lat},{lon}"
        else:
            sid = station.get("name") or "station"

    name = station.get("name") or "Unknown station"
    address = station.get("address", "")

    # Distance: prefer distance_km; else min(distance_from_start, distance_from_end)
    dist = station.get("distance_km")
    if not isinstance(dist, (int, float)):
        candidates: List[float] = []
        for k in ("distance_from_start", "distance_from_end"):
            v = station.get(k)
            if isinstance(v, (int, float)):
                candidates.append(float(v))
        dist = min(candidates) if candidates else 0.0

    # Power: parse first number
    power_val = None
    p = station.get("power")
    if isinstance(p, (int, float)):
        power_val = int(p)
    elif p is not None:
        import re as _re
        nums = _re.findall(r"\d+\.?\d*", str(p))
        if nums:
            try:
                power_val = int(float(nums[0]))
            except Exception:
                power_val = None

    cost = station.get("cost") or "—"

    # Availability mapping
    availability = station.get("availability")
    availability_str: str
    if isinstance(availability, str):
        val = availability.strip().lower()
        if val in {"yes", "no", "busy", "available"}:
            availability_str = val
        else:
            availability_str = "available"
    elif isinstance(availability, bool):
        availability_str = "yes" if availability else "no"
    else:
        availability_str = "available"

    return {
        "station_id": str(sid),
        "name": name,
        "address": address,
        "distance_km": float(dist if isinstance(dist, (int, float)) else 0.0),
        "cost": str(cost),
        "power": power_val,
        "availability": availability_str,
    }


def _send_station_cards(dispatcher: CollectingDispatcher, stations: List[Dict[str, Any]], limit: int = 10) -> None:
    """Send stations to frontend in the expected custom payload schema."""
    try:
        mapped = [_map_station_for_ui(
            s) for s in stations[:limit] if isinstance(s, dict)]
        if mapped:
            dispatcher.utter_message(json_message={
                "stations": mapped,
                "show_availability": True,
            })
    except Exception:
        # Non-fatal if UI payload fails
        pass


class ActionCollectInitialLocation(Action):
    def name(self) -> Text:
        return "action_collect_initial_location"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        # Check if this is the first time (greeting)
        conversation_context = tracker.get_slot("conversation_context")

        if not conversation_context:
            # First time - show greeting and ask for location
            dispatcher.utter_message(
                text="Hello! Welcome to Melbourne EV Charging Assistant! ⚡\n\n"
                     "📍 **Getting your location...**\n\n"
                     "Please accept the location permission when prompted.\n\n"
            )
            return [SlotSet("conversation_context", ConversationContexts.INITIAL_LOCATION_COLLECTION)]

        # Check for user location from frontend metadata first
        user_lat = tracker.latest_message.get('metadata', {}).get('lat')
        user_lng = tracker.latest_message.get('metadata', {}).get('lng')

        if user_lat and user_lng:
            dispatcher.utter_message(
                text="✅ **Location detected!** Now I can help you find the best charging options.\n\n"
                     "Please select an option:\n\n"
                     "1. 🗺️ **Route Planning** - Plan charging stops for your journey\n"
                     "2. 🚨 **Emergency Charging** - Find nearest stations when battery is low\n"
                     "3. ⚡ **Charging Preferences** - Find stations by your preferences\n\n"
                     "**🎯 Type 1, 2, or 3 to continue!**")

            # Store the coordinates for future use
            return [
                SlotSet("user_lat", user_lat),
                SlotSet("user_lng", user_lng),
                SlotSet("conversation_context", None)  # Reset to show menu
            ]
        else:
            # Check if user typed a suburb name
            message = tracker.latest_message.get('text', '').strip()

            if message and len(message) > 0:
                # Try to get coordinates from suburb name
                try:
                    coords = data_service._get_location_coordinates(message)
                    if coords:
                        lat, lng = coords
                        dispatcher.utter_message(
                            text=f"✅ **Location set to {message}!** Now I can help you find the best charging options.\n\n"
                                 "Please select an option:\n\n"
                                 "1. 🗺️ **Route Planning** - Plan charging stops for your journey\n"
                                 "2. 🚨 **Emergency Charging** - Find nearest stations when battery is low\n"
                                 "3. ⚡ **Charging Preferences** - Find stations by your preferences\n\n"
                            "**🎯 Type 1, 2, or 3 to continue!**")

                        return [
                            SlotSet("user_lat", lat),
                            SlotSet("user_lng", lng),
                            SlotSet("current_location", message),
                            # Reset to show menu
                            SlotSet("conversation_context", None)
                        ]
                    else:
                        dispatcher.utter_message(
                            text=f"❌ **Location not found**\n\n"
                                 f"I couldn't find '{message}' in Melbourne.\n\n"
                                 "**Please try:**\n"
                                 "• A different suburb name (e.g., 'Richmond', 'Carlton')\n"
                                 "• Share your GPS location 📱\n\n"
                                 "💡 **Tip:** Make sure the suburb is in Melbourne!")
                        return []
                except Exception as e:
                    dispatcher.utter_message(
                        text="❌ **Error processing location**\n\n"
                             "Please try:\n"
                             "• A different suburb name\n"
                             "• Share your GPS location 📱")
                    return []
            else:
                # No message, ask again
                dispatcher.utter_message(
                    text="📍 **Location Required**\n\n"
                         "I need your current location to help you.\n\n"
                    #  "**Please either:**\n"
                    #  "• Share your GPS location 📱\n"
                    #  "• Type your suburb name (e.g., 'Richmond')\n\n"
                         "💡 **Tip:** Sharing your location gives you the most accurate results!")
                return []


class ActionHandleAnyInput(Action):
    def name(self) -> Text:
        return "action_handle_any_input"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        message = tracker.latest_message.get('text', '').strip()
        conversation_context = tracker.get_slot("conversation_context")

        lower_msg = message.lower()
        if any(phrase in lower_msg for phrase in ["thanks", "thank you", "thx", "no thanks", "no thank you"]):
            dispatcher.utter_message(text=Messages.GOODBYE)
            return [SlotSet("conversation_context", ConversationContexts.ENDED)]

        if conversation_context:
            return []

        if message == "1":
            dispatcher.utter_message(
                text=f"🗺️ **Route Planning**\n\n{Messages.ROUTE_PLANNING_PROMPT}\n\n💡 **Example:** 'from Carlton to Geelong'")
            return [SlotSet("conversation_context", ConversationContexts.ROUTE_PLANNING)]

        elif message == "2":

            dispatcher.utter_message(
                text=f"🚨 **Emergency Charging**\n\n{Messages.EMERGENCY_PROMPT}\n\n💡 **Example:** 'Richmond'")
            return [SlotSet("conversation_context", ConversationContexts.EMERGENCY_CHARGING)]

        elif message == "3":

            dispatcher.utter_message(
                text=f"⚡ **Charging Preferences**\n\n{Messages.PREFERENCE_PROMPT}\n\n• Cheapest 💰\n• Fastest ⚡\n• Premium 🌟")
            return [SlotSet("conversation_context", ConversationContexts.PREFERENCE_CHARGING)]

        # If not a valid menu option, show the menu again
        else:

            dispatcher.utter_message(text=Messages.MAIN_MENU)
            return []


"""
Removed unused ActionHandleInitialInput.
"""


class ActionHandleMenuSelection(Action):
    def name(self) -> Text:
        return "action_handle_menu_selection"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        message = tracker.latest_message.get('text', '').strip()
        conversation_context = tracker.get_slot("conversation_context")

        if conversation_context == ConversationContexts.ROUTE_PLANNING_RESULTS:

            # Get the route information from slots
            start_location = tracker.get_slot("start_location")
            end_location = tracker.get_slot("end_location")

            stations = data_service.get_route_stations(
                start_location, end_location)

            if stations:
                # Look for a station that matches the user's input
                selected_station = None
                for station in stations:
                    station_name = station.get('name', '').lower()
                    if message.lower() in station_name or station_name in message.lower():
                        selected_station = station
                        break

                if selected_station:
                    # Show detailed information about the selected station
                    response = f"🔋 **Station Details: {selected_station.get('name', 'Unknown Station')}**\n\n"
                    response += f"📍 **Location:** {selected_station.get('suburb', 'Location available')}\n"
                    response += f"⚡ **Power:** {selected_station.get('power', 'Power info available')} charging\n"
                    response += f"💰 **Cost:** {selected_station.get('cost', 'Cost info available')}\n"
                    response += f"🔌 **Connector:** {selected_station.get('connector_type', 'Connector info available')}\n"
                    response += f"📱 **Network:** {selected_station.get('network', 'Network info available')}\n\n"

                    # Add route context
                    response += f"🗺️ **Route:** {start_location} → {end_location}\n\n"

                    response += "**What would you like to do next?**\n\n"
                    response += "• **Get directions** 🧭\n"
                    response += "• **Check availability** ✅\n"
                    response += "• **Plan another route** 🗺️\n"
                    response += "• **Return to main menu** 🏠"

                    dispatcher.utter_message(text=response)

                    return [
                        SlotSet("selected_station",
                                selected_station.get('name')),
                        SlotSet("conversation_context",
                                ConversationContexts.STATION_DETAILS)
                    ]
                else:
                    dispatcher.utter_message(
                        text=f"❌ **Station not found**\n\n"
                             f"Please select a station from the list shown above:\n\n"
                             f"💡 **Available stations:**\n")

                    # Show the available stations again
                    dispatcher.utter_message(text=format_station_list(
                        stations, limit=10, show_indices=True))

                    dispatcher.utter_message(
                        text="**Simply type the station name to get details**")

                    return []
            else:
                # Keep user in route results; don't reset context on failure
                dispatcher.utter_message(
                    text=(
                        "❌ **No stations available**\n\n"
                        f"Unable to retrieve station information for the route {start_location} → {end_location}"
                    )
                )
                return []

        if conversation_context == ConversationContexts.ROUTE_PLANNING and ('from' in message.lower() and 'to' in message.lower()):
            # Extract start and end locations from the message
            start_location = None
            end_location = None

            # Split by 'from' and take everything after it
            after_from = message.lower().split('from', 1)[1]

            # Simple string search for " to " (with spaces)
            to_index = after_from.find(' to ')

            if to_index != -1:
                start_location = after_from[:to_index].strip()
                # +4 for " to " (including spaces)
                end_location = after_from[to_index + 4:].strip()

                if start_location and end_location and len(start_location) > 0 and len(end_location) > 0:
                    pass
                else:
                    # Debug trimmed
                    start_location = None
                    end_location = None
            else:
                start_location = None
                end_location = None

            if not start_location or not end_location:
                if not start_location and not end_location:
                    dispatcher.utter_message(
                        text="🗺️ **Route Planning**\n\nProvide your route: 'from [start] to [destination]'")
                elif not start_location:
                    dispatcher.utter_message(
                        text=f"🗺️ **Route Planning**\n\n✅ End location: {end_location}\n❌ Missing start location\n\nProvide: 'from [start] to {end_location}'")
                else:  # not end_location
                    dispatcher.utter_message(
                        text=f"🗺️ **Route Planning**\n\n✅ Start location: {start_location}\n❌ Missing end location\n\nProvide: 'from {start_location} to [destination]'")

                # Stay in route planning mode
                return [SlotSet("conversation_context", ConversationContexts.ROUTE_PLANNING)]

            # If we have both locations, process the route
            if start_location and end_location:
                # Set the slots and find charging stations
                # Try to get charging stations from the data service
                stations = data_service.get_route_stations(
                    start_location, end_location)

                if stations:
                    _send_station_cards(dispatcher, stations, limit=10)
                    response = f"🎯 **Route Confirmed:** {start_location} → {end_location}\n\n"
                    response += f"Found {len(stations)} charging stations along your route:\n\n"

                    displayed = []
                    for i, station in enumerate(stations[:10]):
                        response += f"**{i+1}. {station.get('name', f'Station {i+1}')}**\n"
                        dist = station.get('distance_km')
                        if isinstance(dist, (int, float)):
                            response += f"📍 {dist:.1f} km away\n\n"
                        else:
                            response += f"📍 Distance info unavailable\n\n"
                        displayed.append({
                            'name': station.get('name', f'Station {i+1}')
                        })

                    response += "💡 Simply type the station name"

                    dispatcher.utter_message(text=response)

                    # Set the conversation context to ROUTE_PLANNING_RESULTS so station selection works
                    return [
                        SlotSet("start_location", start_location),
                        SlotSet("end_location", end_location),
                        SlotSet("displayed_stations", displayed),
                        SlotSet("conversation_context",
                                ConversationContexts.ROUTE_PLANNING_RESULTS)
                    ]
                else:
                    dispatcher.utter_message(
                        text=(
                            "🗺️ **Route Planning**\n\n"
                            f"❌ No charging stations found from {start_location} to {end_location}.\n\n"
                            "💡 Try another pair of locations (e.g., 'from Richmond to Dandenong')."
                        )
                    )
                    # Stay in route planning; do NOT switch to results context
                    return [
                        SlotSet("conversation_context",
                                ConversationContexts.ROUTE_PLANNING)
                    ]

        if conversation_context == ConversationContexts.EMERGENCY_CHARGING:
            return []

        if conversation_context == ConversationContexts.PREFERENCE_CHARGING and any(word in message.lower() for word in ['cheapest', 'fastest', 'premium', 'cheap', 'fast']):
            return []

        if message == "1":

            dispatcher.utter_message(
                text="🗺️ **Route Planning** - Plan charging stops for your journey\n\n"
                     "Where are you traveling to?\n\n"
                     "💡 **Examples:**\n"
                     "• 'to Carlton / to Port Melbourne / to Collingwood'\n"
                #  "• 'from Carlton to Geelong' (custom start point)"
            )
            return [SlotSet("conversation_context", ConversationContexts.ROUTE_PLANNING)]

        elif message == "2":

            dispatcher.utter_message(
                text="🚨 **Emergency Charging** - Find nearest stations when battery is low\n\n"
                     "Tell me your car model or connector type (e.g., 'CHAdeMO', 'Tesla Model 3', 'Type 2', 'CCS').")
            return [SlotSet("conversation_context", ConversationContexts.EMERGENCY_CHARGING)]

        elif message == "3":

            dispatcher.utter_message(
                text="⚡ **Charging Preferences** - Find stations by your preferences\n\n"
                     "What's most important to you?\n\n"
                     "• Cheapest 💰\n"
                     "• Fastest ⚡\n"
                     "• Premium 🌟")
            return [SlotSet("conversation_context", ConversationContexts.PREFERENCE_CHARGING)]

        else:
            if conversation_context:
                return []

            dispatcher.utter_message(text=Messages.MAIN_MENU)
            return []


"""
Removed unused ActionCollectUserLocation.
"""


class ActionHandleRouteInput(Action):
    def name(self) -> Text:
        return "action_handle_route_input"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        message = tracker.latest_message.get('text', '').lower().strip()
        conversation_context = tracker.get_slot("conversation_context")

        # Only process if we're in route planning context
        if conversation_context != ConversationContexts.ROUTE_PLANNING:
            return []

        # Check for stored user location from initial collection
        stored_lat = tracker.get_slot("user_lat")
        stored_lng = tracker.get_slot("user_lng")
        stored_location = tracker.get_slot("current_location")

        if stored_lat and stored_lng:
            raw_text = tracker.latest_message.get('text', '') or ''
            raw_text = raw_text.strip()
            if raw_text:
                import re
                # Strip an optional leading 'to'
                dest_text = re.sub(r'^\s*to\b', '', raw_text,
                                   flags=re.IGNORECASE).strip()
                if dest_text:
                    start_location = (stored_lat, stored_lng)
                    end_location = dest_text
                    return self._process_route(dispatcher, start_location, end_location)

        # Check for traditional "from [start] to [destination]" format
        if 'from' in message and 'to' in message:

            # Extract start and end locations from the message
            start_location = None
            end_location = None

            try:
                # Split by 'from' and take everything after it
                after_from = message.split('from', 1)[1]

                # Use regex to find the word "to" (not just any "to")
                import re
                # Look for "to" as a word boundary, not inside other words
                to_match = re.search(r'\bto\b', after_from, re.IGNORECASE)

                if to_match:
                    to_index = to_match.start()
                    start_location = after_from[:to_index].strip()
                    # +2 for "to"
                    end_location = after_from[to_index + 2:].strip()

                    # Validate that we actually got meaningful text
                    if start_location and end_location and len(start_location) > 0 and len(end_location) > 0:
                        pass
                    else:

                        start_location = None
                        end_location = None
                else:

                    start_location = None
                    end_location = None
            except Exception as e:

                start_location = None
                end_location = None

            if not start_location or not end_location:
                if not start_location and not end_location:
                    dispatcher.utter_message(
                        text="🗺️ **Route Planning**\n\nProvide your route: 'from [start] to [destination]'")
                elif not start_location:
                    dispatcher.utter_message(
                        text=f"🗺️ **Route Planning**\n\n✅ End location: {end_location}\n❌ Missing start location\n\nProvide: 'from [start] to {end_location}'")
                else:  # not end_location
                    dispatcher.utter_message(
                        text=f"🗺️ **Route Planning**\n\n✅ Start location: {start_location}\n❌ Missing end location\n\nProvide: 'from {start_location} to [destination]'")

                # Stay in route planning mode
                return [SlotSet("conversation_context", ConversationContexts.ROUTE_PLANNING)]

            # If we have both locations, process the route
            if start_location and end_location:
                # Set the slots and find charging stations
                try:
                    # Try to get charging stations from the data service
                    stations = data_service.get_route_stations(
                        start_location, end_location)

                    if stations:
                        _send_station_cards(dispatcher, stations, limit=10)
                        response = f"🎯 **Route Confirmed:** {start_location} → {end_location}\n\n"
                        response += f"Found {len(stations)} charging stations along your route:\n\n"

                        displayed = []
                        for i, station in enumerate(stations[:3]):
                            response += f"**{i+1}. {station.get('name', f'Station {i+1}')}**\n"
                            dist = station.get('distance_km')
                            if isinstance(dist, (int, float)):
                                response += f"📍 {dist:.1f} km away\n\n"
                            else:
                                response += f"📍 Distance info unavailable\n\n"
                            displayed.append({
                                'name': station.get('name', f'Station {i+1}')
                            })

                        response += "**Which station would you like to know more about?**\n\n"
                        response += "💡 Simply type the station name"

                        dispatcher.utter_message(text=response)

                        # Set the conversation context to ROUTE_PLANNING_RESULTS so station selection works
                        return [
                            SlotSet("start_location", start_location),
                            SlotSet("end_location", end_location),
                            SlotSet("conversation_context",
                                    ConversationContexts.ROUTE_PLANNING_RESULTS)
                        ]
                    else:
                        # Check if locations were resolved
                        start_coords = data_service._get_location_coordinates(
                            start_location)
                        end_coords = data_service._get_location_coordinates(
                            end_location)

                        if not start_coords:
                            dispatcher.utter_message(
                                text=f"❌ **Location Error:** '{start_location}' not found in coordinates dataset\n\n"
                                     f"🔍 **Debug Info:**\n"
                                     f"• Start location: '{start_location}' → No coordinates found\n"
                                     f"• End location: '{end_location}' → {'Found' if end_coords else 'Not found'}\n"
                                     f"• Check if '{start_location}' exists in Co-ordinates.csv\n\n"
                                     f"💡 **Try again with a different location:**\n"
                                     f"Example: 'from Box Hill to Melbourne' or 'from Richmond to Melbourne'")

                            # Stay in route planning mode, don't set results context
                            return [
                                SlotSet("start_location", None),
                                SlotSet("end_location", None),
                                SlotSet("conversation_context",
                                        ConversationContexts.ROUTE_PLANNING)
                            ]

                        elif not end_coords:
                            dispatcher.utter_message(
                                text=f"❌ **Location Error:** '{end_location}' not found in coordinates dataset\n\n"
                                     f"🔍 **Debug Info:**\n"
                                     f"• Start location: '{start_location}' → Found\n"
                                     f"• End location: '{end_location}' → No coordinates found\n"
                                     f"• Check if '{end_location}' exists in Co-ordinates.csv\n\n"
                                     f"💡 **Try again with a different route:**\n"
                                     f"Example: 'from {start_location} to Melbourne' or 'from {start_location} to Carlton'")

                            # Stay in route planning mode, don't set results context
                            return [
                                SlotSet("start_location", start_location),
                                SlotSet("end_location", None),
                                SlotSet("conversation_context",
                                        ConversationContexts.ROUTE_PLANNING)
                            ]

                        else:
                            dispatcher.utter_message(
                                text=f"🎯 **Route Confirmed:** {start_location} → {end_location}\n\n"
                                     f"📍 **Distance:** {data_service._calculate_distance(start_coords, end_coords):.1f} km\n"
                                     f"❌ **No charging stations found** along this route\n\n"
                                     f"🔍 **Debug Info:**\n"
                                     f"• Both locations resolved successfully\n"
                                     f"• Route distance: {data_service._calculate_distance(start_coords, end_coords):.1f} km\n"
                                     f"• Search radius: {min(data_service._calculate_distance(start_coords, end_coords) * 0.3, 20.0):.1f} km\n"
                                     f"• Check charger_info_mel.csv for stations in this area")

                            # Set results context only when both locations are valid
                            return [
                                SlotSet("start_location", start_location),
                                SlotSet("end_location", end_location),
                                SlotSet("conversation_context",
                                        ConversationContexts.ROUTE_PLANNING_RESULTS)
                            ]
                except Exception as e:
                    print(f"Error finding route stations: {e}")
                    # Check if locations were resolved
                    start_coords = data_service._get_location_coordinates(
                        start_location)
                    end_coords = data_service._get_location_coordinates(
                        end_location)

                    dispatcher.utter_message(
                        text=f"❌ **System Error:** Exception occurred while processing route\n\n"
                             f"🔍 **Debug Info:**\n"
                             f"• Exception: {str(e)}\n"
                             f"• Start location: '{start_location}' → {'Found' if start_coords else 'Not found'}\n"
                             f"• End location: '{end_location}' → {'Found' if end_coords else 'Not found'}\n"
                             f"• Check console logs for full error details\n\n"
                             f"💡 **Try again with a different route:**\n"
                             f"Example: 'from Box Hill to Melbourne' or 'from Richmond to Carlton'")

                    return [
                        SlotSet("start_location", None),
                        SlotSet("end_location", None),
                        SlotSet("conversation_context",
                                ConversationContexts.ROUTE_PLANNING)
                    ]
        else:
            dispatcher.utter_message(
                text="🗺️ **Route Planning**\n\n"
                     f"💡 **Available locations in dataset:**\n"
                     f"• Melbourne, Box Hill, Richmond, Carlton\n"
                     f"• St Kilda, Brighton, Geelong, Dandenong\n"
                     f"• And 190+ other suburbs\n\n"

            )
            return []

    def _process_route(self, dispatcher: CollectingDispatcher, start_location: str, end_location: str) -> List[Dict[Text, Any]]:
        """Helper method to process route with given start and end locations"""
        try:
            # Try to get charging stations from the data service
            stations = data_service.get_route_stations(
                start_location, end_location)

            if stations:
                _send_station_cards(dispatcher, stations, limit=10)
                response = f"🎯 **Route Confirmed:** {start_location} → {end_location}\n\n"
                response += f"Found {len(stations)} charging stations along your route:\n\n"

                displayed = []
                for i, station in enumerate(stations[:3]):
                    response += f"**{i+1}. {station.get('name', f'Station {i+1}')}**\n"
                    dist = station.get('distance_km')
                    if isinstance(dist, (int, float)):
                        response += f"📍 {dist:.1f} km away\n\n"
                    else:
                        response += f"📍 Distance info unavailable\n\n"
                    displayed.append({
                        'name': station.get('name', f'Station {i+1}')
                    })

                response += "**Which station would you like to know more about?**\n\n"
                response += "💡 Simply type the station name"

                dispatcher.utter_message(text=response)

                # Set the conversation context to ROUTE_PLANNING_RESULTS so station selection works
                return [
                    SlotSet("start_location", start_location),
                    SlotSet("end_location", end_location),
                    SlotSet("conversation_context",
                            ConversationContexts.ROUTE_PLANNING_RESULTS)
                ]
            else:
                dispatcher.utter_message(
                    text=f"❌ **No charging stations found** along route: {start_location} → {end_location}\n\n"
                         "💡 **Try:**\n"
                         "• A different destination\n"
                         "• Check if both locations are in Melbourne area")
                return [SlotSet("conversation_context", ConversationContexts.ROUTE_PLANNING)]

        except Exception as e:
            dispatcher.utter_message(
                text=f"❌ **Error processing route**\n\n"
                     f"Please try again or use the format: 'from [start] to [destination]'")
            return [SlotSet("conversation_context", ConversationContexts.ROUTE_PLANNING)]


"""
Removed unused ActionHandleEmergencyInput.
"""


class ActionHandlePreferenceInput(Action):
    def name(self) -> Text:
        return "action_handle_preference_input"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        message = tracker.latest_message.get('text', '').lower().strip()
        conversation_context = tracker.get_slot("conversation_context")

        # Only process if we're in preference charging context
        if conversation_context != ConversationContexts.PREFERENCE_CHARGING:
            return []

        if message in ["cheapest", "fastest", "premium"]:
            preference_type = message

            dispatcher.utter_message(
                text=f"⚡ **{preference_type.title()} Charging** selected!\n\n"
                     f"Where would you like to find {preference_type} charging stations?\n\n"
                     f"💡 **Example:** 'Melbourne' or 'Box Hill'")

            return [
                SlotSet("preference_type", preference_type),
                SlotSet("conversation_context",
                        ConversationContexts.PREFERENCE_CHARGING)
            ]

        # If not a valid preference, show options again
        else:
            dispatcher.utter_message(
                text="⚡ **Charging Preferences**\n\n"
                     "Please select one of these options:\n\n"
                     "• **Cheapest** 💰 - Find the most affordable stations\n"
                     "• **Fastest** ⚡ - Find the highest power stations\n"
                     "• **Premium** 🌟 - Find high-quality stations")
            return []


class ActionHandleRouteInfo(Action):
    def name(self) -> Text:
        return "action_handle_route_info"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        conversation_context = tracker.get_slot("conversation_context")
        if conversation_context == ConversationContexts.ROUTE_PLANNING_RESULTS:
            message = tracker.latest_message.get('text', '').lower().strip()
            start_location = tracker.get_slot("start_location")
            end_location = tracker.get_slot("end_location")

            stations = data_service.get_route_stations(
                start_location, end_location)
            if stations:
                # Prefer stations previously displayed if available
                displayed = tracker.get_slot("displayed_stations") or []
                selected_station = None

                def normalize(text: str) -> str:
                    return (text or '').lower().strip()

                # Try exact/contains match against displayed names first
                for s in displayed:
                    name = normalize(s.get('name'))
                    if name and (name in message or message in name):
                        selected_station = next(
                            (st for st in stations if normalize(st.get('name')) == name), None)
                        if not selected_station:
                            selected_station = next(
                                (st for st in stations if name in normalize(st.get('name'))), None)
                        if selected_station:
                            break

                # Fallback: search all stations in current route list
                if not selected_station:
                    for st in stations:
                        st_name = normalize(st.get('name'))
                        if st_name and (st_name in message or message in st_name):
                            selected_station = st
                            break

                if selected_station:
                    # Reuse the existing display util for details
                    details_resp = ActionHandleRouteStationSelection()._display_station_details(
                        dispatcher, selected_station, start_location, end_location
                    )
                    return details_resp

            # If no match, remind the user clearly
            dispatcher.utter_message(
                text=(
                    "Please type the exact station name from the list above.\n\n"
                    "Example: 'Evie Portland' or 'Newbridge Public Hall'"
                )
            )
            return []

        start_location = None
        end_location = None

        raw_text = tracker.latest_message.get('text', '') or ''
        raw_text = raw_text.strip()
        if raw_text:
            import re
            # Only treat messages that START with 'to' as destination-only inputs here
            if re.match(r'^\s*to\b', raw_text, flags=re.IGNORECASE):
                dest_text = re.sub(r'^\s*to\b', '', raw_text,
                                   flags=re.IGNORECASE).strip()
                if dest_text and 'from' not in raw_text.lower():
                    stored_lat = tracker.get_slot("user_lat")
                    stored_lng = tracker.get_slot("user_lng")
                    if stored_lat and stored_lng:
                        start_location = (stored_lat, stored_lng)
                        end_location = dest_text
                        slots = [
                            SlotSet("start_location", start_location),
                            SlotSet("end_location", end_location)
                        ]
                        return slots + self._find_route_stations(dispatcher, start_location, end_location)
                    else:
                        dispatcher.utter_message(
                            text=f"🗺️ **Route Planning**\n\n"
                                 f"✅ Destination: {dest_text}\n"
                                 f"❌ I don't have your current location stored\n\n"
                                 f"Please provide your starting point:\n"
                                 f"• 'from [start] to {dest_text}'\n"
                                 f"• Or share your location again")
                        return []
        return []

    def _find_route_stations(self, dispatcher: CollectingDispatcher, start_location, end_location: str) -> List[Dict[Text, Any]]:
        stations = data_service.get_route_stations(
            start_location, end_location)

        if stations:
            # Format start location for display
            if isinstance(start_location, tuple):
                start_display = f"Your Location ({start_location[0]:.4f}, {start_location[1]:.4f})"
            else:
                start_display = start_location

            response = f"🎯 Found {len(stations)} charging stations from **{start_display}** to **{end_location}**:\n\n"
            response += format_station_list(stations,
                                            limit=5, show_indices=False) + "\n\n"
            response += "**Which station would you like to know more about?**\n\n"

            dispatcher.utter_message(text=response)
            return [
                SlotSet("conversation_context",
                        ConversationContexts.ROUTE_PLANNING_RESULTS),
                SlotSet("displayed_stations", stations[:5]),
                SlotSet("start_location", start_location),
                SlotSet("end_location", end_location)
            ]
        else:
            if REAL_TIME_INTEGRATION_AVAILABLE and real_time_manager:
                try:
                    real_time_data = real_time_manager.get_enhanced_route_planning(
                        start_location, end_location)

                    if real_time_data.get('success'):
                        response = self._format_real_time_route_response(
                            start_location, end_location, real_time_data)
                        dispatcher.utter_message(text=response)
                        return [
                            SlotSet("conversation_context",
                                    ConversationContexts.ROUTE_PLANNING_RESULTS),
                            SlotSet("start_location", start_location),
                            SlotSet("end_location", end_location)
                        ]
                except Exception as e:
                    print(f"Real-time integration error: {e}")

            dispatcher.utter_message(
                text=f"No charging stations found from {start_location} to {end_location}. Please try a different route.")
            return []

    def _format_real_time_route_response(self, start_location, end_location: str, real_time_data: Dict[str, Any]) -> str:
        # Format start location for display
        if isinstance(start_location, tuple):
            start_display = f"Your Location ({start_location[0]:.4f}, {start_location[1]:.4f})"
        else:
            start_display = start_location

        response = f"🎯 **Real-time Route: {start_display} → {end_location}**\n\n"

        if real_time_data.get('route_info'):
            route = real_time_data['route_info']
            response += f"🗺️ **Route:** {route.get('distance_km', 0):.1f} km, {route.get('duration_minutes', 0):.0f} min\n"
            if route.get('traffic_delay_minutes', 0) > 0:
                response += f"🚦 **Traffic Delay:** +{route.get('traffic_delay_minutes', 0):.0f} min\n"

        if real_time_data.get('traffic_info'):
            traffic = real_time_data['traffic_info']
            response += f"🚦 **Traffic:** {traffic.get('traffic_status', 'Unknown')}\n"
            response += f"⚡ **Speed:** {traffic.get('current_speed_kmh', 0)} km/h\n"

        response += f"\n⚡ **Charging Stations:** Finding stations with real-time data...\n"
        response += f"💡 **Source:** TomTom API | 🕐 **Updated:** Just now"

        return response


class ActionHandleEmergencyCharging(Action):
    def name(self) -> Text:
        return "action_handle_emergency_charging"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        # Check for stored user location
        stored_lat = tracker.get_slot("user_lat")
        stored_lng = tracker.get_slot("user_lng")

        if not stored_lat or not stored_lng:
            dispatcher.utter_message(
                text="❌ I don't have your current location stored.\n\nPlease share your location first.")
            return []

        # Ask for connector type/car model first
        dispatcher.utter_message(
            text="🚨 **Emergency Charging** - Find nearest stations when battery is low\n\n"
                 "Tell me your car model or connector type (e.g., 'Tesla Model 3', 'Type 2', 'CCS').")

        return [SlotSet("conversation_context", ConversationContexts.EMERGENCY_CHARGING)]


class ActionHandleEmergencyLocationInput(Action):
    def name(self) -> Text:
        return "action_handle_emergency_location_input"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        conversation_context = tracker.get_slot("conversation_context")
        if conversation_context not in [ConversationContexts.EMERGENCY_CHARGING, ConversationContexts.EMERGENCY_RESULTS]:
            return []

        # Check for stored user location
        stored_lat = tracker.get_slot("user_lat")
        stored_lng = tracker.get_slot("user_lng")

        if not stored_lat or not stored_lng:
            dispatcher.utter_message(
                text="❌ I don't have your current location stored.\n\nPlease share your location first.")
            return []

        # Use stored coordinates
        current_location = f"({stored_lat:.4f}, {stored_lng:.4f})"

        if current_location:
            # Handle polite termination within emergency flow
            raw_message = tracker.latest_message.get('text', '')
            message = raw_message.lower().strip()
            if any(phrase in message for phrase in ["thanks", "thank you", "thx"]):
                dispatcher.utter_message(text=Messages.GOODBYE)
                return [SlotSet("conversation_context", ConversationContexts.ENDED)]

            connector = self._infer_connector_from_message(message)
            start_location = (stored_lat, stored_lng)
            stations = data_service.get_emergency_stations_from_coordinates(
                start_location)

            if stations:
                if connector:
                    # Find closest station with matching connector
                    best_station = None
                    for st in stations:
                        conn_str = str(st.get('connection_types', '')).lower()
                        power_str = str(st.get('power', '')).lower()

                        # Check if connector matches (including numeric codes)
                        if self._connector_matches(connector, conn_str, power_str):
                            best_station = st
                            break

                    if best_station:
                        # Show station details with immediate directions and real-time traffic option
                        response = f"🚨 Closest {connector.upper()} station near {current_location}:\n\n"
                        response += f"🔌 **{best_station.get('name', 'Unknown')}**\n"
                        response += f"📍 {best_station.get('address', 'Address available')}\n"
                        response += f"⚡ {best_station.get('power', 'Power info available')}\n"
                        response += f"💰 {best_station.get('cost', 'Cost info available')}\n"
                        response += f"🔌 Connector: {connector.upper()}\n\n"

                        # Add directions immediately
                        maps_link = ActionAdvancedDirections()._build_maps_link(
                            current_location, best_station.get(
                                'address') or best_station.get('name')
                        )
                        response += f"🧭 **Directions:** {maps_link}\n\n"

                        # Ask about real-time traffic (using same trigger words as flow 1)
                        response += "**Would you like real-time traffic information?**\n\n"
                        response += "• Type 'traffic update' or 'traffic conditions' for real-time details\n"
                        response += "• Type 'no' or 'skip' to continue"

                        dispatcher.utter_message(text=response)
                        return [
                            SlotSet("selected_station",
                                    best_station.get('name')),
                            SlotSet("conversation_context",
                                    ConversationContexts.EMERGENCY_RESULTS),
                            SlotSet("start_location", current_location),
                            SlotSet("end_location", best_station.get(
                                'address') or best_station.get('name'))
                        ]
                    else:
                        # No connector match found - show user-friendly message
                        dispatcher.utter_message(
                            text=f"❌ No {connector.upper()} compatible stations found near your location.\n\n"
                                 f"💡 Try a different connector type or check nearby stations without connector filtering.")
                        return []
                else:
                    # No connector specified, show closest station
                    best_station = stations[0]

                    # Build maps link for directions
                    maps_link = ActionAdvancedDirections()._build_maps_link(
                        current_location, best_station.get(
                            'address') or best_station.get('name')
                    )

                    # Get real-time traffic information
                    traffic_line = "🚦 Traffic: unavailable right now"
                    try:
                        if REAL_TIME_INTEGRATION_AVAILABLE and real_time_manager:
                            # Use station address for traffic calculation
                            destination_hint = best_station.get(
                                'address') or best_station.get('name')
                            traffic = real_time_manager.get_traffic_conditions(
                                current_location, destination_hint)
                            if traffic:
                                status = traffic.get(
                                    'traffic_status', 'Unknown')
                                speed = traffic.get('current_speed_kmh', 0)
                                delay = traffic.get(
                                    'estimated_delay_minutes', 0)
                                src = traffic.get('data_source') or 'Real-time'
                                traffic_line = f"🚦 Traffic: {status} • {speed} km/h • +{delay} min | {src}"
                    except Exception as _:
                        pass

                    response = f"🚨 Closest station near {current_location}:\n\n"
                    response += f"🔌 **{best_station.get('name', 'Unknown')}**\n"
                    response += f"📍 {best_station.get('address', 'Address available')}\n"
                    response += f"⚡ {best_station.get('power', 'Power info available')}\n"
                    response += f"💰 {best_station.get('cost', 'Cost info available')}\n"
                    response += f"{traffic_line}\n"
                    response += f"🔗 {maps_link}"

                    dispatcher.utter_message(text=response)
                    return [
                        SlotSet("selected_station", best_station.get('name')),
                        SlotSet("conversation_context",
                                ConversationContexts.STATION_DETAILS)
                    ]
            else:
                dispatcher.utter_message(
                    text=f"❌ No emergency charging stations found near {current_location}. Please try a different area.")
                return []

    def _find_emergency_stations(self, dispatcher: CollectingDispatcher, current_location: str) -> List[Dict[Text, Any]]:
        stations = data_service.get_emergency_stations(current_location)

        if stations:
            _send_station_cards(dispatcher, stations, limit=5)
            response = f"🚨 Emergency charging stations near {current_location}:\n\n"

            for i, station in enumerate(stations, 1):
                response += f"{i}. **{station['name']}** - {station['distance_km']}km away, {station['cost']} ✅\n"

            response += "\nAll have available charging points. Which one?"
            dispatcher.utter_message(text=response)
            return [SlotSet("conversation_context", ConversationContexts.EMERGENCY_RESULTS)]
        else:
            dispatcher.utter_message(
                text=f"No charging stations found near {current_location}. Please try a different location.")
            return []

    def _infer_connector_from_message(self, message: str) -> Optional[str]:
        msg = (message or '').lower()

        # Direct connector mentions
        if 'chademo' in msg:
            return 'chademo'
        if 'ccs2' in msg or 'ccs 2' in msg or 'ccs' in msg:
            return 'ccs'
        if 'type 2' in msg or 'mennekes' in msg:
            return 'type 2'
        if 'tesla' in msg:
            return 'tesla'

        car_ccs = [
            # Hyundai/Kia
            'ioniq', 'kona', 'ev6', 'e-niro', 'niro', 'ev6', 'soul ev',
            # MG
            'mg zs', 'mg 4', 'mg 5', 'mg marvel', 'mg cyberster',
            # Polestar/Volvo
            'polestar', 'volvo xc40', 'volvo c40', 'volvo ex30', 'volvo ex90',
            # BYD
            'byd', 'atto 3', 'dolphin', 'seal', 'tang', 'han',
            # Volkswagen Group
            'id.3', 'id.4', 'id.5', 'id.buzz', 'audi e-tron', 'audi q4', 'porsche taycan',
            # BMW
            'bmw i3', 'bmw i4', 'bmw ix', 'bmw i7', 'bmw i5',
            # Mercedes
            'eqa', 'eqb', 'eqc', 'eqe', 'eqs', 'mercedes ev',
            # Ford
            'mustang mach-e', 'f-150 lightning', 'e-transit',
            # Chevrolet
            'bolt', 'bolt euv', 'silverado ev', 'blazer ev',
            # Other popular EVs
            'rivian r1t', 'rivian r1s', 'lucid air', 'fisker ocean', 'canoo'
        ]

        car_chademo = [
            # Nissan
            'leaf', 'ariya',
            # Mitsubishi
            'outlander phev', 'i-miev',
            # Kia (older models)
            'soul ev 2014-2019'
        ]

        car_type2 = [
            # Tesla (older models in Europe)
            'tesla model s', 'tesla model x', 'tesla model 3', 'tesla model y',
            # European EVs
            'renault zoe', 'peugeot e-208', 'opel corsa-e', 'fiat 500e',
            # Japanese EVs
            'toyota bz4x', 'subaru solterra', 'lexus rz'
        ]

        car_tesla = [
            # Tesla (North America/Asia)
            'tesla model s', 'tesla model x', 'tesla model 3', 'tesla model y',
            'cybertruck', 'roadster'
        ]

        # Check for car models
        for kw in car_ccs:
            if kw in msg:
                return 'ccs'
        for kw in car_chademo:
            if kw in msg:
                return 'chademo'
        for kw in car_type2:
            if kw in msg:
                return 'type 2'
        for kw in car_tesla:
            if kw in msg:
                return 'tesla'

        return None

    def _connector_matches(self, connector: str, conn_str: str, power_str: str) -> bool:
        """Check if connector matches station data (including numeric codes)"""
        connector = connector.lower()

        # Direct text matching
        if connector in conn_str or connector in power_str:
            return True

        # Numeric code mapping for Type 2 (Mennekes)
        if connector == 'type 2':
            # IEC 62196 Type 2 codes: 2, 25, 33
            type2_codes = ['2', '25', '33']
            for code in type2_codes:
                if code in conn_str:
                    return True

        # Numeric code mapping for CCS
        elif connector == 'ccs':
            # CCS codes: 1, 21, 31
            ccs_codes = ['1', '21', '31']
            for code in ccs_codes:
                if code in conn_str:
                    return True

        # Numeric code mapping for CHAdeMO
        elif connector == 'chademo':
            # CHAdeMO codes: 4, 24, 34
            chademo_codes = ['4', '24', '34']
            for code in chademo_codes:
                if code in conn_str:
                    return True

        # Numeric code mapping for Tesla
        elif connector == 'tesla':
            # Tesla codes: 5, 25, 35
            tesla_codes = ['5', '25', '35']
            for code in tesla_codes:
                if code in conn_str:
                    return True

        return False


class ActionHandlePreferenceCharging(Action):
    def name(self) -> Text:
        return "action_handle_preference_charging"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        message = tracker.latest_message.get('text', '').lower().strip()
        conversation_context = tracker.get_slot("conversation_context")
        preference = None

        if conversation_context == ConversationContexts.ROUTE_PLANNING_RESULTS:
            start_location = tracker.get_slot("start_location")
            end_location = tracker.get_slot("end_location")
            try:
                stations = data_service.get_route_stations(
                    start_location, end_location)
                if stations:
                    displayed = tracker.get_slot("displayed_stations") or []
                    selected_station = None

                    def normalize(text: str) -> str:
                        return (text or '').lower().strip()

                    for s in displayed:
                        name = normalize(s.get('name'))
                        if name and (name in message or message in name):
                            selected_station = next(
                                (st for st in stations if normalize(st.get('name')) == name), None)
                            if not selected_station:
                                selected_station = next(
                                    (st for st in stations if name in normalize(st.get('name'))), None)
                            if selected_station:
                                break

                    if not selected_station:
                        for st in stations:
                            st_name = normalize(st.get('name'))
                            if st_name and (st_name in message or message in st_name):
                                selected_station = st
                                break

                    if selected_station:
                        return ActionHandleRouteStationSelection()._display_station_details(
                            dispatcher, selected_station, start_location, end_location
                        )
            except Exception as e:
                print(f"PreferenceCharging quick-select error: {e}")

            # If no station matched, ask the user to type exact name again
            dispatcher.utter_message(
                text=(
                    "Please type the exact station name from the list above.\n\n"
                    "Example: 'Evie Portland' or 'Newbridge Public Hall'"
                )
            )
            return []

        # Only handle preferences inside the preference charging context
        if conversation_context != ConversationContexts.PREFERENCE_CHARGING:
            return []

        if any(word in message for word in ['cheapest', 'cheap', 'lowest', 'cost']):
            preference = PreferenceTypes.CHEAPEST
        elif any(word in message for word in ['fastest', 'fast', 'speed', 'ultra']):
            preference = PreferenceTypes.FASTEST

        elif any(word in message for word in ['premium', 'best', 'luxury', 'amenities']):
            preference = PreferenceTypes.PREMIUM

        if preference:
            # Check for stored user location first
            stored_lat = tracker.get_slot("user_lat")
            stored_lng = tracker.get_slot("user_lng")

            if stored_lat and stored_lng:
                # Use stored GPS coordinates
                current_location = f"({stored_lat:.4f}, {stored_lng:.4f})"

                # Get stations by preference using stored coordinates
                try:
                    coords = (stored_lat, stored_lng)
                    stations = data_service.get_stations_by_preference(
                        coords, preference)

                    if stations:
                        _send_station_cards(dispatcher, stations, limit=5)
                        return [
                            SlotSet("charging_preference", preference),
                            SlotSet("current_location", current_location),
                            SlotSet("conversation_context",
                                    ConversationContexts.PREFERENCE_RESULTS),
                            SlotSet("displayed_stations", stations[:5])
                        ]
                    else:
                        dispatcher.utter_message(
                            text=f"❌ No {preference.lower()} charging stations found near your location. Please try a different preference.")
                        return [SlotSet("charging_preference", preference)]

                except Exception as e:
                    dispatcher.utter_message(
                        text=f"❌ Error finding {preference.lower()} stations: {str(e)}")
                    return [SlotSet("charging_preference", preference)]
            else:
                # Fallback to asking for location if GPS not available
                dispatcher.utter_message(
                    text=f"⚡ {preference} charging stations. Please provide your location.")
                return [SlotSet("charging_preference", preference)]
        else:
            dispatcher.utter_message(
                text="Please choose a preference:\n\n"
                     "• **Cheapest** - Lowest cost per kWh 💰\n"
                     "• **Fastest** - Ultra-fast charging speeds ⚡\n"
                     "• **Premium** - Best facilities & amenities 🌟")
            return []


class ActionGetDirectionsById(Action):
    def name(self) -> Text:
        return "action_get_directions_by_id"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        # Extract station_id from slash intent payload
        station_id: Optional[str] = None
        for ent in tracker.latest_message.get("entities", []) or []:
            if ent.get("entity") == "station_id" and ent.get("value") is not None:
                station_id = str(ent.get("value"))
                break

        displayed: List[Dict[str, Any]] = tracker.get_slot(
            "displayed_stations") or []

        # Try to find matching station in displayed list
        target_station: Optional[Dict[str, Any]] = None
        if isinstance(displayed, list):
            for st in displayed:
                if not isinstance(st, dict):
                    continue
                try:
                    mapped = _map_station_for_ui(st)
                    if station_id and str(mapped.get("station_id")) == str(station_id):
                        target_station = st
                        break
                except Exception:
                    continue

        # Determine origin and destination strings for maps link
        user_lat = tracker.get_slot("user_lat")
        user_lng = tracker.get_slot("user_lng")
        origin_str = f"({user_lat}, {user_lng})" if user_lat is not None and user_lng is not None else "My Location"

        if target_station is None:
            # Fallback to using station_id directly as destination if it looks like coordinates
            dest_hint = None
            if isinstance(station_id, str) and "," in station_id:
                dest_hint = station_id
            else:
                # Last resort: use any selected station name in slot
                dest_hint = tracker.get_slot(
                    "selected_station") or station_id or "Destination"
            maps_link = ActionAdvancedDirections()._build_maps_link(origin_str, str(dest_hint))
            dispatcher.utter_message(text=f"🧭 **Directions:** {maps_link}")
            # Store route so traffic action can use it
            start_slot = [user_lat, user_lng] if user_lat is not None and user_lng is not None else (
                tracker.get_slot("current_location") or "My Location")
            return [
                SlotSet("start_location", start_slot),
                SlotSet("end_location", str(dest_hint)),
            ]

        # Use station address/name
        destination = target_station.get("address") or target_station.get(
            "name") or station_id or "Destination"
        maps_link = ActionAdvancedDirections()._build_maps_link(origin_str, destination)

        dispatcher.utter_message(text=f"🧭 **Directions:** {maps_link}")
        # Persist route for traffic lookup
        start_slot = [user_lat, user_lng] if user_lat is not None and user_lng is not None else (
            tracker.get_slot("current_location") or "My Location")
        responses: List[Dict[Text, Any]] = [
            SlotSet("start_location", start_slot),
            SlotSet("end_location", destination),
        ]
        dispatcher.utter_message(text="Would you like real-time traffic for this route?",
                                 buttons=[
                                     {"title": "Yes, show traffic",
                                         "payload": "/get_traffic_info"},
                                     {"title": "No, thanks", "payload": "/goodbye"},
                                 ])
        return responses


class ActionHandlePreferenceLocationInput(Action):
    def name(self) -> Text:
        return "action_handle_preference_location_input"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        preference = tracker.get_slot("charging_preference")
        location = tracker.get_slot(
            "current_location") or tracker.latest_message.get('text', '').strip()

        if not preference:
            dispatcher.utter_message(
                text="Please select a charging preference first.")
            return []

        if not location:
            dispatcher.utter_message(text="Please provide your location.")
            return []

        coords = data_service._get_location_coordinates(location)
        if not coords:
            dispatcher.utter_message(
                text=f"❌ I can't find charging stations in {location}.")
            return []

        stations = data_service.get_stations_by_preference(
            coords, preference)

        if stations:
            _send_station_cards(dispatcher, stations, limit=5)
            return [
                SlotSet("conversation_context",
                        ConversationContexts.PREFERENCE_RESULTS),
                SlotSet("current_location", location),
                SlotSet("displayed_stations", stations[:5])
            ]
        else:
            dispatcher.utter_message(
                text=f"No {preference.lower()} charging stations found near {location}. Please try a different location.")
            return []


class ActionHandleRouteStationSelection(Action):
    def name(self) -> Text:
        return "action_handle_route_station_selection"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        conversation_context = tracker.get_slot("conversation_context")
        # Allow selection both right after route results and after a comparison view
        if conversation_context not in [ConversationContexts.ROUTE_PLANNING_RESULTS, ConversationContexts.STATION_DETAILS]:

            pref_contexts = [ConversationContexts.PREFERENCE_CHARGING,
                             ConversationContexts.PREFERENCE_RESULTS]
            if conversation_context in pref_contexts:
                displayed = tracker.get_slot("displayed_stations") or []
                names = ", ".join([s.get('name')
                                   for s in displayed]) or "the list above"
                dispatcher.utter_message(
                    text=(
                        "❌ I can't match that to a station from the current list. "
                        f"Please type one of: {names}."
                    )
                )
                return []
            # If no context, likely the input didn't include a recognisable station or route
            dispatcher.utter_message(
                text=(
                    "❌ I couldn't determine a destination. "
                    "This can happen if the station name isn't in the dataset or wasn't clear. "
                )
            )
            return []

        message = tracker.latest_message.get('text', '').lower().strip()
        decision_phrases = [
            "i go with", "i choose", "i select", "i pick",
            "i would like to go to", "i would like to go", "i would like",
            "i'd like to go to", "i'd like to go", "i'd like",
            "i will go with", "i'll go with", "go with", "take"
        ]
        decision_requested = any(
            phrase in message for phrase in decision_phrases)
        selected_station_name = self._extract_station_name(message)

        if not selected_station_name:
            dispatcher.utter_message(
                text=(
                    "❌ I couldn't extract a station from your message. "
                    "It might not be in the dataset or was unclear. Try the exact name shown above, e.g., 'Lynbrook Village'."
                )
            )
            return []

        start_location = tracker.get_slot("start_location")
        end_location = tracker.get_slot("end_location")

        try:
            stations = data_service.get_route_stations(
                start_location, end_location)
            if stations:
                # Prefer the stations we actually displayed
                displayed = tracker.get_slot("displayed_stations") or []
                selected_station = None
                # Try match against displayed snapshot first
                if displayed:
                    for s in displayed:
                        name = (s.get('name') or '').lower()
                        if selected_station_name.lower() in name or name in selected_station_name.lower():
                            # find full station dict from current stations by name
                            selected_station = next((st for st in stations if (
                                st.get('name', '').lower() == name)), None)
                            if not selected_station:
                                # fallback to first partial match
                                selected_station = next(
                                    (st for st in stations if name in st.get('name', '').lower()), None)
                            break
                # Fallback to current stations list if not found via snapshot
                if not selected_station:
                    for station in stations:
                        if selected_station_name.lower() in station.get('name', '').lower():
                            selected_station = station
                            break

                if selected_station:
                    if decision_requested:
                        # Directly provide directions and trigger traffic when possible
                        current_location = tracker.get_slot("current_location")
                        origin_for_link = start_location or current_location or "My Location"
                        maps_link = ActionAdvancedDirections()._build_maps_link(
                            origin_for_link,
                            selected_station.get(
                                'address') or selected_station.get('name')
                        )
                        response = (
                            f"🧭 **Directions**\n\n"
                            f"Start: {origin_for_link}\n"
                            f"Destination: {selected_station.get('name')}\n\n"
                            f"🔗 {maps_link}"
                        )
                        dispatcher.utter_message(text=response)
                        from rasa_sdk.events import FollowupAction
                        events = [
                            SlotSet("selected_station",
                                    selected_station.get('name')),
                            SlotSet("end_location",
                                    selected_station.get('name')),
                            SlotSet("conversation_context", None),
                        ]
                        # Persist a concrete origin if we have one (avoid hardcoding defaults)
                        if start_location:
                            events.append(
                                SlotSet("start_location", start_location))
                        elif current_location:
                            events.append(
                                SlotSet("start_location", current_location))
                        # Trigger traffic only if both origin and destination are known
                        if start_location or current_location:
                            events.append(FollowupAction(
                                "action_traffic_info"))
                        return events
                    else:
                        return self._display_station_details(dispatcher, selected_station, start_location, end_location)
                else:
                    # Provide a dataset-aware explanation instead of robotic repeat
                    displayed_names = ", ".join(
                        [s.get('name') for s in (
                            tracker.get_slot("displayed_stations") or [])]
                    ) or "the list above"
                    dispatcher.utter_message(
                        text=(
                            "❌ I couldn't find that station in the dataset for this route. "
                            f"Please choose one of: {displayed_names}."
                        )
                    )
                    return []
            else:
                dispatcher.utter_message(
                    text="No stations found. Please plan a route again.")
                return []

        except Exception as e:
            print(f"Error in station selection: {e}")
            dispatcher.utter_message(
                text="Unable to process station selection. Please try again.")
            return []

    def _extract_station_name(self, message: str) -> Optional[str]:
        message = message.strip()
        if len(message) > 2:
            return message
        return None

    def _display_station_details(self, dispatcher: CollectingDispatcher, station_details: Dict, start_location: str, end_location: str) -> List[Dict[Text, Any]]:
        """Display detailed station information and next action options"""
        response = f"🎯 **{station_details['name']}**\n\n"
        response += f"📍 **Address:** {station_details.get('address', 'Address available')}\n"
        response += f"⚡ **Power:** {station_details.get('power', 'Power info available')} charging\n"
        response += f"💰 **Cost:** {station_details.get('cost', 'Cost info available')}\n"
        response += f"🕐 **Charging time:** {station_details.get('charging_time', 'Time estimate available')}\n"
        response += f"🔌 **Available points:** {station_details.get('points', 'Point info available')}\n\n"

        response += "**🎯 What would you like to do next?**\n\n"
        response += "• Type 'get directions' to this station 🧭\n"
        response += "• Type 'compare options' 📊\n"
        response += "• Type 'check availability' ✅"

        dispatcher.utter_message(text=response)
        return [
            SlotSet("conversation_context",
                    ConversationContexts.STATION_DETAILS),
            SlotSet("selected_station", station_details.get('name'))
        ]


class ActionHandleEmergencyStationSelection(Action):
    def name(self) -> Text:
        return "action_handle_emergency_station_selection"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        conversation_context = tracker.get_slot("conversation_context")
        if conversation_context not in [ConversationContexts.EMERGENCY_CHARGING, ConversationContexts.EMERGENCY_RESULTS]:
            return []

        message = tracker.latest_message.get('text', '').lower().strip()
        selected_station_name = self._extract_station_name(message)

        if not selected_station_name:
            dispatcher.utter_message(
                text="Please type the station name from the list above.")
            return []

        current_location = tracker.get_slot("current_location")
        # Battery no longer required in emergency flow
        if not current_location:
            dispatcher.utter_message(
                text="❌ I need your location to proceed. Please type your suburb (e.g., 'Richmond').")
            return []

        stations = data_service.get_emergency_stations(current_location)
        if stations:
            selected_station = None
            station_index = None

            for i, station in enumerate(stations):
                if selected_station_name.lower() in station.get('name', '').lower():
                    selected_station = station
                    station_index = i + 1
                    break

            if selected_station:
                return self._display_emergency_station_details(dispatcher, selected_station, station_index, current_location)
            else:
                dispatcher.utter_message(
                    text="Station not found. Please type the exact station name from the list above.")
                return []
        else:
            dispatcher.utter_message(
                text="No emergency stations found. Please try a different location.")
            return []

    def _extract_station_name(self, message: str) -> Optional[str]:
        message = message.strip()
        if len(message) > 2:
            return message
        return None

    def _display_emergency_station_details(self, dispatcher: CollectingDispatcher, selected_station: Dict, station_index: int, current_location: str) -> List[Dict[Text, Any]]:
        response = f"🚨 **Emergency Station {station_index}: {selected_station['name']}**\n\n"
        response += f"📍 **Address:** {selected_station.get('address', 'Address available')}\n"
        response += f"⚡ **Power:** {selected_station.get('power', 'Power info available')} charging\n"
        response += f"💰 **Cost:** {selected_station.get('cost', 'Cost info available')}\n"
        response += f"🕐 **Charging time:** {selected_station.get('charging_time', 'Time estimate available')}\n"
        response += f"🔌 **Available points:** {selected_station.get('points', 'Point info available')}\n\n"

        response += "**🎯 What would you like to do next?**\n\n"
        response += "• Type 'get directions' to this station 🧭\n"
        response += "• Type 'compare options' 📊\n"
        response += "• Type 'check availability' ✅"

        dispatcher.utter_message(text=response)
        return [
            SlotSet("conversation_context",
                    ConversationContexts.STATION_DETAILS),
            SlotSet("selected_station", selected_station.get('name'))
        ]


class ActionHandlePreferenceStationSelection(Action):
    def name(self) -> Text:
        return "action_handle_preference_station_selection"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        conversation_context = tracker.get_slot("conversation_context")
        if conversation_context not in [ConversationContexts.PREFERENCE_CHARGING, ConversationContexts.PREFERENCE_RESULTS]:
            return []

        message = tracker.latest_message.get('text', '').lower().strip()
        selected_station_name = self._extract_station_name(message)

        if not selected_station_name:
            dispatcher.utter_message(
                text="Please type the station name from the list above.")
            return []

        preference = tracker.get_slot("charging_preference")
        location = tracker.get_slot("current_location")

        if not preference or not location:
            dispatcher.utter_message(
                text="❌ Missing preference or location. Please start over.")
            return []

        try:
            # Check if location is already GPS coordinates
            if isinstance(location, str) and location.startswith('(') and location.endswith(')'):
                # Extract coordinates from string format "(lat, lng)"
                try:
                    lat_str, lng_str = location.strip('()').split(',')
                    coords = (float(lat_str.strip()), float(lng_str.strip()))
                except:
                    dispatcher.utter_message(
                        text=f"❌ Invalid location format: {location}")
                    return []
            else:
                # Try to convert text location to coordinates
                coords = data_service._get_location_coordinates(location)
                if not coords:
                    dispatcher.utter_message(
                        text=f"❌ I can't find charging stations in {location}.")
                    return []

            stations = data_service.get_stations_by_preference(
                coords, preference)
            if stations:
                selected_station = None
                station_index = None

                for i, station in enumerate(stations):
                    if selected_station_name.lower() in station.get('name', '').lower():
                        selected_station = station
                        station_index = i + 1
                        break

                if selected_station:
                    # Directly provide directions, then trigger real-time traffic if possible
                    current_location = tracker.get_slot("current_location")
                    origin_for_link = current_location or tracker.get_slot(
                        "start_location") or "My Location"
                    maps_link = ActionAdvancedDirections()._build_maps_link(
                        origin_for_link,
                        selected_station.get(
                            'address') or selected_station.get('name')
                    )
                    response = (
                        f"🧭 **Directions**\n\n"
                        f"Start: {origin_for_link}\n"
                        f"Destination: {selected_station.get('name')}\n\n"
                        f"🔗 {maps_link}"
                    )
                    dispatcher.utter_message(text=response)
                    from rasa_sdk.events import FollowupAction
                    events: List[Dict[Text, Any]] = [
                        SlotSet("selected_station",
                                selected_station.get('name')),
                        SlotSet("end_location", selected_station.get('name')),
                        SlotSet("start_location", current_location or tracker.get_slot(
                            "start_location")),
                        SlotSet("conversation_context", None),
                    ]
                    if current_location or tracker.get_slot("start_location"):
                        events.append(FollowupAction("action_traffic_info"))
                    return events
                else:
                    dispatcher.utter_message(
                        text="Station not found. Please type the exact station name from the list above.")
                    return []
            else:
                dispatcher.utter_message(
                    text="No preference-based stations found. Please try a different location.")
                return []

        except Exception as e:
            print(f"Error in preference station selection: {e}")
            dispatcher.utter_message(
                text="Unable to process station selection. Please try again.")
            return []

    def _extract_station_name(self, message: str) -> Optional[str]:
        message = message.strip()
        if len(message) > 2:
            return message
        return None

    def _display_preference_station_details(self, dispatcher: CollectingDispatcher, selected_station: Dict, station_index: int, location: str, preference: str) -> List[Dict[Text, Any]]:
        response = f"⚡ **{preference.title()} Station {station_index}: {selected_station['name']}**\n\n"
        response += f"📍 **Address:** {selected_station.get('address', 'Address available')}\n"
        response += f"⚡ **Power:** {selected_station.get('power', 'Power info available')} charging\n"
        response += f"💰 **Cost:** {selected_station.get('cost', 'Cost info available')}\n"
        response += f"🕐 **Charging time:** {selected_station.get('charging_time', 'Time estimate available')}\n"
        response += f"🔌 **Available points:** {selected_station.get('points', 'Point info available')}\n\n"

        response += "**🎯 What would you like to do next?**\n\n"
        response += "• Type 'get directions' to this station 🧭\n"
        response += "• Type 'compare options' 📊\n"
        response += "• Type 'check availability' ✅"

        dispatcher.utter_message(text=response)
        return [
            SlotSet("conversation_context",
                    ConversationContexts.STATION_DETAILS),
            SlotSet("selected_station", selected_station.get('name'))
        ]


class ActionHandleActionChoice(Action):
    def name(self) -> Text:
        return "action_handle_action_choice"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        conversation_context = tracker.get_slot("conversation_context")
        if conversation_context != ConversationContexts.STATION_DETAILS:
            return []

        message = tracker.latest_message.get('text', '').lower().strip()
        choice = None

        if any(word in message for word in ['compare', 'comparison', 'other', 'options']):
            choice = "compare"
        elif any(word in message for word in ['availability', 'available', 'status', 'check']):
            choice = "availability"

        if choice == "compare":
            return self._show_comparison(dispatcher, tracker)
        elif choice == "availability":
            self._show_availabilty(dispatcher, tracker)

            return [SlotSet("conversation_context", ConversationContexts.ENDED)]
        else:
            dispatcher.utter_message(
                text="Please type one of: 'get directions', 'compare options', or 'check availability'")
            return []

    def _show_comparison(self, dispatcher: CollectingDispatcher, tracker: Tracker) -> List[Dict[Text, Any]]:
        start_location = tracker.get_slot("start_location")
        end_location = tracker.get_slot("end_location")
        displayed = tracker.get_slot("displayed_stations") or []

        stations_to_compare: List[Dict[str, Any]] = []
        try:
            route_stations = data_service.get_route_stations(
                start_location, end_location) if start_location and end_location else []
            # Map by lowercased name for quick lookup
            by_name = {(s.get('name') or '').lower()
                        : s for s in route_stations}

            # Prefer the ones we showed to the user first
            for s in displayed[:5]:
                name = (s.get('name') or '').lower()
                if name and name in by_name:
                    stations_to_compare.append(by_name[name])

            if len(stations_to_compare) < 3:
                for s in route_stations:
                    if s not in stations_to_compare:
                        stations_to_compare.append(s)
                    if len(stations_to_compare) >= 5:
                        break
        except Exception as e:
            print(f"Compare options error: {e}")

        if not stations_to_compare:
            dispatcher.utter_message(
                text="❌ No stations available to compare right now.")
            return []

        lines: List[str] = []
        lines.append("📊 Comparison of options on your route:\n")
        for i, st in enumerate(stations_to_compare, 1):
            lines.append(f"{i}. **{st.get('name','Unknown')}**")
            lines.append(f"   📍 {st.get('suburb','Location available')}")
            lines.append(f"   ⚡ {st.get('power','Power info available')}")
            lines.append(f"   💰 {st.get('cost','Cost info available')}")
            lines.append(f"   🔌 Points: {st.get('points','Info available')}\n")

        lines.append(
            "Type a station name to choose one, or type 'get directions' for the selected station.")
        dispatcher.utter_message(text="\n".join(lines))
        return [SlotSet("conversation_context", ConversationContexts.STATION_DETAILS)]

    def _show_availabilty(self, dispatcher: CollectingDispatcher, tracker: Tracker) -> List[Dict[Text, Any]]:

        # Prefer selected station coordinates when available; otherwise use stored user location
        lat = None
        lng = None

        selected_station = tracker.get_slot("selected_station")
        displayed_stations = tracker.get_slot("displayed_stations") or []
        if selected_station and displayed_stations:
            for st in displayed_stations:
                if st.get("name") == selected_station:
                    lat = st.get("latitude")
                    lng = st.get("longitude")
                    break

        if lat is None or lng is None:
            lat = tracker.get_slot("user_lat")
            lng = tracker.get_slot("user_lng")

        if lat is None or lng is None:
            dispatcher.utter_message(
                text=f"I need a location to check availability.")
            return []

        status, updated_at, data = data_service._get_station_availability(
            float(lat), float(lng))

        # Guard against non-dict payloads (e.g., string errors)
        if not isinstance(data, dict):
            msg = f"🔌 **Station Availability:** {status}\n"
            if isinstance(data, str) and data.strip():
                msg += data.strip()
            else:
                msg += "No additional availability details available."
            dispatcher.utter_message(text=msg)
            return []

        msg = f"🔌 **Station Availability:**\n"
        connectors = data.get('connectors', [])
        if not connectors:
            info = data.get('raw')
            if isinstance(info, str) and info.strip():
                msg += info.strip()
            else:
                msg += f"{status}"
            dispatcher.utter_message(text=msg)
            return []
        for conn in connectors:
            conn_type = conn.get('type', 'Unknown')
            total = conn.get('total', 0)
            current = conn.get('availability', {}).get('current', {})
            available = current.get('available', 0)
            occupied = current.get('occupied', 0)
            reserved = current.get('reserved', 0)
            unknown = current.get('unknown', 0)
            out_of_service = current.get('outOfService', 0)
            msg += (
                f"\n• **{conn_type}** (Total: {total})\n"
                f"   - Available: {available}\n"
                f"   - Occupied: {occupied}\n"
                f"   - Reserved: {reserved}\n"
                f"   - Unknown: {unknown}\n"
                f"   - Out of Service: {out_of_service}\n"
            )
            # Show per power level if available
            per_power = conn.get('availability', {}).get('perPowerLevel', [])
            for p in per_power:
                power_kw = p.get('powerKW', '')
                msg += (
                    f"   - Power: {power_kw} kW | "
                    f"Available: {p.get('available', 0)}, "
                    f"Occupied: {p.get('occupied', 0)}, "
                    f"Reserved: {p.get('reserved', 0)}, "
                    f"Unknown: {p.get('unknown', 0)}, "
                    f"Out of Service: {p.get('outOfService', 0)}\n"
                )

        dispatcher.utter_message(text=msg)
        return []


class ActionHandleFollowUp(Action):
    def name(self) -> Text:
        return "action_handle_follow_up"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        conversation_context = tracker.get_slot("conversation_context")

        if conversation_context == ConversationContexts.GETTING_DIRECTIONS:
            dispatcher.utter_message(
                text="🗺️ Directions are being calculated...")
        elif conversation_context == ConversationContexts.COMPARING_STATIONS:
            dispatcher.utter_message(
                text="📊 Station comparison in progress...")
        elif conversation_context == ConversationContexts.CHECKING_AVAILABILITY:
            dispatcher.utter_message(text="✅ Checking availability...")
        else:
            dispatcher.utter_message(text="How can I help you further?")

        return []


class ActionAdvancedDirections(Action):
    def name(self) -> Text:
        return "action_advanced_directions"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        """Provide directions and traffic-aware ETA using real-time integration if available."""
        start_location = tracker.get_slot("start_location")
        end_location = tracker.get_slot("end_location")
        selected_station = tracker.get_slot("selected_station")

        # Prioritize selected_station over end_location when user asks for directions to a specific station
        if selected_station:
            end_location = selected_station

        if not start_location or not end_location:
            dispatcher.utter_message(
                text=(
                    "🧭 Please provide both start and destination to get directions.\n\n"
                    "Example: 'from Richmond to Malvern Central'"
                )
            )
            return []

        # Try real-time enhanced route planning
        if REAL_TIME_INTEGRATION_AVAILABLE and real_time_manager:
            try:
                real_time_data = real_time_manager.get_enhanced_route_planning(
                    start_location, end_location
                )

                if real_time_data and real_time_data.get("success"):
                    route_info = real_time_data.get("route_info") or {}
                    traffic_info = real_time_data.get("traffic_info") or {}

                    distance_km = route_info.get("distance_km")
                    duration_min = route_info.get("duration_minutes")
                    delay_min = route_info.get("traffic_delay_minutes")

                    # Send structured payload for frontend first
                    instr_list = route_info.get("instructions") or []
                    if not isinstance(instr_list, list):
                        instr_list = []
                    instr_list = [str(s) for s in instr_list][:10]
                    try:
                        dispatcher.utter_message(json_message={
                            "type": "directions",
                            "origin": start_location,
                            "destination": end_location,
                            "distance_km": distance_km,
                            "eta_min": duration_min,
                            "delay_min": delay_min,
                            "instructions": instr_list,
                            "maps_url": self._build_maps_link(start_location, end_location),
                        })
                    except Exception:
                        pass

                    # Log computed directions metrics for observability
                    logger.info(
                        f"Directions computed: {start_location} → {end_location} | "
                        f"distance_km={distance_km} | duration_min={duration_min} | delay_min={delay_min}"
                    )

                    response_parts: List[str] = []
                    response_parts.append(
                        f"🗺️ Directions: {start_location} → {end_location}"
                    )

                    if distance_km is not None or duration_min is not None:
                        dist_txt = f"{distance_km:.1f} km" if isinstance(
                            distance_km, (int, float)) else "—"
                        dur_txt = f"{int(duration_min)} min" if isinstance(
                            duration_min, (int, float)) else "—"
                        response_parts.append(
                            f"• Distance: {dist_txt} | ETA: {dur_txt}")

                    if isinstance(delay_min, (int, float)) and delay_min > 0:
                        response_parts.append(
                            f"• Traffic delay: +{int(delay_min)} min")

                    if traffic_info:
                        congestion = traffic_info.get("congestion_level")
                        status = traffic_info.get("traffic_status")
                        if status or congestion is not None:
                            response_parts.append(
                                f"• Traffic: {status or 'Unknown'}"
                                + (f" (level {congestion})" if congestion is not None else "")
                            )

                    dispatcher.utter_message(text="\n".join(response_parts))
                    # Ask if the user wants real-time traffic next
                    dispatcher.utter_message(
                        text="Would you like real-time traffic for this route?",
                        buttons=[
                            {"title": "Yes, show traffic",
                                "payload": "/get_traffic_info"},
                            {"title": "No, thanks", "payload": "/goodbye"},
                        ],
                    )
                    return [
                        SlotSet("conversation_context",
                                None),
                        SlotSet("start_location", start_location),
                        SlotSet("end_location", end_location),
                    ]

            except Exception as e:
                print(f"Error in ActionAdvancedDirections real-time flow: {e}")

        dispatcher.utter_message(
            text=(
                f"🗺️ Directions requested for {start_location} → {end_location}.\n"
                f"Real-time data unavailable right now. Please try again later."
            )
        )
        # Still offer traffic button so user learns about the option
        dispatcher.utter_message(
            text="Would you like to check traffic for this route?",
            buttons=[
                {"title": "Yes, show traffic", "payload": "/get_traffic_info"},
                {"title": "No, thanks", "payload": "/goodbye"},
            ],
        )
        return [
            SlotSet("conversation_context",
                    None),
            SlotSet("start_location", start_location),
            SlotSet("end_location", end_location),
        ]

    def _build_maps_link(self, origin: str, destination: str) -> str:
        """Create a Google Maps link for directions."""
        origin_enc = quote_plus((origin or "My Location").strip())
        destination_enc = quote_plus((destination or "").strip())
        if destination_enc:
            return (
                f"https://www.google.com/maps/dir/?api=1&origin={origin_enc}"
                f"&destination={destination_enc}&travelmode=driving"
            )
        return f"https://www.google.com/maps/search/?api=1&query={origin_enc}"


class ActionTrafficInfo(Action):
    def name(self) -> Text:
        return "action_traffic_info"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        """Provide live traffic conditions for the current or selected route."""
        start_location = tracker.get_slot("start_location")
        end_location = tracker.get_slot("end_location")
        selected_station = tracker.get_slot("selected_station")

        if selected_station and not end_location:
            end_location = selected_station

        if not start_location or not end_location:
            dispatcher.utter_message(
                text=(
                    "🚦 To show traffic, I need a route.\n\n"
                    "Provide it like: 'from Richmond to Malvern Central'"
                )
            )
            return []

        if REAL_TIME_INTEGRATION_AVAILABLE and real_time_manager:
            try:
                traffic = real_time_manager.get_traffic_conditions(
                    start_location, end_location
                )
                if traffic:
                    status = traffic.get("traffic_status", "Unknown")
                    congestion = traffic.get("congestion_level")
                    delay_min = traffic.get("estimated_delay_minutes")
                    current_speed = traffic.get("current_speed_kmh")
                    free_flow_speed = traffic.get("free_flow_speed_kmh")

                    details: List[str] = []
                    details.append(
                        f"🚦 Traffic: {start_location} → {end_location}"
                    )
                    details.append(f"• Status: {status}")
                    if congestion is not None:
                        details.append(f"• Congestion level: {congestion}")
                    if isinstance(delay_min, (int, float)) and delay_min >= 0:
                        details.append(
                            f"• Estimated delay: {int(delay_min)} min")
                    if isinstance(current_speed, (int, float)) and isinstance(free_flow_speed, (int, float)):
                        details.append(
                            f"• Speed: {int(current_speed)} km/h (free-flow {int(free_flow_speed)} km/h)"
                        )

                    dispatcher.utter_message(text="\n".join(details))
                    dispatcher.utter_message(text=Messages.GOODBYE)
                    return [SlotSet("conversation_context", ConversationContexts.ENDED)]
            except Exception as e:
                print(f"Error in ActionTrafficInfo: {e}")

        dispatcher.utter_message(
            text=(
                f"🚦 Traffic information for {start_location} → {end_location} is unavailable right now."
            )
        )
        dispatcher.utter_message(text=Messages.GOODBYE)
        return [SlotSet("conversation_context", ConversationContexts.ENDED)]


class ActionEnhancedChargerInfo(Action):
    def name(self) -> Text:
        return "action_enhanced_charger_info"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        selected_station = tracker.get_slot("selected_station")

        if not selected_station:
            dispatcher.utter_message(
                text="❌ Please select a charging station first from the list above."
            )
            return []

        try:
            details = data_service.get_station_details(selected_station)
            if not details:
                dispatcher.utter_message(
                    text=f"❌ I couldn't find details for '{selected_station}'. Please pick another station."
                )
                return []

            response = (
                f"🔌 **{details.get('name', selected_station)}**\n\n"
                f"📍 **Address:** {details.get('address', 'Address available')}\n"
                f"⚡ **Power:** {details.get('power', 'Power info available')}\n"
                f"🔌 **Points:** {details.get('points', 'Points info available')}\n"
                f"💰 **Cost:** {details.get('cost', 'Cost info available')}\n"
                f"🕐 **Charging time:** {details.get('charging_time', 'Time estimate available')}\n"
            )

            dispatcher.utter_message(text=response)
            return []
        except Exception as e:
            print(f"Error in ActionEnhancedChargerInfo: {e}")
            dispatcher.utter_message(
                text="❌ Unable to fetch charger details right now.")
            return []


class ActionEnhancedPreferenceFiltering(Action):
    def name(self) -> Text:
        return "action_enhanced_preference_filtering"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        # Misclassification guards: if user is in route or emergency flows and typed something like "show me <station>", forward to the appropriate handler
        conversation_context = tracker.get_slot("conversation_context")
        if conversation_context in [ConversationContexts.ROUTE_PLANNING_RESULTS, ConversationContexts.STATION_DETAILS]:
            try:
                return ActionHandleRouteStationSelection().run(dispatcher, tracker, domain)
            except Exception as e:
                print(
                    f"Error forwarding to route station selection from preference filter action: {e}")
                # Fall through to neutral no-op in case of error
                return []
        if conversation_context in [ConversationContexts.EMERGENCY_RESULTS, ConversationContexts.EMERGENCY_CHARGING]:
            try:
                return ActionHandleEmergencyStationSelection().run(dispatcher, tracker, domain)
            except Exception as e:
                print(
                    f"Error forwarding to emergency station selection from preference filter action: {e}")
                return []

        if conversation_context in [ConversationContexts.PREFERENCE_CHARGING, ConversationContexts.PREFERENCE_RESULTS]:
            dispatcher.utter_message(
                text=(
                    "❌ Preference-based filtering is unavailable right now. "
                    "This feature will be added later."
                )
            )
            return [SlotSet("conversation_context", ConversationContexts.ENDED)]
        return []
