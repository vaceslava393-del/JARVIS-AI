import os
import re
import json
import time
import base64
import secrets
import threading
import subprocess
import webbrowser
from pathlib import Path
from urllib.parse import quote_plus, urlparse
from contextlib import asynccontextmanager
from difflib import SequenceMatcher

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# ============================================================
# JARVIS AI 4.0 — Windows + Internet + voice + proactive chat
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)
load_dotenv(BASE_DIR / ".env")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()
GROQ_URL = os.getenv(
    "GROQ_URL",
    "https://api.groq.com/openai/v1/chat/completions"
).strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_CODEX_MODEL = os.getenv("OPENAI_CODEX_MODEL", "").strip()
OPENAI_URL = "https://api.openai.com/v1/responses"

async def ask_codex(task: str):
    if not OPENAI_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Не задан OPENAI_API_KEY"
        )

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            OPENAI_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": OPENAI_CODEX_MODEL,
                "input": task
            }
        )

        response.raise_for_status()
        data = response.json()

        return data.get("output_text", "Нет ответа от Codex")

HOST = os.getenv("JARVIS_HOST", "127.0.0.1")
PORT = int(os.getenv("JARVIS_PORT", "9014"))

JANYM_USERNAME = os.getenv("JARVIS_USERNAME", "").strip()
JANYM_PASSWORD = os.getenv("JARVIS_PASSWORD", "")

MAX_REQUEST_BYTES = 256 * 1024
AUTH_FAILURE_LIMIT = 5
AUTH_FAILURE_WINDOW = 300
AUTH_LOCKOUT_SECONDS = 900
RATE_WINDOW_SECONDS = 60

security_lock = threading.Lock()
auth_failures = {}
rate_buckets = {}

# Never execute arbitrary shell commands from AI.
SAFE_APPS = {
    "калькулятор": ["calc.exe"],
    "calculator": ["calc.exe"],
    "блокнот": ["notepad.exe"],
    "notepad": ["notepad.exe"],
    "paint": ["mspaint.exe"],
    "паинт": ["mspaint.exe"],
    "пейнт": ["mspaint.exe"],
    "проводник": ["explorer.exe"],
    "explorer": ["explorer.exe"],
    "диспетчер задач": ["taskmgr.exe"],
    "task manager": ["taskmgr.exe"],
    "командная строка": ["cmd.exe"],
    "cmd": ["cmd.exe"],
}

ALLOWED_FILE_ROOTS = [
    Path.home() / "Desktop",
    Path.home() / "Documents",
    Path.home() / "Downloads",
    Path.home() / "OneDrive" / "Desktop",
    Path.home() / "OneDrive" / "Documents",
]

TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".html", ".css", ".json",
    ".xml", ".ini", ".cfg", ".log", ".csv", ".yaml", ".yml"
}
SKIP_DIRS = {
    "node_modules", ".git", ".venv", "venv", "__pycache__",
    "AppData", "Windows", "Program Files", "Program Files (x86)"
}

def _trim_times(values, cutoff):
    return [x for x in values if x > cutoff]

def _client_ip(request: Request):
    # Do not trust X-Forwarded-For from arbitrary clients.
    return request.client.host if request.client else "unknown"

def _rate_limit(ip: str, path: str):
    now = time.monotonic()
    if path == "/api/chat":
        bucket, limit = "chat", 12
    elif path == "/api/proactive":
        bucket, limit = "proactive", 5
    else:
        bucket, limit = "general", 90

    key = (ip, bucket)
    with security_lock:
        for old_key, stamps in list(rate_buckets.items()):
            if not stamps or now - stamps[-1] > RATE_WINDOW_SECONDS * 2:
                rate_buckets.pop(old_key, None)
        stamps = _trim_times(rate_buckets.get(key, []), now - RATE_WINDOW_SECONDS)
        if len(stamps) >= limit:
            rate_buckets[key] = stamps
            return max(1, int(RATE_WINDOW_SECONDS - (now - stamps[0])))
        stamps.append(now)
        rate_buckets[key] = stamps
    return 0

def _record_auth_failure(ip: str):
    now = time.monotonic()
    with security_lock:
        state = auth_failures.setdefault(
            ip, {"times": [], "blocked_until": 0}
        )
        state["times"] = _trim_times(
            state["times"], now - AUTH_FAILURE_WINDOW
        )
        state["times"].append(now)
        if len(state["times"]) >= AUTH_FAILURE_LIMIT:
            state["blocked_until"] = now + AUTH_LOCKOUT_SECONDS
            state["times"] = []

def _is_blocked(ip: str):
    with security_lock:
        state = auth_failures.get(ip)
        return bool(state and state.get("blocked_until", 0) > time.monotonic())

@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"JANYM AI 4.0: http://{HOST}:{PORT}")
    yield
    print("JANYM stopped.")

app = FastAPI(
    title="JANYM AI 4.0",
    version="4.0",
    lifespan=lifespan
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.middleware("http")
async def security_middleware(request: Request, call_next):
    ip = _client_ip(request)

    def secure(response):
        # Защитные HTTP-заголовки
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(self), microphone=(self), geolocation=()"
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "connect-src 'self' https:; "
            "media-src 'self' blob:; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "frame-ancestors 'none'"
        )

        # Удаляем заголовок Server безопасным способом
        if "Server" in response.headers:
            del response.headers["Server"]

        return response

    if not JANYM_USERNAME or len(JANYM_PASSWORD) < 16:
        return secure(JSONResponse(
            {"detail": "Настрой .env: JARVIS_USERNAME и JARVIS_PASSWORD (минимум 16 символов)."},
            status_code=503
        ))

    if _is_blocked(ip):
        return secure(JSONResponse(
            {"detail": "Слишком много неверных паролей. Попробуй позже."},
            status_code=429,
            headers={"Retry-After": str(AUTH_LOCKOUT_SECONDS)}
        ))

    auth = request.headers.get("authorization", "")
    valid = False
    if auth.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth[6:], validate=True).decode("utf-8")
            username, password = decoded.split(":", 1)
            valid = (
                secrets.compare_digest(username, JANYM_USERNAME )
                and secrets.compare_digest(password, JANYM_PASSWORD)
            )
        except (ValueError, UnicodeDecodeError):
            valid = False

    if not valid:
        _record_auth_failure(ip)
        return secure(PlainTextResponse(
            "Требуется вход в JANYM.",
            status_code=401,
            headers={
                "WWW-Authenticate": 'Basic realm="JANYM AI", charset="UTF-8"',
                "Cache-Control": "no-store"
            }
        ))

    origin = request.headers.get("origin")
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and origin:
        from urllib.parse import urlsplit
        origin_host = (urlsplit(origin).netloc or "").casefold()
        request_host = request.headers.get("host", "").casefold()
        if not origin_host or origin_host != request_host:
            return secure(JSONResponse(
                {"detail": "Запрос с другого сайта заблокирован."},
                status_code=403
            ))

    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) > MAX_REQUEST_BYTES:
                return secure(JSONResponse(
                    {"detail": "Слишком большой запрос."}, status_code=413
                ))
        except ValueError:
            return secure(JSONResponse(
                {"detail": "Некорректный Content-Length."}, status_code=400
            ))

    wait = _rate_limit(ip, request.url.path)
    if wait:
        return secure(JSONResponse(
            {"detail": "Слишком много запросов. Подожди немного."},
            status_code=429,
            headers={"Retry-After": str(wait)}
        ))

    try:
        return secure(await call_next(request))
    except Exception:
        return secure(JSONResponse(
            {"detail": "Внутренняя ошибка сервера."}, status_code=500
        ))

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=6000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=20)

