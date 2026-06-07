"""
Bot Logic AI - Logika chatbota salonu fryzjerskiego Kleopatra
"""

import logging
import re
import time
import threading
from datetime import datetime, timedelta
import pytz
from openai import OpenAI
import os
from calendar_service import format_available_slots, create_appointment, cancel_appointment, verify_appointment_exists
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ==============================================
# KONFIGURACJA AI
# ==============================================

api_key = os.getenv('OPENAI_API_KEY')
if not api_key:
    logger.error("BŁĄD: Brak zmiennej środowiskowej OPENAI_API_KEY")
    raise Exception("Brak OpenAI API key")

client = OpenAI(
    base_url="https://models.inference.ai.azure.com",
    api_key=api_key,
    timeout=30.0
)

# ==============================================
# HISTORIA UŻYTKOWNIKÓW Z TTL
# ==============================================

user_conversations = {}       # {user_id: [messages]}
user_last_activity = {}       # {user_id: timestamp}
SESSION_TTL_SECONDS = 60 * 60 * 24  # 24 godziny

def _cleanup_expired_sessions():
    """Usuń wygasłe sesje (wywołuj w tle)"""
    now = time.time()
    expired = [
        uid for uid, last in user_last_activity.items()
        if now - last > SESSION_TTL_SECONDS
    ]
    for uid in expired:
        user_conversations.pop(uid, None)
        user_last_activity.pop(uid, None)
    if expired:
        logger.info(f"🧹 Usunięto {len(expired)} wygasłych sesji")

def get_user_history(user_id):
    """Pobierz historię rozmowy użytkownika"""
    _cleanup_expired_sessions()
    if user_id not in user_conversations:
        user_conversations[user_id] = []
    user_last_activity[user_id] = time.time()
    return user_conversations[user_id]

def add_to_history(user_id, role, message):
    """Dodaj wiadomość do historii"""
    history = get_user_history(user_id)
    history.append({"role": role, "content": message})
    logger.info(f"📝 Dodano do historii {role}: '{message[:50]}...' (historia: {len(history)} wiadomości)")

    # Ogranicz historię do ostatnich 20 wiadomości
    if len(history) > 20:
        user_conversations[user_id] = history[-20:]
        logger.info("📚 Skrócono historię do 20 wiadomości")

# ==============================================
# FUNKCJA DATY
# ==============================================

def get_current_date_info():
    """Zwraca aktualną datę i czas dla AI"""
    tz = pytz.timezone('Europe/Warsaw')
    now = datetime.now(tz)

    day_names = {
        'Monday': 'poniedziałek', 'Tuesday': 'wtorek', 'Wednesday': 'środa',
        'Thursday': 'czwartek', 'Friday': 'piątek', 'Saturday': 'sobota', 'Sunday': 'niedziela'
    }
    month_names = {
        'January': 'styczeń', 'February': 'luty', 'March': 'marzec',
        'April': 'kwiecień', 'May': 'maj', 'June': 'czerwiec',
        'July': 'lipiec', 'August': 'sierpień', 'September': 'wrzesień',
        'October': 'październik', 'November': 'listopad', 'December': 'grudzień'
    }

    today_pl = day_names.get(now.strftime('%A'), now.strftime('%A').lower())
    tomorrow = now + timedelta(days=1)
    tomorrow_pl = day_names.get(tomorrow.strftime('%A'), tomorrow.strftime('%A').lower())
    pojutrze_pl = day_names.get((now + timedelta(days=2)).strftime('%A'), 'pojutrze')
    month_pl = month_names.get(now.strftime('%B'), now.strftime('%B'))

    return (
        f"📅 AKTUALNA DATA I CZAS:\n"
        f"- Dzisiaj: {today_pl}, {now.day} {month_pl} {now.year}\n"
        f"- Jutro: {tomorrow_pl}\n"
        f"- Pojutrze: {pojutrze_pl}\n"
        f"- Godzina: {now.strftime('%H:%M')}\n\n"
        f"MAPOWANIE WZGLĘDNYCH DAT:\n"
        f"- 'jutro' = {tomorrow_pl}\n"
        f"- 'dzisiaj' = {today_pl}\n"
        f"- 'pojutrze' = {pojutrze_pl}"
    )

# ==============================================
# CZYSZCZENIE ODPOWIEDZI AI
# ==============================================

