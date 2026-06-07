// ================================
// KONFIGURACJA SMART FAQ BOT
// ================================

// UWAGA: Wklej tutaj swój klucz API z Together AI
// Instrukcja: https://together.ai → API Keys

// USUŃ klucz API z frontend (teraz jest bezpieczny w backend):
// const OPENAI_API_KEY = '...'; // USUNIĘTE!

// Backend URL:
const BACKEND_API_URL = 'http://localhost:5000/api/chat';

// Zawsze włączone (backend ma klucz):
const IS_API_CONFIGURED = true;

// ================================
// PROMPT SYSTEMOWY DLA AI
// ================================
const SYSTEM_PROMPT = `
WAŻNE: Odpowiadaj BEZPOŚREDNIO bez pokazywania swojego procesu myślowego!

Jesteś AI asystentem salonu fryzjerskiego "Kleopatra" w Warszawie przy ul. Pięknej 15.

ZASADY ODPOWIEDZI:
- NIE pokazuj swojego procesu myślowego
- NIE pisz "Okay, the user is asking..." 
- Odpowiadaj TYLKO po polsku
- Bądź bezpośredni i pomocny
- Używaj max 2-3 emoji na wiadomość

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

NASZ ZESPÓŁ:
- Anna Kowalska - Senior Stylist (10 lat doświadczenia)
  * Specjalizacja: koloryzacja, stylizacja ślubna
  * Tel. bezpośredni: 123-456-790
- Kasia Nowak - Hair Artist (8 lat doświadczenia)
  * Specjalizacja: strzyżenia kreatywne, przedłużanie włosów
  * Tel. bezpośredni: 123-456-791

CENNIK USŁUG:
STRZYŻENIE:
- Damskie (mycie + strzyżenie + styling): 80-120 zł
- Męskie: 50-70 zł
- Dziecięce (do 12 lat): 40 zł

KOLORYZACJA:
- Całościowe farbowanie: 120-180 zł
- Retusz odrostów: 80 zł
- Pasemka/refleksy: 150-250 zł
- Ombre/Balayage: 200-350 zł
- Rozjaśnianie: 180-300 zł

STYLIZACJA & PIELĘGNACJA:
- Mycie + styling: 60 zł
- Upięcie okolicznościowe: 80-120 zł
- Maseczka regenerująca: +30 zł
- Keratynowe prostowanie: 300 zł
- Przedłużanie włosów: od 400 zł

DODATKOWE INFORMACJE:
- Parking dostępny za salonem
- Płatność: gotówka, karta, BLIK
- Rezerwacja online: www.salon-kleopatra.pl/rezerwacja
- Social media: @salon_kleopatra (Instagram), Salon Kleopatra Warszawa (Facebook)

ZASADY KOMUNIKACJI:
1. Bądź miły, pomocny i profesjonalny
2. Używaj emoji, ale nie przesadzaj (max 2-3 na wiadomość)
3. Odpowiadaj krótko i konkretnie
4. Jeśli nie znasz odpowiedzi, przekieruj do recepcji (tel. 123-456-789)
5. Zawsze oferuj pomoc w umówieniu wizyty
6. W sytuacjach pilnych lub skomplikowanych, od razu przekierowuje do człowieka
7. Pamiętaj o godzinach otwarcia przy ustalaniu terminów

PRZYKŁADOWE SYTUACJE:
- Reklamacje → przekieruj do kierownika
- Pilne sprawy → połącz z recepcją
- Szczegółowe porady → umów konsultację ze stylistą
- Problemy techniczne → podaj alternatywny kontakt

Odpowiadaj TYLKO po polsku. Bądź pomocny i empatyczny.
`;

// ================================
// ROZPOZNAWANIE INTENCJI
// ================================
const INTENTS = {
    APPOINTMENT: ['termin', 'umówić', 'wizyta', 'appointment', 'rezerwacja', 'wolne'],
    EMERGENCY: ['pilne', 'dzisiaj', 'natychmiast', 'emergency', 'szybko', 'teraz'],
    COMPLAINT: ['reklamacja', 'problem', 'niezadowolony', 'źle', 'complaint'],
    PRICES: ['cena', 'ceny', 'cennik', 'ile kosztuje', 'koszt', 'price'],
    SERVICES: ['usługi', 'usługa', 'co oferujecie', 'services'],
    LOCATION: ['gdzie', 'adres', 'lokalizacja', 'location', 'dojazd'],
    CONTACT: ['kontakt', 'telefon', 'numer', 'dzwonić', 'contact']
};

// ================================
// BAZA WIEDZY (FALLBACK)
// ================================
const KNOWLEDGE_BASE = {
    services: {
        keywords: ['usługi', 'usługa', 'co oferujecie', 'services', 'strzyżenie', 'farbowanie'],
        response: `✂️ **Nasze usługi:**

**STRZYŻENIE:**
• Damskie - 80-120 zł
• Męskie - 50-70 zł  
• Dziecięce - 40 zł

**KOLORYZACJA:**
• Farbowanie - 120-180 zł
• Pasemka - 150-250 zł
• Ombre/Balayage - 200-350 zł

**INNE:**
• Stylizacja - 60 zł
• Keratynowe prostowanie - 300 zł
• Przedłużanie włosów - od 400 zł

📞 Chcesz umówić wizytę? Zadzwoń: 123-456-789`
    },
    
    appointments: {
        keywords: ['termin', 'terminy', 'wolne', 'umówić', 'rezerwacja'],
        response: `📅 **Rezerwacja wizyt:**

**Sposoby umówienia:**
📞 Telefon: 123-456-789
🌐 Online: www.salon-kleopatra.pl/rezerwacja
📱 WhatsApp: 123-456-789

**Godziny otwarcia:**
• Pn-Pt: 9:00-19:00
• Sobota: 9:00-16:00
• Niedziela: zamknięte

**Nasi specjaliści:**
👩‍🦱 Anna - koloryzacja, stylizacja
👩‍🦰 Kasia - strzyżenia, przedłużanie

Mogę połączyć Cię z recepcją! 📞`
    },
    
    default: {
        keywords: [],
        response: `Dziękuję za wiadomość! 😊 

Jestem AI asystentem salonu "Kleopatra". Mogę pomóc z:
• Informacjami o usługach i cenach
• Sprawdzeniem wolnych terminów  
• Kontaktem z naszymi specjalistami

📞 **W pilnych sprawach dzwoń: 123-456-789**

Jak mogę Ci pomóc?`
    }
};

// ================================
// USTAWIENIA CHATBOTA
// ================================
const BOT_CONFIG = {
    maxTokens: 500,
    temperature: 0.7,
    //model: "meta-llama/Llama-3.2-3B-Instruct-Turbo", // Szybszy i bez "thinking"
    model: "deepseek-ai/DeepSeek-R1-Distill-Llama-70B-free", // Together AI model
    typingDelay: {
        min: 1000,
        max: 3000
    },
    maxConversationHistory: 10
};