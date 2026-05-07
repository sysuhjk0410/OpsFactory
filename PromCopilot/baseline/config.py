import os
import re
from dotenv import load_dotenv

load_dotenv()

HISTORY_CSV = "data/history.csv"
HISTORY_EMBEDDING_CSV = "data/history_embedding.csv"
QUESTIONS_CSV = "data/question.csv"
QUESTIONS_EMBEDDING_CSV = "data/question_embedding.csv"

EMBEDDING_MODEL = "local-hash-embedding"

CHAT_MODEL = os.getenv("LLM_MODEL", "Qwen/Qwen3-0.6B")
CHAT_MODEL_RUN_NAME = re.sub(r"[^A-Za-z0-9._-]+", "__", CHAT_MODEL).strip("_") or "model"

# TOP_N = 0
# TOP_N = 1
# TOP_N = 3
TOP_N = 10

USER_LLM_API_KEY = os.getenv("LLM_API_KEY", "")
USER_LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://127.0.0.1:8000/v1")
USER_LLM_PROVIDER = os.getenv("LLM_PROVIDER", "local")

PROMETHEUS_BASE_URL = "http://10.176.122.154:19090"
