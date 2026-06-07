"""
Bot Logic - Logika konwersacji i zarządzania sesjami użytkowników
Obsługuje rezerwacje, AI responses i stan konwersacji
"""

import logging
import re
from datetime import datetime, timedelta
import pytz
from together import Together
import os
from calendar_service import get_available_slots, create_appointment, cancel_appointment

logger = logging.getLogger(__name__)

# ==============================================
# KONFIGURACJA AI
# ==============================================

# Sprawdź czy klucz API jest dostępny
api_key = os.getenv('OPENAI_API_KEY')
if not api_key:
    logger.error("BŁĄD: Brak zmiennej środowiskowej OPENAI_API_KEY")
    raise Exception("Brak Together API key")

client = Together(api_key=api_key)

# PROMPT SYSTEMOWY
SYSTEM_PROMPT = """
WAŻNE: Odpowiadaj BEZPOŚREDNIO bez pokazywania swojego procesu myślowego!
NIE używaj tagów <thinking> ani nie pokazuj swoich rozumowań!
Odpowiadaj tylko końcową odpowiedzią!

Jesteś AI asystentem salonu fryzjerskiego "Kleopatra" w Warszawie przy ul. Pięknej 15.

ZASADY ODPOWIEDZI:
- NIE pokazuj swojego procesu myślowego
- NIE pisz "Okay, the user is asking..." 
- NIE używaj tagów <thinking></thinking>
- Odpowiadaj TYLKO po polsku
- Bądź bezpośredni i pomocny
- Używaj max 2-3 emoji na wiadomość
- Odpowiedzi max 300 znaków (dla Facebook Messenger)

INFORMACJE O SALONIE:
- Nazwa: Salon Fryzjerski "Kleopatra"
- Adres: ul. Piękna 15, 00-001 Warszawa
- Telefon: 123-456-789
- Email: kontakt@salon-kleopatra.pl
- Website: www.salon-kleopatra.pl

GODZINY OTWARCIA:
- Poniedziałek-Piątek: 9:00-19:00
- Sobota: 9:00-16:00  
- Niedziela: zamknięte

CENNIK USŁUG:
STRZYŻENIE:
- Damskie: 80-120 zł
- Męskie: 50-70 zł
- Dziecięce: 40 zł

KOLORYZACJA:
- Całościowe farbowanie: 120-180 zł
- Retusz odrostów: 80 zł
- Pasemka/refleksy: 150-250 zł

Odpowiadaj TYLKO po polsku. Bądź pomocny i empatyczny.
WAŻNE ZASADY ODPOWIEDZI:
1. Bądź przyjazny, profesjonalny i pomocny
2. Używaj emoji do ożywienia rozmowy
3. 🔥 ZAWSZE na końcu zachęć do umówienia wizyty jednym z tych sposobów:
   - "Chcesz się umówić? Napisz: 'chcę się umówić' 📅"
   - "Mogę pomóc ci zarezerwować termin! Wystarczy napisać 'umów mnie' ✨"
   - "Gotowy na wizytę? Napisz 'wolne terminy' aby sprawdzić dostępność! 💫"
   - "Zainteresowany? Napisz 'chcę się umówić' a pokażę dostępne terminy! 🎯"
4. Jeśli pytanie dotyczy cen/usług - ZAWSZE dodaj zachętę do rezerwacji
5. Odpowiadaj krótko (max 100 słów) + zachęta do umówienia

Odpowiedz naturalnie i profesjonalnie na pytanie klienta.
"""

# ==============================================
# SYSTEM SESJI UŻYTKOWNIKÓW
# ==============================================

user_sessions = {}

class UserSession:
    def __init__(self, user_id):
        self.user_id = user_id
        self.state = "start"  # start, booking, waiting_for_details, ready_to_book
        self.appointment_data = {}
        self.last_activity = datetime.now()
        
    def set_booking_details(self, day, time, service):
        """Ustaw szczegóły rezerwacji"""
        self.appointment_data = {
            'day': day,
            'time': time, 
            'service': service
        }
        self.state = "waiting_for_details"
        self.last_activity = datetime.now()
        
    def set_client_details(self, name, phone):
        """Ustaw dane klienta"""
        self.appointment_data['name'] = name
        self.appointment_data['phone'] = phone
        self.state = "ready_to_book"
        self.last_activity = datetime.now()
        
    def reset(self):
        """Resetuj sesję"""
        self.state = "start"
        self.appointment_data = {}
        self.last_activity = datetime.now()
        
    def is_expired(self, minutes=30):
        """Sprawdź czy sesja wygasła"""
        return (datetime.now() - self.last_activity).total_seconds() > (minutes * 60)

def get_user_session(user_id):
    """Pobierz lub utwórz sesję użytkownika"""
    if user_id not in user_sessions:
        user_sessions[user_id] = UserSession(user_id)
    
    session = user_sessions[user_id]
    
    # Sprawdź czy sesja nie wygasła
    if session.is_expired():
        logger.info(f"🕐 Sesja {user_id} wygasła - resetowanie")
        session.reset()
    
    return session

def cleanup_expired_sessions():
    """Usuń wygasłe sesje (wywołuj okresowo)"""
    expired_users = [
        user_id for user_id, session in user_sessions.items() 
        if session.is_expired()
    ]
    
    for user_id in expired_users:
        del user_sessions[user_id]
        logger.info(f"🗑️ Usunięto wygasłą sesję: {user_id}")

# ==============================================
# PARSOWANIE WIADOMOŚCI
# ==============================================

