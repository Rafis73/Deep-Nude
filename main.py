import os
import requests
import json
from datetime import datetime
import sys

# Библиотеки Google
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# --- КОНФИГУРАЦИЯ ---
ELEVENLABS_API_KEY = os.getenv('ELEVENLABS_API_KEY')
ELEVENLABS_AGENT_ID = os.getenv('ELEVENLABS_AGENT_ID') # ID вашего агента
GOOGLE_DOC_ID = os.getenv('GOOGLE_DOC_ID')
GOOGLE_DRIVE_FOLDER_ID = os.getenv('GOOGLE_DRIVE_FOLDER_ID')
GOOGLE_CREDENTIALS_JSON_STR = os.getenv('GOOGLE_CREDENTIALS_JSON')

PROCESSED_IDS_FILE = 'processed_ids.txt'
API_BASE_URL = "https://api.elevenlabs.io/v1"

def get_google_services():
    """Аутентификация в Google и получение сервисов Docs и Drive."""
    try:
        creds_json = json.loads(GOOGLE_CREDENTIALS_JSON_STR)
        creds = Credentials.from_service_account_info(
            creds_json,
            scopes=['https://www.googleapis.com/auth/documents', 'https://www.googleapis.com/auth/drive']
        )
        return build('docs', 'v1', credentials=creds), build('drive', 'v3', credentials=creds)
    except Exception as e:
        print(f"Ошибка аутентификации в Google: {e}")
        return None, None

def get_processed_ids():
    """Загружает ID обработанных записей из файла."""
    if not os.path.exists(PROCESSED_IDS_FILE):
        return set()
    with open(PROCESSED_IDS_FILE, 'r') as f:
        return set(line.strip() for line in f)

def save_processed_id(conversation_id):
    """Сохраняет ID обработанной записи в файл."""
    with open(PROCESSED_IDS_FILE, 'a') as f:
        f.write(conversation_id + '\n')

def get_new_conversations():
    """Получает все разговоры, сделанные конкретным агентом."""
    if not ELEVENLABS_AGENT_ID:
        print("Ошибка: ID агента не указан в переменных окружения.")
        return []
    
    url = f"{API_BASE_URL}/convai/conversations"
    headers = {"xi-api-key": ELEVENLABS_API_KEY}
    params = {"page_size": 100}
    
    agent_conversations = []
    
    while True:
        try:
            print(f"Запрашиваем разговоры, курсор: {params.get('cursor', 'N/A')}")
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            
            for conv in data.get("conversations", []):
                if conv.get("agent_id") == ELEVENLABS_AGENT_ID:
                    agent_conversations.append(conv)

            if not data.get("has_more"):
                break
            params["cursor"] = data.get("next_cursor")

        except requests.RequestException as e:
            print(f"Ошибка при запросе к ConvAI API: {e}")
            break
            
    print(f"Найдено всего {len(agent_conversations)} разговоров для агента {ELEVENLABS_AGENT_ID}.")
    return agent_conversations

def get_conversation_details(conversation_id):
    """Получает полную транскрибацию и метаданные разговора."""
    url = f"{API_BASE_URL}/convai/conversations/{conversation_id}"
    headers = {"xi-api-key": ELEVENLABS_API_KEY}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"Ошибка получения деталей для {conversation_id}: {e}")
        return None

def download_conversation_audio(conversation_id):
    """Скачивает аудиофайл разговора."""
    url = f"{API_BASE_URL}/convai/conversations/{conversation_id}/audio"
    headers = {"xi-api-key": ELEVENLABS_API_KEY}
    try:
        response = requests.get(url, headers=headers, stream=True)
        response.raise_for_status()
        
        filename = f"{conversation_id}.mp3"
        with open(filename, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f"Аудиофайл {filename} успешно скачан.")
        return filename
    except requests.RequestException as e:
        print(f"Ошибка скачивания аудио для {conversation_id}: {e}")
        return None

def upload_to_drive(drive_service, filename, folder_id):
    """Загружает файл на Google Drive."""
    try:
        file_metadata = {'name': filename, 'parents': [folder_id]}
        media = MediaFileUpload(filename, mimetype='audio/mpeg', resumable=True)
        file = drive_service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()
        print(f"Файл {filename} загружен на Google Drive. Ссылка: {file.get('webViewLink')}")
        return file.get('webViewLink')
    except Exception as e:
        print(f"Ошибка загрузки на Google Drive: {e}")
        return None

def format_transcript(transcript_data):
    """Форматирует транскрипцию в читаемый вид."""
    lines = []
    for msg in transcript_data:
        role = msg.get("role", "UNKNOWN").capitalize()
        text = msg.get("message", "").strip()
        if text:
            lines.append(f"{role}: {text}")
    return "\n".join(lines)

def append_to_google_doc(docs_service, text, audio_link, start_time_str):
    """Добавляет запись в Google Doc."""
    try:
        content = (
            f"--- Запись от {start_time_str} ---\n\n"
            f"Транскрибация:\n{text}\n\n"
            f"Ссылка на аудиофайл: {audio_link}\n\n"
            "-----------------------------------------\n\n"
        )
        requests_body = [{'insertText': {'location': {'index': 1}, 'text': content}}]
        docs_service.documents().batchUpdate(documentId=GOOGLE_DOC_ID, body={'requests': requests_body}).execute()
        print("Запись успешно добавлена в Google Doc.")
    except Exception as e:
        print(f"Ошибка добавления в Google Doc: {e}")

def main():
    print("Начало работы скрипта...")
    docs_service, drive_service = get_google_services()
    if not all([docs_service, drive_service]):
        sys.exit("Не удалось подключиться к сервисам Google. Завершение работы.")

    processed_ids = get_processed_ids()
    print(f"Загружено {len(processed_ids)} уже обработанных ID.")
    
    conversations = get_new_conversations()
    if not conversations:
        print("Новых разговоров не найдено или не удалось их получить.")
        return
        
    # Сортируем от старых к новым для последовательной обработки
    conversations.sort(key=lambda c: c.get("start_time_unix_secs", 0))

    new_items_found = 0
    for conv_summary in conversations:
        conv_id = conv_summary.get('conversation_id')
        if conv_id and conv_id not in processed_ids:
            new_items_found += 1
            print(f"\n--- Обработка новой записи: {conv_id} ---")

            details = get_conversation_details(conv_id)
            if not details:
                continue

            start_ts = details.get("metadata", {}).get("start_time_unix_secs", 0)
            start_time_str = datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d %H:%M:%S') if start_ts else "N/A"
            
            transcript_text = format_transcript(details.get("transcript", []))
            if not transcript_text:
                transcript_text = "Транскрибация пуста."

            audio_filename = download_conversation_audio(conv_id)
            if not audio_filename:
                continue

            audio_link = upload_to_drive(drive_service, audio_filename, GOOGLE_DRIVE_FOLDER_ID)
            
            if audio_link:
                append_to_google_doc(docs_service, transcript_text, audio_link, start_time_str)
            
            save_processed_id(conv_id)
            os.remove(audio_filename)

    if new_items_found == 0:
        print("Новых записей для обработки не найдено.")
    
    print("Работа скрипта завершена.")

if __name__ == '__main__':
    main()
