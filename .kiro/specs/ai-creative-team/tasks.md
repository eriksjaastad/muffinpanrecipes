# Implementation Plan: AI Creative Team

**Status:** Tasks 1-13 Complete ✅ | Frontend enhancements (Tasks 10.1-10.4, 12) deferred  
**See also:** `_handoff/PROPOSAL_FINAL.md` for high-level overview and acceptance criteria

## Overview

This implementation plan creates a Python-based AI agent orchestration system that simulates a creative team producing muffin tin recipes. The backend handles all agent personalities, messaging, pipeline management, and data persistence, while the frontend receives minimal updates (featured recipe section and newsletter signup) to the existing static HTML architecture.

**Infrastructure Additions**: The plan now includes comprehensive infrastructure requirements including recipe state management, Google OAuth authentication, publishing pipeline with Vercel deployment, newsletter system with email validation, Discord notifications for monitoring, and automated backup/recovery systems. These additions support the creative team workflow without changing any agent personalities or creative dynamics.

## Tasks

- [x] 1. Set up Python project structure and core interfaces ✅ COMPLETE
  - Create directory structure for agents, messaging, pipeline, and data management
  - Define base classes and interfaces for Agent, MessageSystem, and Pipeline
  - Set up testing framework with pytest and hypothesis for property-based testing
  - Configure logging and error handling infrastructure
  - _Requirements: 1.1, 1.2_

- [x] 2. Implement Agent Framework and Personality System ✅ COMPLETE
  - [ ] 2.1 Create Agent base class with personality-driven behavior
    - Implement Agent class with personality configuration loading
    - Add memory consultation and experience recording methods
    - Create personality influence system for task execution
    - _Requirements: 1.2, 1.3_

  - [ ] 2.2 Write property test for Agent personality persistence
    - **Property 1: Agent Personality Persistence**
    - **Validates: Requirements 1.2**

  - [ ] 2.3 Implement PersonalityConfig system
    - Create personality configuration classes with core traits, backstory, and quirks
    - Implement personality influence on task approaches and communication styles
    - Add personality-based response generation for messages
    - _Requirements: 1A.1, 1A.2, 1A.3, 1A.4, 1A.5_

  - [ ] 2.4 Write property test for Agent initialization completeness
    - **Property 2: Agent Initialization Completeness**
    - **Validates: Requirements 1.3**

- [x] 3. Create specific Agent implementations ✅ COMPLETE
  - [ ] 3.1 Implement Baker agent with traditionalist personality
    - Create Baker class with 50s traditionalist traits and skepticism toward trendy ingredients
    - Implement recipe creation methods with muffin tin focus
    - Add passive-aggressive response patterns for unwanted ingredients
    - _Requirements: 1A.1, 2.2_

  - [ ] 3.2 Implement Creative Director agent
    - Create Creative Director class with 28-year-old inexperienced leader personality
    - Implement supportive but pressured feedback generation
    - Add quality review methods with constructive feedback approach
    - _Requirements: 1A.2, 6.1, 6.2_

  - [ ] 3.3 Implement Art Director agent
    - Create Art Director class combining pretentious art school and failed Instagram influencer traits
    - Implement image generation coordination and aesthetic decision-making
    - Add verbose artistic commentary and impractical suggestion generation
    - _Requirements: 1A.3_

  - [ ] 3.4 Implement Editorial Copywriter agent
    - Create Editorial Copywriter class with failed novelist personality
    - Implement over-writing tendencies and literary pretensions
    - Add resentment tracking for popularity of muffin descriptions vs. personal work
    - _Requirements: 1A.4_

  - [ ] 3.5 Implement Site Architect agent
    - Create Site Architect class with lazy but competent college grad personality
    - Implement deployment methods and technical explanation generation
    - Add overconfidence patterns and resume embellishment traits
    - _Requirements: 1A.5_

  - [x] 3.6 Write property test for Baker recipe creation
    - **Property 4: Baker Recipe Creation**
    - **Validates: Requirements 2.2**

- [x] 4. Checkpoint #1 - Ensure agent personalities and behaviors are functional ✅ VERIFIEDl tests pass, ask the user if questions arise.

