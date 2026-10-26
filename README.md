# WhatsApp AI Cricket Agent

A small test bot. You send a cricket question on WhatsApp, and an AI answers it.

It uses:
- **360dialog Sandbox**: a free test WhatsApp number (the bot's "phone").
- **OpenRouter**: gives the bot a free AI brain.
- **webhook.site**: a free mailbox that holds your incoming WhatsApp messages until the bot picks them up. This means you do **not** need ngrok or any tunnel.
- **Python + FastAPI**: the code that connects everything.

> This is a learning and testing project. It is not ready for real customers.

---

## How it works (the simple version)

```
You send a WhatsApp message
        |
        v
360dialog Sandbox puts it in your webhook.site mailbox
        |
        v
Your bot (running on your laptop) checks the mailbox every 3 seconds
        |
        v
The bot asks the AI (OpenRouter) for an answer
        |
        v
The bot sends the answer back through 360dialog
        |
        v
You get the reply on WhatsApp
```

---

## What you need before you start

1. A computer with **Windows** and **Python 3.11 or newer**.
   Check: open PowerShell and type `python --version`.
   If it says "not recognized", install Python from https://www.python.org/downloads/ and tick **"Add Python to PATH"** during install.
2. **Git** (only if you want to download this project from GitHub): https://git-scm.com/download/win
3. **WhatsApp** on your phone.
4. Two free accounts (steps below): OpenRouter, and the 360dialog Sandbox (which needs only WhatsApp).

---

## Step 1: Download the project

Open PowerShell and run:

```powershell
git clone https://github.com/YOUR-USERNAME/YOUR-REPO-NAME.git
cd YOUR-REPO-NAME
```

(Replace the address with your real repository address.)

## Step 2: Install the packages

```powershell
python -m pip install -r requirements.txt
```

This installs everything the project needs. The list of packages is in the file `requirements.txt`.

> Tip: if you like keeping projects separate, you can use a virtual environment first:
> `python -m venv .venv` then `.\.venv\Scripts\Activate.ps1`.
> If PowerShell blocks it, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned` and try again.
> This is optional.

## Step 3: Get your 360dialog Sandbox key

1. On your phone, open this link: https://wa.me/551146733492?text=START
2. WhatsApp opens. Send the message **START** (capital letters).
3. Wait a few minutes. The sandbox replies with your **API key**.
   It looks like a long mix of letters and numbers.
4. Copy the key somewhere private. You will paste it into your `.env` file in Step 6.

Good to know:
- The sandbox only talks to **your** phone number, the one you used to send START.
- You get about **200 message requests**. When they run out, send START again for a new key.
- Never share this key, post it online, or put it in GitHub.

## Step 4: Get your OpenRouter key (free)

1. Go to https://openrouter.ai/ and sign up.
2. Go to https://openrouter.ai/settings/keys.
3. Click **Create Key**, give it any name, and copy the key. It starts with `sk-or-`.
4. Keep it private. You will paste it in Step 6.

The bot uses the model `openrouter/free`, which picks a free AI model for you. Free models can be slow, busy, or rate limited sometimes. That is normal.

## Step 5: Get your webhook.site mailbox address

1. Go to https://webhook.site
2. The page shows **"Your unique URL"**. It looks like `https://webhook.site/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`.
3. Copy it. You need it in Step 6 and Step 7.

Important: webhook.site stores your messages on their website, and anyone who knows your address can read them. Use only test messages, and make a fresh address when you are done.

## Step 6: Create your `.env` file (your private settings)

The `.env` file is where your secret keys live. It is **never** uploaded to GitHub (the project already blocks it).

1. Copy the example file:
   ```powershell
   Copy-Item .env.example .env
   ```
2. Open it in Notepad (or VS Code):
   ```powershell
   notepad .env
   ```
3. Fill in these lines. Write the value right after the `=` sign, with no spaces and no quotes:

   | Line | What to put |
   |------|-------------|
   | `D360_API_KEY=` | The key from Step 3 |
   | `OPENROUTER_API_KEY=` | The key from Step 4 |
   | `WEBHOOKSITE_URL=` | The address from Step 5 |

   Example shape (fake values):
   ```
   D360_API_KEY=abc123yourkey
   OPENROUTER_API_KEY=sk-or-v1-yourkey
   WEBHOOKSITE_URL=https://webhook.site/11111111-2222-3333-4444-555555555555
   ```
4. Leave every other line as it is. Save the file.

## Step 7: Tell the sandbox where to send messages

Run this with your own webhook.site address from Step 5:

```powershell
python scripts/register_webhook.py https://webhook.site/YOUR-UUID-HERE
```

You should see `360dialog answered HTTP 200`. That means the sandbox accepted the address.

## Step 8: Start the bot

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Look for this line in the output:

```
webhook.site poller started (every 3.0s)
```

Leave this window open. The bot only works while it is running. Press `Ctrl+C` to stop it.

Quick check: open http://localhost:8000/health in your browser. You should see `"status":"ok"` and `"missing_settings":[]`. If a key name is listed, that setting is missing in your `.env`.

## Step 9: Test it on WhatsApp

On your phone, open the chat with the sandbox number (+55 11 4673-3492) and send:

- `What is a hat-trick in cricket?`
- Then a follow-up: `Who has done it in Test cricket?`
- Then: `Hi`

Within a few seconds you should get replies. In the bot window you will see:

- `Processing message` (the bot received your message)
- `OpenRouter answered with model ...` (the AI replied)
- `360dialog ACCEPTED reply` (the sandbox accepted the answer)

Be careful: "ACCEPTED" only means the sandbox took the message. The real proof is the answer appearing on your phone.

Each reply uses 1 of your ~200 sandbox messages.

---

## If something goes wrong

| What you see | What to do |
|--------------|-----------|
| `python` is "not recognized" | Install Python and tick "Add to PATH". Then open a new PowerShell. |
| `Errno 10048 ... only one usage of each socket address` | Another copy of the bot is already running. Close it (or run `Get-NetTCPConnection -LocalPort 8000` to find it, then `Stop-Process -Id <PID>`). Two copies would answer twice. |
| Bot runs but no message appears | Check that Step 7 printed `HTTP 200`. Then open your webhook.site page: if your message is not there, the sandbox is sending somewhere else. Run Step 7 again. |
| `360dialog did NOT accept reply (HTTP 401)` | Your `D360_API_KEY` is wrong or missing. |
| `(HTTP 403)` | The sandbox only replies to your own number. Send from the phone you used for START. |
| `(HTTP 429)` | You used all ~200 sandbox messages. Send START again for a new key. |
| You get "Sorry, I can't answer right now" | The free AI is busy or rate limited. Wait a minute. Check the log line `LLM failed ...` for the reason (401 = bad OpenRouter key, 429 = busy, 404 = model gone). |
| `/health` shows `missing_settings` | Fill in those names in `.env`, save, and restart the bot. |
| webhook.site says `token not found` | Your free address expired. Make a new one (Step 5), update `.env`, and redo Step 7. |

---

## Run the automated tests

```powershell
python -m pytest -q
```

The tests do not use the internet, do not use your keys, and do not send real messages.

---

## Extra settings (optional, in `.env`)

| Setting | What it does |
|---------|-------------|
| `OPENROUTER_MODEL` | Which AI model to use. Default `openrouter/free`. Browse https://openrouter.ai/models (free ones end in `:free`). |
| `GREETING_MODE_ENABLED=true` | Makes "hi", "hello", "hey" get a fixed introduction instead of an AI answer. |
| `GREETING_INTRO_TEXT=` | The introduction text used by greeting mode. |
| `D360_RECIPIENT_FIELD=recipient` | Only if the sandbox gave you a "BSUID" instead of using your phone number. Also set `D360_RECIPIENT_ID=` to that BSUID. |
| `HISTORY_MAX_MESSAGES` | How many past messages the bot remembers per person (default 10). |
| `WEBHOOKSITE_INTERVAL` | Seconds between mailbox checks (default 3). |

---

## Project files, in one line each

| File | Job |
|------|-----|
| `app/main.py` | Starts the web app and the mailbox checker. |
| `app/poller.py` | Checks webhook.site for new messages. |
| `app/webhook.py` | Reads the message and finds who sent it and what they wrote. |
| `app/agent.py` | The brain: remembers the chat, avoids double replies, builds the answer. |
| `app/llm_client.py` | Talks to OpenRouter (the AI). |
| `app/whatsapp_client.py` | Sends the reply through 360dialog. |
| `app/config.py` | Reads your `.env` settings. |
| `scripts/register_webhook.py` | Tells the sandbox where to send messages. |
| `tests/` | Automatic tests. |
| `requirements.txt` | The list of packages to install. |
| `.env.example` | A blank settings template. Safe to share. |
| `.env` | **Your real secrets. Never share. Never upload.** |

---

## Limits you should know

- The bot only works while it is running on your laptop. Closing the window or restarting it stops it.
- Its memory of the chat is temporary. Restarting the bot erases it.
- It cannot look up live scores or very recent results. It tells you that honestly.
- Only text messages work. Photos, voice notes and other media are ignored.
- Free AI models can be slow, busy, or change without notice.
- The sandbox allows about 200 messages and one phone number (yours). It is not for real customers.

## Safety rules

- Never put your keys in GitHub, screenshots, or chat messages.
- If you ever shared a key by accident, make a new one: send START again for 360dialog, and delete and recreate your OpenRouter key.
- The file `.env` is already listed in `.gitignore`, so Git ignores it.
- Before you push, you can double check with: `git status` (the file `.env` must NOT be listed).

---

## Moving to a real product later

The sandbox is for testing only. For real use you would need a real 360dialog WhatsApp Business account, a server that runs all the time with a fixed web address, a database instead of temporary memory, and a paid or self-hosted AI model with predictable limits.
