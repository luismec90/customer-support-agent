<!-- @format -->

> 🎉 **Live Demo – Call Now:** **+1&nbsp;209-266-6917**
>
> If you're looking to launch or scale a voice chat solution, let's connect: **luismontoya3141@gmail.com**

# AI-Powered Customer Support Agent (Dial-in)

This project demonstrates how to create a voice bot that can receive phone calls via Dailys PSTN capabilities to enable voice conversations.

## How It Works

1. Daily receives an incoming call to your phone number.
2. Daily calls your webhook server (`/start` endpoint).
3. The server creates a Daily room with dial-in capabilities
4. The server starts the bot process with the room details
5. The caller is put on hold with music
6. The bot joins the Daily room and signals readiness
7. Daily forwards the call to the Daily room
8. The caller and the bot are connected, and the bot handles the conversation

## Prerequisites

- A Daily account with an API key
- An OpenAI API key for the bot's intelligence
- A Cartesia API key for text-to-speech

---

# Instructions

Lets divide this process in to two major sections.

1. Bot setup
1. Server setup

# Set-up the bot

The following steps walk you through packaging and deploying the bot as a managed container on [Pipecat Cloud](https://pipecat.daily.co/).

### 1. Set up your Python environment

```bash
# Create a virtual environment
python3 -m venv .venv

# Activate it
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install the Pipecat Cloud CLI
pip install pipecatcloud
```

### 2. Authenticate with Pipecat Cloud

```bash
pcc auth login
```

### 3. Acquire required API keys

This starter requires the following API keys:

- **OpenAI API Key**: Get from [platform.openai.com/api-keys](https://platform.openai.com/api-keys)
- **Cartesia API Key**: Get from [play.cartesia.ai/keys](https://play.cartesia.ai/keys)
- **Deepgram API Key**: Get from [deepgram.com](https://deepgram.com/)

### 4. Set API keys

```bash
# Copy the example env file
cp env.example .env

# Edit .env to add your API keys:
# OPENAI_API_KEY=
# DEEPGRAM_API_KEY=
# CARTESIA_API_KEY=
```

**Create a secret set from your .env file**:

```bash
pcc secrets set customer-support-agent-secrets --file .env
```

### 5. Building and Deploying

For detailed instructions on building, deploying, and running your agent, please refer to the [Pipecat Cloud documentation](https://docs.pipecat.daily.co/quickstart).

1. **Build the Docker image**:

   ```shell
   docker build --platform=linux/arm64 -t customer-support-agent:latest .
   ```

2. **Push to a container registry**:

   ```shell
   docker tag customer-support-agent:latest your-repository/customer-support-agent:latest
   docker push your-repository/customer-support-agent:latest
   ```

3. **Deploy to Pipecat Cloud**:

   ```shell
   pcc deploy customer-support-agent your-repository/customer-support-agent:latest --secrets customer-support-agent-secrets
   ```

## Server Setup

### 1. Run the server

⚠️ **Public hosting required**

Daily.co must be able to reach the `/start` webhook exposed by `server.py`. For production you therefore need to run this script on a publicly-reachable server. Any host that can run long-lived Python processes will do, but a Platform-as-a-Service such as [Render](https://render.com/) makes things especially easy:

1. Push this repository to GitHub.
2. Sign in to Render and choose **New → Web Service**.
3. Point Render at your repo, pick a region, and set the **Start command** to `python server.py`.
4. Add the environment variables from your local `.env` in **Settings → Environment**.
5. Expose port `7860` (or whichever port you configured in `server.py`).
6. Deploy – Render will build the image and give you a public URL which you can paste into your Daily dial-in config.

For a more in-depth walkthrough see Render's [Python quick-start guide](https://render.com/docs/deploy-python).

#### For local testing, use ngrok to expose your local server

```bash
python server.py
ngrok http 7860
# Then use the provided URL in the next step (e.g., https://abc123.ngrok.io/start)
```

### 2. Buy a phone number

1. Go to https://pipecat.daily.co/{your-organization-name}/settings/telephony and buy a phone number

2. Set-up the webhook Daily should reach for incoming calls

## Testing

Call the purchased phone number. The system should answer the call, put you on hold briefly, then connect you with the bot.

## Troubleshooting

### Call is not being answered

- Check that your dial-in config is correctly configured to point towards your ngrok server and correct endpoint
- Make sure the server.py file is running
- Make sure ngrok is correctly setup and pointing to the correct port

### Call connects but no bot is heard

- Ensure your Daily API key is correct and has SIP capabilities
- Verify that the Cartesia API key and voice ID are correct

### Bot starts but disconnects immediately

- Check the Daily logs for any error messages
- Ensure your server has stable internet connectivity