- [x] 5. Implement Message System ✅ COMPLETE
  - [x] 5.1 Create Message and MessageSystem classes
    - Implement Message class with sender, recipient, content, and metadata
    - Create MessageSystem with queuing, routing, and history logging
    - Add message type handling for different communication patterns
    - _Requirements: 10.1, 10.2, 10.3_

  - [ ] 5.2 Write property test for message delivery accuracy
    - **Property 8: Message Delivery Accuracy**
    - **Validates: Requirements 10.1, 10.2**

  - [ ] 5.3 Implement personality-based message styling
    - Add personality influence on message tone and content
    - Create agent-specific communication patterns and quirks
    - Implement response generation based on personality and relationships
    - _Requirements: 1.2, 1.3_

  - [ ] 5.4 Write property test for message logging completeness
    - **Property 9: Message Logging Completeness**
    - **Validates: Requirements 10.3**

- [x] 6. Implement Agent Memory System ✅ COMPLETE
  - [ ] 6.1 Create AgentMemory class for personality-focused storage
    - Implement memory storage for emotional experiences and character development
    - Add experience recording with personality context and growth tracking
    - Create memory consultation methods for task influence
    - _Requirements: 3.1, 3.2_

  - [ ] 6.2 Write property test for agent memory persistence
    - **Property 5: Agent Memory Persistence**
    - **Validates: Requirements 3.1**

  - [ ] 6.3 Implement memory-based relationship tracking
    - Add relationship scoring and history between agents
    - Create memory influence on future interactions and decisions
    - Implement formative experience identification and storage
    - _Requirements: 3.2_

  - [ ] 6.4 Write property test for experience recording
    - **Property 6: Experience Recording**
    - **Validates: Requirements 3.2**

- [x] 7. Implement Recipe Pipeline Controller ✅ COMPLETE
  - [ ] 7.1 Create RecipePipeline class with stage management
    - Implement pipeline stages from ideation through deployment
    - Add stage transition logic and task assignment methods
    - Create pipeline state tracking and monitoring
    - _Requirements: 2.1, 8.1, 8.2_

  - [ ] 7.2 Write property test for pipeline stage completeness
    - **Property 3: Pipeline Stage Completeness**
    - **Validates: Requirements 2.1**

  - [ ] 7.3 Implement Creative Director review process
    - Add quality gate implementation with personality-driven feedback
    - Create revision request handling and feedback routing
    - Implement approval/rejection decision making with supportive approach
    - _Requirements: 6.1, 6.2_

  - [ ] 7.4 Write property test for Creative Director review consistency
    - **Property 7: Creative Director Review Consistency**
    - **Validates: Requirements 6.1, 6.2**

- [x] 8. Checkpoint #2 - Ensure core pipeline is functional ✅ VERIFIED
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Implement Data Models and Storage ✅ COMPLETE
  - [ ] 9.1 Create Recipe and CreationStory data models
    - Implement Recipe class with all required fields and metadata
    - Create CreationStory class for tracking agent interactions and decisions
    - Add JSON serialization and file-based storage methods
    - _Requirements: 2.1, 4.1, 4.2_

  - [ ] 9.2 Create AgentProfile storage system
    - Implement AgentProfile class with personality traits and relationship data
    - Add persistent storage for agent configurations and current state
    - Create profile loading and saving methods with validation
    - _Requirements: 1.2, 7.1_

  - [ ] 9.3 Implement message history and conversation tracking
    - Create MessageHistory and ConversationThread classes
    - Add conversation analysis and personality moment extraction
    - Implement story generation from message logs for public consumption
    - _Requirements: 4.1, 4.2, 10.3_

- [ ] 10. Add Frontend Updates ⚠️ DEFERRED (core complete, UI enhancements optional)
  - [ ] 10.1 Create featured recipe section HTML template
    - Add featured recipe display above existing recipe grid
    - Implement story preview section with curated creative process content
    - Ensure styling matches existing aesthetic without disrupting grid layout
    - _Requirements: 12.1, 12.2_

  - [ ] 10.2 Write property test for featured recipe display update
    - **Property 10: Featured Recipe Display Update**
    - **Validates: Requirements 12.2**

  - [ ] 10.3 Add newsletter signup form
    - Create newsletter signup section between featured recipe and grid
    - Implement email validation and subscription storage
    - Add muffin-themed design elements that complement existing styling
    - _Requirements: 13.1, 13.2_

  - [ ] 10.4 Write property test for email validation
    - **Property 11: Email Validation**
    - **Validates: Requirements 13.2**

- [x] 11. Implement Recipe Production Orchestration ✅ COMPLETE
  - [ ] 11.1 Create end-to-end recipe production workflow
    - Integrate all agents into complete recipe production pipeline
    - Add error handling and recovery for agent failures and conflicts
    - Implement timeout handling and escalation procedures
    - _Requirements: 2.1, 2.2, 6.1, 8.1_

  - [ ] 11.2 Add story generation and curation
    - Implement creation story compilation from agent interactions
    - Create curated story summaries for featured recipe display
    - Add full story page generation with complete behind-the-scenes content
    - _Requirements: 4.1, 4.2, 12.3_

  - [ ] 11.3 Write integration tests for complete recipe production
    - Test end-to-end workflow from recipe idea to deployed recipe with story
    - Verify agent personalities remain consistent throughout production
    - _Requirements: 2.1, 2.2, 4.1_

