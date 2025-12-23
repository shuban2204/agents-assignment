"""
Unit tests for intelligent interruption filtering.
"""

import pytest

from livekit.agents.voice.interruption_filter import (
    DEFAULT_IGNORE_LIST,
    FilterAction,
    FilterDecision,
    InterruptionFilter,
    InterruptionFilterConfig,
)


class TestFilterDecision:
    """Tests for FilterDecision dataclass."""
    
    def test_filter_decision_creation(self):
        """Test creating a FilterDecision with all fields."""
        decision = FilterDecision(
            action=FilterAction.ALLOW,
            reason="Test reason",
            confidence=0.95,
            metadata={"key": "value"}
        )
        
        assert decision.action == FilterAction.ALLOW
        assert decision.reason == "Test reason"
        assert decision.confidence == 0.95
        assert decision.metadata == {"key": "value"}
    
    def test_filter_decision_defaults(self):
        """Test FilterDecision default values."""
        decision = FilterDecision(
            action=FilterAction.FILTER,
            reason="Test"
        )
        
        assert decision.confidence == 1.0
        assert decision.metadata == {}


class TestInterruptionFilterConfig:
    """Tests for InterruptionFilterConfig dataclass."""
    
    def test_config_defaults(self):
        """Test default configuration values."""
        config = InterruptionFilterConfig()
        
        assert config.enabled is True
        assert len(config.ignore_list) > 0
        assert config.case_sensitive is False
        assert config.buffer_time == 0.5
        assert config.custom_filter is None
        assert config.emit_events is True
    
    def test_config_validation_valid(self):
        """Test validation with valid configuration."""
        config = InterruptionFilterConfig(
            buffer_time=1.0,
            ignore_list=["yeah", "ok"]
        )
        
        # Should not raise
        config.validate()
    
    def test_config_validation_negative_buffer_time(self):
        """Test validation rejects negative buffer time."""
        config = InterruptionFilterConfig(buffer_time=-0.5)
        
        with pytest.raises(ValueError, match="buffer_time must be between"):
            config.validate()
    
    def test_config_validation_excessive_buffer_time(self):
        """Test validation rejects buffer time > 2.0."""
        config = InterruptionFilterConfig(buffer_time=3.0)
        
        with pytest.raises(ValueError, match="buffer_time must be between"):
            config.validate()
    
    def test_config_validation_empty_ignore_list(self):
        """Test validation rejects empty ignore list when enabled."""
        config = InterruptionFilterConfig(
            enabled=True,
            ignore_list=[]
        )
        
        with pytest.raises(ValueError, match="ignore_list cannot be empty"):
            config.validate()
    
    def test_config_validation_empty_ignore_list_disabled(self):
        """Test validation allows empty ignore list when disabled."""
        config = InterruptionFilterConfig(
            enabled=False,
            ignore_list=[]
        )
        
        # Should not raise
        config.validate()
    
    def test_config_case_normalization(self):
        """Test ignore list is normalized to lowercase when case-insensitive."""
        config = InterruptionFilterConfig(
            ignore_list=["YEAH", "Ok", "HMM"],
            case_sensitive=False
        )
        
        config.validate()
        
        assert "yeah" in config.ignore_list
        assert "ok" in config.ignore_list
        assert "hmm" in config.ignore_list
        assert "YEAH" not in config.ignore_list
    
    def test_config_case_preservation(self):
        """Test ignore list preserves case when case-sensitive."""
        config = InterruptionFilterConfig(
            ignore_list=["YEAH", "Ok", "HMM"],
            case_sensitive=True
        )
        
        config.validate()
        
        assert "YEAH" in config.ignore_list
        assert "Ok" in config.ignore_list
        assert "HMM" in config.ignore_list


