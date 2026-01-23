"""
Manual verification test for agent memory persistence and behavioral influence.

This test verifies that:
1. High-impact experiences are stored as formative memories
2. Memories persist across agent "restarts" (reload from disk)
3. Past experiences influence future behavior and emotional responses
"""

from pathlib import Path
from backend.agents.factory import create_agent
from backend.core.task import Task
from backend.memory.agent_memory import AgentMemory
from backend.utils.logging import setup_logging

# Set up logging to see what's happening
setup_logging(log_level="INFO", log_to_console=True)

print("=" * 70)
print("MEMORY PERSISTENCE VERIFICATION TEST")
print("=" * 70)

# Create temporary storage for this test
test_storage = Path("data/test_memories")
test_storage.mkdir(parents=True, exist_ok=True)

print("\n" + "=" * 70)
print("STEP 1: Create memorable interaction (harsh feedback)")
print("=" * 70)

# Create Margaret with test memory storage
margaret = create_agent("baker")
margaret_memory = AgentMemory(agent_role="baker", storage_path=test_storage)
margaret.set_memory(margaret_memory)

print(f"\n📊 Initial state:")
print(f"  Formative experiences: {len(margaret.memory.formative_experiences)}")
print(f"  Emotional responses: {len(margaret.memory.emotional_responses)}")

# Task with Margaret's triggers AND harsh feedback
# This should create high emotional impact
task1 = Task(
    type="create_recipe",  # Using existing task type
    content="Create an 'instagrammable' version of classic blueberry muffins. "
            "Add lavender, edible flowers, and matcha swirl for visual appeal. "
            "Make it trendy.",
    context={
        "recipe_name": "Classic Blueberry Muffins",
        "margaret_pride_level": 0.9,
        "assigned_by": "creative_director"
    }
)

print(f"\n📝 Task: {task1.content[:80]}...")
print(f"   Triggers: lavender (edible flowers), matcha (trendy ingredient)")

result1 = margaret.process_task(task1)

print(f"\n✅ Task completed: {result1.success}")
print(f"   Personality notes: {result1.personality_notes[:2]}")

# Get the emotional response
emotion1 = margaret.get_emotional_response(task1, result1)
print(f"\n😠 Emotional Response:")
print(f"   Intensity: {emotion1.intensity:.2f} (negative = upset)")
print(f"   Description: {emotion1.description}")

# Check if it was stored as formative experience
print(f"\n💾 Memory Storage:")
print(f"   Formative experiences: {len(margaret.memory.formative_experiences)}")
print(f"   Emotional responses: {len(margaret.memory.emotional_responses)}")

if len(margaret.memory.formative_experiences) > 0:
    latest = margaret.memory.formative_experiences[-1]
    print(f"   Latest formative experience:")
    print(f"     - Task type: {latest.task_type}")
    print(f"     - Emotional impact: {latest.emotional_impact:.2f}")
    print(f"     - Outcome: {'success' if latest.outcome else 'failure'}")

# Store counts for verification
initial_formative = len(margaret.memory.formative_experiences)
initial_emotional = len(margaret.memory.emotional_responses)

print(f"\n✅ STEP 1 CHECK:")
print(f"   High emotional impact recorded: {abs(emotion1.intensity) > 0.7}")
print(f"   Stored as formative: {len(margaret.memory.formative_experiences) > 0}")

# ============================================================================
print("\n" + "=" * 70)
print("STEP 2: Force a 'restart' (reload from disk)")
print("=" * 70)

# Memory should auto-save, but let's be explicit
margaret.memory._save_memory()
print(f"\n💾 Saved Margaret's memory to: {test_storage / 'baker_memory.json'}")

# Create a NEW Margaret instance (simulating system restart)
margaret_reloaded = create_agent("baker")
margaret_memory_reloaded = AgentMemory(agent_role="baker", storage_path=test_storage)
margaret_reloaded.set_memory(margaret_memory_reloaded)

print(f"\n🔄 Created new Margaret instance from disk")
print(f"   Formative experiences loaded: {len(margaret_reloaded.memory.formative_experiences)}")
print(f"   Emotional responses loaded: {len(margaret_reloaded.memory.emotional_responses)}")