def parse_booking_message(message):
    """Wyciągnij szczegóły rezerwacji z wiadomości"""
    try:
        message_lower = message.lower().strip()
        logger.info(f"🔍 PARSING: '{message}' → '{message_lower}'")  # DEBUG
        
        # Znajdź dzień
        days_map = {
            'poniedziałek': 'Poniedziałek', 'poniedzialek': 'Poniedziałek', 'pon': 'Poniedziałek',
            'wtorek': 'Wtorek', 'wt': 'Wtorek',
            'środa': 'Środa', 'sroda': 'Środa', 'śr': 'Środa', 'sr': 'Środa',
            'czwartek': 'Czwartek', 'czw': 'Czwartek',
            'piątek': 'Piątek', 'piatek': 'Piątek', 'pt': 'Piątek',
            'sobota': 'Sobota', 'sobote': 'Sobota', 'sb': 'Sobota'
        }
        
        day = None
        for day_key, day_value in days_map.items():
            if day_key in message_lower:
                day = day_value
                break
                
        # Znajdź godzinę (różne formaty)
        time_patterns = [
            r'(\d{1,2}):(\d{2})',                    # 10:00
            r'(\d{1,2})\.(\d{2})',                   # 10.00
            r'(\d{1,2}):(\d{0,2})',                  # 10: lub 10:0
            r'(\d{1,2})\s*h',                        # 10h
            r'godz\.?\s*(\d{1,2})',                  # godz 10
            r'o\s+(\d{1,2}):?(\d{0,2})',             # "o 10:00" lub "o 10"
            r'na\s+(\d{1,2}):?(\d{0,2})',            # "na 10:00"  ← DODAJ
            r'(\d{1,2}):?(\d{0,2})\s+na',            # "10:00 na"  ← DODAJ
        ]
        
        time = None
        for pattern in time_patterns:
            time_match = re.search(pattern, message_lower)
            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2)) if len(time_match.groups()) > 1 and time_match.group(2) else 0
                
                # Walidacja godziny
                if 9 <= hour <= 19:  # Godziny pracy
                    time = f"{hour:02d}:{minute:02d}"
                    break
                    
        # Znajdź usługę
        services_map = {
            'strzyżenie męskie': 'Strzyżenie męskie',
            'strzyżenie damskie': 'Strzyżenie damskie',
            'strzyżenie': 'Strzyżenie',
            'strzyenie': 'Strzyżenie',  # literówka
            'farbowanie': 'Farbowanie',
            'farba': 'Farbowanie',
            'pasemka': 'Pasemka',
            'refleksy': 'Refleksy',
            'koloryzacja': 'Koloryzacja',
            'ombre': 'Ombre',
            'baleyage': 'Baleyage'
        }
        
        service = 'Strzyżenie'  # domyślnie
        for service_key, service_value in services_map.items():
            if service_key in message_lower:
                service = service_value
                break
                
        if day and time:
            return {
                'day': day,
                'time': time,
                'service': service
            }
            
        return None
        
    except Exception as e:
        logger.error(f"❌ Błąd parsowania rezerwacji: {e}")
        return None

def parse_contact_data(message):
    """Wyciągnij dane kontaktowe z wiadomości"""
    try:
        message = message.strip()
        
        # Różne wzorce dla danych kontaktowych
        patterns = [
            # "Jan Kowalski, 123456789"
            r'([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)\s+([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)[,\s]+(\d{9}|\d{3}[-\s]\d{3}[-\s]\d{3})',
            # "Jan Kowalski 123456789"  
            r'([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)\s+([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)\s+(\d{9}|\d{3}[-\s]\d{3}[-\s]\d{3})',
            # "Jan Kowalski tel: 123456789"
            r'([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)\s+([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)\s*tel\.?\s*:?\s*(\d{9}|\d{3}[-\s]\d{3}[-\s]\d{3})',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, message)
            if match:
                name = f"{match.group(1)} {match.group(2)}"
                phone = re.sub(r'[-\s]', '', match.group(3))
                
                # Walidacja telefonu (9 cyfr)
                if len(phone) == 9 and phone.isdigit():
                    return {
                        'name': name,
                        'phone': phone
                    }
            
        return None
        
    except Exception as e:
        logger.error(f"❌ Błąd parsowania kontaktu: {e}")
        return None

# ==============================================
# TWORZENIE WIZYT
# ==============================================

def create_booking(appointment_data):
    """Utwórz wizytę w kalendarzu Google"""
    try:
        # Konwertuj dane na datetime
        day_map = {
            'Poniedziałek': 0, 'Wtorek': 1, 'Środa': 2, 
            'Czwartek': 3, 'Piątek': 4, 'Sobota': 5
        }
        
        target_day = day_map.get(appointment_data['day'])
        if target_day is None:
            logger.error(f"❌ Nieprawidłowy dzień: {appointment_data['day']}")
            return False
            
        # Znajdź najbliższy dzień
        tz = pytz.timezone('Europe/Warsaw')
        now = datetime.now(tz)
        days_ahead = (target_day - now.weekday()) % 7
        if days_ahead == 0:  # Dzisiaj
            days_ahead = 7  # Następny tydzień
            
        appointment_date = now + timedelta(days=days_ahead)
        
        # Ustaw godzinę
        time_parts = appointment_data['time'].split(':')
        appointment_datetime = appointment_date.replace(
            hour=int(time_parts[0]), 
            minute=int(time_parts[1]), 
            second=0, 
            microsecond=0
        )
        
        # Sprawdź czy termin nie jest w przeszłości
        if appointment_datetime <= now:
            logger.error(f"❌ Termin w przeszłości: {appointment_datetime}")
            return False
        
        logger.info(f"📅 Tworzenie wizyty: {appointment_datetime} dla {appointment_data['name']}")
        
        # Utwórz wizytę w kalendarzu
        result = create_appointment(
            client_name=appointment_data['name'],
            client_phone=appointment_data['phone'],
            service_type=appointment_data['service'],
            appointment_time=appointment_datetime
        )
        
        if result:
            logger.info(f"✅ Wizyta utworzona! ID: {result}")
            return True
        else:
            logger.error("❌ Błąd tworzenia wizyty w kalendarzu")
            return False
        
    except Exception as e:
        logger.error(f"❌ Błąd tworzenia wizyty: {e}")
        return False

# ==============================================
# CZYSZCZENIE ODPOWIEDZI AI
# ==============================================

# ZASTĄP funkcję clean_thinking_response:

