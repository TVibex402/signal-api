# TVibex402

Free Solana token signal API for AI agents & bots.

## Run locally

```bash
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8080
Endpoints
GET / → Homepage
GET /signal?mint=TOKEN_ADDRESS → Live data
GET /demo → Sample data
GET /health → Health check
curl "http://127.0.0.1:8080/signal?mint=So11111111111111111111111111111111111111112"