def clean_ai_text(value: str) -> str:
    """Convert common Markdown formatting into plain readable text."""
    if not isinstance(value, str):
        return ""
    text = re.sub(r"```(?:[a-zA-Z0-9_-]+)?\s*([\s\S]*?)```", r"\1", value)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "• ", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*(\d+)\.\s+", r"\1) ", text, flags=re.MULTILINE)
    text = re.sub(r"(\*\*|__)(.*?)\1", r"\2", text)
    text = re.sub(r"(?<!\w)[*_~](.*?)[*_~](?!\w)", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def _inside_allowed_root(path: Path):
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for root in ALLOWED_FILE_ROOTS:
        try:
            resolved.relative_to(root.resolve())
            return True
        except (ValueError, OSError):
            continue
    return False

def search_local_files(query: str, limit: int = 10):
    """Search filenames in Desktop/Documents/Downloads, not the whole disk."""
    query = re.sub(r"\s+", " ", query).strip().casefold()
    if not query:
        return {"matches": []}

    matches = []
    visited = 0
    for root in ALLOWED_FILE_ROOTS:
        if not root.is_dir():
            continue
        for current, dirs, files in os.walk(root):
            dirs[:] = [
                d for d in dirs
                if d not in SKIP_DIRS and not d.startswith(".")
            ]
            for filename in files:
                visited += 1
                if visited > 30000:
                    break
                path = Path(current) / filename
                name = path.stem.casefold()
                full = filename.casefold()
                score = 1.0 if query == name else (
                    0.9 if query in full else
                    SequenceMatcher(None, query, name).ratio()
                )
                if score >= 0.48:
                    try:
                        matches.append({
                            "name": filename,
                            "path": str(path),
                            "type": path.suffix.lower(),
                            "score": round(score, 2)
                        })
                    except OSError:
                        pass
            if visited > 30000:
                break

    matches.sort(key=lambda x: (-x["score"], x["name"].casefold()))
    return {"matches": matches[:limit]}

def read_local_text_file(path_text: str):
    path = Path(path_text)
    if not _inside_allowed_root(path):
        raise ValueError("Разрешено читать только файлы в Desktop, Documents и Downloads.")
    if not path.is_file() or path.suffix.lower() not in TEXT_EXTENSIONS:
        raise ValueError("Этот тип файла нельзя безопасно прочитать как текст.")
    if path.stat().st_size > 300_000:
        raise ValueError("Файл слишком большой для чтения.")
    return path.read_text(encoding="utf-8", errors="replace")[:20000]

def discover_app_shortcuts():
    """Find Start Menu/Desktop shortcuts; only launch a selected shortcut."""
    folders = [
        Path(os.getenv("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        Path(os.getenv("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        Path.home() / "Desktop",
        Path.home() / "OneDrive" / "Desktop"
    ]
    items = []
    for folder in folders:
        if not folder.is_dir():
            continue
        try:
            for path in folder.rglob("*"):
                if path.is_file() and path.suffix.lower() in (".lnk", ".url"):
                    label = path.stem
                    if any(x in label.casefold() for x in ("uninstall", "удалить", "readme")):
                        continue
                    items.append({"label": label, "path": str(path)})
                    if len(items) >= 5000:
                        return items
        except OSError:
            continue
    return items

def search_apps(query: str, limit: int = 8):
    query = query.strip().casefold()
    if not query:
        return []
    results = []
    for item in discover_app_shortcuts():
        label = item["label"].casefold()
        score = 1.0 if label == query else (
            0.9 if label.startswith(query) else
            0.8 if query in label else
            SequenceMatcher(None, query, label).ratio()
        )
        if score >= 0.55:
            results.append({**item, "score": round(score, 2)})
    results.sort(key=lambda x: (-x["score"], x["label"].casefold()))
    return results[:limit]

def open_app(app_name: str):
    name = app_name.strip().casefold()
    if name in SAFE_APPS:
        subprocess.Popen(SAFE_APPS[name], shell=False)
        return f"Запускаю {app_name}."

    matches = search_apps(app_name, limit=5)
    if not matches:
        return "Не нашёл такую программу в меню Пуск или на рабочем столе."
    if len(matches) > 1 and matches[0]["score"] - matches[1]["score"] < 0.12:
        names = ", ".join(x["label"] for x in matches[:5])
        return f"Нашёл несколько похожих программ: {names}. Уточни название."

    selected = matches[0]
    if os.name != "nt":
        raise ValueError("Запуск ярлыков доступен только в Windows.")
    os.startfile(selected["path"])
    return f"Открываю {selected['label']}."

def open_website(target: str):
    target = target.strip()
    if not target:
        raise ValueError("Не указано название сайта.")
    if re.match(r"^https?://", target, re.I):
        url = target
    elif re.match(r"^www\.", target, re.I):
        url = "https://" + target
    elif re.match(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/.*)?$", target, re.I):
        url = "https://" + target
    else:
        # Search by site/topic rather than interpreting natural language as a command.
        url = "https://www.google.com/search?q=" + quote_plus(target)
    parsed = urlparse(url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Недопустимый адрес сайта.")
    webbrowser.open(url, new=2)
    return f"Открываю в браузере: {url}"

async def search_web(query: str, limit: int = 6):
    """Retrieve public web search snippets using DuckDuckGo HTML."""
    query = re.sub(r"\s+", " ", query).strip()[:180]
    if not query:
        return {"results": []}
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/124 Safari/537.36"
        )
    }
    async with httpx.AsyncClient(timeout=12, follow_redirects=True, headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()

    from html.parser import HTMLParser

    class Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.items = []
            self.current = None
            self.in_title = False
            self.in_snippet = False

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            classes = attrs.get("class", "").split()
            if tag == "a" and "result__a" in classes:
                self.current = {
                    "title": "", "url": attrs.get("href", ""), "snippet": ""
                }
                self.in_title = True
            elif self.current is not None and "result__snippet" in classes:
                self.in_snippet = True

        def handle_data(self, data):
            if self.current is None:
                return
            value = re.sub(r"\s+", " ", data).strip()
            if not value:
                return
            if self.in_title:
                self.current["title"] += (" " if self.current["title"] else "") + value
            elif self.in_snippet:
                self.current["snippet"] += (" " if self.current["snippet"] else "") + value

        def handle_endtag(self, tag):
            if tag == "a" and self.in_title and self.current:
                self.in_title = False
                self.items.append(self.current)
                self.current = None
            if tag in ("div", "span"):
                self.in_snippet = False

    parser = Parser()
    parser.feed(response.text)
    results = []
    for item in parser.items:
        link = item["url"].strip()
        host = (urlparse(link).hostname or "").lower()
        if not link.startswith(("http://", "https://")):
            continue
        if not host or "duckduckgo.com" in host:
            continue
        results.append({
            "title": item["title"][:180],
            "url": link,
            "snippet": item["snippet"][:350]
        })
        if len(results) >= limit:
            break
    return {"results": results}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Найти актуальную информацию в открытом интернете. Используй для вопросов о фактах, новостях, сайтах и информации.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Поисковый запрос"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Найти файлы по имени в папках Рабочий стол, Документы и Загрузки пользователя.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Название или часть названия файла"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_text_file",
            "description": "Прочитать найденный текстовый файл из разрешённых пользовательских папок. Сначала найди файл через search_files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Полный путь из результата поиска"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_apps",
            "description": "Найти установленную программу по ярлыкам меню Пуск и рабочего стола.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Название программы"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Открыть программу Windows по названию. Не запускай неизвестные команды и не подбирай похожее приложение при неоднозначности.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Название программы"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "open_website",
            "description": "Открыть сайт или поиск в браузере по названию.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Адрес сайта или название"}
                },
                "required": ["target"]
            }
        }
    }
]