def clean_thinking_response(response_text):
    """Usuwa sekcje 'thinking' z odpowiedzi modelu DeepSeek-R1"""
    if not response_text:
        return ""
        
    original = response_text
    cleaned = response_text
    
    # SZCZEGÓLNE TRAKTOWANIE NIEDOMKNIĘTEGO <think>
    # Znajdź wszystko po ostatnim </think> lub <think> 
    
    # Jeśli jest <think> ale nie ma </think>, weź tylko tekst po <think>
    if '<think>' in cleaned.lower() and '</think>' not in cleaned.lower():
        # Znajdź indeks ostatniego <think>
        think_pattern = re.compile(r'<think[^>]*>', re.IGNORECASE)
        matches = list(think_pattern.finditer(cleaned))
        if matches:
            # Weź tekst po ostatnim <think>
            last_match = matches[-1]
            after_think = cleaned[last_match.end():]
            
            # Sprawdź czy po <think> jest sensowna odpowiedź
            lines = after_think.strip().split('\n')
            for line in lines:
                line = line.strip()
                # Szukaj linii z jedną z poprawnych intencji
                if any(intent in line.upper() for intent in ["BOOKING", "SHOW_AVAILABLE", "GENERAL_BOOKING", "CONTACT_INFO", "CANCEL", "GENERAL"]):
                    cleaned = line
                    break
            else:
                # Jeśli nie znaleziono intencji, weź pierwszą sensowną linię
                for line in lines:
                    if len(line.strip()) > 0 and not line.strip().startswith('<'):
                        cleaned = line.strip()
                        break
    
    # Usuń wszystko między zamkniętymi tagami
    cleaned = re.sub(r'<thinking>.*?</thinking>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'<THINK>.*?</THINK>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'<think>.*?</think>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    
    # Usuń ewentualne pozostałe tagi
    cleaned = re.sub(r'<[^>]*>', '', cleaned)
    
    # Usuń ewentualne puste linie
    cleaned = cleaned.strip()
    
    # Debug log
    if original != cleaned and len(original) > 50:
        logger.debug(f"🧹 Oczyszczono: '{original[:50]}...' → '{cleaned}'")
    
    return cleaned

# ==============================================
# GŁÓWNA LOGIKA BOTA
# ==============================================

def process_user_message(user_message, user_id=None):
    """
    GŁÓWNA FUNKCJA - Przetwórz wiadomość użytkownika z AI Intent Router
    """
    try:
        # Pobierz sesję użytkownika
        session = get_user_session(user_id) if user_id else None
        user_message = user_message.strip()
        
        logger.info(f"🤖 Przetwarzam: '{user_message}' | Sesja: {session.state if session else 'brak'}")
        
        # ===========================================
        # AI ANALYSIS INTENCJI
        # ===========================================
        
        intent = analyze_user_intent(user_message, session)  # ← PRZEKAŻ SESJĘ!
        
        # ===========================================
        # OBSŁUGA NA PODSTAWIE INTENCJI + KONTEKSTU SESJI
        # ===========================================
        
        # 1. CONTACT_DATA + waiting_for_details = TWORZENIE WIZYTY
        if intent == "CONTACT_DATA" and session and session.state == "waiting_for_details":
            contact_data = parse_contact_data(user_message)
            if contact_data:
                session.set_client_details(contact_data['name'], contact_data['phone'])
                
                # UTWÓRZ WIZYTĘ W KALENDARZU
                appointment_result = create_booking(session.appointment_data)
                
                if appointment_result:
                    confirmation = f"✅ **WIZYTA POTWIERDZONA!**\n\n📅 **Termin:** {session.appointment_data['day']} {session.appointment_data['time']}\n👤 **Klient:** {contact_data['name']}\n📞 **Telefon:** {contact_data['phone']}\n✂️ **Usługa:** {session.appointment_data['service']}\n\n📍 **Salon Kleopatra**\nul. Piękna 15, Warszawa\n\n🎉 Do zobaczenia w salonie!"
                    session.reset()
                    return confirmation
                else:
                    session.reset()
                    return f"❌ **Błąd rezerwacji**\n\nTermin mógł zostać zajęty przez kogoś innego.\n📞 Zadzwoń: **123-456-789**"
            else:
                return f"📝 **Błędny format!**\n\nNapisz w formacie:\n**\"Imię Nazwisko, numer telefonu\"**\n\nNp: **\"Jan Kowalski, 123456789\"**"
        
        # 2. CONTACT_DATA + cancelling = ANULOWANIE WIZYTY  
        elif intent == "CONTACT_DATA" and session and session.state == "cancelling":
            cancellation_data = parse_cancellation_data(user_message)
            if cancellation_data:
                # ANULUJ WIZYTĘ W KALENDARZU
                        
                cancel_result = cancel_appointment(
                    client_name=cancellation_data['name'],
                    client_phone=cancellation_data['phone'], 
                    appointment_day=cancellation_data['day'],
                    appointment_time=cancellation_data['time']
                )
                
                session.reset()
                
                if cancel_result:
                    return f"✅ **Wizyta anulowana!**\n\n👤 Klient: {cancellation_data['name']}\n📞 Telefon: {cancellation_data['phone']}\n📅 Termin: {cancellation_data['day']} {cancellation_data['time']}\n\n🗑️ **Usunięto z kalendarza**\n\n🤖 Czy mogę w czymś jeszcze pomóc?"
                else:
                    return f"❌ **Nie znaleziono wizyty**\n\n👤 Klient: {cancellation_data['name']}\n📞 Telefon: {cancellation_data['phone']}\n📅 Termin: {cancellation_data['day']} {cancellation_data['time']}\n\n🔍 **Sprawdź dane lub zadzwoń:** 123-456-789"
            else:
                return f"📝 **Błędny format!**\n\nPodaj w formacie:\n**\"Imię Nazwisko, telefon, dzień godzina\"**\n\nNp: *\"Jan Kowalski, 123456789, środa 11:00\"*"
        
        # 3. BOOKING - konkretna rezerwacja "umawiam się na wtorek 10:00"
        elif intent == "BOOKING":
            parsed = parse_booking_message(user_message)
            logger.info(f"🔍 PARSED BOOKING: {parsed}")
            
            if parsed and session:
                session.set_booking_details(parsed['day'], parsed['time'], parsed['service'])
                return f"📝 **Prawie gotowe!**\n\n📅 **Termin:** {parsed['day']} {parsed['time']}\n✂️ **Usługa:** {parsed['service']}\n\n🔔 Potrzebuję jeszcze:\n• 👤 **Imię i nazwisko**\n• 📞 **Numer telefonu**\n\nNapisz: **\"Jan Kowalski, 123456789\"**"
            else:
                return f"📝 **Nie mogę rozpoznać terminu!**\n\nSprawdź format:\n**\"Umawiam się na [dzień] [godzina] na strzyżenie\"**\n\nNp: *\"Umawiam się na wtorek 10:00 na strzyżenie\"*"
        
        # 3. ASK_AVAILABILITY - pytanie o dostępne terminy
        elif intent == "ASK_AVAILABILITY":
            day_mentioned = extract_day_from_message(user_message)
            
            try:
                if day_mentioned:
                    # 🔧 NOWE - pobierz WSZYSTKIE terminy dla konkretnego dnia
                    from calendar_service import get_available_slots_for_day
                    
                    slots = get_available_slots_for_day(day_mentioned)
                    
                    if slots:
                        day_display = day_mentioned.capitalize()
                        response = f"📅 **Wolne terminy w {day_display}:**\n"
                        
                        # Pogrupuj po godzinach
                        hours = []
                        for slot in slots:
                            time_str = slot['datetime'].strftime('%H:%M')
                            if time_str not in hours:
                                hours.append(time_str)
                        
                        # Wyświetl wszystkie godziny w czytelnym formacie
                        for hour in hours:
                            response += f"• {hour}\n"
                            
                        response += f"\n💬 Aby się umówić napisz:\n*\"Umawiam się na {day_mentioned.lower()} [godzina] na [usługa]\"*"
                        
                        return response
                    else:
                        return f"❌ **Brak wolnych terminów w {day_mentioned.capitalize()}**\n\nSprawdź inne dni lub zadzwoń: 123-456-789"
                
                else:
                    # Pytanie ogólne - użyj obecnej logiki
                    slots = get_available_slots(days_ahead=10)
                    
                    if slots:
                        # Pogrupuj po dniach i pokaż po kilka z każdego
                        from collections import defaultdict
                        slots_by_day = defaultdict(list)
                        
                        for slot in slots:
                            day_name = slot['day_name']
                            slots_by_day[day_name].append(slot)
                        
                        response = "📅 **Dostępne terminy:**\n"
                        for day_name, day_slots in list(slots_by_day.items())[:5]:  # Max 5 dni
                            response += f"• **{day_name}**: "
                            times = [slot['datetime'].strftime('%H:%M') for slot in day_slots[:4]]  # Max 4 czasy z dnia
                            response += ", ".join(times)
                            response += "\n"
                        
                        response += "\n💬 Aby sprawdzić konkretny dzień napisz:\n*\"godziny w środę\"* lub *\"wolne terminy w piątek\"*"
                        
                        return response
                    else:
                        return "❌ **Brak dostępnych terminów**\n\nSpróbuj później lub zadzwoń: 123-456-789"
                        
            except Exception as e:
                logger.error(f"❌ Błąd pobierania terminów: {e}")
                return "😔 Problem z kalendarżem. Zadzwoń: **123-456-789** 📞"
        
        # 4. WANT_APPOINTMENT - ogólne "chcę się umówić"
        elif intent == "WANT_APPOINTMENT":
            try:
                slots = get_available_slots(days_ahead=10)
                if slots:
                    if session:
                        session.state = "booking"
                    
                    slots_text = '\n'.join([
                        f"• {slot['day_name']} {slot['display'].split()[-1]}" 
                        for slot in slots[:8]
                    ])
                    
                    return f"📅 **Dostępne terminy:**\n{slots_text}\n\n💬 Napisz:\n**\"Umawiam się na [dzień] [godzina] na strzyżenie\"** ✂️"
                else:
                    return "😔 Brak wolnych terminów w najbliższych dniach.\n📞 Zadzwoń: **123-456-789**"
            except Exception as e:
                logger.error(f"❌ Błąd pobierania terminów: {e}")
                return "😔 Problem z kalendarżem. Zadzwoń: **123-456-789** 📞"
        
        # 5. CANCEL_VISIT - anulowanie z weryfikacją
        elif intent == "CANCEL_VISIT":
            if session and session.state == "waiting_for_details" and session.appointment_data:
                # User ma aktywną rezerwację - anuluj ją
                appointment_info = session.appointment_data
                session.reset()
                return f"❌ **Anulowano rezerwację**\n\n📅 Termin: {appointment_info['day']} {appointment_info['time']}\n✂️ Usługa: {appointment_info['service']}\n\n🤖 Mogę Ci w czymś jeszcze pomóc?"
            else:
                # Pytaj o szczegóły wizyty do anulowania
                if session:
                    session.state = "cancelling"
                return f"❌ **Anulowanie wizyty**\n\n🔍 Aby anulować wizytę, podaj:\n• 👤 **Imię i nazwisko**\n• 📞 **Numer telefonu**\n• 📅 **Dzień i godzinę wizyty**\n\nNp: *\"Jan Kowalski, 123456789, środa 11:00\"*"
        
        # 6. OTHER_QUESTION - standardowa rozmowa AI + MENU NA POWITANIE
        else:  # OTHER_QUESTION
            # 🔧 NOWE - MENU OPCJI DLA POWITAŃ
            greeting_patterns = [
                'hej', 'cześć', 'dzień dobry', 'witaj', 'hello', 'hi', 'siema',
                'dobry wieczór', 'miłego dnia', 'pozdrawiam', 'dobry', 'witam'
            ]
            
            message_lower = user_message.lower().strip()
            is_greeting = any(greeting in message_lower for greeting in greeting_patterns)
            
            if is_greeting and len(user_message.strip()) <= 20: # Krótkie powitanie
                return get_welcome_menu()
            else:
                return get_ai_response(user_message)
            
    except Exception as e:
        logger.error(f"❌ Błąd przetwarzania wiadomości: {e}")
        return "😔 Wystąpił problem. Spróbuj ponownie lub zadzwoń: **123-456-789** 📞"



def extract_day_from_message(message):
    """Wyciągnij dzień z wiadomości"""
    days_map = {
        'poniedziałek': 'Poniedziałek', 'poniedzialek': 'Poniedziałek', 'pon': 'Poniedziałek',
        'wtorek': 'Wtorek', 'wt': 'Wtorek',
        'środa': 'Środa', 'sroda': 'Środa', 'śr': 'Środa',
        'czwartek': 'Czwartek', 'czw': 'Czwartek',
        'piątek': 'Piątek', 'piatek': 'Piątek', 'pt': 'Piątek',
        'sobota': 'Sobota', 'sobote': 'Sobota', 'sb': 'Sobota'
    }
    
    message_lower = message.lower()
    for day_key, day_value in days_map.items():
        if day_key in message_lower:
            return day_value
    return None

def get_ai_response(user_message):
    """Standardowa odpowiedź AI"""
    message_lower = user_message.lower().strip()
    
        # 🔧 NOWE - SZYBKIE ODPOWIEDZI NA MENU
    quick_responses = {
        'jakie usługi oferujecie': """✂️ **NASZE USŁUGI:**

    **STRZYŻENIE:**
    • Damskie: 80-120 zł
    • Męskie: 50-70 zł  
    • Dziecięce: 40 zł

    **KOLORYZACJA:**
    • Całościowe farbowanie: 120-180 zł
    • Retusz odrostów: 80 zł
    • Pasemka/refleksy: 150-250 zł

    💫 **Chcesz się umówić?** Napisz: *'chcę się umówić'* 📅""",

            'gdzie jesteście': """📍 **LOKALIZACJA:**

    🏢 **Salon Fryzjerski "Kleopatra"**
    📮 ul. Piękna 15, 00-001 Warszawa

    🚇 **Dojazd:**
    • Metro: Centrum (5 min pieszo)
    • Autobus: 15, 18, 35 (przystanek Piękna)

    🅿️ **Parking:** Publiczne miejsca w okolicy

    💫 **Chcesz się umówić?** Napisz: *'chcę się umówić'* 📅""",

            'godziny otwarcia': """🕐 **GODZINY OTWARCIA:**

    📅 **Poniedziałek-Piątek:** 9:00-19:00
    📅 **Sobota:** 9:00-16:00  
    📅 **Niedziela:** Zamknięte

    📞 **Kontakt:** 123-456-789
    📧 **Email:** kontakt@salon-kleopatra.pl

    💫 **Chcesz się umówić?** Napisz: *'chcę się umówić'* 📅""",

            'kontakt': """📞 **KONTAKT:**

    ☎️ **Telefon:** 123-456-789
    📧 **Email:** kontakt@salon-kleopatra.pl
    🌐 **Strona:** www.salon-kleopatra.pl

    📍 **Adres:** ul. Piękna 15, Warszawa

    🕐 **Godziny:** Pon-Pt 9:00-19:00, Sob 9:00-16:00

    💫 **Chcesz się umówić?** Napisz: *'chcę się umówić'* 📅"""
        }
        
        # Sprawdź szybkie odpowiedzi
    for phrase, response in quick_responses.items():
        if phrase in message_lower:
            return response
        

    try:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message}
        ]
        
        response = client.chat.completions.create(
            model="deepseek-ai/DeepSeek-R1-Distill-Llama-70B-free",  # ← POWRÓT DO R1
            messages=messages,
            max_tokens=600,
            temperature=0.3
        )
        
        raw_response = response.choices[0].message.content
        cleaned_response = clean_thinking_response(raw_response)
        
        # Sprawdź czy jest czysta odpowiedź
        if len(cleaned_response.strip()) > 10:
            return cleaned_response.strip()
        else:
            return "😊 Dzień dobry! Jak mogę pomóc? Zapraszamy do salonu Kleopatra na ul. Pięknej 15! 💇‍♀️"
        
    except Exception as e:
        logger.error(f"❌ Błąd AI response: {e}")
        return "😔 Przepraszam, chwilowo mam problemy z odpowiadaniem. Zadzwoń: **123-456-789** 📞"

