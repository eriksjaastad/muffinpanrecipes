"""Custom exception classes for the AI Creative Team system."""


class CreativeTeamError(Exception):
    """Base exception for all Creative Team errors."""

    pass


class AgentError(CreativeTeamError):
    """Error related to agent operations."""

    pass


class PersonalityError(AgentError):
    """Error related to personality configuration or loading."""

    pass


class MessageError(CreativeTeamError):
    """Error related to inter-agent messaging."""

    pass


class PipelineError(CreativeTeamError):
    """Error related to recipe pipeline operations."""

    pass


class MemoryError(CreativeTeamError):
    """Error related to agent memory operations."""

    pass


class TaskError(CreativeTeamError):
    """Error related to task execution."""

    pass
