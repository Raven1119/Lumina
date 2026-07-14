import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONVERSATION_MEMORY = ROOT / "Conversation_Memory"
for path in (ROOT, CONVERSATION_MEMORY):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
