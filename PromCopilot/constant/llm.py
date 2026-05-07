import os
import re

# Default to Ops Factory's local Qwen-0.6B endpoint. Users may override these
# with their own compatible endpoint, but the project ships no external API key.
USER_LLM_PROVIDER = os.getenv('LLM_PROVIDER', 'local')
USER_LLM_API_KEY = os.getenv('LLM_API_KEY', '')
USER_LLM_BASE_URL = os.getenv('LLM_BASE_URL', 'http://127.0.0.1:8000/v1')
LOCAL_LLM_MODEL = os.getenv('LLM_MODEL', 'Qwen/Qwen3-0.6B')

INPUT_MAX_TOKEN = 32000


def model_run_name(model):
    return re.sub(r'[^A-Za-z0-9._-]+', '__', str(model or 'model')).strip('_') or 'model'
