#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""server.py.

Webhook server to handle webhook coming from Daily, create a Daily room and start the bot.
"""

import json
import os
import shlex
import subprocess
from contextlib import asynccontextmanager
import time

import aiohttp
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

load_dotenv()

# ----------------- API ----------------- #


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create aiohttp session to be used for API calls
    app.state.session = aiohttp.ClientSession()
    yield
    # Close session when shutting down
    await app.state.session.close()


app = FastAPI(lifespan=lifespan)


@app.post("/start")
async def handle_incoming_daily_webhook(request: Request) -> JSONResponse:
    """Handle incoming Daily call webhook."""
    print("Received webhook from Daily")

    # Get the dial-in properties from the request
    try:
        data = await request.json()
        if "test" in data:
            # Pass through any webhook checks
            return JSONResponse({"test": True})

        if not all(key in data for key in ["From", "To", "callId", "callDomain"]):
            raise HTTPException(
                status_code=400, detail="Missing properties 'From', 'To', 'callId', 'callDomain'"
            )

        # Extract the caller's phone number and other details
        caller_phone = str(data.get("From"))
        to_phone = str(data.get("To"))
        call_id = str(data.get("callId"))
        call_domain = str(data.get("callDomain"))
        
        print(f"Processing call from {caller_phone} to {to_phone}")

        # Get environment variables for Pipecat API
        pipecat_api_key = os.getenv("PIPECAT_API_KEY")
        pipecat_service = os.getenv("PIPECAT_SERVICE")
        
        if not pipecat_api_key:
            raise HTTPException(status_code=500, detail="PIPECAT_API_KEY environment variable not set")
        if not pipecat_service:
            raise HTTPException(status_code=500, detail="PIPECAT_SERVICE environment variable not set")

        # Calculate expiration time (24 hours from now)
        exp_time = int(time.time()) + (24 * 60 * 60)

        # Prepare the payload for Pipecat API
        pipecat_payload = {
            "createDailyRoom": True,
            "dailyRoomProperties": {
                "sip": {
                    "display_name": caller_phone,
                    "sip_mode": "dial-in",
                    "num_endpoints": 1
                },
                "exp": exp_time
            },
            "body": {
                "dialin_settings": {
                    "from": caller_phone,
                    "to": to_phone,
                    "call_id": call_id,
                    "call_domain": call_domain
                }
            }
        }

        # Call Pipecat API
        pipecat_url = f"https://api.pipecat.daily.co/v1/public/{pipecat_service}/start"
        #pipecat_url = "https://webhook.site/1c2af478-7bf4-4fed-87e4-7302b0a12f1e"
        headers = {
            "Authorization": f"Bearer {pipecat_api_key}",
            "Content-Type": "application/json"
        }

        try:
            async with request.app.state.session.post(
                pipecat_url, 
                json=pipecat_payload, 
                headers=headers
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    print(f"Pipecat API error: {response.status} - {error_text}")
                    raise HTTPException(
                        status_code=500, 
                        detail=f"Pipecat API error: {response.status} - {error_text}"
                    )
                
                pipecat_response = await response.json()
                print(f"Pipecat API response: {pipecat_response}")
                
        except aiohttp.ClientError as e:
            print(f"Error calling Pipecat API: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to call Pipecat API: {str(e)}")
        except Exception as e:
            print(f"Error calling Pipecat API: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to call Pipecat API: {str(e)}")

        # Return just a 200 status
        return JSONResponse({})

    except HTTPException:
        raise
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Server error: {str(e)}")


@app.get("/health")
async def health_check():
    """Simple health check endpoint."""
    return {"status": "healthy"}


# ----------------- Main ----------------- #


if __name__ == "__main__":
    # Run the server
    port = int(os.getenv("PORT", "7860"))
    print(f"Starting server on port {port}")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=True)