class TestDefaultIgnoreList:
    """Tests for the default ignore list."""
    
    def test_default_ignore_list_not_empty(self):
        """Test default ignore list contains words."""
        assert len(DEFAULT_IGNORE_LIST) > 0
    
    def test_default_ignore_list_contains_common_words(self):
        """Test default ignore list contains expected backchanneling words."""
        expected_words = ["yeah", "ok", "hmm", "uh-huh", "right"]
        
        for word in expected_words:
            assert word in DEFAULT_IGNORE_LIST, f"Expected '{word}' in default ignore list"
    
    def test_default_ignore_list_all_lowercase(self):
        """Test all words in default ignore list are lowercase."""
        for word in DEFAULT_IGNORE_LIST:
            assert word == word.lower(), f"Word '{word}' should be lowercase"


class TestInterruptionFilterInitialization:
    """Tests for InterruptionFilter initialization."""
    
    def test_filter_default_initialization(self):
        """Test filter initializes with defaults."""
        filter = InterruptionFilter()
        
        assert filter._enabled is True
        assert filter._case_sensitive is False
        assert len(filter._ignore_set) > 0
    
    def test_filter_custom_ignore_list(self):
        """Test filter accepts custom ignore list."""
        custom_list = ["test", "words"]
        filter = InterruptionFilter(ignore_list=custom_list)
        
        assert "test" in filter._ignore_set
        assert "words" in filter._ignore_set
    
    def test_filter_case_insensitive_normalization(self):
        """Test filter normalizes ignore list to lowercase."""
        filter = InterruptionFilter(
            ignore_list=["YEAH", "Ok", "HMM"],
            case_sensitive=False
        )
        
        assert "yeah" in filter._ignore_set
        assert "ok" in filter._ignore_set
        assert "hmm" in filter._ignore_set
    
    def test_filter_case_sensitive_preservation(self):
        """Test filter preserves case when case-sensitive."""
        filter = InterruptionFilter(
            ignore_list=["YEAH", "Ok", "HMM"],
            case_sensitive=True
        )
        
        assert "YEAH" in filter._ignore_set
        assert "Ok" in filter._ignore_set
        assert "HMM" in filter._ignore_set
    
    def test_filter_disabled(self):
        """Test filter can be disabled."""
        filter = InterruptionFilter(enabled=False)
        
        assert filter._enabled is False


class TestInterruptionFilterIsIgnoreListOnly:
    """Tests for is_ignore_list_only method."""
    
    def test_single_ignore_word(self):
        """Test with single word from ignore list."""
        filter = InterruptionFilter(ignore_list=["yeah", "ok"])
        
        assert filter.is_ignore_list_only("yeah") is True
        assert filter.is_ignore_list_only("ok") is True
    
    def test_multiple_ignore_words(self):
        """Test with multiple words from ignore list."""
        filter = InterruptionFilter(ignore_list=["yeah", "ok", "hmm"])
        
        assert filter.is_ignore_list_only("yeah ok") is True
        assert filter.is_ignore_list_only("ok hmm yeah") is True
    
    def test_mixed_input(self):
        """Test with mix of ignored and non-ignored words."""
        filter = InterruptionFilter(ignore_list=["yeah", "ok"])
        
        assert filter.is_ignore_list_only("yeah but wait") is False
        assert filter.is_ignore_list_only("ok stop") is False
    
    def test_non_ignored_word(self):
        """Test with word not in ignore list."""
        filter = InterruptionFilter(ignore_list=["yeah", "ok"])
        
        assert filter.is_ignore_list_only("stop") is False
        assert filter.is_ignore_list_only("wait") is False
    
    def test_empty_string(self):
        """Test with empty string."""
        filter = InterruptionFilter()
        
        assert filter.is_ignore_list_only("") is False
        assert filter.is_ignore_list_only("   ") is False
    
    def test_with_punctuation(self):
        """Test words with punctuation are handled correctly."""
        filter = InterruptionFilter(ignore_list=["yeah", "ok"])
        
        assert filter.is_ignore_list_only("yeah!") is True
        assert filter.is_ignore_list_only("ok.") is True
        assert filter.is_ignore_list_only("yeah, ok!") is True


