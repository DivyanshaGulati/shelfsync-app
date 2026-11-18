# Claude and Groq configuration

Use these settings in `backend/.env` for local development.

```dotenv
AI_PROVIDER=claude
AI_FALLBACK_PROVIDER=groq
CLAUDE_API_KEY=
CLAUDE_MODEL=claude-sonnet-5-5
GROQ_API_KEY=your-key
GROQ_MODEL=openai/gpt-oss-20b
```

The empty Claude key selects Groq directly. For hosting, put the Claude and Groq keys in the backend secret environment. A Claude key makes Claude the first provider for each advisory operation. To pin a local environment to Groq regardless of the Claude key, use `AI_PROVIDER=groq` and `AI_FALLBACK_PROVIDER=none`. Restart FastAPI after changing the configuration.

## Failover behavior

- A timeout, connection error, HTTP 429, or upstream 5xx can switch the advisory operation to the configured backup once. If an outage happens during a tool exchange, the backup starts from the initial prompt and uses its own tool calls.
- Authentication errors, request rejections, malformed model replies, and backend validation errors stop the operation. With any provider error, the Owner can still review quotations and evaluate them using the backend purchasing rules.
- AI recommendations and optional draft prose remain advisory. The backend owns quotation eligibility, prices, purchase authorization, dispatch, and stock.

## Verify providers on your Mac

From the `backend` directory with the virtual environment active, run the following live check. It sends only the test prompt and may use provider credits. With Claude blank, it should report `groq` as the active route. You can also choose **Generate inventory insight** on Overview to test the configured provider using a live stock snapshot.

```bash
python - <<'PY'
import json
from app.core.claude_agent import (
    AgentInvalidResponse, AgentUnavailable, call_provider_messages, provider_route,
)
from app.core.config import settings

try:
    print("Active route:", " -> ".join(p for p in provider_route(settings) if p != "none"))
    reply = call_provider_messages({
        "model": settings.claude_model.strip(),
        "max_tokens": 1200,
        "system": "Return only a JSON object.",
        "messages": [{"role": "user", "content": 'Return exactly {"ok":true} as JSON.'}],
        "tools": [],
    })
    result = "".join(block["text"] for block in reply["content"] if block.get("type") == "text")
    if reply.get("stop_reason") != "end_turn" or json.loads(result) != {"ok": True}:
        raise AgentInvalidResponse("Unexpected provider reply")
except (AgentUnavailable, AgentInvalidResponse, ValueError) as exc:
    raise SystemExit(f"AI smoke test failed ({type(exc).__name__}). Check the configured key, model and provider connectivity.") from None
print("AI smoke test passed.")
PY
```

To exercise the real tool exchange, sign in as Owner, record a regular checkout if a purchase request is needed, prepare quotations, simulate vendor replies, close the collection, and choose **Get AI recommendation** on a request with eligible quotes. The UI should show an advisory pick. The automated tests use mocked Claude and Groq HTTP responses and cover Groq tool calls, failover after a Claude tool round, and backend purchasing safeguards.