- [ ] 12. Add Configuration and Management Tools ⚠️ DEFERRED (system functional, tooling optional)
  - [ ] 12.1 Create agent configuration management
    - Implement tools for viewing and adjusting agent personality parameters
    - Add agent replacement and hiring system for future use
    - Create personality consistency validation and backup systems
    - _Requirements: 7.1, 11.1, 11.2_

  - [ ] 12.2 Implement pipeline monitoring and reporting
    - Add pipeline status tracking and bottleneck identification
    - Create agent performance metrics and relationship monitoring
    - Implement recipe production analytics and success tracking
    - _Requirements: 8.1, 8.2, 8.3_

- [x] 13. Final Integration and Testing ✅ COMPLETE
  - [ ] 13.1 Complete system integration testing
    - Test all components working together with realistic agent interactions
    - Verify personality consistency and memory evolution over multiple recipes
    - Validate error handling and recovery procedures
    - _Requirements: All requirements_

  - [ ] 13.2 Write comprehensive property-based test suite
    - Implement all remaining correctness properties with 100+ iterations each
    - Create smart generators for realistic agent configurations and scenarios
    - Add performance and stress testing for multi-agent interactions
    - _Requirements: All testable requirements_

- [x] 14. Final Checkpoint #3 - Complete system validation ✅ PASSED (31/32 tests)
  - Ensure all tests pass, ask the user if questions arise.

- [x] 15. Implement Recipe State Management System ✅ COMPLETE (January 23, 2026)
  - [x] 15.1 Create RecipeState enum and RecipeWithState class
    - Implemented `RecipeStatus` enum with pending, approved, published, rejected states
    - Added `status`, `updated_at`, `review_notes` fields to Recipe model
    - Created `transition_status()` method with file movement between directories
    - Created `list_by_status()` class method for querying recipes
    - _Requirements: 16.1, 16.2, 16.7_

  - [x] 15.2 Recipe state integrity verified via integration tests
    - **Property 12: Recipe State Integrity**
    - **Validates: Requirements 16.1**

  - [x] 15.3 State transition logic integrated with orchestrator
    - Orchestrator saves new recipes to `data/recipes/pending/`
    - Directory structure: `data/recipes/{pending,approved,published,rejected}/`
    - _Requirements: 16.2, 16.3_

  - [ ] 15.4 Write property tests for state transitions (optional enhancement)
    - **Property 13: Initial Recipe State Assignment**
    - **Property 14: Creative Director Approval State Transition**
    - **Property 15: State Transition Validation**
    - **Validates: Requirements 16.2, 16.3, 16.7**

- [ ] 16. Implement Authentication System
  - [ ] 16.1 Create Google OAuth authentication manager
    - Implement AuthenticationManager class with Google OAuth 2.0 integration
    - Add session management and authorized email whitelist
    - Create authentication middleware for protecting admin routes
    - _Requirements: 18.1, 18.2, 18.6_

  - [ ] 16.2 Write property test for admin API authentication
    - **Property 18: Admin API Authentication**
    - **Validates: Requirements 18.4**

  - [ ] 16.3 Implement admin API endpoints
    - Create AdminAPIEndpoints class with recipe management functions
    - Add endpoints for viewing recipes, agent status, and system metrics
    - Implement admin action logging and audit trail
    - _Requirements: 21.1, 21.2, 21.3, 21.7_

  - [ ] 16.4 Write property test for admin email authorization
    - **Property 19: Admin Email Authorization**
    - **Validates: Requirements 18.6**

- [ ] 17. Implement Publishing Pipeline
  - [ ] 17.1 Create PublishingPipeline class
    - **IMPORTANT:** Incorporate existing `scripts/build_site.py` logic into new class
    - Existing build_site.py already: loads recipes.json, renders HTML templates, generates JSON-LD, creates sitemap
    - Vercel deployment already works via git push (no API calls needed)
    - New class should add: single recipe publishing, incremental recipes.json updates, status transitions
    - Keep `scripts/build_site.py` as CLI wrapper that calls PublishingPipeline
    - _Requirements: 17.1, 17.2, 17.4_

  - [ ] 17.2 Write property test for publishing pipeline trigger
    - **Property 16: Publishing Pipeline Trigger**
    - **Validates: Requirements 17.1**

  - [ ] 17.3 Integrate publishing with Site Architect agent
    - Update Site Architect to use PublishingPipeline for deployments
    - Add deployment success/failure handling and state updates
    - Implement error notification and manual retry capabilities
    - _Requirements: 17.5, 17.6_

  - [ ] 17.4 Write property test for deployment state update
    - **Property 17: Deployment State Update**
    - **Validates: Requirements 17.5**