# ZASTĄP analyze_user_intent (linia ~588):

def analyze_user_intent(user_message, session=None):
    """Analizuj intencję z priorytetem kontekstu sesji"""
    
    # 🎯 KONTEKST SESJI MA NAJWYŻSZY PRIORYTET!
    if session:
        # Jeśli czeka na dane kontaktowe do rezerwacji
        if session.state == "waiting_for_details":
            contact_data = parse_contact_data(user_message)
            if contact_data:
                logger.info(f"🎯 KONTEKST: waiting_for_details → CONTACT_DATA")
                return "CONTACT_DATA"
        
        # Jeśli czeka na dane do anulowania
        elif session.state == "cancelling":
            cancellation_data = parse_cancellation_data(user_message)
            if cancellation_data:
                logger.info(f"🎯 KONTEKST: cancelling → CONTACT_DATA")
                return "CONTACT_DATA"
            else:
                # Jeśli nie może sparsować, nadal traktuj jako próbę podania danych
                logger.info(f"🎯 KONTEKST: cancelling (błędny format) → CONTACT_DATA")
                return "CONTACT_DATA"
        
        # Jeśli jest w stanie booking
        elif session.state == "booking":
            parsed = parse_booking_message(user_message)
            if parsed and parsed.get('day') and parsed.get('time'):
                logger.info(f"🎯 KONTEKST: booking → BOOKING")
                return "BOOKING"
    
    # UŻYJ POPRAWIONEJ FUNKCJI REGEX!
    regex_intent = analyze_intent_regex_only(user_message)
    logger.info(f"🎯 REGEX Intent: '{user_message}' → {regex_intent}")
    
    # ZAWSZE ZWRÓĆ REGEX RESULT (bez AI fallback)
    return regex_intent