def clean_thinking_response(response_text):
    """
    Usuwa bloki <think> (DeepSeek) i inne artefakty.
    GPT-4o ich nie produkuje, ale zostawiamy jako zabezpieczenie.
    """
    if not response_text:
        return ""

    cleaned = response_text
    cleaned = re.sub(r'<think>.*?</think>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'<thinking>.*?</thinking>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'<[^>]+>.*?$', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'<[^>]*>', '', cleaned)

    # Usuń separatory
    if '---' in cleaned:
        cleaned = cleaned.split('---')[0].strip()

    # Limit długości (Facebook: 2000, my używamy 500 dla czytelności)
    if len(cleaned) > 500:
        logger.warning(f"⚠️ Skracam odpowiedź z {len(cleaned)} do 500 znaków")
        cleaned = cleaned[:500] + "..."

    cleaned = '\n'.join(line.strip() for line in cleaned.split('\n') if line.strip())
    cleaned = cleaned.strip()

    if not cleaned or len(cleaned) < 5:
        return "Cześć! Jak mogę ci pomóc? 😊"

    return cleaned

# ==============================================
# SYSTEM PROMPT
# ==============================================

SYSTEM_PROMPT_TEMPLATE = """Jesteś asystentem salonu fryzjerskiego "Kleopatra".

{date_info}

🚨 KRYTYCZNE ZASADY:
- Odpowiadaj ZAWSZE pełnymi zdaniami po polsku!
- Bądź naturalny, pomocny i przyjazny!
- Odpowiadaj TYLKO na pytania dotyczące salonu (usługi, ceny, rezerwacje, godziny, lokalizacja).
- Jeśli pytanie jest niezwiązane z salonem — grzecznie odmów.
- NIE wymyślaj terminów — używaj CHECK_AVAILABILITY.
- NIE używaj placeholderów jak "[imię]", "[telefon]".

📍 INFORMACJE O SALONIE:
- Lokalizacja: Aleje Jerozolimskie, Warszawa
- Poniedziałek–Piątek: 9:00–19:00 (ostatnia rezerwacja 18:30)
- Sobota: 9:00–16:00 (ostatnia rezerwacja 15:30)
- Niedziela: zamknięte
- Usługi: Strzyżenie (80 zł), Farbowanie (150 zł), Stylizacja (120 zł)

🗓️ SPRAWDZANIE WOLNYCH TERMINÓW:
Gdy klient pyta o wolne terminy, odpowiedz DOKŁADNIE w tym formacie (2 linie, nic więcej):

Sprawdzam dostępne terminy na [dzień]... 😊
CHECK_AVAILABILITY:[dzień]

Przykłady:
- "jutro wolne?" → "Sprawdzam dostępne terminy na jutro... 😊\\nCHECK_AVAILABILITY:jutro"
- "piątek?" → "Sprawdzam wolne terminy na piątek!\\nCHECK_AVAILABILITY:piątek"

NIGDY nie wymyślaj godzin typu "9:00, 10:00, 11:00"!

📋 PROCES REZERWACJI (5 kroków):

KROK 1: Zbierz wszystkie dane:
  ✅ Dzień i godzina
  ✅ Usługa
  ✅ Imię i nazwisko
  ✅ Telefon (dokładnie 9 cyfr, bez +48)

KROK 2: Podsumowanie (TYLKO gdy masz WSZYSTKIE dane):
📋 PODSUMOWANIE REZERWACJI:
• Imię i nazwisko: [prawdziwe imię]
• Data i godzina: [prawdziwy dzień i godzina]
• Usługa: [prawdziwa usługa]
• Telefon: [prawdziwy telefon]

Czy wszystkie dane są poprawne? Napisz 'TAK' aby potwierdzić.

KROK 3: Po otrzymaniu TAK/POTWIERDZAM/OK:
✅ REZERWACJA POTWIERDZONA: [imię nazwisko], [dzień godzina], [usługa], tel: [telefon]

Przykład: ✅ REZERWACJA POTWIERDZONA: Anna Kowalska, poniedziałek 09:00, Strzyżenie, tel: 123456789

🚫 ABSOLUTNIE ZAKAZANE:
- Pisanie podsumowania z "(nie podano)" — zamiast tego poproś o brakujące dane
- Używanie przykładowych danych (Jan Kowalski, 123456789)
- Potwierdzanie bez zgody użytkownika
- Godziny inne niż pełne lub w pół (np. 9:15, 14:45 — niedozwolone)

⏰ DOZWOLONE GODZINY: 9:00, 9:30, 10:00, 10:30 ... 18:00, 18:30
Jeśli klient poda niedozwoloną godzinę, zaproponuj najbliższą dozwoloną.

🗑️ PROCES ANULOWANIA:
Zbierz: imię, nazwisko, telefon, dzień i godzinę.
Podsumowanie → pytanie o TAK → format:
❌ ANULACJA POTWIERDZONA: [imię nazwisko], [dzień godzina], tel: [telefon]
"""

