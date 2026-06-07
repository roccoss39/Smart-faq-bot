"""
Google Calendar integration for Salon Kleopatra Bot
"""

import os
import logging
import pytz
from datetime import datetime, timedelta
from collections import defaultdict

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

# ==============================================
# STAŁE
# ==============================================

TIMEZONE = 'Europe/Warsaw'

DAY_NAMES_PL_TO_INT = {
    'poniedziałek': 0, 'wtorek': 1, 'środa': 2,
    'czwartek': 3, 'piątek': 4, 'sobota': 5, 'niedziela': 6
}

DAY_NAMES_INT_TO_PL = {
    0: 'poniedziałek', 1: 'wtorek', 2: 'środa',
    3: 'czwartek', 4: 'piątek', 5: 'sobota', 6: 'niedziela'
}

DAY_NAMES_INT_TO_PL_CAP = {
    0: 'Poniedziałek', 1: 'Wtorek', 2: 'Środa',
    3: 'Czwartek', 4: 'Piątek', 5: 'Sobota', 6: 'Niedziela'
}

DAY_NAMES_ENG_TO_PL_CAP = {
    'Monday': 'Poniedziałek', 'Tuesday': 'Wtorek', 'Wednesday': 'Środa',
    'Thursday': 'Czwartek', 'Friday': 'Piątek', 'Saturday': 'Sobota', 'Sunday': 'Niedziela'
}

# Godziny pracy: (start_h, end_h) lub None = zamknięte
WORKING_HOURS = {
    0: (9, 19),   # Poniedziałek
    1: (9, 19),   # Wtorek
    2: (9, 19),   # Środa
    3: (9, 19),   # Czwartek
    4: (9, 19),   # Piątek
    5: (9, 16),   # Sobota
    6: None       # Niedziela
}

SERVICE_CONFIG = {
    'Strzyżenie':  {'max_clients': 3, 'duration': 30},
    'Farbowanie':  {'max_clients': 1, 'duration': 90},
    'Pasemka':     {'max_clients': 1, 'duration': 120},
    'Stylizacja':  {'max_clients': 2, 'duration': 45},
    'default':     {'max_clients': 2, 'duration': 45},
}

# ==============================================
# SERWIS KALENDARZA
# ==============================================

