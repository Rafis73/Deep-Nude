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
# Эти значения мы будем получать из GitHub Secrets (переменных окружения)
ELEVENLABS_API_KEY = os.getenv('ELEVENLABS_API_KEY')
ELEVENLABS_AGENT_ID = os.getenv('ELEVENLABS_AGENT_ID') # <-- НОВЫЙ ПАРАМЕТР
GOOGLE_DOC_ID = os.getenv('GOOGLE_DOC_ID')
GOOGLE_DRIVE_FOLDER_ID = os.getenv('GOOGLE_DRIVE_FOLDER_ID')
GOOGLE_CREDENTIALS_JSON_STR = os.getenv('GOOGLE_CREDENTIALS_JSON')

# Файл для хранения ID уже обработанных записей
PROCESSED_IDS_FILE = 'processed_ids.txt'
API_BASE_URL = "https://api.elevenlabs.io/v1"

# --- НОВАЯ ФУНКЦИЯ для получения Voice ID агента ---
def get_voice_id_for_agent(agent_id):
    """Получает voice_id, используемый конкретным агентом."""
    if not agent_id:
        print("Ошибка: ID агента не указан в переменных окружения (ELEVENLABS_AGENT_ID).")
        return None
        
    url = f"{API_BASE_URL}/agents/{agent_id}"
    headers = {"xi-api-key": ELEVENLABS_API_KEY}
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        agent_details = response.json()
        voice_id = agent_details.get('voice_id')
        if voice_id:
            print(f"Агент {agent_id} использует voice_id: {voice_id}")
            return voice_id
        else:
            print(f"Ошибка: Не удалось найти voice_id для агента {agent_id}")
            return None
    except requests.RequestException as e:
        print(f"Ошибка при получении данных агента {agent_id}: {e}")
        return None

def get_google_services():
    """Аутентификация в Google и получение сервисов Docs и Drive."""
    try:
        creds_json = json.loads(GOOGLE_CREDENTIALS_JSON_STR)
        creds = Credentials.from_service_account_info(
            creds_json,
            scopes=[
                'https://www.googleapis.com/auth/documents',
                'https://www.googleapis.com/auth/drive'
            ]
        )
        docs_service = build('docs', 'v1', credentials=creds)
        drive_service = build('drive', 'v3', credentials=creds)
        return docs_service, drive_service
    except Exception as e:
        print(f"Ошибка аутентификации в Google: {e}")
        return None, None

def get_processed_ids():
    """Загружает ID обработанных записей из файла."""
    if not os.path.exists(PROCESSED_IDS_FILE):
        return set()
    with open(PROCESSED_IDS_FILE, 'r') as f:
        return set(line.strip() for line in f)

def save_processed_id(history_id):
    """Сохраняет ID обработанной записи в файл."""
    with open(PROCESSED_IDS_FILE, 'a') as f:
        f.write(history_id + '\n')

def get_elevenlabs_history():
    """Получает историю генераций из ElevenLabs."""
    url = f"{API_BASE_URL}/history"
    headers = {"xi-api-key": ELEVENLABS_API_KEY}
    params = {"page_size": 100}
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json().get('history', [])
    except requests.RequestException as e:
        print(f"Ошибка при запросе к ElevenLabs API: {e}")
        return []

def download_audio(history_id):
    """Скачивает аудиофайл по его ID."""
    url = f"{API_BASE_URL}/history/{history_id}/audio"
    headers = {"xi-api-key": ELEVENLABS_API_KEY}
    try:
        response = requests.get(url, headers=headers, stream=True)
        response.raise_for_status()
        
        filename = f"{history_id}.mp3"
        with open(filename, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f"Аудиофайл {filename} успешно скачан.")
        return filename
    except requests.RequestException as e:
        print(f"Ошибка скачивания аудиофайла {history_id}: {e}")
        return None

def upload_to_drive(drive_service, filename, folder_id):
    """Загружает файл на Google Drive."""
    try:
        file_metadata = {
            'name': filename,
            'parents': [folder_id]
        }
        media = MediaFileUpload(filename, mimetype='audio/mpeg', resumable=True)
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink'
        ).execute()
        print(f"Файл {filename} загружен на Google Drive. Ссылка: {file.get('webViewLink')}")
        return file.get('webViewLink')
    except Exception as e:
        print(f"Ошибка загрузки на Google Drive: {e}")
        return None

def append_to_google_doc(docs_service, text, audio_link):
    """Добавляет текст и ссылку в Google Doc."""
    try:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        content = (
            f"--- Запись от {timestamp} ---\n"
            f"Транскрибация:\n{text}\n"
            f"Ссылка на аудиофайл: {audio_link}\n\n"
        )
        requests_body = [
            {'insertText': {'location': {'index': 1}, 'text': content}}
        ]
        docs_service.documents().batchUpdate(
            documentId=GOOGLE_DOC_ID, body={'requests': requests_body}
        ).execute()
        print("Запись успешно добавлена в Google Doc.")
    except Exception as e:
        print(f"Ошибка добавления записи в Google Doc: {e}")

def main():
    print("Начало работы скрипта...")
    
    # 1. Получаем Voice ID целевого агента
    target_voice_id = get_voice_id_for_agent(ELEVENLABS_AGENT_ID)
    if not target_voice_id:
        print("Не удалось получить Voice ID для указанного агента. Завершение работы.")
        sys.exit(1) # Завершаем скрипт с ошибкой

    # 2. Подключаемся к Google
    docs_service, drive_service = get_google_services()
    if not all([docs_service, drive_service]):
        print("Не удалось подключиться к сервисам Google. Завершение работы.")
        sys.exit(1)

    processed_ids = get_processed_ids()
    print(f"Загружено {len(processed_ids)} уже обработанных ID.")
    
    history = get_elevenlabs_history()
    if not history:
        print("История пуста или не удалось ее получить. Завершение работы.")
        return

    new_items_found = 0
    for item in reversed(history):
        history_id = item.get('history_item_id')
        
        # <-- ГЛАВНОЕ ИЗМЕНЕНИЕ: ФИЛЬТРАЦИЯ ПО VOICE ID -->
        if item.get('voice_id') != target_voice_id:
            continue # Пропускаем запись, если она сделана другим голосом

        if history_id and history_id not in processed_ids:
            new_items_found += 1
            print(f"\nНайдена новая запись от целевого агента: {history_id}")
            
            text = item.get('text', 'Текст не найден.')
            audio_filename = download_audio(history_id)
            if not audio_filename:
                continue

            audio_link = upload_to_drive(drive_service, audio_filename, GOOGLE_DRIVE_FOLDER_ID)
            if audio_link:
                append_to_google_doc(docs_service, text, audio_link)
            
            save_processed_id(history_id)
            os.remove(audio_filename)
            print(f"Временный файл {audio_filename} удален.")

    if new_items_found == 0:
        print("Новых записей от целевого агента не найдено.")
    
    print("Работа скрипта завершена.")

if __name__ == '__main__':
    main()
