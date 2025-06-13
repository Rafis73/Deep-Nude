import os
import requests
import json
from datetime import datetime

# Библиотеки Google
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# --- КОНФИГУРАЦИЯ ---
# Эти значения мы будем получать из GitHub Secrets (переменных окружения)
ELEVENLABS_API_KEY = os.getenv('ELEVENLABS_API_KEY')
GOOGLE_DOC_ID = os.getenv('GOOGLE_DOC_ID')
GOOGLE_DRIVE_FOLDER_ID = os.getenv('GOOGLE_DRIVE_FOLDER_ID')
# JSON-ключ сервисного аккаунта будет передан как строка
GOOGLE_CREDENTIALS_JSON_STR = os.getenv('GOOGLE_CREDENTIALS_JSON')

# Файл для хранения ID уже обработанных записей
PROCESSED_IDS_FILE = 'processed_ids.txt'

# --- ЛОГИКА ---

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
    url = "https://api.elevenlabs.io/v1/history"
    headers = {"xi-api-key": ELEVENLABS_API_KEY}
    # Запрашиваем побольше записей, чтобы не пропустить ничего за 5 минут
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
    url = f"https://api.elevenlabs.io/v1/history/{history_id}/audio"
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
        # Форматируем запись
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        content = (
            f"--- Запись от {timestamp} ---\n"
            f"Транскрибация:\n{text}\n"
            f"Ссылка на аудиофайл: {audio_link}\n\n"
        )
        
        # Вставляем текст в начало документа
        requests_body = [
            {
                'insertText': {
                    'location': {
                        'index': 1,
                    },
                    'text': content
                }
            }
        ]
        docs_service.documents().batchUpdate(
            documentId=GOOGLE_DOC_ID, body={'requests': requests_body}
        ).execute()
        print("Запись успешно добавлена в Google Doc.")
    except Exception as e:
        print(f"Ошибка добавления записи в Google Doc: {e}")

def main():
    print("Начало работы скрипта...")
    docs_service, drive_service = get_google_services()
    if not all([docs_service, drive_service]):
        print("Не удалось подключиться к сервисам Google. Завершение работы.")
        return

    processed_ids = get_processed_ids()
    print(f"Загружено {len(processed_ids)} уже обработанных ID.")
    
    history = get_elevenlabs_history()
    if not history:
        print("История пуста или не удалось ее получить. Завершение работы.")
        return

    # API возвращает историю от новых к старым, поэтому обрабатываем в обратном порядке
    new_items_found = 0
    for item in reversed(history):
        history_id = item.get('history_item_id')
        if history_id and history_id not in processed_ids:
            new_items_found += 1
            print(f"\nНайдена новая запись: {history_id}")
            
            # 1. Получаем текст
            text = item.get('text', 'Текст не найден.')
            
            # 2. Скачиваем аудио
            audio_filename = download_audio(history_id)
            if not audio_filename:
                continue

            # 3. Загружаем аудио на Google Drive
            audio_link = upload_to_drive(drive_service, audio_filename, GOOGLE_DRIVE_FOLDER_ID)
            
            # 4. Добавляем запись в Google Doc
            if audio_link:
                append_to_google_doc(docs_service, text, audio_link)
            
            # 5. Отмечаем как обработанное
            save_processed_id(history_id)

            # 6. Удаляем временный аудиофайл
            os.remove(audio_filename)
            print(f"Временный файл {audio_filename} удален.")

    if new_items_found == 0:
        print("Новых записей не найдено.")
    
    print("Работа скрипта завершена.")

if __name__ == '__main__':
    main()