SYSTEM_PROMPT = """
Ты Жаным — спокойный голосовой помощник для пожилого человека и его защита от мошенников.
Отвечай на русском, если пользователь пишет по-русски.
Пиши простым обычным текстом без Markdown, заголовков с решётками,
звёздочек, таблиц и обратных кавычек. Используй короткие предложения и понятные слова.
Сначала дай самый важный безопасный совет, затем не больше пяти последовательных шагов.
Не запугивай и не обвиняй пользователя. Никогда не проси пароль, код из СМС, PIN,
CVV, полный номер карты или данные для входа. Не советуй переводить деньги на
«безопасный счёт», устанавливать программы по просьбе звонящего или переходить
по подозрительным ссылкам. Если пользователь уже сообщил банковские данные,
посоветуй немедленно завершить разговор, самостоятельно позвонить в банк по
номеру на карте, заблокировать карту и обратиться в полицию. Если ситуация
неясна, предложи ничего не делать под давлением и позвать близкого человека.
Не утверждай наверняка, кто звонит, если доказательств нет.
"""

async def execute_tool(name: str, arguments: dict):
    if name == "search_web":
        return await search_web(str(arguments.get("query", "")))
    if name == "search_files":
        return search_local_files(str(arguments.get("query", "")))
    if name == "read_text_file":
        try:
            return {"content": read_local_text_file(str(arguments.get("path", "")))}
        except (OSError, ValueError) as exc:
            return {"error": str(exc)}
    if name == "search_apps":
        return {"matches": search_apps(str(arguments.get("query", "")))}
    if name == "open_app":
        try:
            return {"result": open_app(str(arguments.get("name", "")))}
        except (OSError, ValueError) as exc:
            return {"error": str(exc)}
    if name == "open_website":
        try:
            return {"result": open_website(str(arguments.get("target", "")))}
        except (OSError, ValueError) as exc:
            return {"error": str(exc)}
    return {"error": "Неизвестный инструмент."}

async def ask_groq(user_message: str, history: list):
    if not GROQ_API_KEY:
        raise HTTPException(status_code=503, detail="GROQ_API_KEY не задан в .env")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for item in history[-12:]:
        role = item.get("role")
        content = item.get("content")
        if role in ("user", "assistant") and isinstance(content, str):
            messages.append({"role": role, "content": content[:6000]})
    messages.append({"role": "user", "content": user_message})

    try:
        async with httpx.AsyncClient(timeout=90) as client:
            for _ in range(4):
                response = await client.post(
                    GROQ_URL,
                    headers={
                        "Authorization": f"Bearer {GROQ_API_KEY}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": GROQ_MODEL,
                        "messages": messages,
                        "tools": TOOLS,
                        "tool_choice": "auto",
                        "temperature": 0.4,
                        "max_tokens": 1400
                    }
                )
                if response.status_code >= 400:
                    try:
                        err = response.json().get("error", {}).get("message")
                    except Exception:
                        err = None
                    raise HTTPException(
                        status_code=502,
                        detail=(err or "Ошибка Groq API.")[:800]
                    )

                result = response.json()
                message = result["choices"][0]["message"]
                tool_calls = message.get("tool_calls") or []

                if not tool_calls:
                    return clean_ai_text(message.get("content") or "")

                messages.append(message)
                for call in tool_calls:
                    function = call.get("function", {})
                    tool_name = function.get("name", "")
                    try:
                        args = json.loads(function.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    tool_result = await execute_tool(tool_name, args)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": json.dumps(
                            tool_result, ensure_ascii=False
                        )[:12000]
                    })

            return "Я выполнил доступные поисковые действия, но не смог завершить ответ. Попробуй уточнить запрос."

    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Groq не ответил вовремя.")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="Нет подключения к Groq API.")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Не удалось обработать ответ Groq API.")

@app.post("/api/chat")
async def chat(data: ChatRequest):
    reply = await ask_groq(data.message.strip(), data.history)
    return {"reply": reply or "Я здесь. Чем могу помочь?"}

@app.post("/api/proactive")
async def proactive():
    prompt = (
        "Начни короткий естественный разговор с пользователем. "
        "Скажи что-нибудь уместное: дружелюбное приветствие, интересный факт "
        "или предложение помочь с компьютером. Не повторяй одинаковые фразы. "
        "Не утверждай, что знаешь, чем пользователь занят. Один-два предложения."
    )
    reply = await ask_groq(prompt, [])
    return {"reply": reply or "Я здесь, если понадоблюсь."}

