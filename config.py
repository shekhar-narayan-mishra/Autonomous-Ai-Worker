import os
import dotenv

dotenv.load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MODEL_NAME = os.getenv("MODEL_NAME", "openai/gpt-oss-120b")
FALLBACK_MODEL = "openai/gpt-oss-120b"
