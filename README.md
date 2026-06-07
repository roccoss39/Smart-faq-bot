# Smart FAQ Bot - Salon Fryzjerski "Kleopatra"

🤖 **Chatbot dla salonu fryzjerskiego** z integracją AI (GPT-4o-mini via GitHub Models), Facebook Messenger, Google Calendar i interfejsem webowym. Obsługuje wielu użytkowników równocześnie z separacją sesji.

## 🚀 Funkcje

- 🤖 **GPT-4o-mini** (GitHub Models — bezpłatny) lub OpenAI API
- 📘 **Facebook Messenger** — natywna integracja z webhookiem
- 📅 **Google Calendar** — rezerwacje, anulowania, sprawdzanie terminów
- 👥 **Multi-user** — każdy użytkownik ma własną sesję z TTL 24h
- 💬 **Interfejs webowy** — responsywny chat (`index.html`)
- 📱 **Mobile-first** — działa na telefonach

## 📁 Struktura projektu

```
smart-faq-bot/
├── backend.py              # Serwer Flask + endpointy API + webhook FB
├── bot_logic_ai.py         # Logika AI, sesje, integracja z kalendarzem
├── calendar_service.py     # Google Calendar API
├── index.html              # Interfejs webowy
├── style.css               # Style
├── script.js               # Frontend JavaScript
├── config.js               # Konfiguracja URL backendu
├── credentials.json        # Google Service Account (NIE commituj!)
├── .env                    # Zmienne środowiskowe (NIE commituj!)
├── .env.example            # Przykład konfiguracji
└── README.md               # Ten plik
```

## ⚡ Szybki start

### 1. Klonowanie i środowisko

```bash
git clone https://github.com/roccoss39/Smart-faq-bot.git
cd Smart-faq-bot

python -m venv .venv
source .venv/bin/activate          # Linux/Mac
# .venv\Scripts\activate           # Windows

pip install flask flask-cors python-dotenv requests openai pytz \
            google-auth google-auth-oauthlib google-api-python-client
```

### 2. Plik `.env`

```bash
cp .env.example .env
nano .env
```

Zawartość `.env`:

```env
# AI — GitHub Models (darmowy) lub OpenAI
OPENAI_API_KEY=ghp_twój_token_github

# Facebook Messenger
FACEBOOK_PAGE_ACCESS_TOKEN=EAAxxxxxxxxxxxxxxx
FACEBOOK_VERIFY_TOKEN=twój_sekretny_token
FACEBOOK_PAGE_ID=750208294831428

# Google Calendar
GOOGLE_CALENDAR_ID=twój_calendar_id@group.calendar.google.com
```

### 3. Google Calendar

Umieść plik `credentials.json` (Google Service Account) w głównym folderze projektu.  
Uprawnienia wymagane: `https://www.googleapis.com/auth/calendar`

### 4. Uruchomienie

```bash
python backend.py
```

Serwer startuje na `http://localhost:5000`.  
Sprawdź: `http://localhost:5000/api/health`

### 5. Test lokalny (bez Facebooka)

```bash
curl -X POST http://localhost:5000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Cześć, jakie macie usługi?"}]}'
```

Lub otwórz `index.html` w przeglądarce.

## 🔧 Facebook Messenger

### Wymagania

- Konto Facebook Developers
- Strona Facebook podpięta do aplikacji
- Page Access Token z uprawnieniami `pages_messaging`

### Konfiguracja webhooka

```bash
# Terminal 1 — backend
python backend.py

# Terminal 2 — ngrok (tunel HTTPS)
ngrok http 5000
```

