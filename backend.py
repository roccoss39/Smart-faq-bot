"""
Backend - Flask server z webhook'ami Facebook
Obsługuje tylko komunikację, logika w bot_logic_ai.py
"""

import os
import logging
import json
import time
import threading
from collections import OrderedDict
from datetime import datetime

import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

from bot_logic_ai import process_user_message_smart

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# ==============================================
# KONFIGURACJA
# ==============================================

FACEBOOK_VERIFY_TOKEN = os.getenv('FACEBOOK_VERIFY_TOKEN')
FACEBOOK_PAGE_ACCESS_TOKEN = os.getenv('FACEBOOK_PAGE_ACCESS_TOKEN')
FACEBOOK_PAGE_ID = os.getenv('FACEBOOK_PAGE_ID', '750208294831428')

if not FACEBOOK_VERIFY_TOKEN:
    logger.error("❌ Brak FACEBOOK_VERIFY_TOKEN w .env — webhook weryfikacja nie zadziała!")

required_vars = ['FACEBOOK_PAGE_ACCESS_TOKEN', 'OPENAI_API_KEY', 'FACEBOOK_VERIFY_TOKEN']
missing_vars = [v for v in required_vars if not os.getenv(v)]
if missing_vars:
    logger.error(f"❌ Brakujące zmienne środowiskowe: {missing_vars}")
else:
    logger.info("✅ Wszystkie zmienne środowiskowe załadowane")

# ==============================================
# DEDUPLIKACJA WIADOMOŚCI Z TTL
# ==============================================

MAX_CACHE_SIZE = 1000
MESSAGE_TTL = 60 * 10  # 10 minut
processed_messages: OrderedDict[str, float] = OrderedDict()  # {message_id: timestamp}


def is_message_processed(message_id: str) -> bool:
    now = time.time()
    # Wyczyść stare wpisy
    expired = [mid for mid, ts in list(processed_messages.items()) if now - ts > MESSAGE_TTL]
    for mid in expired:
        processed_messages.pop(mid, None)
    return message_id in processed_messages


def mark_message_processed(message_id: str):
    now = time.time()
    processed_messages[message_id] = now
    # Ogranicz rozmiar
    while len(processed_messages) > MAX_CACHE_SIZE:
        processed_messages.popitem(last=False)

# ==============================================
# FACEBOOK MESSAGING
# ==============================================

def _send_single_message(recipient_id: str, text: str) -> bool:
    """Wyślij jedną wiadomość przez Facebook Graph API."""
    try:
        url = f"https://graph.facebook.com/v18.0/{FACEBOOK_PAGE_ID}/messages"
        response = requests.post(
            url,
            json={'recipient': {'id': recipient_id}, 'message': {'text': text}},
            params={'access_token': FACEBOOK_PAGE_ACCESS_TOKEN},
            timeout=10
        )
        if response.status_code == 200:
            return True
        logger.error(f"❌ FB API {response.status_code}: {response.text}")
        return False
    except Exception as e:
        logger.error(f"❌ Błąd wysyłania: {e}")
        return False


def send_facebook_message(recipient_id: str, text: str) -> bool:
    """Wyślij wiadomość (dzieli na części jeśli za długa)."""
    if not FACEBOOK_PAGE_ACCESS_TOKEN:
        logger.error("Brak Facebook Page Access Token")
        return False

    if len(text) <= 1900:
        return _send_single_message(recipient_id, text)

    # Podziel na części
    parts = [text[i:i+1900] for i in range(0, len(text), 1900)]
    for i, part in enumerate(parts):
        if i > 0:
            time.sleep(0.5)
        if not _send_single_message(recipient_id, part):
            return False
    return True


def handle_message_async(sender_id: str, message_text: str, message_id: str):
    """Obsłuż wiadomość w osobnym wątku — nie blokuje webhooka."""
    if is_message_processed(message_id):
        logger.info(f"🔄 Duplikat: {message_id} — pomijam")
        return
    mark_message_processed(message_id)

    logger.info(f"💬 Wiadomość od {sender_id}: {message_text}")
    response = process_user_message_smart(message_text, sender_id)
    send_facebook_message(sender_id, response)
    logger.info(f"✅ Wysłano do {sender_id}: '{response[:60]}...'")

# ==============================================
# WEBHOOK ENDPOINTS
# ==============================================

