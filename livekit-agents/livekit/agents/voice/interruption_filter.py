"""
Intelligent interruption filtering for LiveKit voice agents.

This module provides context-aware interruption filtering that distinguishes
between passive acknowledgements (backchanneling) and active interruptions
based on the agent's current speaking state.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from ..log import logger


class FilterAction(Enum):
    """Actions the interruption filter can take."""
    
    ALLOW = "allow"  # Allow the interruption
    FILTER = "filter"  # Block the interruption
    PENDING = "pending"  # Waiting for more information


@dataclass
class FilterDecision:
    """
    Represents the result of an interruption filtering decision.
    
    Attributes:
        action: The action to take (ALLOW, FILTER, PENDING)
        reason: Human-readable explanation of the decision
        confidence: Confidence level (0.0 to 1.0)
        metadata: Additional context about the decision
    """
    action: FilterAction
    reason: str
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


# Default list of common backchanneling words
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


@dataclass
class InterruptionFilterConfig:
    """
    Configuration for interruption filtering.
    
    Attributes:
        enabled: Whether filtering is active
        ignore_list: Words/phrases to ignore when agent is speaking
        case_sensitive: Whether to match case-sensitively
        buffer_time: Max time to buffer interruptions (seconds)
        custom_filter: Optional custom filtering function
        emit_events: Whether to emit filtering events
    """
    enabled: bool = True
    ignore_list: list[str] = field(default_factory=lambda: DEFAULT_IGNORE_LIST.copy())
    case_sensitive: bool = False
    buffer_time: float = 0.5
    custom_filter: Callable[[str, str], bool] | None = None
    emit_events: bool = True
    
    def validate(self) -> None:
        """Validate configuration parameters."""
        if self.buffer_time < 0 or self.buffer_time > 2.0:
            raise ValueError("buffer_time must be between 0 and 2.0 seconds")
        
        if self.enabled and not self.ignore_list:
            raise ValueError("ignore_list cannot be empty when filtering is enabled")
        
        # Normalize ignore list
        if not self.case_sensitive:
            self.ignore_list = [word.lower() for word in self.ignore_list]


class InterruptionFilter:
    """
    Filters interruptions based on agent state and user input content.
    
    This filter distinguishes between passive acknowledgements (backchanneling)
    and active interruptions based on whether the agent is currently speaking.
    """
    
    def __init__(
        self,
        ignore_list: list[str] | None = None,
        custom_filter_fn: Callable[[str, str], bool] | None = None,
        enabled: bool = True,
        case_sensitive: bool = False,
    ) -> None:
        """
        Initialize the interruption filter.
        
        Args:
            ignore_list: Custom list of backchanneling words (uses default if None)
            custom_filter_fn: Optional custom filtering logic
            enabled: Whether to enable filtering
            case_sensitive: Whether to perform case-sensitive matching
        """
        self._enabled = enabled
        self._case_sensitive = case_sensitive
        self._custom_filter_fn = custom_filter_fn
        
        # Use default ignore list if none provided
        if ignore_list is None:
            ignore_list = DEFAULT_IGNORE_LIST.copy()
        
        # Normalize to lowercase if case-insensitive
        if not case_sensitive:
            ignore_list = [word.lower() for word in ignore_list]
        
        # Convert to set for O(1) lookup
        self._ignore_set = set(ignore_list)
        
        logger.debug(
            "InterruptionFilter initialized",
            extra={
                "enabled": enabled,
                "ignore_list_size": len(self._ignore_set),
                "case_sensitive": case_sensitive,
            }
        )
    
    def _normalize_text(self, text: str) -> str:
        """Normalize text based on case sensitivity setting."""
        return text if self._case_sensitive else text.lower()
    
    def _tokenize(self, text: str) -> list[str]:
        """
        Tokenize text into words.
        
        Args:
            text: The text to tokenize
            
        Returns:
            List of words
        """
        # Simple whitespace-based tokenization
        # Remove punctuation and split on whitespace
        import string
        
        # Remove punctuation
        translator = str.maketrans('', '', string.punctuation)
        cleaned = text.translate(translator)
        
        # Split and filter empty strings
        words = [word for word in cleaned.split() if word]
        
        return words
    
    def is_ignore_list_only(self, transcription: str) -> bool:
        """
        Check if transcription contains only words from the ignore list.
        
        Args:
            transcription: The transcribed text to analyze
            
        Returns:
            True if all words are in ignore list, False otherwise
        """
        if not transcription or not transcription.strip():
            return False
        
        # Normalize and tokenize
        normalized = self._normalize_text(transcription)
        words = self._tokenize(normalized)
        
        if not words:
            return False
        
        # Check if all words are in ignore list
        return all(word in self._ignore_set for word in words)
    
    def has_command_words(self, transcription: str) -> bool:
        """
        Check if transcription contains any non-ignored words.
        
        Args:
            transcription: The transcribed text to analyze
            
        Returns:
            True if any word is not in ignore list, False otherwise
        """
        if not transcription or not transcription.strip():
            return False
        
        # Normalize and tokenize
        normalized = self._normalize_text(transcription)
        words = self._tokenize(normalized)
        
        if not words:
            return False
        
        # Check if any word is NOT in ignore list
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
        
        # If custom filter provided, use it
        if self._custom_filter_fn is not None:
            try:
                should_filter = self._custom_filter_fn(transcription, agent_state)
                return FilterDecision(
                    action=FilterAction.FILTER if should_filter else FilterAction.ALLOW,
                    reason="Custom filter decision",
                    confidence=1.0,
                    metadata={"custom_filter": True}
                )
            except Exception as e:
                logger.error(
                    "Error in custom filter function, falling back to default",
                    exc_info=e,
                    extra={"transcription": transcription}
                )
                # Fall through to default logic
        
        # If agent is not speaking, always allow (process as valid input)
        if not agent_speaking:
            return FilterDecision(
                action=FilterAction.ALLOW,
                reason="Agent not speaking, processing as user input",
                confidence=1.0,
                metadata={"agent_speaking": False}
            )
        
        # Agent is speaking - check if input is ignore-list-only
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
        
        # Default to allowing interruption
        return FilterDecision(
            action=FilterAction.ALLOW,
            reason="Default allow",
            confidence=0.5
        )