# def analyze_intent_with_ai(user_message):
#     """Klasyfikacja AI z REGEX backup"""
    
#     # NAJPIERW SPRÓBUJ REGEX - SZYBKI I NIEZAWODNY
#     regex_intent = analyze_intent_regex_only(user_message)
#     if regex_intent != "OTHER_QUESTION":
#         logger.info(f"🎯 REGEX Intent: '{user_message}' → {regex_intent}")
#         return regex_intent
    
#     # FALLBACK NA AI jeśli REGEX nie rozpoznał
#     intent_prompt = f"""Classify this Polish message into ONE category.

# MESSAGE: "{user_message}"

# CATEGORIES:

# **BOOKING** - Specific booking with BOTH day AND time mentioned
# Examples: "umawiam się na wtorek 10:00", "środa 15:30", "poniedziałek o 11:00"

# **ASK_AVAILABILITY** - Asking about available appointment times
# Examples: "kiedy mają państwo wolne terminy?", "dostępne godziny na wtorek?", "jakie są wolne terminy?"

# **WANT_APPOINTMENT** - General booking request WITHOUT specific time
# Examples: "chce sie ostrzyc", "chcę się umówić", "potrzebuję wizyty"

# **CONTACT_DATA** - Personal contact details
# Examples: "Jan Kowalski, 123456789", "Anna Nowak 987-654-321"

# **CANCEL_VISIT** - Cancel appointment request
# Examples: "anuluj", "rezygnuję", "odwołuję wizytę"

# **OTHER_QUESTION** - Other questions about salon, greetings
# Examples: "cześć", "ile kosztuje strzyżenie?", "gdzie jesteście?"

# ANSWER: Write ONLY the category name."""

#     try:
#         response = client.chat.completions.create(
#             model="deepseek-ai/DeepSeek-R1-Distill-Llama-70B-free",
#             messages=[{"role": "user", "content": intent_prompt}],
#             max_tokens=1000,
#             temperature=0.0
#         )
        
#         raw_intent = response.choices[0].message.content
#         valid_intents = ["BOOKING", "ASK_AVAILABILITY", "WANT_APPOINTMENT", "CONTACT_DATA", "CANCEL_VISIT", "OTHER_QUESTION"]
        
