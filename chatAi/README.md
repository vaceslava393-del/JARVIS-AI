# JARVIS AI

JARVIS AI — локальный веб-интерфейс и Android-клиент для общения с моделью Groq, голосового ввода и озвучивания ответов. Python-сервер работает на компьютере, а браузер и APK подключаются к нему по HTTP.

Важно: APK не содержит Python, Groq API key и `.env`. Во время использования приложения сервер на компьютере должен быть запущен.

## Возможности

- текстовый чат с Groq;
- голосовой ввод и озвучивание ответов;
- поиск и запуск разрешённых приложений Windows;
- поиск видео и фильмов;
- камера компьютера;
- распознавание объектов через OpenCV, Ultralytics и YOLO;
- Android-клиент для локальной сети.

## Структура проекта

```text
JARVIS_AI/
├── JARVIS_AI.py                 # FastAPI-сервер и API
├── .env                         # секреты; не публиковать
├── requirements.txt             # основные библиотеки
├── static/                      # HTML, CSS, JavaScript и фон
└── android/                     # Android Studio / Gradle проект
```

## 1. Требования

### Python-сервер

- Windows 10/11;
- 64-битный Python 3.11 или новее;
- интернет для Groq и первой установки библиотек;
- Groq API key;
- для камеры — веб-камера и 1–2 ГБ свободного места.

Python 3.11–3.13 — наиболее совместимый вариант. Проект также проверялся с Python 3.14.

Проверка:

```powershell
py --version
```

Если команда не найдена, установите 64-битный Python и включите `Add Python to PATH`.

### Android-сборка

- Android Studio;
- Android SDK Platform 36;
- Android Build-Tools 35+;
- JDK 17+ или встроенный JBR Android Studio;
- интернет при первой сборке Gradle;
- телефон с Android 7.0 / API 24 или новее.

## 2. Установка библиотек

Выполняйте команды из папки, где лежит `JARVIS_AI.py`.

Установите все зависимости одной командой:

```powershell
pip install -r requirements.txt
```


```powershell
pip install -r requirements-vision.txt
```
Виртуальное окружение создавать не нужно. `requirements.txt` включает основные библиотеки, камеру, YOLO и PyTorch.

Основные библиотеки:

- `fastapi` — HTTP API;
- `uvicorn` — веб-сервер;
- `httpx` — запросы к Groq;
- `python-dotenv` — чтение `.env`;
- `opencv-python` — камера и обработка кадров;
- `ultralytics` — YOLO;
- `torch` и `torchvision` — устанавливаются вместе с Ultralytics.

PyTorch имеет большой размер, поэтому установка полного набора может занять несколько минут.

Проверка:

```powershell
py -c "import fastapi,uvicorn,httpx,dotenv; print('Core: OK')"
py -c "import cv2,torch; from ultralytics import YOLO; print('Vision: OK')"
```

## 3. Настройка `.env`

Создайте `.env` в корне проекта:

```env
GROQ_API_KEY=ваш_ключ_Groq
GROQ_MODEL=openai/gpt-oss-120b
JARVIS_USERNAME=jarvis
JARVIS_PASSWORD=сложный_пароль_минимум_16_символов
JARVIS_HOST=0.0.0.0
JARVIS_PORT=9014
```

| Переменная | Назначение |
|---|---|
| `GROQ_API_KEY` | секретный ключ Groq API |
| `GROQ_MODEL` | используемая модель Groq |
| `JARVIS_USERNAME` | логин браузера и APK |
| `JARVIS_PASSWORD` | пароль длиной минимум 16 символов |
| `JARVIS_HOST` | `0.0.0.0` разрешает телефон; `127.0.0.1` — только ПК |
| `JARVIS_PORT` | порт, по умолчанию `9014` |

Никогда не добавляйте настоящий `.env` в Git, APK или публичный архив. Для передачи создайте `.env.example` без секретов. Если ключ или пароль были опубликованы, замените их.

## 4. Запуск сервера

Откройте PowerShell в папке `chatAi` и выполните:

```powershell
py JARVIS_AI.py
```

Откройте:

```text
http://127.0.0.1:9014
```

Введите логин и пароль из `.env`. Для остановки нажмите `Ctrl+C`.

Проверка порта:

```powershell
Test-NetConnection 127.0.0.1 -Port 9014
```

Ожидается `TcpTestSucceeded : True`. Логи фонового запуска: `server.log` и `server-error.log`.

## 5. Подключение телефона

1. Задайте в `.env` `JARVIS_HOST=0.0.0.0`.
2. Запустите сервер.
3. Подключите компьютер и телефон к одной Wi-Fi сети.
4. Выполните `ipconfig` и найдите IPv4 компьютера, например `192.168.8.60`.
5. В APK нажмите `СЕРВЕР`.
6. Введите `http://IP_КОМПЬЮТЕРА:9014`, например:

```text
http://192.168.8.60:9014
```