W panelu [Facebook Developers](https://developers.facebook.com):

1. Aplikacja → **Use cases → Webhooks**
2. **Select product:** Page
3. **Callback URL:** `https://twój-url.ngrok-free.app/`
4. **Verify Token:** wartość `FACEBOOK_VERIFY_TOKEN` z `.env`
5. **Verify and Save** → przewiń do pola `messages` i włącz **Subscribed**

> ⚠️ Ngrok generuje nowy URL przy każdym restarcie — aktualizuj Callback URL po każdym restarcie.  
> Dla stałego URL użyj `ngrok http --domain=twoja-domena.ngrok-free.app 5000`

> ⚠️ Aplikacja w trybie **Development** — wiadomości z Messengera docierają tylko po opublikowaniu aplikacji przez Meta App Review.

## 🤖 Zmiana modelu AI

Projekt domyślnie używa **GitHub Models** (darmowe, limit ~150 req/dzień).

### GitHub Models (domyślny, darmowy)

Token: github.com → Settings → Developer settings → Personal access tokens

```env
OPENAI_API_KEY=ghp_twój_token
```

`bot_logic_ai.py` używa `base_url="https://models.inference.ai.azure.com"`.

### OpenAI API (płatny, bez limitu)

```env
OPENAI_API_KEY=sk-proj-twój_klucz
```

W `bot_logic_ai.py` usuń `base_url`:

```python
client = OpenAI(api_key=api_key, timeout=30.0)
```

Zmień model na `gpt-4o` jeśli potrzebujesz wyższej jakości.

## 🗓️ System rezerwacji

Bot prowadzi klienta przez pełny proces:

```
Klient: "Chcę się umówić na piątek"
Bot:    "Sprawdzam wolne terminy na piątek..."
        [pobiera z Google Calendar]
        "Dostępne: 09:00, 10:00, 14:30..."
Klient: "14:30"
Bot:    "Jaką usługę wybierasz?"
Klient: "Strzyżenie, Anna Nowak, 987654321"
Bot:    📋 Podsumowanie → pyta o TAK
Klient: "TAK"
Bot:    ✅ REZERWACJA POTWIERDZONA → zapisuje do kalendarza
```

**Usługi:** Strzyżenie (80 zł), Farbowanie (150 zł), Stylizacja (120 zł)  
**Godziny:** Pon–Pt 9:00–19:00, Sobota 9:00–16:00, Niedziela — zamknięte

## 🛠️ Endpointy API

| Metoda | Endpoint | Opis |
|--------|----------|------|
| GET    | `/`      | Weryfikacja webhooka FB |
| POST   | `/`      | Odbiór wiadomości FB |
| POST   | `/api/chat` | Interfejs webowy |
| GET    | `/api/health` | Status serwisu |
| GET    | `/api/debug/sessions` | Aktywne sesje |
| POST   | `/api/debug/reset/<id>` | Reset sesji |

## 🚨 Rozwiązywanie problemów

**Bot nie odpowiada na Facebooku**
```bash
# Sprawdź logi — czy webhook dociera?
# Ngrok dashboard: http://127.0.0.1:4040
# Upewnij się że aplikacja FB jest opublikowana
```

**Błąd 400 z `/api/chat`**
```bash
# Frontend wysyła {"messages": [...]} — sprawdź format w script.js
```

**Błąd Google Calendar**
```bash
# Sprawdź czy credentials.json jest w folderze projektu
# Sprawdź czy GOOGLE_CALENDAR_ID jest ustawione w .env
# Sprawdź uprawnienia Service Account do kalendarza
```

**Limit GitHub Models (429)**
```bash
# Limit ~150 req/dzień na darmowym planie
# Przejdź na OpenAI API dla produkcji
```

## 🚀 Deployment na produkcję

```bash
# VPS — instalacja
git clone https://github.com/roccoss39/Smart-faq-bot.git
cd Smart-faq-bot && pip install -r requirements.txt
cp .env.example .env && nano .env

# Uruchomienie z gunicorn
pip install gunicorn
gunicorn -w 2 -b 0.0.0.0:5000 backend:app
```

Nginx (reverse proxy):

```nginx
server {
    listen 80;
    server_name yourdomain.com;
    location / {
        proxy_pass http://localhost:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Na produkcji użyj płatnego OpenAI API — GitHub Models ma limit 150 req/dzień.

## 💰 Koszty

| Składnik | Koszt |
|----------|-------|
| GitHub Models (AI) | Darmowy (150 req/dzień) |
| OpenAI gpt-4o-mini | ~$0.15/1M tokenów |
| ngrok darmowy | Zmienny URL |
| ngrok płatny | ~$8/mies (stały URL) |
| VPS | ~20–50 zł/mies |
| Facebook | Darmowy |

## 📄 Licencja

MIT License

---

**Stack:** Python · Flask · OpenAI API · Google Calendar API · Facebook Graph API · JavaScript  
**Kontakt:** podziewski39@o2.pl