# ==============================================
# PARSOWANIE I INTEGRACJA Z KALENDARZEM
# ==============================================

DAY_TO_INT = {
    'poniedziałek': 0, 'wtorek': 1, 'środa': 2,
    'czwartek': 3, 'piątek': 4, 'sobota': 5
}

DAY_TO_CAPITALIZED = {
    'poniedziałek': 'Poniedziałek', 'wtorek': 'Wtorek', 'środa': 'Środa',
    'czwartek': 'Czwartek', 'piątek': 'Piątek', 'sobota': 'Sobota'
}


def _resolve_date(day_pl: str, time_str: str) -> datetime | None:
    """Zamień nazwę dnia (pl) + czas na konkretny datetime."""
    tz = pytz.timezone('Europe/Warsaw')
    now = datetime.now(tz)

    if day_pl in ('dzisiaj', 'dziś'):
        return tz.localize(datetime.combine(now.date(), datetime.strptime(time_str, '%H:%M').time()))

    if day_pl == 'jutro':
        d = (now + timedelta(days=1)).date()
        return tz.localize(datetime.combine(d, datetime.strptime(time_str, '%H:%M').time()))

    if day_pl in DAY_TO_INT:
        target = DAY_TO_INT[day_pl]
        current = now.weekday()
        if target > current:
            days_ahead = target - current
        elif target == current:
            candidate = tz.localize(datetime.combine(now.date(), datetime.strptime(time_str, '%H:%M').time()))
            days_ahead = 0 if candidate > now else 7
        else:
            days_ahead = 7 - (current - target)
        d = (now + timedelta(days=days_ahead)).date()
        return tz.localize(datetime.combine(d, datetime.strptime(time_str, '%H:%M').time()))

    return None


def _handle_booking(cleaned_response: str) -> str:
    """Dodaj wizytę do Google Calendar i zaktualizuj odpowiedź."""
    pattern = r"✅ REZERWACJA POTWIERDZONA:\s*\n?\s*([^,]+),\s*([^,]+),\s*([^,]+),\s*tel:\s*(\d+)"
    match = re.search(pattern, cleaned_response)
    if not match:
        logger.error("❌ Nie udało się sparsować danych rezerwacji")
        return cleaned_response

    name = match.group(1).strip()
    datetime_str = match.group(2).strip().lower()
    service = match.group(3).strip()
    phone = match.group(4).strip()

    parts = datetime_str.split()
    if len(parts) < 2:
        logger.error(f"❌ Nieprawidłowy format daty: {datetime_str}")
        return cleaned_response

    day_pl, time_str = parts[0], parts[1]
    appointment_dt = _resolve_date(day_pl, time_str)

    if not appointment_dt:
        logger.error(f"❌ Nie można określić daty dla: {day_pl}")
        return cleaned_response

    logger.info(f"📅 Tworzę rezerwację: {name}, {appointment_dt}, {service}, {phone}")
    calendar_result = create_appointment(
        client_name=name,
        client_phone=phone,
        service_type=service,
        appointment_time=appointment_dt
    )

    if not calendar_result:
        logger.error("❌ create_appointment zwróciło False")
        return cleaned_response.replace(
            "✅ REZERWACJA POTWIERDZONA:",
            "❌ BŁĄD REZERWACJI:"
        ) + "\n\n⚠️ Problem z zapisem wizyty.\n📞 Zadzwoń do salonu: 123-456-789"

    # Weryfikacja (bez blokowania — w tle)
    time.sleep(2)
    verification = verify_appointment_exists(
        client_name=name,
        client_phone=phone,
        appointment_datetime=appointment_dt,
        service_type=service
    )

    if verification:
        logger.info(f"✅ Spotkanie zweryfikowane: {verification['event_id']}")
        date_display = appointment_dt.strftime('%A, %d %B %Y o %H:%M')
        result = cleaned_response.replace("📅 Rezerwuję wizytę w kalendarzu...", "").strip()
        result += f"\n\n✅ Wizyta zapisana w kalendarzu!"
        result += f"\n📅 {date_display}"
        result += f"\n🆔 ID: {verification['event_id'][:8]}..."
        result += f"\n\n💇 Czekamy na Ciebie!"
        return result
    else:
        logger.error("❌ Weryfikacja nieudana")
        return cleaned_response + "\n\n⚠️ Problem z zapisem wizyty.\n📞 Zadzwoń do salonu: 123-456-789"