7. Введите логин и пароль из `.env`.

Для Android Emulator используйте `http://10.0.2.2:9014`.

Если Windows спросит о брандмауэре, разрешите Python доступ для частной сети. Не открывайте порт `9014` в публичный интернет без HTTPS и reverse proxy.

## 6. Голос на Android

Голосовой ввод использует системную службу распознавания Android, часто Google Speech Services. Нужны разрешение на микрофон, TTS-движок и русский голос.

Проверка:

1. Откройте `Чат`.
2. Включите `Голос: ВКЛ`.
3. Нажмите `Проверить голос`.
4. Нажмите `Слушать` и произнесите фразу.
5. `Стоп` прекращает микрофон и озвучивание.

Если звука нет, откройте настройки Android → язык и ввод → синтез речи, выберите системный движок и установите русский голос. Также проверьте мультимедийную громкость.

## 7. Сборка APK через Gradle

```powershell
cd android
$env:JAVA_HOME="C:\Program Files\Android\Android Studio\jbr"
.\gradlew.bat assembleDebug
```

Готовый APK:

```text
android\app\build\outputs\apk\debug\app-debug.apk
```

Полная пересборка:

```powershell
.\gradlew.bat clean assembleDebug
```

## 8. Сборка через Android Studio

1. Откройте Android Studio.
2. Выберите `Open` и папку `JARVIS_AI/android`.
3. Дождитесь Gradle Sync.
4. Выберите `Build` → `Build APK(s)`.
5. Нажмите `locate` после завершения.

Debug APK подходит для тестов. Для публикации нужен собственный keystore и release signing config, после чего выполняется `.\gradlew.bat assembleRelease`. Не публикуйте debug APK как официальный релиз.

## 9. Установка APK

1. Передайте `app-debug.apk` на телефон.
2. Разрешите установку из выбранного файлового менеджера.
3. Если обновление не применяется, удалите старое приложение.
4. Установите APK, запустите сервер и задайте его адрес.

Через ADB:

```powershell
adb install -r android\app\build\outputs\apk\debug\app-debug.apk
```

## 10. Проверки проекта

```powershell
py -m py_compile JARVIS_AI.py
node --check static\app.js
node --check static\voice-fixes.js
cd android
.\gradlew.bat assembleDebug
```

Перед передачей проекта:

- удалите `.env` из архива;
- не передавайте `.gradle` и каталоги `build`, если они не нужны;
- проверьте исходники на API key и пароли;
- приложите `.env.example`;
- оставьте `README.md` и `requirements.txt`.

## 11. Частые проблемы

### Чёрный экран или нет подключения в APK

- запустите сервер командой `py JARVIS_AI.py`;
- проверьте общую Wi-Fi сеть;
- нажмите `СЕРВЕР` и исправьте IP;
- задайте `JARVIS_HOST=0.0.0.0`;
- разрешите Python в брандмауэре.

### `ModuleNotFoundError`

```powershell
pip install -r requirements.txt
```

### Порт 9014 занят

```powershell
Get-NetTCPConnection -LocalPort 9014
```

Остановите старый сервер или измените `JARVIS_PORT`, а затем порт в адресе APK.

### Телефон не подключается

Сначала проверьте `http://127.0.0.1:9014` на ПК, затем `http://IP_КОМПЬЮТЕРА:9014`. Если первый адрес работает, а второй нет, причина обычно в `JARVIS_HOST`, брандмауэре или изоляции клиентов Wi-Fi.

### Нет голоса

- включите голос;
- нажмите `Проверить голос`;
- установите русский TTS-голос;
- увеличьте мультимедийную громкость;
- после обновления удалите старый APK и установите новый.

### Камера не работает

- закройте другие программы с камерой;
- выполните `pip install -r requirements.txt`;
- разрешите камеру в Windows;
- первая загрузка модели YOLO может требовать интернет.

### Ошибка Groq

- проверьте ключ и интернет;
- проверьте доступность выбранной модели;
- после изменения `.env` перезапустите сервер.

## 12. Инструкция для другого ИИ или разработчика

1. Прочитать README и структуру проекта.
2. Не выводить секреты из `.env`.
3. Выполнить `pip install -r requirements.txt`.
4. Проверить Python и JavaScript.
5. Запустить сервер и проверить HTTP 200 с Basic Auth.
6. Никогда не встраивать `.env` или Groq key в APK.
7. Для Android использовать `android/` и Gradle Wrapper.
8. После изменения Java пересобрать APK.
9. Изменения Python требуют перезапуска сервера. Изменения `static/` обычно не требуют сборки APK, потому что интерфейс загружается с сервера.
10. Сообщить пользователю путь к APK и результаты проверок.

## Безопасность

JARVIS запускает приложения Windows, использует камеру и внешний API. Используйте его только в доверенной локальной сети. Basic Auth без HTTPS не шифрует трафик и не подходит для публичного сервера.