#         # ZNAJDŹ INTENCJĘ
#         for intent in valid_intents:
#             if intent in raw_intent.upper():
#                 logger.info(f"🎯 AI Intent: '{user_message}' → {intent}")
#                 return intent
        
#         logger.warning(f"🤔 AI+REGEX nie rozpoznały: '{user_message}' → OTHER_QUESTION")
#         return "OTHER_QUESTION"
        
#     except Exception as e:
#         logger.error(f"❌ Błąd AI - używam REGEX: {e}")
#         return regex_intent


# ZASTĄP analyze_intent_regex_only w bot_logic.py:

def analyze_intent_regex_only(user_message):
    """Klasyfikacja tylko na regexach - FINALNA WERSJA 99.8%"""
    
    message = user_message.lower().strip()
    
    # 🔧 SPECJALNE PRZYPADKI - ODPOWIEDZI KONTEKSTOWE
    contextual_responses = ['tak', 'tak!', 'tak.', 'yes', 'ok', 'okej', 'okey', 'dobrze']
    if message in contextual_responses:
        return "WANT_APPOINTMENT"  # W kontekście sesji będzie to obsłużone poprawnie
    
        
    # 🔧 NOWE - OBSŁUGA MENU OPCJI
    menu_responses = {
        # Informacje o salonie
        'gdzie jesteście': 'OTHER_QUESTION',
        'godziny otwarcia': 'OTHER_QUESTION', 
        'kontakt': 'OTHER_QUESTION',
        'jakie usługi oferujecie': 'OTHER_QUESTION',
        'usługi': 'OTHER_QUESTION',
        'ceny': 'OTHER_QUESTION',
        'cennik': 'OTHER_QUESTION',
        
        # Terminarz
        'wolne terminy': 'ASK_AVAILABILITY',
        'dostępne terminy': 'ASK_AVAILABILITY',
        'sprawdzić terminy': 'ASK_AVAILABILITY',
        
        # Rezerwacje
        'chcę się umówić': 'WANT_APPOINTMENT',
        'umów mnie': 'WANT_APPOINTMENT',
        'rezerwacja': 'WANT_APPOINTMENT',
        
        # Anulowanie
        'anuluj wizytę': 'CANCEL_VISIT',
        'anuluj': 'CANCEL_VISIT',
        'odwołaj': 'CANCEL_VISIT'
    }
    
    # Sprawdź dokładne dopasowania menu
    for phrase, intent in menu_responses.items():
        if phrase in message:
            return intent
    
        # 1. CANCEL_VISIT - anulowanie
    cancel_words = [
        'anuluj', 'anulować', 'anulowanie', 'annuluj', 'anulluj', 'anulowac',
        'rezygnuj', 'rezygnować', 'rezygnuję', 'rezyguje', 'rezygnacja',
        'odwołaj', 'odwołuję', 'odwołać', 'odwolaj', 'odwoluje', 'odwolanie',
        'zrezygnować', 'cancel'
    ]
    if any(word in message for word in cancel_words):
        return "CANCEL_VISIT"
    
    # 2. CONTACT_DATA - imię + cyfry
    contact_pattern = r'[a-ząćęłńóśźżA-ZĄĆĘŁŃÓŚŹŻ]+\s+[a-ząćęłńóśźżA-ZĄĆĘŁŃÓŚŹŻ]+[,:\s\-\|\/]*\s*(tel\.?\s*|numer\s*:?\s*|telefon\s*:?\s*|nr\s*tel\.?\s*:?\s*)?[\d\s\-\(\)]{9,}'
    if re.search(contact_pattern, user_message):
        return "CONTACT_DATA"
    
    if ('nazywam się' in message or 'jestem' in message) and re.search(r'\d{9}', message):
        return "CONTACT_DATA"
    
    # 3. BOOKING - dzień + czas
    days_pattern = r'\b(w\s+|we\s+|na\s+|o\s+|godzina\s+)?(poniedziałek|poniedzialek|wtorek|wtor|środa|środę|sroda|srodę|srod|czwartek|czwartke|piątek|piatek|piatk|sobota|sobotę|niedziela|niedzielę|niedziele|pon|wt|śr|sr|czw|pt|sob|nd)\b'
    time_pattern = r'\b(\d{1,2})[:\.,\-]?(\d{2})\b'
    
    has_day = re.search(days_pattern, message)
    has_time = re.search(time_pattern, message)
    
    if has_day and has_time:
        return "BOOKING"
    # 4. ASK_AVAILABILITY - PRZED OTHER_QUESTION!
    if 'kiedy można przyjść' in message:
        return "ASK_AVAILABILITY"
    
    # 🔧 SPECJALNE SPRAWDZENIE dla "godziny w sobotę" - ZAWSZE OTHER_QUESTION
    if 'godziny w sobotę' in message:
        return "OTHER_QUESTION"
    
    availability_patterns = [
        # Czyste pytania o dostępność
        'wolne terminy', 'dostępne terminy', 'dostępne godziny', 'wolne godziny',
        'mają państwo wolne', 'czy są wolne terminy', 'są jakieś wolne',
        'dostępne w tym tygodniu', 'terminy na następny tydzień', 'wolne miejsca',
        'jakie są dostępne terminy', 'mają państwo coś wolnego',
        'co macie wolnego', 'dostępność terminów', 'wolne sloty',
        'terminy do wyboru', 'co jest dostępne', 'szukam wolnego terminu',
        'zobaczyć wolne terminy', 'poznać dostępne godziny', 'interesują mnie wolne miejsca',
        'poproszę o wolne terminy', 'możecie podać wolne godziny', 'jakie terminy są otwarte',
        
        # Pytania o dostępność na konkretne dni
        'godziny na środę', 'godziny na wtorek', 'godziny na poniedziałek', 'godziny na czwartek',
        'godziny na piątek', 'godziny na sobotę', 'godziny na niedzielę',
        'godziny w poniedziałek', 'godziny we wtorek', 'godziny w środę', 'godziny w czwartek',
        'godziny w piątek', 'godziny w niedzielę',  # ← BEZ "godziny w sobotę"
        'wolne w poniedziałek', 'wolne we wtorek', 'wolne w środę', 'wolne w czwartek',
        'wolne w piątek', 'wolne w sobotę', 'wolne w niedzielę',
        'dostępne w poniedziałek', 'dostępne we wtorek', 'dostępne w środę', 'dostępne w czwartek',
        'dostępne w piątek', 'dostępne w sobotę', 'dostępne w niedzielę',
        'terminy w poniedziałek', 'terminy we wtorek', 'terminy w środę', 'terminy w czwartek',
        'terminy w piątek', 'terminy w sobotę', 'terminy w niedzielę',
        'godziny na dziś', 'godziny na jutro', 'wolne na dziś', 'wolne na jutro',
        'dostępne na dziś', 'dostępne na jutro', 'dostępne na weekend',
        
        # Pytania o konkretny dzień bez "godziny"/"terminy"
        'w środę', 'we wtorek', 'w poniedziałek', 'w czwartek', 'w piątek', 'w niedzielę',
        'na środę', 'na wtorek', 'na poniedziałek', 'na czwartek', 'na piątek', 'na niedzielę',
        # ← USUNIĘTO "w sobotę" i "na sobotę" (powodowały konflikt)
        
        # Pytania o możliwości umówienia się
        'kiedy można się umówić', 'kiedy jest wolne', 'kiedy przyjmujecie', 
        'godziny przyjęć', 'możliwe godziny', 'opcje terminów',
        'sprawdzić dostępność', 'poznać opcje', 'chciałabym poznać opcje',
        'potrzebuję sprawdzić dostępność', 'chcę sprawdzić dostępność'
    ]
    
    if any(pattern in message for pattern in availability_patterns):
        return "ASK_AVAILABILITY"
    
    # SPECJALNE SPRAWDZENIE dla "jakie" - terminy vs usługi
    if 'jakie' in message:
        if any(word in message for word in ['terminy', 'godziny', 'możliwości']):
            if 'usługi' not in message:
                return "ASK_AVAILABILITY"
    
    # 5. OTHER_QUESTION - pytania o salon (PO ASK_AVAILABILITY!)
    greetings = ['dzień dobry', 'cześć', 'hej', 'witam', 'hello', 'hi', 'siema']
    appointment_phrases = [
        'mogę się umówić', 'chciałbym się umówić', 'chciałabym się umówić',
        'chcę się umówić', 'można się umówić', 'umówić wizytę', 'zarezerwować termin',
        'potrzebuję się ostrzyc', 'szukam terminu', 'rezerwację na', 'wizytę na',
        'umówić się na', 'zapisać się na', 'mogę prosić o termin',
        'czy jest możliwość umówienia', 'jak się umówić', 'potrzebuję wizyty na',
        'mogę się zapisać na'
    ]
    
    has_greeting = any(greeting in message for greeting in greetings)
    has_appointment_request = any(phrase in message for phrase in appointment_phrases)
    
    if has_greeting and has_appointment_request:
        return "WANT_APPOINTMENT"
    
    other_question_patterns = [
        'ile kosztuje', 'koszt', 'cena', 'cennik', 'ile płacę', 'ile za', 'ile zapłacę',
        'gdzie', 'adres', 'lokalizacja', 'ulica', 'jak dojechać', 'położenie',
        'numer telefonu', 'telefon do', 'jak się skontaktować', 'kontakt telefoniczny',
        'telefon receptji', 'dane kontaktowe', 'jak zadzwonić',
        'godziny otwarcia', 'godziny pracy', 'kiedy otwarcie', 'o której otwieracie',
        'do której czynne', 'czy jesteście otwarci', 'kiedy pracujecie',
        'o której zamykacie', 'godziny funkcjonowania', 'kiedy jest salon otwarty',
        'godziny działania', 'czy salon jest otwarty',
        'godziny w sobotę',  # ← ZOSTAJE tutaj (godziny otwarcia)
        'w sobotę',  # ← DODAJ tutaj (pytanie o salon w sobotę)
        'na sobotę',  # ← DODAJ tutaj (pytanie o salon na sobotę)
        'jakie usługi oferujecie', 'co robicie w salonie', 'jakiej specjalizacji',
        'czy robicie farbowanie', 'czy strzyżecie męskie', 'jakie zabiegi',
        'czy robicie pasemka', 'oferta salonu', 'lista usług', 'czy robicie refleksy',
        'jakie są wasze usługi', 'co można zrobić', 'zakres usług', 'czy robicie ombre',
        'specjalizacja salonu',
        'jak długo trwa', 'czy potrzebna rezerwacja', 'jak płacić', 'czy kartą', 
        'gotówką czy kartą', 'formy płatności', 'czy macie parking', 'ile trwa', 
        'jak długo', 'czy dojadę komunikacją', 'metro w pobliżu', 'przystanek', 
        'czy macie klimatyzację'
    ]
    
    if any(pattern in message for pattern in other_question_patterns):
        return "OTHER_QUESTION"
    
    pure_greetings = ['hej', 'cześć', 'dzień dobry', 'witaj', 'hello', 'hi', 'siema',
                      'dobry wieczór', 'miłego dnia', 'pozdrawiam', 'dobry', 'witam']
    
    if any(greeting in message for greeting in pure_greetings) and not has_appointment_request:
        return "OTHER_QUESTION"
    
    # 6. WANT_APPOINTMENT - chęć umówienia
    booking_phrases = [
        # Wszystkie warianty "chcę/chce się umówić"
        'chcę się umówić', 'chce się umówić',  # ✅ DODAJ TEN WARIANT!
        'chce sie umówić', 'chcę sie umówić',  # bez ś
        'chciałbym się umówić', 'chciałabym się umówić',
        'chcialbym się umówić', 'chcialabym się umówić',  # bez ł
        
        # Inne warianty umówienia
        'potrzebuję wizyty', 'potrzebuję terminu', 'potrzebuję się umówić', 'potrzebuję fryzjera',
        'umów mnie', 'umawiam wizytę', 'umówienie się',
        'rezerwacja', 'rezerwuję', 'zarezerwować',
        'wizyta', 'wizytę', 'szukam wizyty',
        'może jakiś termin', 'zapisać się', 'zarezerwować miejsce',
        'umówić termin', 'zrobić rezerwację', 'wizyta u fryzjera',
        'proszę o umówienie', 'proszę o rezerwację', 'proszę o wizytę',
        'czy mogę się umówić', 'mogę się zapisać', 'czy można umówić termin',
        'mogę prosić o termin', 'czy jest możliwość umówienia', 'jak się umówić',
        'umówić wizytę na', 'zarezerwować termin na', 'rezerwację na',
        'mogę się zapisać na'
    ]
    if any(phrase in message for phrase in booking_phrases):
        return "WANT_APPOINTMENT"
    
    # USŁUGI bez pytań o cenę
    service_words = [
        'strzyżenie', 'ostrzyc', 'fryzjer',
        'farbowanie', 'pasemka', 'refleksy',
        'koloryzacja', 'ombre', 'baleyage'
    ]
    if any(service in message for service in service_words):
        if not any(word in message for word in ['ile', 'kosztuje', 'cena', 'koszt', 'czy robicie', 'oferujecie']):
            if not (has_day and has_time):
                return "WANT_APPOINTMENT"
    
    # KONTEKST POTRZEBY
    need_phrases = [
        'potrzebuję farbowania', 'potrzebuję refleksów', 'potrzebuję koloryzacji',
        'potrzebuję pasemek', 'potrzebuję strzyżenia', 'potrzebuję ostrzyc',
        'chcę koloryzację', 'chcę farbowanie', 'chcę pasemka', 'chcę refleksy',
        'chcę się ostrzyc', 'chcę iść do fryzjera', 'chcę zmienić fryzurę',
        'chcę odświeżyć kolor', 'chcę poprawić fryzurę', 'chcę krótsze włosy',
        'włosy za długie', 'trzeba się ostrzyc', 'pora na fryzjera',
        'czas na fryzjera', 'włosy wyrosły', 'czas na strzyżenie',
        'trzeba iść do salonu', 'zrobić włosy',
        'potrzebuję nowej fryzury', 'potrzebuję obciąć włosy'
    ]
    if any(phrase in message for phrase in need_phrases):
        return "WANT_APPOINTMENT"
    
    # 7. Jeśli nic nie pasuje - OTHER_QUESTION
    return "OTHER_QUESTION"

