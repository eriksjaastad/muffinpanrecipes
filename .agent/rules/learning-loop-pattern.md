# Learning Loop Pattern

purpose: Close the gap between "documented" and "applied"
scope: Any project with AI collaboration or automation

## Core Loop

trigger: failure, unexpected behavior, or exceptional success
step_1: Document what happened and root cause
step_2: Track as learning debt (DOCUMENTED but not yet COMPILED)
step_3: Apply relevant learnings before next related task
step_4: After failure, check if it was preventable

## Four Components

component_1: Trigger — event that signals "something worth learning from"
component_2: Document — capture what happened, where (learnings file, incident report)
component_3: Analyze — determine root cause, was this preventable?
component_4: Reinforce — update instructions, constraints, or templates to prevent recurrence

## The Key Question

after_every_failure: "Was this preventable from existing knowledge?"
if_yes: learning loop has a gap — fix the process, flag as preventable failure
if_no: new learning discovered — document and reinforce

## Learning Debt Lifecycle

lifecycle: OBSERVED → DOCUMENTED → COMPILED → APPLIED
learning_debt: documented but not yet compiled into templates or rules
compilation_trigger: 2+ preventable failures from the same learning → MUST compile into templates

## Downstream Harm Estimate

when: risky tasks, complex automations, destructive operations
include_in_prompt: what breaks if this fails, who pays, recovery cost
include_in_prompt: known pitfalls from learnings files that apply
include_in_prompt: assumptions that would break the approach

## Prompt Integration

rule: task prompts should reference applicable learnings from the project's learnings file
rule: Floor Manager blocks execution if learnings acknowledgment is missing or stale
rule: The Architect consults learnings BEFORE drafting task prompts

## Implementation Scale

simple_projects: single learnings file with active learnings table and log
complex_projects: learnings file + prompt harm estimates + preventable failure tracking + debt tracker
start_simple: add complexity only when the simple version isn't catching failures
example: see Project-workflow.md "Failure Log Table" for a production learning loop implementation