@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>JANYM AI 4.0</title>
<style>
:root{color-scheme:light;--blue:#174f78;--blue-dark:#103b5b;--cream:#f5f1e8;--paper:#fffefb;--ink:#17212b;--green:#176b45;--red:#a52828;--line:#c8d3dc}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:#fff7ef url("/static/assets/3.png") center/cover fixed no-repeat;color:var(--ink);font:20px/1.55 "Segoe UI",Arial,sans-serif;padding:28px}
button,textarea,select,input{font:inherit}
button:focus-visible,textarea:focus-visible,select:focus-visible{outline:4px solid #f3b61f;outline-offset:3px}
.wrap{max-width:960px;margin:auto;padding:24px}
.hero{display:grid;grid-template-columns:1fr 1.08fr;overflow:hidden;background:rgba(255,255,255,.94);border:8px solid rgba(255,255,255,.78);border-radius:38px;margin-bottom:24px;box-shadow:0 10px 28px rgba(84,67,47,.16)}
.hero-copy{display:flex;flex-direction:column;justify-content:center;padding:32px}
.brand{font-size:38px;line-height:1.15;font-weight:850;color:var(--blue-dark);margin:0 0 12px}
.subtitle{font-size:22px;color:#364b58;margin:0 0 18px}
.promise-image{display:block;width:100%;max-width:430px;height:auto;margin-top:2px}
.icon{width:34px;height:34px;flex:0 0 auto;fill:none;stroke:currentColor;stroke-width:2.4;stroke-linecap:round;stroke-linejoin:round}.icon.small{width:25px;height:25px}.icon.large{width:54px;height:54px;stroke-width:2}
.hero-image{width:100%;height:100%;min-height:280px;object-fit:cover;object-position:64% center;display:block}
.panel{background:rgba(255,255,255,.95);border:8px solid rgba(255,255,255,.72);border-radius:36px;padding:28px;box-shadow:0 10px 28px rgba(84,67,47,.15)}
.safety{display:flex;gap:11px;align-items:center;background:#fff4df;border-left:7px solid #d89822;border-radius:12px;padding:13px 16px;margin-bottom:20px;font-weight:750;color:#633b09}
.safety-icon{font-size:28px;line-height:1}
.section-title{margin:0 0 4px;font-size:29px;color:var(--blue-dark)}
.help{margin:0;color:#4c5d68;font-size:19px}
.quick-actions{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:20px 0}
button{min-height:58px;border:0;border-radius:13px;padding:13px 18px;background:var(--blue);color:white;font-weight:750;cursor:pointer;box-shadow:0 3px 0 var(--blue-dark)}
button:hover{background:var(--blue-dark)}button:active{transform:translateY(2px);box-shadow:none}button:disabled{opacity:.55;cursor:wait}
button.secondary{background:white;color:var(--blue-dark);border:2px solid var(--blue);box-shadow:none}
.image-button{min-height:0;padding:0;border:0;background:transparent;box-shadow:none;border-radius:18px;line-height:0;overflow:visible}.image-button:hover{background:transparent;filter:brightness(1.04)}.image-button:active{transform:translateY(3px);box-shadow:none}.image-button img{display:block;width:100%;height:auto}
.messages{height:37vh;min-height:270px;max-height:480px;overflow:auto;display:flex;flex-direction:column;gap:14px;margin:16px 0;padding:6px;scrollbar-width:auto}
.messages:empty{display:none}
.bubble{padding:16px 18px;border-radius:15px;white-space:pre-wrap;overflow-wrap:anywhere;max-width:92%;line-height:1.6}
.bot{background:#edf5fa;border:2px solid #aac7d9;align-self:flex-start}
.user{background:var(--blue);color:white;align-self:flex-end}
.composer{display:grid;grid-template-columns:1fr minmax(190px,28%);gap:12px;align-items:stretch}
textarea{width:100%;min-height:104px;resize:vertical;background:white;color:var(--ink);border:3px solid #6f899b;border-radius:13px;padding:15px;font-size:21px}
.send{align-self:stretch;display:flex;align-items:center}.send img{width:100%}
.voice-box{display:flex;align-items:center;justify-content:space-between;gap:24px;margin-top:18px;padding-top:18px;border-top:2px solid #dce4e9}
#micToggle,#micStop{width:100%}
#micStop{min-height:68px;font-size:21px;background:var(--red);box-shadow:0 3px 0 #6f1717}
.voice-controls{display:flex;flex:0 0 calc((100% - 24px)/3)}.voice-controls button[hidden]{display:none}
#voiceState{font-weight:700;color:#244759}
.input-dock{display:contents}
.mobile-action,.mobile-send,.mobile-mic-icon,.mobile-header,.mobile-input-title,.mobile-nav{display:none}
.sr-only{position:absolute!important;width:1px!important;height:1px!important;padding:0!important;margin:-1px!important;overflow:hidden!important;clip:rect(0,0,0,0)!important;white-space:nowrap!important;border:0!important}
@media(max-width:720px){
  body{font-size:17px;padding:0;background-attachment:scroll;color:#073269}
  .wrap{padding:10px 10px calc(188px + env(safe-area-inset-bottom));overflow-x:hidden}
  .hero{display:none}
  .mobile-header{display:flex;align-items:center;justify-content:center;gap:8px;margin:2px 0 10px;font-size:24px;font-weight:900;color:#082f63}.mobile-header .icon{width:32px;height:32px}
  .panel{padding:0;background:transparent;border:0;border-radius:0;box-shadow:none}
  .safety{display:none}
  .safety .icon{width:34px;height:34px}
  .section-title{font-size:27px;line-height:1.1;margin:0 0 2px;color:#073269}
  .help{font-size:16px;color:#1762a0}
  .quick-actions{grid-template-columns:1fr;gap:8px;margin:12px 0 14px}
  .quick-actions .image-button,.quick-actions .image-button:nth-child(3){position:relative;display:flex;align-items:center;justify-content:flex-start;width:100%;min-height:72px;padding:8px 46px 8px 12px;border:1px solid rgba(255,255,255,.9);border-radius:16px;line-height:1.1;box-shadow:0 4px 10px rgba(56,68,83,.12)}
  .quick-actions .image-button:nth-child(1){background:linear-gradient(100deg,#fff0f0,#ffe3e6)}
  .quick-actions .image-button:nth-child(2){background:linear-gradient(100deg,#e6f3ff,#dcecff)}
  .quick-actions .image-button:nth-child(3){grid-column:auto;background:linear-gradient(100deg,#fff6da,#ffedc0)}
  .quick-actions .image-button::after{content:'›';position:absolute;right:16px;top:50%;transform:translateY(-54%);font-size:38px;font-weight:400;color:#073269}
  .quick-actions .image-button>img{display:none}
  .mobile-action{display:flex;flex-direction:row;align-items:center;gap:12px;color:#073269;font-size:18px;font-weight:850;text-align:left}
  .mobile-action .icon{width:43px;height:43px;padding:9px;border-radius:50%;color:white;stroke-width:2}
  .quick-actions .image-button:nth-child(1) .icon,.quick-actions .image-button:nth-child(2) .icon,.quick-actions .image-button:nth-child(3) .icon{background:#287fd7}
  .messages{height:52vh;min-height:300px;max-height:none;margin:14px 0;padding:3px}
  .bubble{max-width:96%;padding:11px 13px;font-size:17px;line-height:1.45}
  .input-dock{position:fixed;z-index:48;left:14px;right:14px;bottom:calc(78px + env(safe-area-inset-bottom));display:block;margin:0;padding:8px;background:rgba(255,255,255,.98);border:1px solid #d7dfeb;border-radius:16px;box-shadow:0 5px 16px rgba(49,57,65,.16)}
  .mobile-input-title{display:block;margin:0 0 5px;font-size:16px;line-height:1.1;color:#073269}
  .composer{grid-template-columns:minmax(0,1fr) 48px;gap:7px;align-items:stretch}
  textarea{height:54px;min-height:54px;max-height:54px;padding:8px 10px;font-size:16px;border:2px solid #8795af;border-radius:12px;resize:none}
  .send{display:flex;align-items:center;justify-content:center;width:48px;min-height:48px;height:54px;padding:0;justify-self:end;background:#1678cf;border-radius:12px;box-shadow:0 4px 10px rgba(0,65,112,.23);line-height:1}
  .send:hover,.send:focus,.send:active,.send:disabled{background:#1678cf!important;opacity:1;filter:none}
  .send:disabled .mobile-send{opacity:.5}
  .send>img{display:none}.mobile-send{display:flex;align-items:center;justify-content:center;color:white;font-size:0}.mobile-send .icon{width:25px;height:25px}
  .voice-box{position:fixed;z-index:51;left:50%;bottom:calc(8px + env(safe-area-inset-bottom));display:flex;justify-content:center;align-items:center;width:68px;height:68px;margin:0;padding:0;background:transparent;border:0;box-shadow:none;transform:translateX(-50%)}
  .voice-box #voiceState{display:none}
  .voice-controls{display:flex;align-items:center;justify-content:center;flex:0 0 68px;width:68px;height:68px;margin:0}
  #micToggle,#micStop{display:flex;align-items:center;justify-content:center;width:66px;height:66px;min-height:66px;padding:0;border:5px solid white;border-radius:50%;box-shadow:0 6px 15px rgba(0,74,129,.3),0 2px 5px rgba(0,0,0,.16)}
  #micToggle{background:linear-gradient(145deg,#328ee7,#0062b8)}
  #micToggle>img{display:none}.mobile-mic-icon{display:block;width:34px;height:34px}
  #micStop{background:#b92b2b;font-size:0;box-shadow:0 7px 18px rgba(139,24,24,.34),0 2px 5px rgba(0,0,0,.2)}
  #micStop .icon{width:30px;height:30px}
  .voice-controls #micToggle[hidden],.voice-controls #micStop[hidden]{display:none}
}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}

@media(max-width:720px){body{background:linear-gradient(#fff,#eafff5);color:#25193e}.wrap{padding:12px 18px 30px;min-height:100dvh}.mobile-header{color:#281b42;font-size:27px}.section-title,.help,.quick-actions{display:none}.panel{display:flex;flex-direction:column;align-items:center}.voice-stage{display:flex;flex-direction:column;align-items:center;width:100%;padding-top:5vh}.rings{position:relative;width:min(70vw,290px);aspect-ratio:1;border-radius:50%;display:grid;place-items:center;background:radial-gradient(circle,#42e5a1,#b8f7d7 55%,#e7fff1 72%,transparent 73%)}.rings:before,.rings:after{content:'';position:absolute;inset:8%;border:4px solid #77eeb2;border-radius:50%}.rings:after{inset:20%}.voice-stage.active .rings:before{animation:pulse 1.8s infinite}.voice-stage.active .rings:after{animation:pulse 1.8s .6s infinite}@keyframes pulse{to{transform:scale(1.5);opacity:0}}.face{width:45%;z-index:1;filter:drop-shadow(0 5px 8px #00834b88)}.stage-title{font-size:clamp(24px,7vw,32px);font-weight:850;text-align:center;margin:16px 0;color:#241638}.waveform{display:flex;align-items:center;justify-content:center;gap:5px;height:55px}.waveform i{height:12px;width:5px;border-radius:6px;background:#13b861}.voice-stage.active .waveform i{animation:wave .7s ease-in-out infinite alternate;animation-delay:calc(var(--n)*-70ms)}@keyframes wave{to{height:44px}}.stage-hint{max-width:400px;width:100%;padding:17px;margin:15px 0;background:#e8e9f9;border-radius:18px;font-size:18px;text-align:center;line-height:1.4}.messages{width:100%;height:auto;min-height:0;max-height:24vh;margin:8px 0}.input-dock{position:static;display:flex;flex-direction:column;align-items:center;order:3;width:100%;margin:8px 0;padding:0;border:0;background:none;box-shadow:none}.mobile-input-title{display:none}.composer{width:100%;max-width:400px;order:2;margin-top:12px}.voice-box{position:static;transform:none;width:100%;height:auto;order:1}.voice-controls{flex:1;width:100%;height:auto}#micToggle,#micStop{width:160px;height:160px;min-height:160px;border:6px solid white;border-radius:50%;display:flex;flex-direction:column;gap:5px;font-size:18px;line-height:1.1}#micToggle{background:linear-gradient(#38d991,#089d5d)}#micStop{background:linear-gradient(#ff5148,#e9231d)}#micToggle:after{content:'Начать говорить';font-weight:800}#micStop:after{content:'Завершить разговор';font-weight:800}.voice-controls #micToggle[hidden],.voice-controls #micStop[hidden]{display:none}}
@media(prefers-reduced-motion:reduce){.voice-stage.active .rings:before,.voice-stage.active .rings:after,.voice-stage.active .waveform i{animation:none}}

/* The original desktop layout remains unchanged. */
@media(min-width:721px){.voice-stage{display:none!important}}
/* Voice-only mobile interface */
@media(max-width:720px){
  .composer,.mobile-input-title{display:none!important}
  .messages{display:none!important}
  .stage-hint{display:block!important}
  .input-dock{margin-top:18px}
  .voice-stage{padding-top:3vh}
  .voice-stage .face{width:62%;max-width:205px;filter:drop-shadow(0 8px 10px #087f5a55)}
  .voice-stage .rings{isolation:isolate}
  .voice-stage .rings:before,.voice-stage .rings:after{animation:none!important;opacity:.45}
  .voice-stage.speaking .rings:before{animation:pulse 1.5s ease-out infinite!important}
  .voice-stage.speaking .rings:after{animation:pulse 1.5s ease-out .55s infinite!important}
  .voice-stage .waveform i{height:5px;transition:height .12s ease}
  .voice-stage.recording .waveform i{animation:wave .6s ease-in-out infinite alternate;animation-delay:calc(var(--n)*-63ms)}
  .voice-stage.speaking .waveform i{animation:none}
  .stage-hint{font-size:19px}
}
@media(prefers-reduced-motion:reduce){.voice-stage.speaking .rings:before,.voice-stage.speaking .rings:after,.voice-stage.recording .waveform i{animation:none!important}}
/* Mobile viewport fit only; desktop and application logic unchanged. */
@media(max-width:720px){
  html,body{height:100%;overflow:hidden}
  body{height:100dvh;min-height:0;padding:0}
  .wrap{height:100dvh;min-height:0;overflow:hidden;display:flex;flex-direction:column;padding:clamp(6px,1.5dvh,12px) 18px max(8px,env(safe-area-inset-bottom))}
  .mobile-header{flex:0 0 auto;margin:0 0 clamp(2px,1dvh,10px)}
  .panel{flex:1;min-height:0;justify-content:space-evenly;overflow:hidden}
  .voice-stage{flex:0 1 auto;min-height:0;padding-top:0;justify-content:center}
  .voice-stage .rings{width:min(70vw,290px,29dvh);flex-shrink:1}
  .stage-title{font-size:clamp(19px,4.5dvh,32px);margin:clamp(3px,1dvh,12px) 0}
  .waveform{height:clamp(20px,5dvh,55px);flex-shrink:0}
  .stage-hint{font-size:clamp(14px,2.25dvh,19px);padding:clamp(8px,1.5dvh,17px);margin:clamp(4px,1dvh,12px) 0;line-height:1.3}
  .input-dock{flex:0 0 auto;margin:0;padding:0}
  .voice-box{margin:0;padding:0}
  #micToggle,#micStop{width:clamp(88px,19dvh,160px);height:clamp(88px,19dvh,160px);min-height:0;font-size:clamp(12px,2dvh,18px)}
}
</style>
</head>
<body>
<svg class="sr-only" aria-hidden="true">
  <symbol id="i-shield" viewBox="0 0 24 24"><path d="M12 3 20 6v5c0 5.2-3.3 8.5-8 10-4.7-1.5-8-4.8-8-10V6l8-3Z"/><path d="m8.5 12 2.2 2.2 4.8-5"/></symbol>
  <symbol id="i-phone" viewBox="0 0 24 24"><path d="M7 3h3l1.4 4.2-2 1.4a15 15 0 0 0 6 6l1.4-2L21 14v3c0 2.2-1.8 4-4 4C9.3 21 3 14.7 3 7c0-2.2 1.8-4 4-4Z"/><path d="M17 3v5M17 11v.1"/></symbol>
  <symbol id="i-message" viewBox="0 0 24 24"><path d="M4 4h16v12H9l-5 4V4Z"/><path d="m8 10 2.2 2.2L16 7"/></symbol>
  <symbol id="i-lock" viewBox="0 0 24 24"><rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/><path d="M20 3v4M20 9v.1"/></symbol>
  <symbol id="i-send" viewBox="0 0 24 24"><path d="m3 4 18 8-18 8 3-8-3-8Z"/><path d="M6 12h15"/></symbol>
  <symbol id="i-mic" viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M9 21h6"/></symbol>
  <symbol id="i-stop" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><rect x="8" y="8" width="8" height="8" rx="1"/></symbol>
  <symbol id="i-home" viewBox="0 0 24 24"><path d="m3 11 9-8 9 8v10h-6v-6H9v6H3V11Z"/></symbol>
  <symbol id="i-help" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M9.8 9a2.4 2.4 0 1 1 3.5 2.1c-.9.5-1.3 1-1.3 2M12 17v.1"/></symbol>
  <symbol id="i-settings" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6 1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/></symbol>
</svg>
<div class="wrap">
<header class="hero">
  <div class="hero-copy">
    <h1 class="brand">Жаным рядом</h1>
    <p class="subtitle">Расскажите, что случилось. Я спокойно подскажу, что делать.</p>
    <img class="promise-image" src="/static/assets/buttons/safe-help.png" alt="Бережная помощь от мошенников">
  </div>
  <img class="hero-image" src="/static/janym-grandmother.png" alt="Добрая пожилая женщина уверенно пользуется телефоном с помощью Жаным">
</header>

<div class="mobile-header"><svg class="icon" aria-hidden="true"><use href="#i-shield"/></svg><span>Жаным рядом</span></div>

<main class="panel">
  <div class="safety" role="note">
    <svg class="icon" aria-hidden="true"><use href="#i-shield"/></svg>
    <div>Никому не называйте код из СМС — даже сотруднику банка.</div>
  </div>
  <h2 class="section-title">Что произошло?</h2>
  <p class="help">Просто нажмите одну большую кнопку.</p>
  <div class="quick-actions" aria-label="Быстрая помощь">
    <button class="image-button" type="button" data-prompt="Мне звонит незнакомый человек и просит назвать данные или перевести деньги. Помоги понять, мошенник ли это и что делать прямо сейчас."><img src="/static/assets/buttons/call.png" alt="Мне звонит незнакомец"><span class="mobile-action"><svg class="icon" aria-hidden="true"><use href="#i-phone"/></svg><span>Незнакомый звонок</span></span></button>
    <button class="image-button" type="button" data-prompt="Я получил подозрительное сообщение. Объясни, как безопасно его проверить и чего нельзя делать."><img src="/static/assets/buttons/message.png" alt="Проверить сообщение"><span class="mobile-action"><svg class="icon" aria-hidden="true"><use href="#i-message"/></svg><span>Проверить сообщение</span></span></button>
    <button class="image-button" type="button" data-prompt="Я уже сообщил код, пароль или данные карты незнакомому человеку. Дай короткие срочные шаги, что делать сейчас."><img src="/static/assets/buttons/disclosed.png" alt="Я уже сообщил данные"><span class="mobile-action"><svg class="icon" aria-hidden="true"><use href="#i-lock"/></svg><span>Сообщил данные</span></span></button>
  </div>
  <div class="voice-stage" id="voiceStage"><div class="rings"><svg class="face" viewBox="0 0 220 220" role="img" aria-label="Добрый нарисованный внук"><defs><linearGradient id="shirt" x2="1" y2="1"><stop stop-color="#4b9fe8"/><stop offset="1" stop-color="#2166b8"/></linearGradient></defs><ellipse cx="110" cy="202" rx="70" ry="12" fill="#167b5a" opacity=".14"/><path d="M37 207q2-57 73-57t73 57" fill="url(#shirt)"/><path d="M92 146v22q18 19 36 0v-22" fill="#f5b88c"/><ellipse cx="110" cy="94" rx="64" ry="72" fill="#ffd1a8"/><ellipse cx="48" cy="105" rx="10" ry="16" fill="#f5b88c"/><ellipse cx="172" cy="105" rx="10" ry="16" fill="#f5b88c"/><path d="M47 91Q34 34 79 24q16-25 44-11 54-8 59 68l-12 13-9-29q-17 4-28-13-32 28-79 22z" fill="#493029"/><path d="M72 97q11-9 23 0M126 97q11-9 23 0" stroke="#513a35" stroke-width="5" fill="none" stroke-linecap="round"/><circle cx="84" cy="103" r="4" fill="#30282b"/><circle cx="137" cy="103" r="4" fill="#30282b"/><path d="M105 105l-5 20q10 7 20 0" fill="none" stroke="#df9878" stroke-width="3" stroke-linecap="round"/><path d="M89 136q21 19 43 0" stroke="#a94e56" stroke-width="5" fill="none" stroke-linecap="round"/><circle cx="68" cy="125" r="12" fill="#f3a6a0" opacity=".45"/><circle cx="153" cy="125" r="12" fill="#f3a6a0" opacity=".45"/><path d="M70 168l40 27 40-27" fill="none" stroke="#d4efff" stroke-width="7" stroke-linecap="round"/></svg></div><div class="stage-title" id="stageTitle">Я рядом, чтобы помочь</div><div class="waveform" aria-hidden="true"><i style="--n:0"></i><i style="--n:1"></i><i style="--n:2"></i><i style="--n:3"></i><i style="--n:4"></i><i style="--n:5"></i><i style="--n:6"></i><i style="--n:7"></i><i style="--n:8"></i><i style="--n:9"></i><i style="--n:10"></i><i style="--n:11"></i><i style="--n:12"></i><i style="--n:13"></i><i style="--n:14"></i><i style="--n:15"></i><i style="--n:16"></i><i style="--n:17"></i><i style="--n:18"></i></div><div class="stage-hint" id="stageHint">Расскажите, что случилось. Я внимательно вас выслушаю.</div></div><div class="messages" id="messages" aria-live="polite"></div>
  <div class="input-dock">
    <h3 class="mobile-input-title">Опишите ситуацию</h3>
    <form id="form" class="composer">
      <textarea id="message" aria-label="Ваш вопрос" placeholder="Например: мне позвонили из банка и попросили код…" required maxlength="6000"></textarea>
      <button id="send" class="image-button send" type="submit"><img src="/static/assets/buttons/send.png" alt="Отправить"><span class="mobile-send"><svg class="icon" aria-hidden="true"><use href="#i-send"/></svg>Отправить</span></button>
    </form>
    <div class="voice-box">
      <div id="voiceState" aria-live="polite">Голосовой помощник готов.</div>
      <div class="voice-controls">
        <button id="micToggle" class="image-button" type="button" aria-label="Говорить голосом"><img src="/static/assets/buttons/voice.png" alt="Говорить голосом"><svg class="icon mobile-mic-icon" aria-hidden="true"><use href="#i-mic"/></svg></button>
        <button id="micStop" type="button" hidden><svg class="icon" aria-hidden="true"><use href="#i-stop"/></svg> Остановить микрофон</button>
      </div>
    </div>
  </div>
  <div class="sr-only" aria-hidden="true">
    <label for="voiceSelect">Голос ответа</label><select id="voiceSelect"></select>
    <input id="rate" type="hidden" value=".9"><input id="pitch" type="hidden" value=".9">
  </div>
</main>
</div>

<script>
const $=id=>document.getElementById(id);
const messages=$('messages'), input=$('message');
let history=[], voices=[], recognition=null, speakEnabled=true;
let listening=false, sending=false;

function bubble(text, kind='bot'){
  const el=document.createElement('div');
  el.className='bubble '+kind;
  el.textContent=text;
  messages.appendChild(el);
  messages.scrollTop=messages.scrollHeight;
  return el;
}
function loadVoices(){
  if(!('speechSynthesis' in window))return;
  const old=$('voiceSelect').value;
  voices=speechSynthesis.getVoices();
  const ru=voices.filter(v=>v.lang.toLowerCase().startsWith('ru'));
  const ordered=[...ru,...voices.filter(v=>!ru.includes(v))];
  $('voiceSelect').replaceChildren();
  ordered.forEach(v=>{
    const option=document.createElement('option');
    option.value=v.name;
    option.textContent=v.name+' ('+v.lang+')';
    $('voiceSelect').appendChild(option);
  });
  if(old)$('voiceSelect').value=old;
}
if('speechSynthesis' in window){
  loadVoices();
  speechSynthesis.addEventListener('voiceschanged',loadVoices);
}
function plainText(text){
  return String(text||'')
    .replace(/```[\s\S]*?```/g,'')
    .replace(/[*#_`~]/g,'')
    .trim();
}
function say(text){
  if(!speakEnabled||!('speechSynthesis' in window))return;
  // Не даём распознавателю принять голос самого JANYM за новую команду.
  speechPlaying=true;
  updateVoiceVisual();
  stopRecognition();
  speechSynthesis.cancel();
  const utterance=new SpeechSynthesisUtterance(plainText(text));
  const voice=voices.find(v=>v.name===$('voiceSelect').value);
  utterance.lang='ru-RU';
  utterance.rate=Number($('rate').value);
  utterance.pitch=Number($('pitch').value);
  if(voice){utterance.voice=voice;utterance.lang=voice.lang;}
  const resume=()=>{
    speechPlaying=false;
    updateVoiceVisual();
    if(microphoneEnabled)startRecognition();
  };
  utterance.onend=resume;
  utterance.onerror=resume;
  speechSynthesis.speak(utterance);
}
async function sendMessage(value){
  const message=String(value??input.value).trim();
  if(!message||sending)return;
  sending=true;
  bubble(message,'user');
  input.value='';
  $('send').disabled=true;
  try{
    const wait=bubble('Думаю…');
    if(window.matchMedia('(min-width:721px)').matches){
      messages.scrollIntoView({behavior:'smooth',block:'start'});
    }
    const response=await fetch('/api/chat',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({message,history:history.slice(-12)})
    });
    const data=await response.json();
    if(!response.ok)throw Error(data.detail||'Ошибка запроса');
    const reply=plainText(data.reply||'Я здесь.');
    wait.textContent=reply;
    history.push({role:'user',content:message});
    history.push({role:'assistant',content:reply});
    history=history.slice(-20);
    say(reply);
  }catch(error){
    bubble('Ошибка: '+error.message);
  }finally{
    sending=false;
    $('send').disabled=false;
    input.focus();
  }
}
$('form').addEventListener('submit',event=>{
  event.preventDefault();
  sendMessage(input.value);
});
input.addEventListener('keydown',event=>{
  if(event.key==='Enter'&&!event.shiftKey){
    event.preventDefault();
    $('form').requestSubmit();
  }
});
document.querySelectorAll('[data-prompt]').forEach(button=>{
  button.addEventListener('click',()=>sendMessage(button.dataset.prompt));
});

const SpeechRecognition =
    window.SpeechRecognition ||
    window.webkitSpeechRecognition;

let microphoneEnabled = false;
let speechPlaying = false;
function updateVoiceVisual(){
 const stage=$('voiceStage');
 stage.classList.toggle('speaking',speechPlaying);
 stage.classList.toggle('recording',microphoneEnabled && listening && !speechPlaying);
 $('stageTitle').textContent=speechPlaying?'Жаным отвечает…':microphoneEnabled?'Я вас слушаю…':'Я рядом, чтобы помочь';
 $('stageHint').textContent=speechPlaying?'Сейчас я всё объясню.':microphoneEnabled?'Расскажите, что случилось. Я вас слушаю.':'Расскажите, что случилось. Я внимательно вас выслушаю.';
}

let restartTimer = null;

function setVoiceState(text) {
    $('voiceState').textContent = text;
}

function startRecognition() {
    if (!recognition || !microphoneEnabled || speechPlaying || listening) return;
    clearTimeout(restartTimer);
    try {
        recognition.start();
    } catch (error) {
        // InvalidStateError означает, что распознаватель уже запускается.
        if (error.name !== 'InvalidStateError') {
            setVoiceState('Не удалось включить микрофон: ' + error.message);
        }
    }
}

function stopRecognition() {
    clearTimeout(restartTimer);
    if (!recognition || !listening) return;
    try { recognition.stop(); } catch (error) {}
}

function enableMicrophone() {
    if (!recognition) {
        setVoiceState('Этот браузер не поддерживает распознавание речи. Открой JANYM в Chrome или Edge.');
        return;
    }
    microphoneEnabled = true;
    updateVoiceVisual();
    jarvisActive = true;
    $('micToggle').hidden = true;
    $('micStop').hidden = false;
    setVoiceState('Включаю микрофон… Если браузер спросит, нажмите «Разрешить».');
    if (speakEnabled && 'speechSynthesis' in window) {
        say('Я слушаю. Расскажите, что произошло.');
    } else {
        startRecognition();
    }
}

function disableMicrophone() {
    microphoneEnabled = false;
    updateVoiceVisual();
    jarvisActive = false;
    stopRecognition();
    $('micToggle').hidden = false;
    $('micStop').hidden = true;
    setVoiceState('Микрофон выключен.');
}

if (SpeechRecognition) {
    recognition = new SpeechRecognition();

    recognition.lang = "ru-RU";
    recognition.continuous = true;
    recognition.interimResults = false;
    recognition.maxAlternatives = 3;

    recognition.onstart = function() {
        listening = true;
        updateVoiceVisual();
        setVoiceState(jarvisActive
            ? 'JANYM слушает команду…'
            : 'Микрофон включён. Скажи: «Жаным, включись».');
    };

    recognition.onresult = function(event) {
        const result =
            event.results[event.results.length - 1];

        if (result.isFinal) {
            const alternatives = Array.from(result).map(item => item.transcript);
            handleVoiceCommand(alternatives);
        }
    };

    recognition.onend = function() {
        listening = false;
        updateVoiceVisual();
        if (microphoneEnabled && !speechPlaying) {
            restartTimer = setTimeout(startRecognition, 350);
        }
    };

    recognition.onerror = function(event) {
        listening = false;
        updateVoiceVisual();
        if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
            microphoneEnabled = false;
            updateVoiceVisual();
            jarvisActive = false;
            $('micToggle').hidden = false;
            $('micStop').hidden = true;
            setVoiceState('Доступ к микрофону запрещён. Разреши микрофон для 127.0.0.1 в настройках браузера и нажми кнопку ещё раз.');
        } else if (event.error !== 'no-speech' && event.error !== 'aborted') {
            setVoiceState('Ошибка микрофона: ' + event.error + '. Нажмите зелёную кнопку ещё раз.');
        }
    };
} else {
    $('micToggle').disabled = true;
    setVoiceState('Распознавание речи не поддерживается. Используй Chrome или Edge.');
}

$('micToggle').onclick=enableMicrophone;
$('micStop').onclick=disableMicrophone;
let jarvisActive = false;

function normalizeVoice(text) {
    return text.toLowerCase()
        .replace(/ё/g, "е")
        .replace(/[^а-яa-z0-9\s]/gi, " ")
        .replace(/\s+/g, " ")
        .trim();
}

function handleVoiceCommand(alternatives) {
    const heard = Array.isArray(alternatives) ? alternatives : [alternatives];
    const normalized = heard.map(normalizeVoice);
    const command = normalized[0] || '';
    const wakeDetected = normalized.some(value =>
        /(?:жаным|джаным|ж а н ы м)\s+(?:включись|проснись)/.test(value)
    );

    if (wakeDetected) {
        jarvisActive = true;
        setVoiceState('JANYM включён и слушает. Скажи команду после ответа.');
        say("Я слушаю. Чем помочь?");
        return;
    }

    if (/^(?:жаным|джаным)\s+(?:выключись|усни)/.test(command)) {
        jarvisActive = false;
        setVoiceState('Режим ожидания. Скажи: «Жаным, включись».');
        say("Перехожу в режим ожидания.");
        return;
    }

    if (jarvisActive && command) {
        setVoiceState('Услышал: «' + heard[0] + '». Отвечаю…');
        sendMessage(heard[0]);
    }
}
</script>
</body>
</html>""")


# Optional camera module. Install with:
# pip install opencv-python ultralytics
try:
    import cv2
except ImportError:
    cv2 = None
try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None

_camera = None
_camera_model = None
_camera_lock = threading.Lock()
_camera_stats = {"people": 0, "objects": {}, "error": ""}

@app.post("/api/camera/start")
def camera_start():
    global _camera
    if cv2 is None:
        raise HTTPException(
            status_code=503,
            detail="OpenCV не установлен. Выполни: pip install opencv-python"
        )
    with _camera_lock:
        if _camera is None or not _camera.isOpened():
            _camera = cv2.VideoCapture(
                0, cv2.CAP_DSHOW if os.name == "nt" else 0
            )
        if not _camera.isOpened():
            _camera.release()
            _camera = None
            raise HTTPException(
                status_code=503,
                detail="Камера не найдена или занята другим приложением."
            )
    if YOLO is None:
        return {
            "ok": True,
            "message": "Камера включена. Для распознавания установи ultralytics."
        }
    return {"ok": True, "message": "Камера и распознавание объектов включены."}

@app.post("/api/camera/stop")
def camera_stop():
    global _camera
    with _camera_lock:
        if _camera is not None:
            _camera.release()
            _camera = None
        _camera_stats.update({"people": 0, "objects": {}, "error": ""})
    return {"ok": True, "message": "Камера выключена."}

@app.get("/api/camera/status")
def camera_status():
    active = bool(cv2 is not None and _camera is not None and _camera.isOpened())
    return {
        "active": active,
        "vision_available": YOLO is not None,
        "people": _camera_stats["people"],
        "objects": _camera_stats["objects"],
        "error": _camera_stats["error"]
    }

def _camera_frames():
    global _camera_model
    while True:
        with _camera_lock:
            if _camera is None or not _camera.isOpened():
                break
            ok, frame = _camera.read()
            if not ok:
                break
            if YOLO is not None:
                try:
                    if _camera_model is None:
                        _camera_model = YOLO("yolov8n.pt")
                    result = _camera_model.predict(
                        frame, verbose=False, conf=0.45
                    )[0]
                    counts = {}
                    people = 0
                    for box in result.boxes:
                        class_id = int(box.cls[0])
                        label = result.names.get(class_id, str(class_id))
                        counts[label] = counts.get(label, 0) + 1
                        if class_id == 0:
                            people += 1
                    _camera_stats.update({
                        "people": people,
                        "objects": counts,
                        "error": ""
                    })
                    frame = result.plot()
                except Exception as exc:
                    _camera_stats["error"] = str(exc)[:160]
            ok, encoded = cv2.imencode(".jpg", frame)
            if not ok:
                continue
            data = encoded.tobytes()
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + data + b"\r\n"

@app.get("/api/camera/stream")
def camera_stream():
    if cv2 is None:
        raise HTTPException(status_code=503, detail="OpenCV не установлен.")
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        _camera_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


if __name__ == "__main__":
    import uvicorn
    print("=" * 55)
    print("JANYM AI 4.0")
    print(f"http://{HOST}:{PORT}")
    print("=" * 55)
    uvicorn.run(app, host=HOST, port=PORT, workers=1)