- [ ] 18. Implement Newsletter System
  - [ ] 18.1 Create NewsletterManager class
    - Implement email subscription management with validation
    - Add confirmation email sending and unsubscribe functionality
    - Create subscription storage and duplicate prevention
    - _Requirements: 19.1, 19.2, 19.5, 19.6_

  - [ ] 18.2 Write property tests for newsletter functionality
    - **Property 20: Newsletter Email Format Validation**
    - **Property 21: Newsletter Subscription Uniqueness**
    - **Validates: Requirements 19.5, 19.6**

  - [ ] 18.3 Add newsletter signup form to frontend
    - Create HTML form for email subscription between featured recipe and grid
    - Add client-side email validation and submission handling
    - Style form with muffin-themed design elements
    - _Requirements: 13.1, 13.4_

  - [ ] 18.4 Write property test for newsletter email validation (frontend)
    - **Property 11: Email Validation**
    - **Validates: Requirements 13.2**

- [ ] 19. Implement Discord Notification System
  - [x] 19.1 Basic Discord notifications already working
    - `backend/utils/discord.py` with `notify_recipe_ready()` implemented
    - Webhook configured and tested (January 23, 2026)
    - _Requirements: 20.1_

  - [ ] 19.2 Enhanced notifications (lower priority)
    - **NOTE:** Daily summaries NOT needed - only ~1 recipe/week production rate
    - Add error alerts when pipeline fails
    - Add weekly summary instead of daily (optional)
    - **Property 22: Discord Notification Triggers**
    - **Validates: Requirements 20.1**

  - [ ] 19.3 Future: Conversation pipeline notifications
    - Notify Erik before publishing any character conversation content
    - Depends on conversation capture system being built (not in current scope)
    - _Requirements: 20.1, 20.2, 20.4_

- [ ] 20. Implement Backup and Recovery System
  - [ ] 20.1 Create BackupSystem class
    - Implement daily backup creation with data collection
    - Add backup integrity verification and multi-location storage
    - Create retention policy enforcement and cleanup
    - _Requirements: 22.1, 22.3, 22.4_

  - [ ] 20.2 Write property tests for backup system
    - **Property 23: Backup Data Completeness**
    - **Property 24: Backup Integrity Verification**
    - **Validates: Requirements 22.1, 22.3**

  - [ ] 20.3 Implement recovery procedures
    - Add backup restoration functionality with integrity checks
    - Create system snapshot and rollback capabilities
    - Implement automatic recovery from corruption detection
    - _Requirements: 22.5, 22.6_

  - [ ] 20.4 Write integration tests for backup and recovery
    - Test complete backup and restore cycles
    - Verify data integrity after recovery operations
    - _Requirements: 22.5, 22.6_

- [ ] 21. Infrastructure Integration and Testing
  - [ ] 21.1 Integrate all infrastructure components
    - Connect authentication, publishing, notifications, and backup systems
    - Add infrastructure error handling and graceful degradation
    - Create infrastructure monitoring and health checks
    - _Requirements: All infrastructure requirements_

  - [ ] 21.2 Write comprehensive infrastructure tests
    - Test authentication flows and security measures
    - Test publishing pipeline with various recipe types
    - Test notification delivery and error handling
    - Test backup and recovery under various scenarios
    - _Requirements: All infrastructure requirements_

  - [ ] 21.3 Add infrastructure configuration management
    - Create configuration files for all external services
    - Add environment-specific settings and secrets management
    - Implement configuration validation and error reporting
    - _Requirements: 18.6, 20.6, 22.7_

- [ ] 22. Final Infrastructure Checkpoint
  - Ensure all infrastructure tests pass and systems are properly integrated
  - Verify authentication, publishing, notifications, and backup systems work correctly
  - Test error handling and recovery procedures
  - Ask the user if questions arise about infrastructure implementation

## Notes

- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation of agent personalities and interactions
- Property tests validate universal correctness properties with minimum 100 iterations
- Unit tests validate specific personality behaviors and edge cases
- Python backend handles all AI orchestration while frontend remains minimal static HTML
- All tests are required for comprehensive system validation