def _handle_cancellation(cleaned_response: str) -> str:
    """Anuluj wizytę w Google Calendar."""
    pattern = r"❌ ANULACJA POTWIERDZONA:\s*([^,]+),\s*([^,]+),\s*tel:\s*(\d+)"
    match = re.search(pattern, cleaned_response)
    if not match:
        logger.error("❌ Nie udało się sparsować danych anulacji")
        return cleaned_response

    name = match.group(1).strip()
    datetime_str = match.group(2).strip().lower()
    phone = match.group(3).strip()

    parts = datetime_str.split()
    if len(parts) < 2:
        logger.error(f"❌ Nieprawidłowy format daty anulacji: {datetime_str}")
        return cleaned_response

    day_pl, time_str = parts[0], parts[1]
    day_cap = DAY_TO_CAPITALIZED.get(day_pl)

    if not day_cap:
        logger.error(f"❌ Nieznany dzień: {day_pl}")
        return cleaned_response

    cancel_result = cancel_appointment(
        client_name=name,
        client_phone=phone,
        appointment_day=day_cap,
        appointment_time=time_str
    )

    if cancel_result:
        logger.info(f"🗑️ Anulowano: {cancel_result}")
        return cleaned_response + f"\n\n🗑️ Wizyta anulowana!\n📅 {day_cap} o {time_str}"
    else:
        logger.error("❌ Nie znaleziono wizyty do anulowania")
        return cleaned_response + "\n\n⚠️ Nie znaleziono wizyty w kalendarzu."

# ==============================================
# GŁÓWNA FUNKCJA
# ==============================================

def process_user_message_smart(user_message: str, user_id: str) -> str:
    """Przetwórz wiadomość użytkownika i zwróć odpowiedź bota."""

    if not user_message or not user_message.strip():
        return "Cześć! Jak mogę ci pomóc? 😊"

    history = get_user_history(user_id)
    add_to_history(user_id, "user", user_message)

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(date_info=get_current_date_info())

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": system_prompt}] + history,
            max_tokens=700,
            temperature=0.2
        )

        raw_response = response.choices[0].message.content
        logger.info(f"🟡 RAW AI: {raw_response[:300]}")

        cleaned = clean_thinking_response(raw_response)

        # Obsługa CHECK_AVAILABILITY
        if "CHECK_AVAILABILITY:" in raw_response:
            day_match = re.search(r'CHECK_AVAILABILITY:(\w+)', raw_response)
            if day_match:
                day = day_match.group(1).strip()
                logger.info(f"📅 Sprawdzam dostępność na: {day}")
                availability = format_available_slots(day)
                natural = re.sub(r'CHECK_AVAILABILITY:\w+', '', cleaned).strip()
                cleaned = f"{natural}\n\n{availability}" if natural and len(natural) > 10 else availability

        # Walidacja podsumowania — blokuj jeśli są puste pola
        if "📋 PODSUMOWANIE REZERWACJI:" in cleaned:
            if "(nie podano)" in cleaned:
                logger.error("❌ BLOKADA: Podsumowanie z brakującymi danymi")
                return "Potrzebuję jeszcze kilku informacji:\n• Imię i nazwisko\n• Dzień i godzina\n• Usługa\n• Telefon (9 cyfr)\n\nPodaj brakujące dane 😊"
            if "Jan Kowalski" in cleaned or ("123456789" in cleaned and "tel:" not in cleaned):
                logger.error("❌ BLOKADA: Przykładowe dane w podsumowaniu")
                return "Potrzebuję Twoich danych:\n📞 Podaj imię, nazwisko i telefon"

        # Walidacja telefonu przed rezerwacją
        if "✅ REZERWACJA POTWIERDZONA:" in cleaned:
            phone_match = re.search(r'tel:\s*(\d+)', cleaned)
            if phone_match:
                phone = phone_match.group(1)
                if len(phone) != 9:
                    logger.error(f"❌ Nieprawidłowy telefon: {phone}")
                    return f"❌ Numer telefonu '{phone}' jest nieprawidłowy.\n📞 Podaj 9-cyfrowy numer (bez +48)."
            cleaned = _handle_booking(cleaned)

        elif "❌ ANULACJA POTWIERDZONA:" in cleaned:
            cleaned = _handle_cancellation(cleaned)

        add_to_history(user_id, "assistant", cleaned)
        logger.info(f"🧠 Odpowiedź: '{cleaned[:80]}...'")
        return cleaned

    except Exception as e:
        logger.error(f"❌ Błąd AI: {e}")
        return "Przepraszam, wystąpił błąd. Spróbuj ponownie."

# ==============================================
# STATYSTYKI
# ==============================================

def get_user_stats():
    return {
        "total_conversations": len(user_conversations),
        "active_conversations": len([h for h in user_conversations.values() if h])
    }

logger.info("🤖 Bot Logic AI zainicjalizowany")
logger.info(f"🔑 OpenAI API key: {'✅' if api_key else '❌'}")