import os
import sys

from dotenv import load_dotenv
from loguru import logger
from openai.types.chat import ChatCompletionToolParam
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    EndTaskFrame,
)
from pipecat.observers.loggers.transcription_log_observer import (
    TranscriptionLogObserver,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.llm_service import LLMService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.services.daily import DailyParams, DailyTransport
from pipecatcloud.agent import DailySessionArguments
from dataclasses import dataclass
from typing import Dict, Optional


DEFAULT_BOT_NAME = "Rachel"
DEFAULT_CARTERSIA_ENGLISH_VOICE_ID = "6f84f4b8-58a2-430c-8c79-688dad597532"
IMPORTANT_RULES = "CRITICAL: Your FIRST response must only greet the caller, give your name, and politely ask for their name. Do NOT ask for reservation numbers, emails, or any verification details yet."

DEFAULT_MAIN_PROMPT_TEMPLATE = f"""{IMPORTANT_RULES}

You are {DEFAULT_BOT_NAME}, a friendly and empathetic customer support agent for Customer Solutions.

Your objectives:
1. Greet the caller warmly, introduce yourself as {DEFAULT_BOT_NAME}, and (if unknown) ask for their name.
2. After learning the caller's name, address them personally and ask how you can help.
3. Only request account verification details (reservation number, full name, email) if the caller's request requires access to their account (e.g. refunds, booking changes).
4. Verify that all three details match our records before performing account-level actions such as refunds.
5. Use the provided `process_refund` tool to send a confirmation email when a refund is approved.
6. Maintain a warm, professional and concise tone. Be empathetic and helpful.
7. Always end the call with a warm goodbye and an invitation to reach out again if needed.

Customer records for verification (use ONLY when necessary):
- Reservation 'RES12345XYZ' | Name 'John Doe'           | Email 'john.doe@example.com'
- Reservation 'ABC789DEF'   | Name 'Sarah Johnson'      | Email 'sarah.johnson@gmail.com'
- Reservation 'XYZ456GHI'   | Name 'Michael Chen'       | Email 'michael.chen@yahoo.com'
- Reservation 'DEF123JKL'   | Name 'Emily Rodriguez'    | Email 'emily.rodriguez@hotmail.com'
- Reservation 'GHI789MNO'   | Name 'David Thompson'     | Email 'david.thompson@outlook.com'
- Reservation 'JKL456PQR'   | Name 'Lisa Wang'          | Email 'lisa.wang@company.com'

Important rules:
- Speak in English only.
- Always use the caller's name once known.
- Do not ask for verification until it is needed.
- If the user no longer needs assistance, then call `terminate_call` immediately.
"""

load_dotenv(override=True)
logger.remove()
logger.add(sys.stderr, level="DEBUG")


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


@dataclass
class DialInConfig:
    """Normalized representation of dial-in related settings returned by Daily.

    Daily can send fields in different capitalizations (snake vs. camel case).  This
    dataclass ensures the rest of the codebase can rely on a single, predictable
    structure.
    """

    dialed_phone_number: Optional[str]
    caller_phone_number: Optional[str]
    dialin_settings: Optional[Dict[str, str]]


def parse_dialin_settings(body: Dict) -> DialInConfig:
    """Extract and normalize dial-in specific information from the request body.

    Args:
        body: Raw request body received by the FastAPI endpoint.

    Returns:
        DialInConfig: A dataclass holding the normalized information. If the
        request does not contain dial-in details, every attribute will be
        ``None``.
    """

    raw = body.get("dialin_settings") or {}

    # These keys may come in varying capitalizations.
    dialed = raw.get("To") or raw.get("to")
    caller = raw.get("From") or raw.get("from")

    settings: Optional[Dict[str, str]] = None
    if raw:
        settings = {
            "call_id": raw.get("callId") or raw.get("call_id"),
            "call_domain": raw.get("callDomain") or raw.get("call_domain"),
        }

    return DialInConfig(dialed, caller, settings)


# Function call for the bot to terminate the call.
async def terminate_call(
    function_name, tool_call_id, args, llm: LLMService, context, result_callback
):
    """Function the bot can call to terminate the call."""
    await llm.queue_frame(EndTaskFrame(), FrameDirection.UPSTREAM)
    await result_callback("""Say: 'Okay, thank you! Have a great day!'""")


class DialInHandler:
    """Handles all dial-in related functionality and event handling.

    This class encapsulates the logic for incoming calls and handling
    all dial-in related events from the Daily platform.
    """

    def __init__(self, transport, task, context_aggregator):
        """Initialize the DialInHandler.

        Args:
            transport: The Daily transport instance
            task: The PipelineTask instance
            context_aggregator: The context aggregator for the LLM
        """
        self.transport = transport
        self.task = task
        self.context_aggregator = context_aggregator
        self._register_handlers()

    def _register_handlers(self):
        """Register all event handlers related to dial-in functionality."""

        @self.transport.event_handler("on_dialin_ready")
        async def on_dialin_ready(transport, data):
            """Handler for when the dial-in is ready (SIP addresses registered with the SIP network)."""
            # Forward SIP status updates to your telephony platform if needed.
            logger.debug(f"Dial-in ready: {data}")

        @self.transport.event_handler("on_dialin_connected")
        async def on_dialin_connected(transport, data):
            """Handler for when a dial-in call is connected."""
            logger.debug(f"Dial-in connected: {data} and set_bot_ready")

        @self.transport.event_handler("on_dialin_stopped")
        async def on_dialin_stopped(transport, data):
            """Handler for when a dial-in call is stopped."""
            logger.debug(f"Dial-in stopped: {data}")

        @self.transport.event_handler("on_dialin_error")
        async def on_dialin_error(transport, data):
            """Handler for dial-in errors."""
            logger.error(f"Dial-in error: {data}")
            # The bot should leave the call if there is an error
            await self.task.cancel()

        @self.transport.event_handler("on_dialin_warning")
        async def on_dialin_warning(transport, data):
            """Handler for dial-in warnings."""
            logger.warning(f"Dial-in warning: {data}")

        @self.transport.event_handler("on_first_participant_joined")
        async def on_first_participant_joined(transport, participant):
            """Handler for when the first participant joins the call."""
            logger.info("First participant joined: {}", participant["id"])
            
            # Start recording the call
            try:
                await transport.start_recording()
                logger.info("Recording started successfully")
            except Exception as e:
                logger.error("Failed to start recording: {}", e)
            
            # Capture the participant's transcription
            await transport.capture_participant_transcription(participant["id"])

            # For the dial-in case, we want the bot to greet the user.
            # We can prompt the bot to speak by putting the context into the pipeline.
            await self.task.queue_frames([self.context_aggregator.user().get_context_frame()])

async def main(room_url: str, token: str, body: dict):
    """Orchestrates the full life-cycle of a single Voice-AI bot session.

    The function wires together the different Pipecat components:

    1.  Transport (Daily): Handles low-level WebRTC media exchange.
    2.  LLM (OpenAI): Provides the conversation brain.
    3.  TTS (Cartesia): Converts LLM responses into speech.
    4.  Pipeline / Runner:  Streams audio + text frames through the system.

    All heavy lifting is delegated to Pipecat – the goal here is to provide a
    clear, declarative setup so that developers can tweak individual pieces
    without first becoming Pipecat experts.
    """

    logger.debug("Starting bot in room: {}", room_url)

    # -------------------------------------------------------------------
    # 1. Dial-in configuration
    # -------------------------------------------------------------------
    dialin_config = parse_dialin_settings(body)

    logger.debug(
        "Dial-in settings | To: %s | From: %s | Settings: %s",
        dialin_config.dialed_phone_number,
        dialin_config.caller_phone_number,
        dialin_config.dialin_settings,
    )

    transport = DailyTransport(
        room_url,
        token,
        "Voice AI Bot",
        DailyParams(
            api_url=os.getenv("DAILY_API_URL"),
            api_key=os.getenv("DAILY_API_KEY"),
            dialin_settings=dialin_config.dialin_settings,
            audio_in_enabled=True,
            audio_out_enabled=True,
            video_out_enabled=False,
            vad_analyzer=SileroVADAnalyzer(),
            transcription_enabled=True
        ),
    )

    # Configure STT, LLM and TTS services
    llm = OpenAILLMService(api_key=os.getenv("OPENAI_API_KEY"), model="gpt-4o-mini")
    tts = CartesiaTTSService(
        api_key=os.getenv("CARTESIA_API_KEY"),
        voice_id=DEFAULT_CARTERSIA_ENGLISH_VOICE_ID,
    )
    
    messages = [
        {
            "role": "system",
            "content": DEFAULT_MAIN_PROMPT_TEMPLATE
        },
    ]

    # Registering the terminate_call function as a tool
    # This is used to terminate the call when the bot is done
    llm.register_function("terminate_call", terminate_call)
    tools = [
        ChatCompletionToolParam(
            type="function",
            function={
                "name": "terminate_call",
                "description": "Terminate the call",
            },
        )
    ]

    # This sets up the LLM context by providing messages and tools
    context = OpenAILLMContext(messages, tools)
    context_aggregator = llm.create_context_aggregator(context)

    # Build the core voice-AI pipeline
    pipeline = Pipeline(
        [
            transport.input(),
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ]
    )

    pipeline_task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
            enable_metrics=True,
            enable_usage_metrics=True,
            observers=[TranscriptionLogObserver()],
        ),
    )

    # Initialize handlers dict to keep references
    handlers: Dict[str, DialInHandler] = {}
    if dialin_config.dialin_settings:
        handlers["dialin"] = DialInHandler(transport, pipeline_task, context_aggregator)

    # Set up general event handlers
    @transport.event_handler("on_call_state_updated")
    async def on_call_state_updated(transport, state):
        logger.info(f"on_call_state_updated, state: {state}")
        if state == "left":
            await pipeline_task.cancel()

    @transport.event_handler("on_joined")
    async def on_joined(transport, data):
        session_id = data["meetingSession"]["id"]
        bot_id = data["participants"]["local"]["id"]
        logger.info(f"Session ID: {session_id}, Bot ID: {bot_id}")

    @transport.event_handler("on_participant_left")
    async def on_participant_left(transport, participant, reason):
        logger.debug(f"Participant left: {participant}, reason: {reason}")
                # Stop recording the call
        try:
            await transport.stop_recording()
            logger.info("Recording stopped successfully")
        except Exception as e:
            logger.error("Failed to stop recording: {}", e)
            
        await pipeline_task.cancel()

    runner = PipelineRunner(handle_sigint=False, force_gc=True)
    await runner.run(pipeline_task)


async def bot(args: DailySessionArguments):
    """Main bot entry point compatible with the FastAPI route handler.

    Args:
        room_url: The Daily room URL
        token: The Daily room token
        body: The configuration object from the request body can contain dialin_settings, and call_transfer
        session_id: The session ID for logging
    """
    logger.info(f"Bot process initialized {args.room_url} {args.token}")

    try:
        await main(args.room_url, args.token, args.body)
        logger.info("Bot process completed")
    except Exception as e:
        logger.exception(f"Error in bot process: {str(e)}")
        raise