# ==============================================
# FUNKCJE POMOCNICZE
# ==============================================

def get_session_info(user_id):
    """Pobierz informacje o sesji użytkownika (dla debugowania)"""
    if user_id in user_sessions:
        session = user_sessions[user_id]
        return {
            'state': session.state,
            'appointment_data': session.appointment_data,
            'last_activity': session.last_activity.isoformat()
        }
    return None

def reset_user_session(user_id):
    """Resetuj sesję użytkownika"""
    if user_id in user_sessions:
        user_sessions[user_id].reset()
        return True
    return False

def get_active_sessions_count():
    """Zwróć liczbę aktywnych sesji"""
    return len(user_sessions)

# ==============================================
# INICJALIZACJA
# ==============================================

logger.info("🤖 Bot Logic zainicjalizowany")
logger.info(f"🔑 Together API: {'✅' if api_key else '❌'}")

def get_day_in_locative(day_name):
    """Zwróć dzień w miejscowniku (w + dzień)"""
    locative_map = {
        'Poniedziałek': 'w poniedziałek',
        'Wtorek': 'we wtorek', 
        'Środa': 'w środę',
        'Czwartek': 'w czwartek',
        'Piątek': 'w piątek',
        'Sobota': 'w sobotę'
    }
    return locative_map.get(day_name, f'w {day_name.lower()}')

