"""
Intelligent Interruption Handling Agent

This example demonstrates how to use the InterruptionFilter to distinguish
between passive acknowledgements (backchanneling like "yeah", "ok", "hmm")
and active interruptions that should stop the agent.

When the agent is speaking:
- Backchanneling words are filtered out (agent continues speaking)
- Real commands/questions interrupt the agent

When the agent is NOT speaking:
- All user input is processed normally

Usage:
    python intelligent_interruption_agent.py dev

Requirements:
    pip install -r intelligent_interruption_requirements.txt
    
Environment variables needed in .env:
    LIVEKIT_URL=wss://your-server.livekit.cloud
    LIVEKIT_API_KEY=your-api-key
    LIVEKIT_API_SECRET=your-api-secret
    OPENAI_API_KEY=your-openai-key
    DEEPGRAM_API_KEY=your-deepgram-key
    CARTESIA_API_KEY=your-cartesia-key
"""

from __future__ import annotations

import logging
import string
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from dotenv import load_dotenv

from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    cli,
)
from livekit.agents.voice.events import UserInputTranscribedEvent
from livekit.plugins import silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

load_dotenv()

logger = logging.getLogger("intelligent-interruption-agent")
logging.basicConfig(level=logging.INFO)


# ============================================================================
# Interruption Filter Implementation (embedded for standalone use)
# ============================================================================

class FilterAction(Enum):
    """Actions the interruption filter can take."""
    ALLOW = "allow"
    FILTER = "filter"
    PENDING = "pending"


@dataclass
class FilterDecision:
    """Result of an interruption filtering decision."""
    action: FilterAction
    reason: str
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


# Default backchanneling words to ignore when agent is speaking
DEFAULT_IGNORE_LIST = [
    # Single syllable acknowledgements
    "yeah", "yep", "yes", "yup", "ok", "okay",
    "hmm", "mhm", "mm", "mmm", "uh-huh", "uh huh",
    "ah", "aha", "oh", "ooh",
    # Thinking sounds
    "um", "uh", "er", "erm",
    # Agreement
    "right", "sure", "alright", "got it",
    # Encouragement
    "go on", "continue", "i see",
]


class InterruptionFilter:
    """
    Filters interruptions based on agent state and user input content.
    
    Distinguishes between passive acknowledgements (backchanneling)
    and active interruptions based on whether the agent is speaking.
    """
    
    def __init__(
        self,
        ignore_list: list[str] | None = None,
        custom_filter_fn: Callable[[str, str], bool] | None = None,
        enabled: bool = True,
        case_sensitive: bool = False,
    ) -> None:
        self._enabled = enabled
        self._case_sensitive = case_sensitive
        self._custom_filter_fn = custom_filter_fn
        
        if ignore_list is None:
            ignore_list = DEFAULT_IGNORE_LIST.copy()
        
        if not case_sensitive:
            ignore_list = [word.lower() for word in ignore_list]
        
        self._ignore_set = set(ignore_list)
    
    def _normalize_text(self, text: str) -> str:
        return text if self._case_sensitive else text.lower()
    
    def _tokenize(self, text: str) -> list[str]:
        translator = str.maketrans('', '', string.punctuation)
        cleaned = text.translate(translator)
        return [word for word in cleaned.split() if word]
    
    def is_ignore_list_only(self, transcription: str) -> bool:
        """Check if transcription contains only backchanneling words."""
        if not transcription or not transcription.strip():
            return False
        
        normalized = self._normalize_text(transcription)
        words = self._tokenize(normalized)
        
        if not words:
            return False
        
        return all(word in self._ignore_set for word in words)
    
    def has_command_words(self, transcription: str) -> bool:
        """Check if transcription contains any non-ignored words."""
        if not transcription or not transcription.strip():
            return False
        
        normalized = self._normalize_text(transcription)
        words = self._tokenize(normalized)
        
        if not words:
            return False
        
        return any(word not in self._ignore_set for word in words)
    
    def should_filter_interruption(
        self,
        transcription: str,
        agent_state: str,
        agent_speaking: bool,
    ) -> FilterDecision:
        """
        Determine if an interruption should be filtered.
        
        Args:
            transcription: The user's transcribed speech
            agent_state: Current agent state
            agent_speaking: Whether agent is currently speaking
            
        Returns:
            FilterDecision with action (ALLOW, FILTER, PENDING) and reason
        """
        if not self._enabled:
            return FilterDecision(
                action=FilterAction.ALLOW,
                reason="Filtering disabled",
                confidence=1.0
            )
        
        if self._custom_filter_fn is not None:
            try:
                should_filter = self._custom_filter_fn(transcription, agent_state)
                return FilterDecision(
                    action=FilterAction.FILTER if should_filter else FilterAction.ALLOW,
                    reason="Custom filter decision",
                    confidence=1.0,
                    metadata={"custom_filter": True}
                )
            except Exception:
                pass  # Fall through to default logic
        
        # If agent is not speaking, always allow
        if not agent_speaking:
            return FilterDecision(
                action=FilterAction.ALLOW,
                reason="Agent not speaking, processing as user input",
                confidence=1.0,
                metadata={"agent_speaking": False}
            )
        
        # Agent is speaking - check if input is backchanneling only
        if self.is_ignore_list_only(transcription):
            return FilterDecision(
                action=FilterAction.FILTER,
                reason="Backchanneling detected while agent speaking",
                confidence=1.0,
                metadata={
                    "agent_speaking": True,
                    "ignore_list_only": True,
                    "transcription": transcription
                }
            )
        
        # Agent is speaking but input contains command words
        if self.has_command_words(transcription):
            return FilterDecision(
                action=FilterAction.ALLOW,
                reason="Command words detected, allowing interruption",
                confidence=1.0,
                metadata={
                    "agent_speaking": True,
                    "has_command_words": True,
                    "transcription": transcription
                }
            )
        
        return FilterDecision(
            action=FilterAction.ALLOW,
            reason="Default allow",
            confidence=0.5
        )