@app.route('/', methods=['GET'])
def webhook_verify():
    """Weryfikacja webhooka Facebook."""
    mode = request.args.get('hub.mode')
    challenge = request.args.get('hub.challenge')
    verify_token = request.args.get('hub.verify_token')

    logger.info(f"🔍 Weryfikacja: mode={mode}, token={verify_token}")

    if mode == 'subscribe' and verify_token == FACEBOOK_VERIFY_TOKEN:
        logger.info("✅ Webhook zweryfikowany!")
        return challenge, 200

    logger.error(f"❌ Weryfikacja nieudana: oczekiwano '{FACEBOOK_VERIFY_TOKEN}', otrzymano '{verify_token}'")
    return 'Błąd weryfikacji', 403


@app.route('/', methods=['POST'])
def webhook():
    """Obsługa wiadomości Facebook — odpowiada 200 natychmiast, przetwarza w tle."""
    data = request.get_json(silent=True)
    if not data:
        return 'Bad Request', 400

    logger.info(f"📨 Webhook: {json.dumps(data, indent=2, ensure_ascii=False)[:500]}")

    if data.get('object') == 'page':
        for entry in data.get('entry', []):
            for event in entry.get('messaging', []):
                msg = event.get('message', {})

                if not msg or 'text' not in msg:
                    continue
                if msg.get('is_echo'):
                    continue

                sender_id = event['sender']['id']
                message_text = msg['text']
                message_id = msg.get('mid', f"{sender_id}_{time.time()}")

                # Asynchronicznie — nie blokujemy odpowiedzi 200
                t = threading.Thread(
                    target=handle_message_async,
                    args=(sender_id, message_text, message_id),
                    daemon=True
                )
                t.start()

    return 'OK', 200


@app.route('/api/chat', methods=['POST'])
def chat():
    """Endpoint dla interfejsu webowego."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Brak danych'}), 400

    messages = data.get('messages', [])
    if not messages:
        return jsonify({'error': 'Brak wiadomości'}), 400

    # Pobierz ostatnią wiadomość użytkownika
    last_message = next(
        (m.get('content', '') for m in reversed(messages) if m.get('role') == 'user'),
        ''
    )
    if not last_message:
        return jsonify({'error': 'Brak wiadomości użytkownika'}), 400

    user_id = data.get('user_id', 'web_user')
    response = process_user_message_smart(last_message, user_id)
    return jsonify({'response': response})


@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check."""
    try:
        from bot_logic_ai import user_conversations, get_user_stats
        stats = get_user_stats()
        return jsonify({
            'status': 'ok',
            'service': 'Smart FAQ Bot — Salon Kleopatra',
            'model': 'gpt-4o-mini (GitHub Models)',
            'facebook_configured': bool(FACEBOOK_PAGE_ACCESS_TOKEN),
            'active_sessions': stats['total_conversations'],
            'memory_enabled': True,
            'calendar_service': 'enabled',
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/api/debug/sessions', methods=['GET'])
def debug_sessions():
    """Debug — aktywne sesje."""
    try:
        from bot_logic_ai import user_conversations
        info = {
            uid: {
                'length': len(conv),
                'last': conv[-1] if conv else None
            }
            for uid, conv in user_conversations.items()
        }
        return jsonify({'active': len(user_conversations), 'sessions': info})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/debug/reset/<user_id>', methods=['POST'])
def debug_reset_session(user_id):
    """Debug — resetuj sesję."""
    try:
        from bot_logic_ai import user_conversations, user_last_activity
        deleted = user_id in user_conversations
        user_conversations.pop(user_id, None)
        user_last_activity.pop(user_id, None)
        return jsonify({'success': deleted, 'user_id': user_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==============================================
# MAIN
# ==============================================

if __name__ == '__main__':
    logger.info("🚀 Uruchamianie Smart FAQ Bot Backend...")
    logger.info(f"🔑 Verify token: {'✅' if FACEBOOK_VERIFY_TOKEN else '❌ BRAK!'}")
    logger.info(f"📘 FB token: {'✅' if FACEBOOK_PAGE_ACCESS_TOKEN else '❌ BRAK!'}")
    logger.info(f"🤖 Bot Logic: enabled")
    app.run(debug=True, host='0.0.0.0', port=5000)