class CalendarService:
    def __init__(self, credentials_file: str = 'credentials.json', calendar_id: str = None):
        self.credentials_file = credentials_file
        self.calendar_id = calendar_id or os.getenv('GOOGLE_CALENDAR_ID')
        self.service = None

        if not self.calendar_id:
            logger.warning("⚠️ Brak GOOGLE_CALENDAR_ID — dodaj do .env")
        else:
            self._init_service()

    def _init_service(self):
        if not os.path.exists(self.credentials_file):
            logger.error(f"❌ Brak pliku: {self.credentials_file}")
            return
        try:
            creds = Credentials.from_service_account_file(
                self.credentials_file,
                scopes=['https://www.googleapis.com/auth/calendar']
            )
            self.service = build('calendar', 'v3', credentials=creds)
            logger.info("✅ Google Calendar API zainicjalizowane")
        except Exception as e:
            logger.error(f"❌ Błąd inicjalizacji: {e}")

    def is_available(self) -> bool:
        return self.service is not None and bool(self.calendar_id)

    # ------------------------------------------
    # POBIERANIE SLOTÓW
    # ------------------------------------------

    def _parse_event_time(self, time_str: str) -> datetime | None:
        """Parsuj czas wydarzenia do datetime z timezone."""
        try:
            dt = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
            return dt.astimezone(pytz.timezone(TIMEZONE))
        except Exception:
            return None

    def _get_busy_times(self, date: datetime) -> list[tuple]:
        """Pobierz zajęte terminy dla danego dnia."""
        tz = pytz.timezone(TIMEZONE)
        start = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end = date.replace(hour=23, minute=59, second=59, microsecond=0)

        try:
            result = self.service.events().list(
                calendarId=self.calendar_id,
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            busy = []
            for event in result.get('items', []):
                s = self._parse_event_time(event['start'].get('dateTime', ''))
                e = self._parse_event_time(event['end'].get('dateTime', ''))
                if s and e:
                    busy.append((s, e))
            return busy
        except Exception as ex:
            logger.error(f"❌ Błąd pobierania zajętych terminów: {ex}")
            return []

    def _is_busy(self, slot_start: datetime, slot_end: datetime, busy: list) -> bool:
        return any(slot_start < e and slot_end > s for s, e in busy)

    def get_slots_for_date(self, date: datetime, slot_duration: int = 30) -> list[dict]:
        """Wolne sloty dla konkretnej daty."""
        if not self.is_available():
            return []

        weekday = date.weekday()
        hours = WORKING_HOURS.get(weekday)
        if not hours:
            return []

        work_start, work_end = hours
        busy = self._get_busy_times(date)
        tz = pytz.timezone(TIMEZONE)
        now = datetime.now(tz)
        slots = []

        for hour in range(work_start, work_end):
            for minute in (0, 30):
                slot_start = date.replace(hour=hour, minute=minute, second=0, microsecond=0)
                slot_end = slot_start + timedelta(minutes=slot_duration)

                if slot_start <= now:
                    continue
                if slot_end.hour > work_end or (slot_end.hour == work_end and slot_end.minute > 0):
                    continue
                if self._is_busy(slot_start, slot_end, busy):
                    continue

                day_cap = DAY_NAMES_INT_TO_PL_CAP.get(weekday, '')
                slots.append({
                    'datetime': slot_start,
                    'display': f"{day_cap} {slot_start.strftime('%d.%m')} {slot_start.strftime('%H:%M')}",
                    'iso': slot_start.isoformat(),
                    'day_name': DAY_NAMES_INT_TO_PL.get(weekday, ''),
                })

        return slots

    # ------------------------------------------
    # TWORZENIE WIZYTY
    # ------------------------------------------

    def create_appointment(
        self,
        client_name: str,
        client_phone: str,
        service_type: str,
        appointment_time: datetime,
    ) -> str | bool:
        if not self.is_available():
            logger.error("❌ Calendar service niedostępny")
            return False

        cfg = SERVICE_CONFIG.get(service_type, SERVICE_CONFIG['default'])
        duration = cfg['duration']
        end_time = appointment_time + timedelta(minutes=duration)

        event = {
            'summary': f'{service_type} - {client_name}',
            'description': (
                f'👤 Klient: {client_name}\n'
                f'📞 Telefon: {client_phone}\n'
                f'💄 Usługa: {service_type}\n'
                f'🤖 Bot Facebook\n'
                f'📅 {appointment_time.strftime("%d.%m.%Y %H:%M")}'
            ),
            'start': {'dateTime': appointment_time.isoformat(), 'timeZone': TIMEZONE},
            'end':   {'dateTime': end_time.isoformat(),         'timeZone': TIMEZONE},
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'popup', 'minutes': 24 * 60},
                    {'method': 'popup', 'minutes': 60},
                ],
            },
        }

        try:
            created = self.service.events().insert(
                calendarId=self.calendar_id, body=event
            ).execute()
            event_id = created.get('id')
            logger.info(f"✅ Wizyta utworzona: {event_id} — {client_name}")
            return event_id
        except Exception as e:
            logger.error(f"❌ Błąd tworzenia wizyty: {e}")
            return False

    # ------------------------------------------
    # WERYFIKACJA WIZYTY
    # ------------------------------------------

    def verify_appointment(
        self,
        client_name: str,
        client_phone: str,
        appointment_datetime: datetime,
        service_type: str,
    ) -> dict | bool:
        if not self.is_available():
            return False

        search_start = appointment_datetime - timedelta(hours=2)
        search_end   = appointment_datetime + timedelta(hours=2)

        try:
            result = self.service.events().list(
                calendarId=self.calendar_id,
                timeMin=search_start.isoformat(),
                timeMax=search_end.isoformat(),
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            for event in result.get('items', []):
                ev_start = self._parse_event_time(event['start'].get('dateTime', ''))
                if not ev_start:
                    continue

                summary     = event.get('summary', '')
                description = event.get('description', '')

                time_ok    = abs((ev_start - appointment_datetime).total_seconds()) < 300
                name_ok    = client_name.lower() in summary.lower() or client_name.lower() in description.lower()
                phone_ok   = client_phone in description
                service_ok = service_type.lower() in summary.lower()

                if time_ok and (name_ok or phone_ok) and service_ok:
                    logger.info(f"✅ Zweryfikowano: {summary}")
                    return {
                        'exists': True,
                        'event_id': event['id'],
                        'summary': summary,
                        'start_time': event['start'].get('dateTime'),
                    }

            logger.warning(f"❌ Nie znaleziono wizyty: {client_name} {appointment_datetime}")
            return False

        except Exception as e:
            logger.error(f"❌ Błąd weryfikacji: {e}")
            return False

    # ------------------------------------------
    # ANULOWANIE WIZYTY
    # ------------------------------------------

    def cancel_by_details(
        self,
        client_name: str,
        client_phone: str,
        appointment_day: str,
        appointment_time: str,
    ) -> dict | bool:
        """Znajdź i usuń wizytę po danych klienta."""
        if not self.is_available():
            return False

        target_weekday = DAY_NAMES_PL_TO_INT.get(appointment_day.lower())
        if target_weekday is None:
            logger.error(f"❌ Nieznany dzień: {appointment_day}")
            return False

        tz = pytz.timezone(TIMEZONE)
        now = datetime.now(tz)
        try:
            h, m = map(int, appointment_time.split(':'))
        except ValueError:
            logger.error(f"❌ Nieprawidłowy czas: {appointment_time}")
            return False

        # Szukaj w zakresie ±7 dni
        search_start = now - timedelta(days=7)
        search_end   = now + timedelta(days=14)

        try:
            result = self.service.events().list(
                calendarId=self.calendar_id,
                timeMin=search_start.isoformat(),
                timeMax=search_end.isoformat(),
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            for event in result.get('items', []):
                ev_start = self._parse_event_time(event['start'].get('dateTime', ''))
                if not ev_start:
                    continue

                summary     = event.get('summary', '')
                description = event.get('description', '')

                time_ok  = ev_start.hour == h and ev_start.minute == m
                day_ok   = ev_start.weekday() == target_weekday
                name_ok  = client_name.lower() in summary.lower() or client_name.lower() in description.lower()
                phone_ok = client_phone in description

                if time_ok and day_ok and (name_ok or phone_ok):
                    self.service.events().delete(
                        calendarId=self.calendar_id,
                        eventId=event['id']
                    ).execute()
                    logger.info(f"🗑️ Usunięto: {summary}")
                    return {
                        'success': True,
                        'event_id': event['id'],
                        'event_title': summary,
                    }

            logger.warning(f"❌ Nie znaleziono do anulowania: {client_name} {appointment_day} {appointment_time}")
            return False

        except Exception as e:
            logger.error(f"❌ Błąd anulowania: {e}")
            return False


# ==============================================
# SINGLETON
# ==============================================

_calendar_service: CalendarService | None = None


def get_calendar_service() -> CalendarService:
    global _calendar_service
    if _calendar_service is None:
        _calendar_service = CalendarService()
    return _calendar_service


# ==============================================
# PUBLICZNE FUNKCJE (używane przez bot_logic_ai)
# ==============================================

def create_appointment(
    client_name: str,
    client_phone: str,
    service_type: str,
    appointment_time: datetime,
) -> str | bool:
    return get_calendar_service().create_appointment(
        client_name, client_phone, service_type, appointment_time
    )


def cancel_appointment(
    client_name: str,
    client_phone: str,
    appointment_day: str,
    appointment_time: str,
) -> dict | bool:
    return get_calendar_service().cancel_by_details(
        client_name, client_phone, appointment_day, appointment_time
    )


def verify_appointment_exists(
    client_name: str,
    client_phone: str,
    appointment_datetime: datetime,
    service_type: str,
) -> dict | bool:
    return get_calendar_service().verify_appointment(
        client_name, client_phone, appointment_datetime, service_type
    )


def get_available_slots_for_day(day_name: str, slot_duration: int = 30) -> list[dict]:
    """Wolne sloty dla konkretnego dnia tygodnia (po polsku)."""
    svc = get_calendar_service()
    if not svc.is_available():
        return []

    target_weekday = DAY_NAMES_PL_TO_INT.get(day_name.lower())
    if target_weekday is None:
        logger.error(f"❌ Nieprawidłowy dzień: {day_name}")
        return []

    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    current = now.weekday()
    days_ahead = (target_weekday - current) % 7
    if days_ahead == 0 and now.hour >= 18:
        days_ahead = 7

    target_date = now + timedelta(days=days_ahead)
    return svc.get_slots_for_date(target_date, slot_duration)


def format_available_slots(requested_day: str) -> str:
    """Formatuj wolne sloty jako tekst dla użytkownika."""
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)

    # Wyznacz datę docelową
    requested_lower = requested_day.lower()
    if requested_lower in ('dzisiaj', 'dziś'):
        target_date = now
    elif requested_lower == 'jutro':
        target_date = now + timedelta(days=1)
    elif requested_lower == 'pojutrze':
        target_date = now + timedelta(days=2)
    else:
        weekday = DAY_NAMES_PL_TO_INT.get(requested_lower)
        if weekday is None:
            return f"Nie rozumiem nazwy dnia '{requested_day}'. Podaj np. 'jutro', 'piątek'. 😊"
        days_ahead = (weekday - now.weekday()) % 7 or 7
        target_date = now + timedelta(days=days_ahead)

    # Pobierz sloty
    svc = get_calendar_service()
    slots = svc.get_slots_for_date(target_date, slot_duration=30)

    day_pl  = DAY_NAMES_INT_TO_PL.get(target_date.weekday(), '')
    date_str = target_date.strftime('%d.%m.%Y')

    if not slots:
        return f"😔 Brak wolnych terminów na {requested_day} ({day_pl}, {date_str})."

    lines = [f"Wolne terminy na {requested_day} ({day_pl}, {date_str}):"]
    for slot in slots:
        # Wyświetl tylko godzinę — dzień i datę mamy w nagłówku
        lines.append(f"• {slot['datetime'].strftime('%H:%M')}")
    lines.append("\nKtóry termin Ci odpowiada? 😊")

    return '\n'.join(lines)