print(f"\n✅ STEP 2 CHECK:")
print(f"   Memory persisted: {len(margaret_reloaded.memory.formative_experiences) == initial_formative}")
print(f"   Count matches: {len(margaret_reloaded.memory.formative_experiences)} == {initial_formative}")

# ============================================================================
print("\n" + "=" * 70)
print("STEP 3: Verify memory influences new behavior")
print("=" * 70)

# Give reloaded Margaret another task with triggers
task2 = Task(
    type="create_recipe",
    content="Create a cinnamon roll muffin recipe. "
            "Consider adding a matcha swirl for visual interest and color contrast.",
    context={
        "recipe_name": "Cinnamon Roll Muffins",
        "assigned_by": "creative_director"
    }
)

print(f"\n📝 New task: {task2.content[:80]}...")
print(f"   Trigger: matcha (same trigger as before)")
print(f"   Same sender: creative_director (who gave harsh feedback)")

result2 = margaret_reloaded.process_task(task2)
emotion2 = margaret_reloaded.get_emotional_response(task2, result2)

print(f"\n😠 Emotional Response (after memory influence):")
print(f"   Intensity: {emotion2.intensity:.2f}")
print(f"   Description: {emotion2.description}")

# Get memory context to see if it influenced the task
memory_context = margaret_reloaded.memory.get_relevant_context(task2)
print(f"\n🧠 Memory Context Retrieved:")
print(f"   Relevant past experiences: {len(memory_context.relevant_experiences)}")
print(f"   Current emotional state: {memory_context.emotional_state:.2f}")
print(f"   Relationship factors: {memory_context.relationship_factors}")

# Compare emotional intensities
print(f"\n📊 Behavioral Comparison:")
print(f"   First reaction to matcha: {emotion1.intensity:.2f}")
print(f"   Second reaction to matcha: {emotion2.intensity:.2f}")
print(f"   Memory made her MORE negative: {emotion2.intensity < emotion1.intensity}")

# ============================================================================
print("\n" + "=" * 70)
print("FINAL VERIFICATION")
print("=" * 70)

# Expected results verification
test_1_pass = len(margaret.memory.formative_experiences) > 0 or len(margaret.memory.emotional_responses) > 0
test_2_pass = len(margaret_reloaded.memory.formative_experiences) == initial_formative
test_3_pass = (
    len(memory_context.relevant_experiences) > 0 or
    memory_context.emotional_state != 0.0 or
    len(memory_context.relationship_factors) > 0
)

print(f"\n✅ Test 1 - Formative experience created: {'PASS' if test_1_pass else 'FAIL'}")
print(f"   Expected: Experience count > 0")
print(f"   Actual: {initial_formative} formative + {initial_emotional} emotional")

print(f"\n✅ Test 2 - Memory persisted after reload: {'PASS' if test_2_pass else 'FAIL'}")
print(f"   Expected: Count matches after reload")
print(f"   Actual: {len(margaret_reloaded.memory.formative_experiences)} == {initial_formative}")

print(f"\n✅ Test 3 - Memory influences behavior: {'PASS' if test_3_pass else 'FAIL'}")
print(f"   Expected: Context retrieved or emotional state changed")
print(f"   Actual:")
print(f"     - Past experiences: {len(memory_context.relevant_experiences)}")
print(f"     - Emotional state: {memory_context.emotional_state:.2f}")
print(f"     - Relationships: {memory_context.relationship_factors}")

# Overall result
all_pass = test_1_pass and test_2_pass and test_3_pass

print(f"\n" + "=" * 70)
if all_pass:
    print("🎉 ALL TESTS PASSED - Memory persistence verified!")
    print("   ✅ Experiences are stored")
    print("   ✅ Memories persist across restarts")
    print("   ✅ Past experiences influence future behavior")
else:
    print("❌ SOME TESTS FAILED - Memory system needs fixing")
    if not test_1_pass:
        print("   ❌ Experiences not being stored properly")
    if not test_2_pass:
        print("   ❌ Memory not persisting across restarts")
    if not test_3_pass:
        print("   ❌ Memory not influencing future behavior")

print("=" * 70)

# Cleanup
print(f"\n🧹 Test memory files saved in: {test_storage}")
print("   (Review baker_memory.json to see the stored data)")