class TestInterruptionFilterHasCommandWords:
    """Tests for has_command_words method."""
    
    def test_single_command_word(self):
        """Test with single non-ignored word."""
        filter = InterruptionFilter(ignore_list=["yeah", "ok"])
        
        assert filter.has_command_words("stop") is True
        assert filter.has_command_words("wait") is True
    
    def test_mixed_input_with_command(self):
        """Test with mix of ignored and command words."""
        filter = InterruptionFilter(ignore_list=["yeah", "ok"])
        
        assert filter.has_command_words("yeah but wait") is True
        assert filter.has_command_words("ok stop") is True
    
    def test_only_ignored_words(self):
        """Test with only ignored words."""
        filter = InterruptionFilter(ignore_list=["yeah", "ok", "hmm"])
        
        assert filter.has_command_words("yeah ok") is False
        assert filter.has_command_words("hmm yeah") is False
    
    def test_empty_string(self):
        """Test with empty string."""
        filter = InterruptionFilter()
        
        assert filter.has_command_words("") is False
        assert filter.has_command_words("   ") is False


class TestInterruptionFilterShouldFilterInterruption:
    """Tests for should_filter_interruption method."""
    
    def test_filtering_disabled(self):
        """Test filter allows all when disabled."""
        filter = InterruptionFilter(enabled=False)
        
        decision = filter.should_filter_interruption(
            transcription="yeah",
            agent_state="speaking",
            agent_speaking=True
        )
        
        assert decision.action == FilterAction.ALLOW
        assert "disabled" in decision.reason.lower()
    
    def test_agent_not_speaking_allows_input(self):
        """Test filter allows input when agent not speaking."""
        filter = InterruptionFilter(ignore_list=["yeah", "ok"])
        
        decision = filter.should_filter_interruption(
            transcription="yeah",
            agent_state="listening",
            agent_speaking=False
        )
        
        assert decision.action == FilterAction.ALLOW
        assert "not speaking" in decision.reason.lower()
    
    def test_agent_speaking_backchanneling_filtered(self):
        """Test filter blocks backchanneling when agent speaking."""
        filter = InterruptionFilter(ignore_list=["yeah", "ok", "hmm"])
        
        decision = filter.should_filter_interruption(
            transcription="yeah ok",
            agent_state="speaking",
            agent_speaking=True
        )
        
        assert decision.action == FilterAction.FILTER
        assert "backchanneling" in decision.reason.lower()
    
    def test_agent_speaking_command_allowed(self):
        """Test filter allows commands when agent speaking."""
        filter = InterruptionFilter(ignore_list=["yeah", "ok"])
        
        decision = filter.should_filter_interruption(
            transcription="stop",
            agent_state="speaking",
            agent_speaking=True
        )
        
        assert decision.action == FilterAction.ALLOW
        assert "command" in decision.reason.lower()
    
    def test_agent_speaking_mixed_input_allowed(self):
        """Test filter allows mixed input when agent speaking."""
        filter = InterruptionFilter(ignore_list=["yeah", "ok"])
        
        decision = filter.should_filter_interruption(
            transcription="yeah but wait",
            agent_state="speaking",
            agent_speaking=True
        )
        
        assert decision.action == FilterAction.ALLOW
        assert "command" in decision.reason.lower()
    
    def test_custom_filter_function(self):
        """Test custom filter function is used when provided."""
        def custom_filter(transcription: str, agent_state: str) -> bool:
            return "custom" in transcription
        
        filter = InterruptionFilter(
            ignore_list=["yeah"],
            custom_filter_fn=custom_filter
        )
        
        # Should filter because "custom" is in transcription
        decision = filter.should_filter_interruption(
            transcription="custom input",
            agent_state="speaking",
            agent_speaking=True
        )
        
        assert decision.action == FilterAction.FILTER
        
        # Should allow because "custom" is not in transcription
        decision = filter.should_filter_interruption(
            transcription="normal input",
            agent_state="speaking",
            agent_speaking=True
        )
        
        assert decision.action == FilterAction.ALLOW


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