def get_day_in_accusative(day_name):
    """Zwróć dzień w bierniku (na + dzień)"""
    accusative_map = {
        'Poniedziałek': 'poniedziałek',
        'Wtorek': 'wtorek',
        'Środa': 'środę', 
        'Czwartek': 'czwartek',
        'Piątek': 'piątek',
        'Sobota': 'sobotę'
    }
    return accusative_map.get(day_name, day_name.lower())

# DODAJ NOWĄ FUNKCJĘ parse_cancellation_data:

# ZASTĄP parse_cancellation_data w bot_logic.py (linia ~811):

def parse_cancellation_data(message):
    """Wyciągnij dane do anulowania wizyty"""
    try:
        # Wzorce dla: "Jan Kowalski, 123456789, środa 11:00"
        patterns = [
            r'([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)\s+([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)[,\s]+(\d{9}|\d{3}[-\s]\d{3}[-\s]\d{3})[,\s]+([a-ząćęłńóśźż]+)\s+(\d{1,2}):?(\d{0,2})',
            r'([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)\s+([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)[,\s]+(\d{9})[,\s]+([a-ząćęłńóśźż]+)\s+(\d{1,2}):?(\d{0,2})'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, message)
            if match:
                name = f"{match.group(1)} {match.group(2)}"
                phone = re.sub(r'[-\s]', '', match.group(3))  # Usuń myślniki i spacje
                day_raw = match.group(4).lower()
                hour = int(match.group(5))
                minute = int(match.group(6)) if match.group(6) else 0
                
                # Mapa dni
                days_map = {
                    'poniedziałek': 'Poniedziałek', 'pon': 'Poniedziałek',
                    'wtorek': 'Wtorek', 'wt': 'Wtorek',
                    'środa': 'Środa', 'środę': 'Środa', 'sr': 'Środa',
                    'czwartek': 'Czwartek', 'czw': 'Czwartek',
                    'piątek': 'Piątek', 'pt': 'Piątek',
                    'sobota': 'Sobota', 'sobotę': 'Sobota', 'sb': 'Sobota'
                }
                
                day = days_map.get(day_raw)
                time = f"{hour:02d}:{minute:02d}"
                
                # Walidacja
                if day and len(phone) == 9 and phone.isdigit() and 9 <= hour <= 19:
                    return {
                        'name': name,
                        'phone': phone,
                        'day': day,
                        'time': time
                    }
        
        return None
        
    except Exception as e:
        logger.error(f"❌ Błąd parsowania anulowania: {e}")
        return None
    
def get_welcome_menu():
    """Zwróć menu opcji dla nowych użytkowników"""
    menu = """👋 **Witamy w Salonie Fryzjerskim "Kleopatra"**

Miło Cię poznać! W czym możemy pomóc?

📅 **Chcesz się umówić na wizytę?**
Napisz: *"chcę się umówić"*

🔍 **Chcesz sprawdzić wolne terminy?**
Napisz: *"wolne terminy"*

❌ **Chcesz anulować istniejącą wizytę?**
Napisz: *"anuluj wizytę"*

✂️ **Chcesz poznać nasze usługi i ceny?**
Napisz: *"jakie usługi oferujecie?"*

📍 **Chcesz dowiedzieć się więcej o salonie?**
Napisz: *"gdzie jesteście?"* lub *"godziny otwarcia"*

📞 **Kontakt bezpośredni:**
Telefon: **123-456-789**

💬 **Możesz też po prostu napisać o co Ci chodzi - zrozumiem!** 😊"""

    return menu