# ============================================================================
# Agent Implementation
# ============================================================================


class IntelligentInterruptionAgent(Agent):
    """Agent with intelligent interruption filtering."""
    
    def __init__(self) -> None:
        super().__init__(
            instructions="""You are a helpful voice assistant named Kelly.
            Keep your responses conversational and natural.
            Don't use emojis, asterisks, or markdown in your responses.
            When explaining something, take your time - the user may say 
            things like "yeah", "ok", "uh-huh" to show they're listening,
            but that doesn't mean they want you to stop.""",
        )
        
        # Initialize the interruption filter with default backchanneling words
        self._interruption_filter = InterruptionFilter(
            ignore_list=DEFAULT_IGNORE_LIST,
            enabled=True,
            case_sensitive=False,
        )
        
        self._is_speaking = False
    
    async def on_enter(self):
        """Called when the agent starts - generate initial greeting."""
        self.session.generate_reply()
    
    def set_speaking(self, speaking: bool):
        """Update the speaking state."""
        self._is_speaking = speaking
        logger.debug(f"Agent speaking state: {speaking}")
    
    def should_allow_interruption(self, transcript: str) -> bool:
        """
        Determine if user input should interrupt the agent.
        
        Returns True if the interruption should be allowed,
        False if it should be filtered (backchanneling).
        """
        decision = self._interruption_filter.should_filter_interruption(
            transcription=transcript,
            agent_state="speaking" if self._is_speaking else "listening",
            agent_speaking=self._is_speaking,
        )
        
        if decision.action == FilterAction.FILTER:
            logger.info(f"Filtered backchanneling: '{transcript}' - {decision.reason}")
            return False
        else:
            logger.info(f"Allowing input: '{transcript}' - {decision.reason}")
            return True


server = AgentServer()


def prewarm(proc: JobProcess):
    """Prewarm the VAD model for faster startup."""
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session()
async def entrypoint(ctx: JobContext):
    """Main entry point for the voice agent session."""
    
    # Create the intelligent interruption agent
    agent = IntelligentInterruptionAgent()
    
    # Create the session with interruption handling
    session = AgentSession(
        # Speech-to-text
        stt="deepgram/nova-3",
        # LLM for generating responses
        llm="openai/gpt-4.1-mini",
        # Text-to-speech
        tts="cartesia/sonic-2",
        # Turn detection
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        # Enable preemptive generation for lower latency
        preemptive_generation=True,
        # Enable false interruption handling
        resume_false_interruption=True,
        false_interruption_timeout=1.5,
        # Allow interruptions (we'll filter them intelligently)
        allow_interruptions=True,
        # Minimum speech duration to consider as interruption
        min_interruption_duration=0.3,
    )
    
    # Track agent speaking state
    @session.on("agent_state_changed")
    def on_agent_state_changed(ev):
        is_speaking = ev.new_state == "speaking"
        agent.set_speaking(is_speaking)
    
    # Log user transcriptions and filter decisions
    @session.on("user_input_transcribed")
    def on_user_input(ev: UserInputTranscribedEvent):
        if ev.is_final and ev.transcript.strip():
            should_allow = agent.should_allow_interruption(ev.transcript)
            logger.info(
                f"User said: '{ev.transcript}' | "
                f"Agent speaking: {agent._is_speaking} | "
                f"Allow interruption: {should_allow}"
            )
    
    # Start the session
    await session.start(
        agent=agent,
        room=ctx.room,
    )


if __name__ == "__main__":
    cli.run_app(server)
