"""
Property-based tests for the Message System.

Feature: ai-creative-team
"""

import pytest
import tempfile
from hypothesis import given, strategies as st
from pathlib import Path

from backend.messaging.message_system import MessageSystem
from backend.messaging.message_handler import MessageHandler
from backend.core.agent import Message
from backend.core.types import MessageType


# Strategies
agent_roles = st.sampled_from(["baker", "creative_director", "art_director", "copywriter", "site_architect"])
message_types = st.sampled_from(list(MessageType))
message_content = st.text(min_size=10, max_size=200)


# Feature: ai-creative-team, Property 8: Message Delivery Accuracy
@given(
    sender=agent_roles,
    recipient=agent_roles,
    content=message_content,
    msg_type=message_types
)
def test_message_delivery_accuracy(
    sender: str, recipient: str, content: str, msg_type: MessageType
) -> None:
    """
    Property 8: Message Delivery Accuracy
    
    For any message sent from agent A to agent B, the message should:
    1. Arrive in agent B's queue
    2. Contain the exact content sent
    3. Preserve sender and recipient information
    4. Maintain message type
    
    Validates: Requirements 10.1, 10.2
    """
    # Use context manager instead of pytest fixture for hypothesis compatibility
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        
        # Create message system
        msg_system = MessageSystem(storage_path=tmp_path / "messages")
        
        # Register agents
        msg_system.register_agent(sender)
        msg_system.register_agent(recipient)
        
        # Create handler for sender
        handler = MessageHandler(sender, msg_system)
        
        # Send message
        handler.send(
            sender=sender,
            recipient=recipient,
            content=content,
            message_type=msg_type
        )
        
        # Retrieve messages for recipient
        messages = msg_system.get_messages_for(recipient, clear=False)
        
        # Verify message was delivered
        assert len(messages) >= 1, "Message should be queued for recipient"
        
        # Get the last message (the one we just sent)
        delivered_message = messages[-1]
        
        # Verify accuracy
        assert delivered_message.sender == sender, "Sender must be preserved"
        assert delivered_message.recipient == recipient, "Recipient must be preserved"
        assert delivered_message.content == content, "Content must be exact"
        assert delivered_message.message_type == msg_type, "Message type must be preserved"
        assert delivered_message.id is not None, "Message must have ID"
        assert delivered_message.timestamp is not None, "Message must have timestamp"


# Feature: ai-creative-team, Property 9: Message Logging Completeness
@given(
    messages_to_send=st.lists(
        st.tuples(agent_roles, agent_roles, message_content, message_types),
        min_size=1,
        max_size=20
    )
)
def test_message_logging_completeness(messages_to_send: list) -> None:
    """
    Property 9: Message Logging Completeness
    
    All messages sent through the system should:
    1. Be logged in message history
    2. Be retrievable by sender/recipient pair
    3. Be retrievable by message type
    4. Have complete metadata
    
    Validates: Requirements 10.3
    """
    # Use context manager instead of pytest fixture for hypothesis compatibility
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        
        # Create message system
        msg_system = MessageSystem(storage_path=tmp_path / "messages")
        
        # Track what we send
        sent_count = 0
        
        # Send all messages
        for sender, recipient, content, msg_type in messages_to_send:
            msg_system.register_agent(sender)
            msg_system.register_agent(recipient)
            
            handler = MessageHandler(sender, msg_system)
            handler.send(
                sender=sender,
                recipient=recipient,
                content=content,
                message_type=msg_type
            )
            sent_count += 1
        
        # Verify all messages are in history
        assert len(msg_system.message_history) == sent_count, \
            "All sent messages must be logged in history"
        
        # Verify each message has complete metadata
        for msg in msg_system.message_history:
            assert msg.id is not None, "Message must have ID"
            assert msg.sender is not None, "Message must have sender"
            assert msg.recipient is not None, "Message must have recipient"
            assert msg.content is not None, "Message must have content"
            assert msg.message_type is not None, "Message must have type"
            assert msg.timestamp is not None, "Message must have timestamp"
        
        # Verify statistics are accurate
        stats = msg_system.get_statistics()
        assert stats["total_messages"] == sent_count, \
            "Statistics must match actual message count"
        
        # Verify we can retrieve messages by type
        for msg_type in MessageType:
            messages_of_type = msg_system.get_messages_by_type(msg_type)
            expected_count = sum(1 for _, _, _, mt in messages_to_send if mt == msg_type)
            assert len(messages_of_type) == expected_count, \
                f"Should retrieve all {msg_type.value} messages"


def test_message_persistence_across_reload(tmp_path: Path) -> None:
    """Test that messages persist when system is reloaded."""
    storage = tmp_path / "messages"
    
    # Create system and send messages
    msg_system1 = MessageSystem(storage_path=storage)
    msg_system1.register_agent("baker")
    msg_system1.register_agent("creative_director")
    
    handler = MessageHandler("baker", msg_system1)
    handler.send(
        sender="baker",
        recipient="creative_director",
        content="Test message for persistence",
        message_type=MessageType.FEEDBACK_REQUEST
    )
    
    # Force save
    msg_system1._save_history()
    
    initial_count = len(msg_system1.message_history)
    assert initial_count >= 1
    
    # Create new system (reload)
    msg_system2 = MessageSystem(storage_path=storage)
    
    # Verify history was loaded
    assert len(msg_system2.message_history) == initial_count, \
        "Message history should persist across reloads"
    
    # Verify message content
    assert msg_system2.message_history[0].content == "Test message for persistence"


def test_conversation_retrieval(tmp_path: Path) -> None:
    """Test that we can retrieve conversations between specific agents."""
    msg_system = MessageSystem(storage_path=tmp_path / "messages")
    
    # Set up agents
    agents = ["baker", "creative_director", "art_director"]
    for agent in agents:
        msg_system.register_agent(agent)
    
    # Baker <-> Creative Director conversation
    baker_handler = MessageHandler("baker", msg_system)
    cd_handler = MessageHandler("creative_director", msg_system)
    
    baker_handler.send("baker", "creative_director", "Thoughts on this recipe?", MessageType.FEEDBACK_REQUEST)
    cd_handler.send("creative_director", "baker", "Looks great!", MessageType.APPROVAL_NOTIFICATION)
    
    # Unrelated message
    baker_handler.send("baker", "art_director", "Need photos", MessageType.TASK_ASSIGNMENT)
    
    # Retrieve conversation
    conversation = msg_system.get_conversation("baker", "creative_director")
    
    assert len(conversation) == 2, "Should have 2 messages in baker-CD conversation"
    assert all(
        (msg.sender == "baker" or msg.sender == "creative_director") and
        (msg.recipient == "baker" or msg.recipient == "creative_director")
        for msg in conversation
    ), "All messages should be between baker and creative_director"


def test_message_queue_clearing(tmp_path: Path) -> None:
    """Test that message queues can be cleared after retrieval."""
    msg_system = MessageSystem(storage_path=tmp_path / "messages")
    msg_system.register_agent("baker")
    msg_system.register_agent("creative_director")
    
    handler = MessageHandler("baker", msg_system)
    handler.send("baker", "creative_director", "Message 1", MessageType.TASK_ASSIGNMENT)
    handler.send("baker", "creative_director", "Message 2", MessageType.TASK_ASSIGNMENT)
    
    # Get messages without clearing
    messages = msg_system.get_messages_for("creative_director", clear=False)
    assert len(messages) == 2
    
    # Queue should still have messages
    messages_again = msg_system.get_messages_for("creative_director", clear=False)
    assert len(messages_again) == 2
    
    # Clear the queue
    messages_cleared = msg_system.get_messages_for("creative_director", clear=True)
    assert len(messages_cleared) == 2
    
    # Queue should now be empty
    messages_after_clear = msg_system.get_messages_for("creative_director", clear=False)
    assert len(messages_after_clear) == 0


def test_message_statistics() -> None:
    """Test that message statistics are accurately tracked."""
    msg_system = MessageSystem()
    
    # Register agents
    for agent in ["baker", "creative_director", "art_director"]:
        msg_system.register_agent(agent)
    
    # Send various messages
    baker_handler = MessageHandler("baker", msg_system)
    baker_handler.send("baker", "creative_director", "Test 1", MessageType.FEEDBACK_REQUEST)
    baker_handler.send("baker", "creative_director", "Test 2", MessageType.FEEDBACK_REQUEST)
    baker_handler.send("baker", "art_director", "Test 3", MessageType.TASK_ASSIGNMENT)
    
    stats = msg_system.get_statistics()
    
    assert stats["total_messages"] == 3
    assert stats["by_sender"]["baker"] == 3
    assert stats["by_recipient"]["creative_director"] == 2
    assert stats["by_recipient"]["art_director"] == 1
    assert stats["by_type"]["feedback_request"] == 2
    assert stats["by_type"]["task_assignment"] == 1
